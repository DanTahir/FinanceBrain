# FinanceBrain: hackathon submission material

Copy-paste-ready text for the Graph Hacks 2026 (WeMakeDevs x FalkorDB) submission form.

## Project name

**FinanceBrain**

## One-liner

> Privacy-first multi-agent company brain for finance on FalkorDB: cited answers, fraud rings found via PII tokens, memory.

## Short description

FinanceBrain is a multi-agent "company brain" for a synthetic finance firm, with FalkorDB as its only datastore:
graph, vectors, full-text, algorithms, agent memory, blackboard, ACLs and an encrypted PII vault. It ingests email,
Slack, tickets, policies, meetings, CRM notes and a ledger, and pseudonymizes PII before anything reaches the graph
or an LLM. Consistent tokens become graph structure, so fraud rings surface without exposing a phone number. Agents
answer with citations and evidence subgraphs, investigate fraud and propose human-approved actions, remember each
visitor privately, and hand work off through shared graph state. A live feed keeps bitemporal knowledge current.

## Links

| | |
|---|---|
| Repository | https://github.com/DanTahir/FinanceBrain |
| Live demo | https://d1rnt6bwyb9n59.cloudfront.net |
| Demo video | TBD — add YouTube link |
| Data model | [DATA_MODEL](DATA_MODEL.md) |
| Cypher reference | [CYPHER](CYPHER.md) |
| Eval results | [SUMMARY](../evals/results/SUMMARY.md), [golden questions](../evals/results/golden.md) |
| AI disclosure | [AI_DISCLOSURE](AI_DISCLOSURE.md) |

## Results at a glance

- **Golden questions:** 38/39 pass (97%) with real agents and an Opus 5.5 judge (mean 4.85/5); T1 16/16, T2 5/5,
  T3 17/18; 0 information-barrier violations; 100% citation validity.
- **Privacy:** redaction recall 100% on 1,345 injected PII spans (precision 99.6%); canary scan 0 hits over 127,800
  strings in production.
- **Isolation:** 155/155 tenant, information-barrier, memory-privacy and auth checks pass.
- **Retrieval:** hybrid search hit@10 72.7%, MRR 0.51 (vector-only 0.42, full-text-only 0.19).
- **Concurrency:** 253 write batches/s plus 781 reads/s at p95 28 ms on one shared graph (local laptop, 0 errors).

## Tracks entered

All three:

1. Track 1: Agentic reasoning & actions over graphs
2. Track 2: Agent memory & coordination
3. Track 3: Company brain / knowledge graph

---

## Track 1: Agentic reasoning & actions over graphs

FinanceBrain's agents reason over the graph by calling graph tools that they discover through FinanceBrain's own MCP
server. That server exposes 31 tools for retrieval, reasoning, actions, memory and coordination. The Investigator
(Claude Opus 5.5) takes a flagged $48,000 wire and does three things:

- traces linked accounts through shared pseudonymized identifiers
- runs WCC-based ring detection
- opens a case with graph evidence

It then proposes a freeze and a SAR and hands off to the Compliance agent. No MCP tool can approve an action:
approval is a UI step by the CCO persona. Every tool call records its Cypher, parameters and returned ids, and
answers carry citations plus an evidence subgraph, so every action is explainable.

| Focus area | FinanceBrain feature | Where to see it |
|---|---|---|
| Multi-hop reasoning | `find_path`, `linked_accounts`, `blast_radius` (variable-length `DEPENDS_ON`, 7 dependents of PricingSvc across 3 hops) | Chat → tool/Cypher trace; [CYPHER](CYPHER.md) |
| Graph algorithms as tools | `detect_rings` (`algo.WCC` / label propagation), `key_people` (`algo.pageRank` / `algo.betweenness`) | Chat, Graph Explorer; [DATA_MODEL](DATA_MODEL.md#fraud-ring-scoring) |
| MCP tool selection | FastMCP server at `/mcp` with 31 tools; agents run a streaming tool loop (`tool_choice` auto, max 8 steps); `POST /api/mcp/token` mints a 24-hour read-only token for Claude Desktop (per-IP rate-limited) | Agents view; `/mcp` endpoint |
| Explainable actions | `open_case`, `add_case_evidence`, `propose_action` (freeze, SAR, notify, restrict); human Approve/Reject; reveals and approvals logged | Investigations view; Chat → Cypher trace |
| Information barrier | MNPI deal Project Falcon is filtered in Cypher via `CAN_ACCESS` / `ON_DEAL_TEAM`; its codename is masked for outsiders | Chat as engineer vs compliance_officer |

**Headline demo question:** *"Investigate the flagged $48k wire and tell me what we should do about it."*

---

## Track 2: Agent memory & coordination

Every visitor gets a private memory graph (`mem__<tenant>__<persona>__<visitor>`) holding sessions, turns, memory
facts, entity refs and preferences. Before an answer, relevant memories are recalled by vector search. Afterwards,
the Librarian agent consolidates them, superseding old facts rather than overwriting them, and learns tenant-wide
playbooks as procedural memory. Agents coordinate through shared graph state: tasks, claims, handoffs and a
blackboard live in the tenant graph. Investigator-to-Compliance handoffs appear on a timeline. Tenants and visitors
are separate FalkorDB graphs, so isolation is decided server-side. The load test showed 8 writers plus 16 readers on
one graph with 0 errors.

| Focus area | FinanceBrain feature | Where to see it |
|---|---|---|
| Episodic memory | `Session` / `Turn` nodes in the visitor's memory graph; `get_session_history` | Memory view (sessions) |
| Semantic memory | `MemoryFact` (preference, fact, goal, open thread), consolidated by the Librarian, deduplicated by vector distance, superseded rather than overwritten | Memory view (facts, Forget) |
| Procedural memory | `Playbook` → `Step`, learned from successful sessions; `save_playbook` / `find_playbook` | Memory view (playbooks) |
| Persistent history | Conversation history lives in the graph, not the browser; `Turn` nodes store `USED_EVIDENCE` | Memory view; Chat across reloads |
| Shared state and handoffs | `create_task`, `claim_task`, `handoff`, `update_task`, `post_to_blackboard`; `HANDED_OFF_TO` edges | Agents view (handoff timeline, blackboard tasks) |
| High concurrency | Async client with a 32-connection pool; load test of 253 write batches/s plus 781 reads/s at p95 28 ms on one shared graph | [eval results](../evals/results/SUMMARY.md) |
| Per-user / tenant isolation | Separate `brain__<tenant>` and per-visitor `mem__…` graphs; HMAC-signed auth tokens; 155/155 isolation, barrier, memory-privacy and auth checks pass | Tenant and persona switchers; [eval results](../evals/results/SUMMARY.md) |

**Headline demo question:** *"Investigate the flagged $48k wire and tell me what we should do about it."* Follow the
Investigator → Compliance handoff in the Agents view. Ask a follow-up in a later session to see recalled memory.

---

## Track 3: Company brain / knowledge graph

Email, Slack, tickets, policies, meeting transcripts, CRM notes and the ledger are redacted, extracted by Haiku,
entity-resolved, embedded with voyage-4 and written into one graph. FinanceBrain separates who *owns* a system
(bitemporal `OWNS`) from who *knows* it (`KNOWS_ABOUT`, derived from authorship and answers with recency decay). That
exposes a bus-factor risk on ReconLegacy. `trace_decision` walks the crypto-onboarding pause from the regulator email
to the board memo, and on to its reversal. A live feed replays post-cutoff days through the real pipeline, so
knowledge stays current. Answers come with numbered citations and an evidence subgraph.

| Focus area | FinanceBrain feature | Where to see it |
|---|---|---|
| Docs, tickets and chats in one graph | Redact → extract → resolve → embed → `UNWIND` `MERGE` pipeline; 2,219 Halcyon documents | Ingestion & Redaction view; Graph Explorer |
| Who owns vs who knows | `who_owns(as_of)` over `OWNS` intervals; `who_knows` over `KNOWS_ABOUT` weights | Chat; [DATA_MODEL](DATA_MODEL.md#derived-expertise-knows_about) |
| Decision tracing | `Decision` nodes with `DECIDED_IN`, `JUSTIFIED_BY` and `SUPERSEDES` (`dec_crypto_pause` → `dec_crypto_resume`); `trace_decision` | Chat → evidence graph |
| Keeping knowledge current | Live feed ("Replay next day" / "Replay 3 days"); bitemporal `Fact` and `OWNS` with `valid_from` / `valid_to`; nothing deleted; Sentinel opens cases | Ingestion & Redaction view; Investigations view |
| Answers with sources | Hybrid `search_knowledge` (vector + full-text + fact vectors with RRF, ACL-filtered); numbered citations, with unverified ones flagged | Chat (citations, evidence graph) |
| PII never reaches the graph | Presidio plus finance recognizers; consistent `⟨TYPE_NNNN⟩` tokens; KMS-wrapped vault; canary scan with 0 hits | Ingestion & Redaction view ("Try the redactor", canary) |

**Headline demo question:** *"Who should I ask about ReconLegacy, and what is our risk if that person leaves?"*
Also try *"Why did we stop onboarding crypto clients?"*

---

## How FalkorDB is used

FalkorDB is the only datastore.

- **Knowledge graph per tenant** (`brain__<tenant>`) holds:
  - org structure, systems, clients and accounts (tokens only), transactions and documents
  - topics, bitemporal facts and decisions
  - ACL edges, cases, tasks, actions and playbooks
- **Per-visitor memory graphs** (`mem__<tenant>__<persona>__<visitor>`) are created lazily.
- **Pristine snapshots** (`pristine__<tenant>`) are made with `GRAPH.COPY`. "Reset demo" copies them back.
- **Vector indexes** are 1024-d with cosine similarity, on `Document`, `Fact`, `Topic`, `Playbook`, `MemoryFact` and
  `Turn`, queried with `db.idx.vector.queryNodes`.
- **Full-text indexes** cover documents, people, systems, topics, deals, facts, memory facts and turns, queried with
  `db.idx.fulltext.queryNodes`.
- **Graph algorithms are exposed as agent tools:**
  - `algo.WCC` and `algo.labelPropagation` for fraud rings
  - `algo.pageRank` and `algo.betweenness` for key people and pass-through accounts
- **Variable-length paths** power blast radius, fund tracing and paths between entities, including
  `allShortestPaths`.
- **ACLs and the information barrier are pure traversal**, written as pattern predicates in `WHERE`.
- **PII tokens are `PiiToken` nodes.** Shared identifiers become `HAS_IDENTIFIER` structure.
- **`GRAPH.RO_QUERY`** backs the `run_cypher_readonly` tool.
- **Coordination blackboard** (tasks, claims, handoffs) lives in the tenant graph.
- **The same Redis server holds operational data:** rate-limit and budget counters, and the AES-GCM token vault (a
  hash).
- **Production configuration:** pinned image digest, AOF `appendfsync everysec`, `requirepass`, port never
  published, and a nightly `BGSAVE` backup to S3.

## Tech stack

| Layer | Technology |
|---|---|
| Database | FalkorDB (graph, vector, full-text, algorithms) on Redis |
| Backend | Python 3.12 (uv), FastAPI, async `falkordb` client (redis-py 8 pool) |
| Agents and tools | Hand-rolled streaming tool loop; MCP server (Python `mcp` SDK, FastMCP, streamable HTTP) |
| LLMs | OpenRouter via the openai SDK: Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5 |
| Embeddings | `voyageai/voyage-4` (1024 dims) |
| Redaction | Presidio + spaCy with custom finance recognizers; vault data key wrapped by AWS KMS |
| Frontend | Next.js static export, Tailwind, Cytoscape.js, SSE |
| Hosting | AWS us-east-1: EC2 t3.xlarge running Docker Compose (Caddy, API, FalkorDB); CloudFront; ECR; SSM Parameter Store; KMS; S3 backups; idempotent AWS CLI scripts in `infra/` |

## Rules compliance

- [x] **Synthetic data only.** All personas, firms, documents and transactions are synthetic (Faker plus
  LLM-written text from placeholder-only prompts). No real personal data is used.
- [x] **PII redaction.** PII is pseudonymized before it reaches the graph or any LLM or embedding call. An outbound
  guard blocks raw PII. The canary scan finds 0 hits across all graphs (127,800 strings scanned in production).
- [x] **Coding started after kickoff.** The full commit history is public at
  https://github.com/DanTahir/FinanceBrain.
- [x] **Public repository:** https://github.com/DanTahir/FinanceBrain
- [x] **README** with setup instructions and the data model ([README](../README.md),
  [DATA_MODEL](DATA_MODEL.md)).
- [x] **Cypher queries** documented in [docs/CYPHER.md](CYPHER.md).
- [ ] **Demo video:** TBD — add YouTube link.
- [x] **Live URL:** https://d1rnt6bwyb9n59.cloudfront.net
- [x] **Track list:** all three tracks (see above).
- [x] **AI disclosure** in [docs/AI_DISCLOSURE.md](AI_DISCLOSURE.md).

## Team

Team: solo — Dan Tahir (GitHub [DanTahir](https://github.com/DanTahir))