# Graph data model

FinanceBrain keeps **everything** in FalkorDB: knowledge graph, vectors, full-text indexes, PII token structure,
ACLs, agent memory, and the agent coordination blackboard. This page describes the graphs as built by
`backend/graph/schema.py` and `backend/ingest/pipeline.py`. Every query is listed in [CYPHER.md](CYPHER.md),
which is generated from `backend/graph/queries.py`.

## Graph topology (one FalkorDB instance, many graphs)

| Graph | Contents | Written by |
|---|---|---|
| `brain__<tenant>` | Org, systems, clients/accounts/transactions (tokens only), documents, topics, facts, decisions, PII tokens, ACL edges, cases/tasks/actions, playbooks, `:Meta` | ingestion, live feed, agents |
| `mem__<tenant>__<user>` | One user's private memory: sessions, turns, memory facts, entity refs, preferences | Librarian and agents, for that user only |
| `pristine__<tenant>` | `GRAPH.COPY` of the freshly seeded brain; "Reset demo" copies it back | `backend/ingest/seed.py` |

Tenants (`halcyon`, `brightwater`) never share a graph, so isolation is a property of *which graph a query
runs on*, decided server-side from the auth context and never by the model.

```mermaid
erDiagram
  Team ||--o{ Person : MEMBER_OF
  Person }o--o| Person : REPORTS_TO
  Team ||--o{ System : "OWNS {valid_from, valid_to}"
  Person }o--o{ System : "KNOWS_ABOUT {weight, evidence, last_seen}"
  System }o--o{ System : DEPENDS_ON
  Person ||--o{ Client : MANAGES
  Client ||--o{ Account : HOLDS
  Transaction }o--|| Account : FROM_ACCOUNT
  Transaction }o--|| Account : TO_ACCOUNT
  Transaction }o--o| Counterparty : COUNTERPARTY
  Client }o--o{ PiiToken : HAS_IDENTIFIER
  Account }o--o{ PiiToken : HAS_IDENTIFIER
  Person }o--o{ Deal : ON_DEAL_TEAM
  Person ||--o{ Document : AUTHORED
  Document }o--o| Channel : POSTED_IN
  Document }o--o| Thread : IN_THREAD
  Document }o--o{ Topic : ABOUT
  Document }o--o{ PiiToken : MENTIONS
  Document ||--o| RedactionEvent : HAD_REDACTION
  Person }o--o{ Channel : CAN_ACCESS
  Team }o--o{ Document : CAN_ACCESS
  Decision }o--|| Document : DECIDED_IN
  Decision }o--o{ Document : JUSTIFIED_BY
  Decision }o--o| Decision : SUPERSEDES
  Fact }o--|| Document : CITES
  Fact }o--o| Fact : SUPERSEDES
  Fact }o--o{ System : ABOUT
```

## Node labels (brain graph)

| Label | Key properties | Source |
|---|---|---|
| `Person` | id, name, title, email (work), team_id, start_date, end_date | world model (salary, home address and medical notes are **not** ingested) |
| `Team` | id (`team_ops`), name | world model |
| `System` | id (`sys_reconlegacy`), name, description, tier | world model |
| `Client` | id, name (= `⟨CLIENT_NAME_NNNN⟩` token), segment, risk_rating, kyc_status | world model, tokens only |
| `Account` | id, kind, number (= token), opened, status | world model, tokens only |
| `Counterparty` | id, name, country, bank | world model; bank identifiers are tokens |
| `Transaction` | id, ts, amount, currency, kind, memo, fraud_score | ledger (pre-cutoff at seed, the rest via live feed) |
| `Deal` | id, codename, kind, target, status, sensitivity (`MNPI`) | world model |
| `Channel` | id, name, private | world model |
| `Document` | id, kind (email, slack, ticket, doc, meeting, crm), ts, title, text (**redacted**), sensitivity, public, live_feed, doc_type, ticket_key/status/priority, redactions (JSON), embedding (1024-d) | redacted corpus |
| `Thread` | id | Slack/email threading |
| `Topic` | id (`topic_wire_fraud_alert`), name, embedding | LLM extraction, canonicalised by embedding similarity (cosine ≥ 0.86) |
| `Fact` | id, subject_id, predicate, object, object_key, statement, valid_from, valid_to, confidence, embedding | LLM extraction (bitemporal) |
| `Decision` | id, title, status, effective, summary | decision log + LLM extraction |
| `PiiToken` | id (= token, e.g. `⟨PHONE_0042⟩`), type | redaction vault; the raw value lives only in the encrypted vault |
| `RedactionEvent` | id, counts (JSON) | redaction pipeline audit trail |
| `Agent` | id (`brain`, `investigator`, `compliance`, ...), name, role, model | agent roster (`ensure_agents`) |
| `Case` | id (`case_…`), title, kind (fraud_ring, suspicious_activity, mnpi_breach, ...), severity, status (open → in_review → pending_approval → actioned / escalated / closed), summary, created, updated, opened_by, sar_ref | agents at runtime |
| `Action` | id (`act_…`), kind (freeze_account, file_sar, notify_owner, restrict_channel), status (proposed → approved / rejected → executed), target_label, target_id, case_id, rationale, details (e.g. SAR narrative), effect | proposed by agents, decided by a human |
| `Task` | id (`task_…`), title, kind, status (open → claimed → done), from_agent, to_agent, case_id, note, result | the shared blackboard (handoffs) |
| `Playbook`, `Step` | id, ... | procedural memory (item 8) |
| `Meta` | embed_model, embed_dims, schema_version, tenant | `ensure_meta` refuses to mix vector spaces |

Memory graph labels: `User`, `Session`, `Turn` (text, ts, embedding), `MemoryFact` (text, embedding),
`EntityRef` (points at a brain `id` by value), `Preference`.

## Relationship types

| Group | Types |
|---|---|
| Org | `MEMBER_OF`, `REPORTS_TO`, `OWNS {valid_from, valid_to}` (Team or Person → System), `MANAGES` (relationship manager → Client) |
| Derived expertise | `KNOWS_ABOUT {weight, evidence, last_seen}` (Person → System, Client, Deal, Topic, Counterparty) |
| Systems | `DEPENDS_ON` |
| Communication | `AUTHORED`, `SENT_TO`, `ATTENDED`, `POSTED_IN`, `IN_THREAD`, `REPLY_TO`, `ASSIGNED_TO`, `MENTIONS`, `ABOUT` |
| Knowledge | `DECIDED_IN`, `JUSTIFIED_BY`, `CITES`, `SUPERSEDES` (Decision→Decision, Fact→Fact) |
| Finance | `HOLDS`, `FROM_ACCOUNT`, `TO_ACCOUNT`, `COUNTERPARTY`, `ON_DEAL_TEAM` |
| Privacy | `HAS_IDENTIFIER` (Client, Account, Counterparty → PiiToken), `HAD_REDACTION` |
| Access | `CAN_ACCESS` (Person → Channel, Person → Document, Team → Document) |
| Derived money flow | `FLOWS_TO {amount, n, first_ts, last_ts, max_fraud_score, alerts, kind}` (Counterparty → Account, Account → Account, Account → Counterparty) |
| Derived communication | `COMMUNICATES {weight, direct, replies, last}` (Person → Person, MNPI documents excluded) |
| Operations | `OPENED_BY`, `REQUESTED_BY` (Case → Agent / Person), `ASSIGNED_TO` (Case or Task → Agent), `EVIDENCE {note, added_by, ts}` (Case → any entity), `PROPOSED` (Agent → Action), `FOR_CASE` (Action or Task → Case), `TARGETS` (Action → Account / Channel / Person / ...), `APPROVED_BY` / `REJECTED_BY {ts, note}` (Action → Person), `HANDED_OFF_TO {note, ts, task_id, case_id}` (Agent → Agent), `CLAIMED_BY` (Task → Agent) |

## How the graph does the work

### Shared identifiers become structure, without exposing values
Two clients with the same phone number both get `HAS_IDENTIFIER` edges to **one** `⟨PHONE_NNNN⟩` node, because
tokenisation is deterministic per tenant (HMAC of the normalised value). A fraud ring is therefore a connected
component over `Client–PiiToken–Client` paths. It is found by Cypher traversal or `algo.WCC`, and no raw phone,
address or device id is ever stored or sent to an LLM. The resolver deliberately refuses to map a shared token to
any *single* client.

### Access control is traversal
A persona may read a `Document` if it is `public`, or if any of these paths exist:
`(Person)-[:CAN_ACCESS]->(Document)`, `(Person)-[:MEMBER_OF]->(Team)-[:CAN_ACCESS]->(Document)`, or
`(Person)-[:CAN_ACCESS]->(Channel)<-[:POSTED_IN]-(Document)`. MNPI documents (deal codenames) are never public.
They are reachable only by deal-team members (and Compliance for deal metadata). An information-barrier breach is
a document that references the deal (via `MENTIONS`, or found by the full-text index on the codename or target)
whose audience includes someone outside the wall. The audience is the author, the `SENT_TO` recipients, the members
of the channel it was posted in, and anyone granted `CAN_ACCESS`. The wall is the `ON_DEAL_TEAM` set plus the
Compliance team. `barrier_check` computes exactly this. For the CCO only, it also returns the redacted content of
the flagged documents through an audit-logged surveillance read; everyone else gets metadata only.

Case-management nodes (`Case`, `Action`, `Task`) and their edges are invisible to personas without the `act`
scope, in every tool and in `run_cypher_readonly`, so an investigation cannot tip anyone off.

### Bitemporal knowledge
- `OWNS` edges carry `valid_from` / `valid_to`, and successive owners are separate edges, so
  `who_owns(system, as_of)` is a single interval filter.
- Extracted `Fact`s are reified with `valid_from` / `valid_to`. For *functional* predicates (`owned_by`,
  `maintained_by`, `status`, `assigned_to`, `deadline`, `role`), a later fact with a different object closes the
  earlier one and adds `SUPERSEDES`. Restating the same value does not close it, and nothing is deleted.
- Decisions chain the same way: `dec_crypto_resume -[:SUPERSEDES]-> dec_crypto_pause`.

### Derived expertise (`KNOWS_ABOUT`)
Each participant in a document gains weight toward every entity the document is about. The gain is
`role_weight × 0.5^(age_days / 45)`, with ages measured from the demo's "today", 2026-10-15. Each person counts
once per document, with their strongest role, and roles follow **intent rather than volume**:

| Role | Weight | Signal |
|---|---|---|
| `answered` (a reply to *someone else's* question) | 2.0 | knows the answer |
| `documented` (author of a runbook, architecture doc, procedure, model card, ...) | 2.0 | wrote it down |
| `assignee` (ticket) | 1.2 | trusted to fix it |
| `author` (other messages, tickets, memos) | 1.0 | works on it |
| `asked_of` (@-mentioned in, or emailed, a question) | 1.0 | the organisation treats them as the expert |
| `attendee` | 0.5 | was in the room |
| `asked` (author of a question) | 0.3 | needs to know, which is not the same as knowing |
| `mentioned` | 0.3 | referenced |
| `recipient` | 0.15 | was informed |

Why intent matters: on the first seed, with volume-style weights, the top "expert" on ReconLegacy was a
reconciliation analyst who *uses* it daily and asks about it constantly (11 questions, 2 answers). The engineer
who actually maintains it answered 9 questions and asked none. With intent weights she ranks first. Each edge also
carries `answered`, `asked` and `documented` counters, so `who_knows` can explain its ranking ("answered 9
questions about ReconLegacy").

Weights accumulate incrementally (`ON CREATE SET … SET k.weight = k.weight + r.w`), so the live feed updates
expertise without a rebuild. Contrast this with formal `OWNS`. ReconLegacy is formally owned by the Director of
Engineering, but the knowledge sits with one Staff Engineer whose last day is 2026-10-09. The bus-factor demo is
the gap between "who owns X" and "who actually knows X".

### Derived analytics edges (`FLOWS_TO`, `COMMUNICATES`)
The ledger stores `FROM_ACCOUNT` and `TO_ACCOUNT` as edges *out of* each `Transaction`, so money is not directly
traversable. `backend/ingest/derive.py` aggregates the ledger into one `FLOWS_TO` edge per ordered pair, by
transaction kind:
- inbound (`wire_in`, `ach_in`): Counterparty → Account;
- internal: Account → Account;
- outbound (`wire_out`, `ach_out`): Account → Counterparty;
- card transactions are ignored.

It also aggregates addressed messages and replies into `COMMUNICATES`, leaving out MNPI documents so that the
derived network never reveals walled-off deal activity. Both edge types are rebuilt in about 0.5 s (halcyon has
3,131 `FLOWS_TO` and 746 `COMMUNICATES` edges) after the seed and after live-feed batches. They power:
- `trace_funds`: variable-length `FLOWS_TO*1..h` paths whose intermediate hops are accounts;
- `algo.betweenness` over the money-flow network, which finds pass-through hubs;
- `key_people`: `algo.pageRank` and `algo.betweenness` over `COMMUNICATES`.

### Fraud-ring scoring
`detect_rings` runs `algo.WCC` over `(:Client)-[:HAS_IDENTIFIER]->(:PiiToken)`. It skips the routing-number hub
token and components larger than 40 clients. Each component then gets a risk score from five parts:

| Part | Weight |
|---|---|
| Identifier strength (SSN/device/phone/email strong; address/DOB weak) | 0.30 |
| `FLOWS_TO` transfers between accounts of *different* member clients | 0.20 |
| A common exit counterparty that is **exclusive** to the group (members are at least half of all its payers) | 0.15 |
| Inbound deposits between 8,000 and 10,000 (structuring) | 0.15 |
| Alerts and fraud scores | 0.20 |

The two qualifiers came from a test failure. A benign 3-client household first scored "medium" for two reasons:
money moving between one client's own accounts, and payments to a popular merchant that 33 accounts pay. With the
qualifiers it scores low, and the planted ring (5 clients, phone + address + device, FRAUD-2291, exit cp_063)
scores high.

### Cases, actions and the blackboard
Agents write their work into the tenant graph:
1. The Investigator calls `open_case` (a `Case` with `EVIDENCE` edges to transactions, accounts, clients, tokens and
   documents). It de-duplicates against open cases of the same kind that share evidence.
2. It calls `propose_action` (`Agent-[:PROPOSED]->Action-[:FOR_CASE]->Case`, `Action-[:TARGETS]->Account`).
3. It calls `handoff`. This creates a `Task` `ASSIGNED_TO` the Compliance agent and an
   `Agent-[:HANDED_OFF_TO {note}]->Agent` edge, and moves the case to `in_review`.
4. The Compliance agent calls `claim_task` (`CLAIMED_BY`), verifies the work, drafts the SAR as a `file_sar` action,
   sets `pending_approval` and calls `complete_task`.

There is deliberately **no MCP tool that approves**. Approval is a REST call from the UI by a persona with the
`approve` scope (the CCO). It records `APPROVED_BY` / `REJECTED_BY` to that `Person` and then executes the effect:
- `freeze_account` sets `Account.status = 'frozen'` and remembers the prior status;
- `restrict_channel` flags the channel;
- `file_sar` stamps a SAR reference on the case.

"Reset demo" (`ops --clear`) restores the accounts and channels and deletes the ops nodes.

### Seed cut and live feed
The seed loads the world **as of the cutoff** (2026-10-01): pre-cutoff documents and transactions, ownership
intervals and employment as they stood then. Later events stay out of the graph until the live feed replays them
through the same pipeline. These are the KYC ownership transfer (10-06), the recon expert's departure (10-09),
the second ring wire, the Falcon leak and the crypto-onboarding reversal (10-12).

## Indexes and constraints

| Kind | Brain graph | Memory graph |
|---|---|---|
| Range | `id` on every label; `Document.ts/kind/thread_id/ticket_key`; `Transaction.ts`; `Fact.subject_id/predicate/valid_from`; `PiiToken.type`; `Person.name/team_id`; `Task/Case/Action.status`; edge `OWNS.valid_from`, `KNOWS_ABOUT.weight` | `id` on every label; `Session.started`; `Turn.ts` |
| Unique | `id` on every label (`GRAPH.CONSTRAINT … UNIQUE NODE <Label> PROPERTIES 1 id`) | same |
| Full-text | `Document(title, text)`, `Person(name, title)`, `System(name, description)`, `Topic(name)`, `Deal(codename, target)`, `Fact(statement)` | `MemoryFact(text)`, `Turn(text)` |
| Vector (1024-d, cosine) | `Document.embedding`, `Fact.embedding`, `Topic.embedding` | `MemoryFact.embedding`, `Turn.embedding` |

Embeddings come from `voyageai/voyage-4` via OpenRouter, run on redacted text only. They are cached in
`data/extracted/embeddings/`, so reseeding makes no embedding calls.

## FalkorDB constraints we design around

- Map-valued properties are rejected, so nested data (`redactions`, `counts`) is stored as JSON strings.
- Null properties are not stored, so absent values are simply omitted (`clean_props`).
- Labels cannot be parameters, so per-label query variants are generated from one template and registered as
  `NAME[Label]`.
- Vector `queryNodes` returns cosine *distance* (lower is better), and ACL filtering after `YIELD` is a post-filter,
  so retrieval over-fetches before filtering.
