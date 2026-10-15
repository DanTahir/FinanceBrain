> Draft — to be published by the author.

# Building a company brain that never sees your PII: FinanceBrain on FalkorDB

*By Dan Tahir, built for Graph Hacks 2026 (WeMakeDevs x FalkorDB)*

Ask anyone at a finance firm where the knowledge lives and you'll get a list: email, Slack, the ticketing system, a
wiki nobody updates, meeting notes, CRM notes and, off to one side, the transaction ledger. The answer to "why did we
stop onboarding crypto clients?" is spread across five of those systems. The person who really understands the
legacy reconciliation system is not the person the org chart says owns it. A fraud ring shows up in the ledger, but
you only see it once you notice that five "unrelated" clients share a phone number.

A company brain should join all of that up. In finance, that runs straight into two hard problems:

1. **Trust.** An answer about a wire, a deal or a regulator is worthless unless you can see exactly where it came
   from, and an agent that *acts* (freezes an account, drafts a SAR) has to be explainable and approved by a human.
2. **Privacy.** The data is full of PII: phone numbers, addresses, SSNs, card and account numbers. You do not want
   any of it in your graph, your vector index or your LLM prompts.

FinanceBrain is my attempt at both. I built it solo during the hackathon window, working with an AI coding agent
that wrote most of the code under my direction and review. **All the data is synthetic**: a fictional firm, Halcyon
Ridge Capital (~80 staff, 2,219 documents), and a smaller one, Brightwater Credit Union, which is there to prove
tenant isolation.

Live demo: **https://d1rnt6bwyb9n59.cloudfront.net** · Code: **https://github.com/DanTahir/FinanceBrain**

## What it does

FinanceBrain ingests synthetic email, Slack, tickets, policies, meeting transcripts, CRM notes and a ledger. It
**pseudonymizes PII before anything reaches the graph or any LLM or embedding call**. Then a team of agents works on
top of the graph. It covers all three hackathon tracks:

- **Agentic reasoning and actions over graphs.** An Investigator agent picks up a flagged $48,000 wire, finds the
  fraud ring behind it with graph algorithms, opens a case, proposes a freeze and a SAR, and hands off to a Compliance
  agent. Nothing happens until a human with the right scope clicks Approve.
- **Agent memory and coordination.** Every visitor gets private episodic and semantic memory. Agents share
  procedural memory (playbooks) and coordinate through tasks and a blackboard stored in the graph.
- **Company brain.** Documents, tickets and chats live in one graph. It answers "who owns" and "who actually knows",
  traces decisions back to the regulator email that started them, and keeps knowledge current as new data streams in.
  Every answer carries numbered citations and an evidence subgraph.

## Why a graph, and why FalkorDB

Most of the questions people actually ask are about relationships:

- Who knows about X?
- What depends on Y?
- Which clients share an identifier?
- Which document justified this decision, and what superseded it?

Those are traversals. But a company brain also needs semantic search over text, keyword search over codenames, graph
algorithms, per-user memory and somewhere for agents to coordinate.

The design choice I'm happiest with is that **FalkorDB is the only datastore**. The knowledge graph, the vector
index, the full-text index, agent memory, the coordination blackboard, ACLs, rate-limit and budget counters, and the
encrypted PII token vault all live in one FalkorDB/Redis server. There's no separate vector DB to keep in sync and no
Postgres for memory. Tenants and visitors are just different graphs:

| Graph | Purpose |
|---|---|
| `brain__<tenant>` | the company knowledge graph, plus cases, tasks, blackboard and playbooks |
| `pristine__<tenant>` | a seed snapshot used by "Reset demo" |
| `mem__<tenant>__<persona>__<visitor>` | one visitor's private memory |

Isolation is a property of *which graph a query runs on*. The server decides that from the auth context. The model
never decides it.

Here are three short, simplified examples of what this looks like. The full registry of queries is in
[CYPHER.md](CYPHER.md), and the schema is in [DATA_MODEL](DATA_MODEL.md).

**Blast radius** is a variable-length path over `DEPENDS_ON`:

```cypher
MATCH p = (dep:System)-[:DEPENDS_ON*1..3]->(s:System {id: $system})
RETURN dep.name, min(length(p)) AS hops
ORDER BY hops
```

For PricingSvc this returns seven dependents:

- 1 hop: OMS, PortfolioMgr, RiskEngine and TradeBlotter
- 2 hops: SettlementEngine and ReportingBI
- 3 hops: ReconLegacy

Variable-length bounds must be literals in FalkorDB, so the registry keeps one query per hop count.

**Semantic search with ACLs as traversal.** The vector index returns a top-k list. Access control is then a set of
pattern predicates on the same graph:

```cypher
MATCH (p:Person {id: $me})
CALL db.idx.vector.queryNodes('Document', 'embedding', 40, vecf32($q)) YIELD node, score
WHERE node.public OR (p)-[:CAN_ACCESS]->(node)
   OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(node)
   OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(node)
RETURN node.id, score
ORDER BY score ASC
```

The `search_knowledge` tool fuses this with full-text search (`db.idx.fulltext.queryNodes`) and fact vectors using
reciprocal-rank fusion.

**The fraud ring** comes from the trick described in the next section:

```cypher
MATCH (c1:Client)-[:HAS_IDENTIFIER]->(t:PiiToken)<-[:HAS_IDENTIFIER]-(c2:Client)
WHERE c1.id < c2.id
RETURN t.type, c1.id, c2.id
```

At scale, `detect_rings` runs `algo.WCC` over the bipartite `Client`–`PiiToken` subgraph and then scores each
component.

FalkorDB also ships the algorithms I needed as procedures. I use `algo.WCC` and label propagation for rings, and
`algo.pageRank` and `algo.betweenness` for key people and pass-through accounts. That made "graph algorithms as agent
tools" a short step rather than a separate system.

## The privacy trick: tokens become structure

Redaction runs first, before extraction, embedding or graph writes. It uses Presidio plus spaCy, with custom finance
recognizers:

- Luhn-checked card numbers
- ABA routing checksums
- IBAN mod-97
- SSNs, phone numbers and email addresses
- a client-name deny-list built from the CRM

Each detected value is replaced with a **consistent** token such as `⟨PHONE_0042⟩`. The sequence number is derived
from an HMAC of the normalized value, so the same phone number always produces the same token within a tenant.

That consistency is the key idea. Every token becomes a `PiiToken` node, and clients and accounts link to it with
`HAS_IDENTIFIER`. Two clients who share a phone number end up pointing at **one** node. A shared identifier turns
into graph structure without anyone storing or seeing the value.

That is how the planted ring is found. Five Halcyon clients share phone, address and device tokens, and the ring's
layered transfers exit through a single counterparty. No raw phone number is involved at any point: not in the graph,
not in a prompt, not in an embedding.

The raw values do exist, in one place. They sit in a token vault as AES-GCM ciphertexts in a Redis hash, with the
data key wrapped by AWS KMS (envelope encryption). Only the compliance officer persona can reveal a value, and every
reveal is logged as an `Action`.

Two backstops check that the pipeline holds:

- A **canary scan** walks every string property of every graph looking for raw PII patterns. The answer must be 0.
- An **outbound guard** blocks any LLM or embedding call whose payload contains raw PII patterns.

The same "access is traversal" idea enforces an information barrier around an MNPI deal, Project Falcon. Outsiders
cannot resolve the deal. Its codename is masked as `⟨restricted⟩` in structural results, and MNPI documents are
filtered in Cypher through `CAN_ACCESS` and `ON_DEAL_TEAM`.

## Agents and MCP

There are seven agents, each with a specific job:

| Agent | Role |
|---|---|
| Concierge | routes requests and loads memory |
| Brain | answers questions with citations and an evidence subgraph |
| Investigator | fraud, AML and barrier work; proposes actions (runs on Claude Opus 5.5) |
| Compliance | drafts SARs, runs barrier checks, takes over cases via handoff |
| Comms | drafts messages |
| Sentinel | watches new live-feed data and opens cases |
| Librarian | consolidates sessions into memory facts and playbooks |

They all run in a hand-rolled streaming tool loop (`tool_choice` auto, at most 8 steps). They discover their tools
through FinanceBrain's own **MCP server**, served over streamable HTTP at `/mcp`. It exposes 31 tools in five groups:

- **Retrieval**, including `run_cypher_readonly`, which uses `GRAPH.RO_QUERY`
- **Reasoning**: `find_path`, `who_knows`, `who_owns(as_of)`, `trace_decision`, `blast_radius`, `detect_rings`,
  `key_people`, and more
- **Actions**
- **Memory**
- **Coordination**

Auth uses signed HMAC tokens that carry tenant, persona, user and scopes. The live site mints a 24-hour read-only
token (`POST /api/mcp/token`), so you can point Claude Desktop at the same server and the same ACLs.

Explainability is built in, not added on:

- Every tool call records its Cypher, parameters, row counts and returned ids. The Chat view shows this as a
  collapsible trace panel.
- Citations only count documents that tools actually returned. Anything else is flagged as unverified.

There is deliberately **no MCP tool that approves anything**. Approval happens only in the UI, by a persona with the
`approve` scope.

## Memory per visitor

Each browser visitor gets their own `mem__…` graph, keyed by a cookie. It holds `Session`, `Turn`, `MemoryFact`,
`EntityRef` and `Preference` nodes.

Before an agent answers, relevant memories are recalled by vector search and injected into the prompt. After the
answer, the Librarian (Haiku) consolidates the turns into memory facts. It deduplicates by vector distance and
supersedes old facts instead of overwriting them. When a session followed a successful multi-tool method, the
Librarian also learns a tenant-wide `Playbook` that other agents can find later.

The Memory view shows all of this, including a Forget button.

## Keeping knowledge current

A brain that's only right on seed day isn't much of a brain. The corpus is split at a cutoff of 2026-10-01. In the
Ingestion & Redaction view, "Replay next day" sends later days through the real pipeline:

1. redact
2. extract with Haiku
3. resolve entities
4. embed
5. write with batched `UNWIND … MERGE`

After each day, the Sentinel scans the new data. Over the replay it does four things:

- picks up the second ring wire and opens a case
- catches a Falcon leak in Slack
- flags the departure of the only real ReconLegacy expert
- records the KYC pipeline moving from Ops to Compliance

Knowledge is **bitemporal**. `OWNS` edges and extracted facts carry `valid_from` and `valid_to`, and newer facts
`SUPERSEDE` older ones. Nothing is deleted, so `who_owns` can answer both "who owns the KYC pipeline now?" and "who
owned it in August?".

The crypto-onboarding decision works the same way. It was paused in August and reversed in October through a
`SUPERSEDES` chain, and `trace_decision` walks it from the regulator email to the board memo.

## Lessons learned

**Bedrock hit a quota wall, so I switched to OpenRouter.** I planned on Bedrock. Even after the use-case form, Claude
5.5 came back with "not available for this account", and the applied quotas were 0 for both Claude 5.5 and Cohere
Embed v4. I moved all LLM and embedding calls to OpenRouter. Cohere and Titan embeddings aren't available there, so I
use `voyageai/voyage-4` at 1024 dimensions. The production IAM role has no Bedrock permissions at all.

**There's no forced tool choice, so extraction uses a JSON schema.** Claude 5.5 on OpenRouter doesn't support forced
`tool_choice`. Extraction therefore uses `response_format` with a JSON schema and `provider.require_parameters`. The
generic JSON helper falls back to plain JSON with Pydantic validation and one repair retry.

**redis-py 8 changed the default socket timeout.** The asyncio client now defaults `socket_timeout` to 5 s, and the
`falkordb` client inherits it. My first production canary scan died with `TimeoutError` even though the server would
have finished. I raised the timeout to 65 s, just above FalkorDB's `TIMEOUT_MAX`, and added a regression test.

**FalkorDB has no null properties and no map properties.** Setting a property to null removes it, and map-valued
properties are rejected. Missing values are simply omitted, and nested data such as redaction counts is stored as
JSON strings. A related gotcha: vector `queryNodes` returns cosine **distance**, so lower is better. I verified that
in a Phase 0 spike before writing any retrieval code ([FALKORDB_NOTES](FALKORDB_NOTES.md)).

**`GRAPH.COPY` gives instant resets, with one catch.** "Reset demo" copies the pristine snapshot back over the live
graph with `GRAPH.COPY`. That's fast and copies indexes too. It forks the server, though, and can collide with an AOF
rewrite or a `BGSAVE`, so the copy retries with backoff.

## Results

These numbers come from the eval suite (summary in [evals/results/SUMMARY.md](../evals/results/SUMMARY.md)).

| Area | Result |
|---|---|
| Golden questions (39, real agents, Opus 5.5 judge) | 38/39 pass (97%), judge mean 4.85/5, 100% entity recall, 100% citation validity, 0 barrier violations; T1 16/16, T2 5/5, T3 17/18 |
| Redaction, Halcyon | 100% recall (1,345/1,345 injected spans), 99.6% precision |
| Redaction, Brightwater | 100% recall, 100% precision |
| Canary scan | 0 hits (127,800 strings scanned in production) |
| Graph checks for the storylines | 19/19 pass per tenant |
| Tenant isolation, information barrier, memory privacy and auth matrix | 155/155 checks pass |
| Retrieval (11 questions, per-persona ACL) | hit@10 72.7%, MRR 0.51 |
| Load test, read-only baseline | 1,168 reads/s at p95 17 ms (16 readers) |
| Load test, mixed | 253 write batches/s (12.6k rows/s) plus 781 reads/s at p95 28 ms (8 writers + 16 readers, one graph) |
| Production seed | runs fully offline from the committed extraction and embedding cache, for $0.000 in LLM cost |

On retrieval, hybrid search beat vector-only (MRR 0.42) and full-text-only (MRR 0.19). The expected sets are narrow,
so recall understates relevance. The load test ran on my dev laptop with a Docker FalkorDB, with 0 errors.

A live blast-radius answer takes about 6 s and costs about $0.025. The full 39-question golden run (real agents, both
phases, live-feed replay included) takes about 16 minutes and about $7 of model calls. Per-question results are in
[evals/results/golden.md](../evals/results/golden.md).

The golden number has a history, and I kept it. The first full run scored 36/39. Two of its failures were my eval's
fault: a "right now" question that was ambiguous at the live-feed cutoff, and a reference answer that contradicted my
own control-room policy. The third was a real gap: the Q&A agent had no tool to read procedural memory, so it could
not find the playbook the Librarian had learned. I fixed the gap, corrected the two items, and re-ran everything. The
one remaining failure is a judgement call on bus-factor risk, and I left it alone rather than tune prompts to the
eval. Both runs are in the repo.

## Try it

Open **https://d1rnt6bwyb9n59.cloudfront.net**. Pick a tenant and persona, then try one of the guided questions:

- "Investigate the flagged $48k wire and tell me what we should do about it."
- "Who should I ask about ReconLegacy, and what is our risk if that person leaves?"
- "Why did we stop onboarding crypto clients?"

Then switch to the engineer persona and ask about Project Falcon to watch the information barrier hold.

The demo has a few guardrails:

- per-IP rate limits
- a daily budget, after which it serves cached guided answers
- a Reset demo button

The code, infra scripts and docs are at **https://github.com/DanTahir/FinanceBrain**.

## Closing

The idea I'd take from this project is that **privacy and structure don't have to compete**. Pseudonymize first, and
make the pseudonyms consistent. The graph then still sees *that* two things are connected without ever learning
*what* connects them. FalkorDB made it practical to keep everything else in one place too: graph, vectors, text,
algorithms, memory and coordination. The result is a brain you can audit end to end.