# Cypher reference

Generated from `backend/graph/queries.py` (`python -m backend.graph.queries --write`). Every query FinanceBrain runs is listed here; per-label variants are shown once with one example label.

## Ingestion

### `UPSERT_NODES` (22 variants)

Batch upsert of :Person nodes keyed by id. `r.props` is a flat map of primitive properties (FalkorDB rejects map-valued properties, so nested data is stored as JSON strings).

Variants: `Person`, `Team`, `System`, `Client`, `Account`, `Counterparty`, `Transaction`, `Deal`, `Channel`, `Document`, `Thread`, `Topic`, `Decision`, `Fact`, `PiiToken`, `RedactionEvent`, `Agent`, `Task`, `Case`, `Action`, `Playbook`, `Step`. Example (`Person`):

```cypher
UNWIND $rows AS r
MERGE (n:Person {id: r.id})
SET n += r.props
```

### `MERGE_EDGES` (50 variants)

Batch merge of (Person)-[:MEMBER_OF]->(Team) edges between existing nodes; rows whose endpoints are not in the graph (yet) are skipped by the MATCH.

Variants: `Person-MEMBER_OF->Team`, `Person-REPORTS_TO->Person`, `System-DEPENDS_ON->System`, `Client-HOLDS->Account`, `Person-MANAGES->Client`, `Transaction-FROM_ACCOUNT->Account`, `Transaction-TO_ACCOUNT->Account`, `Transaction-COUNTERPARTY->Counterparty`, `Client-HAS_IDENTIFIER->PiiToken`, `Account-HAS_IDENTIFIER->PiiToken`, `Counterparty-HAS_IDENTIFIER->PiiToken`, `Person-ON_DEAL_TEAM->Deal`, `Person-CAN_ACCESS->Channel`, `Person-CAN_ACCESS->Document`, `Team-CAN_ACCESS->Document`, `Person-AUTHORED->Document`, `Document-SENT_TO->Person`, `Person-ATTENDED->Document`, `Document-POSTED_IN->Channel`, `Document-IN_THREAD->Thread`, `Document-REPLY_TO->Document`, `Document-ASSIGNED_TO->Person`, `Document-HAD_REDACTION->RedactionEvent`, `Document-ABOUT->Topic`, `Document-ABOUT->Client`, `Document-MENTIONS->Person`, `Document-MENTIONS->Team`, `Document-MENTIONS->System`, `Document-MENTIONS->Client`, `Document-MENTIONS->Account`, `Document-MENTIONS->Deal`, `Document-MENTIONS->Counterparty`, `Document-MENTIONS->Transaction`, `Document-MENTIONS->Document`, `Document-MENTIONS->PiiToken`, `Decision-DECIDED_IN->Document`, `Decision-JUSTIFIED_BY->Document`, `Decision-CITES->Document`, `Decision-SUPERSEDES->Decision`, `Decision-ABOUT->Topic`, `Fact-CITES->Document`, `Fact-SUPERSEDES->Fact`, `Fact-ABOUT->Person`, `Fact-ABOUT->Team`, `Fact-ABOUT->System`, `Fact-ABOUT->Client`, `Fact-ABOUT->Account`, `Fact-ABOUT->Deal`, `Fact-ABOUT->Counterparty`, `Fact-ABOUT->Topic`. Example (`Person-MEMBER_OF->Team`):

```cypher
UNWIND $rows AS r
MATCH (a:Person {id: r.src}), (b:Team {id: r.dst})
MERGE (a)-[e:MEMBER_OF]->(b)
SET e += r.props
```

### `MERGE_OWNS` (2 variants)

Formal ownership with a validity interval. Keyed on valid_from so successive ownership periods of the same system stay separate edges (bitemporal history for `who_owns(as_of)`).

Variants: `Person`, `Team`. Example (`Person`):

```cypher
UNWIND $rows AS r
MATCH (a:Person {id: r.src}), (s:System {id: r.dst})
MERGE (a)-[e:OWNS {valid_from: r.valid_from}]->(s)
SET e += r.props
```

### `END_OWNERSHIP`

Close an open ownership interval (live feed: team transfer).

```cypher
UNWIND $rows AS r
MATCH (a {id: r.src})-[e:OWNS]->(s:System {id: r.dst})
WHERE e.valid_to IS NULL AND e.valid_from < r.valid_to
SET e.valid_to = r.valid_to
```

### `KNOWS_ABOUT` (5 variants)

Incrementally accumulate derived expertise: weight += intent- and recency-weighted participation (answered / documented > assigned > authored > asked of > ... > asked), evidence += document count, answered/asked/documented counters explain the ranking, last_seen = latest activity.

Variants: `System`, `Client`, `Deal`, `Topic`, `Counterparty`. Example (`System`):

```cypher
UNWIND $rows AS r
MATCH (p:Person {id: r.src}), (x:System {id: r.dst})
MERGE (p)-[k:KNOWS_ABOUT]->(x)
ON CREATE SET k.weight = 0.0, k.evidence = 0, k.answered = 0, k.asked = 0, k.documented = 0
SET k.weight = k.weight + r.w, k.evidence = k.evidence + r.n, k.answered = k.answered + r.answered,
    k.asked = k.asked + r.asked, k.documented = k.documented + r.documented,
    k.last_seen = CASE WHEN k.last_seen IS NULL OR r.last_seen > k.last_seen
                       THEN r.last_seen ELSE k.last_seen END
```

### `SET_EMBEDDINGS` (3 variants)

Attach 1024-dim vectors (vecf32) for the vector index.

Variants: `Document`, `Fact`, `Topic`. Example (`Document`):

```cypher
UNWIND $rows AS r
MATCH (n:Document {id: r.id})
SET n.embedding = vecf32(r.v)
```

### `FACTS_FOR_KEYS`

All facts sharing a (subject_id, predicate) key, for supersession.

```cypher
UNWIND $keys AS k
MATCH (f:Fact {subject_id: k.s, predicate: k.p})
RETURN f.id, f.subject_id, f.predicate, f.object_key, f.valid_from, f.valid_to
```

### `SET_FACT_VALID_TO`

Close (or reopen, with null) a fact's validity interval.

```cypher
UNWIND $rows AS r
MATCH (f:Fact {id: r.id})
SET f.valid_to = r.valid_to
```

### `ALL_TOPICS`

Existing topics, for embedding-similarity canonicalisation of new topic phrases.

```cypher
MATCH (t:Topic) RETURN t.id, t.name
```

### `GET_META`

Embedding model and schema version recorded in this graph.

```cypher
MATCH (m:Meta {id: 'meta'}) RETURN m.embed_model, m.embed_dims, m.schema_version
```

### `SET_META`

Create or update the :Meta node.

```cypher
MERGE (m:Meta {id: 'meta'}) SET m += $props
```

### `LABEL_COUNTS`

Node count per label (seed report).

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC
```

### `REL_COUNTS`

Relationship count per type (seed report).

```cypher
MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS n ORDER BY n DESC
```

## Retrieval (ACL-filtered)

### `SEARCH_DOCS_VECTOR`

Semantic document search. The vector index is queried first (cosine *distance*, lower is better) and the ACL is
applied as a post-filter, so the caller over-fetches `$k` candidates (adaptive: 80, then 400 for low-privilege
personas) to still return `$limit` visible documents.

```cypher
CALL db.idx.vector.queryNodes('Document', 'embedding', $k, vecf32($v)) YIELD node AS d, score
MATCH (p:Person {id: $pid})
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d)) AND ($kinds IS NULL OR d.kind IN $kinds) AND ($since IS NULL OR d.ts >= $since) AND ($until IS NULL OR d.ts < $until)
RETURN d.id AS id, score
ORDER BY score ASC
LIMIT $limit
```

### `SEARCH_DOCS_FULLTEXT`

Keyword document search over the full-text index on Document(title, text) (score: higher is better), with the
same ACL post-filter. Fused with the vector results by reciprocal-rank fusion in `search_knowledge`.

```cypher
CALL db.idx.fulltext.queryNodes('Document', $q) YIELD node AS d, score
MATCH (p:Person {id: $pid})
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d)) AND ($kinds IS NULL OR d.kind IN $kinds) AND ($since IS NULL OR d.ts >= $since) AND ($until IS NULL OR d.ts < $until)
RETURN d.id AS id, score
ORDER BY score DESC
LIMIT $limit
```

### `SEARCH_FACTS_VECTOR`

Semantic search over extracted bitemporal facts. A fact is visible only if at least one document it cites is
visible to the persona, so facts never leak restricted (e.g. MNPI) sources.

```cypher
CALL db.idx.vector.queryNodes('Fact', 'embedding', $k, vecf32($v)) YIELD node AS f, score
MATCH (p:Person {id: $pid})
MATCH (f)-[:CITES]->(d:Document)
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
RETURN f.id AS id, f.statement AS statement, f.subject_id AS subject, f.predicate AS predicate,
       f.valid_from AS valid_from, f.valid_to AS valid_to, collect(DISTINCT d.id)[..3] AS docs, min(score) AS score
ORDER BY score ASC
LIMIT $limit
```

### `DOC_DETAILS`

Hydrate documents (ACL re-checked) with author, channel, thread and mentioned entities, for citations and the
evidence subgraph.

```cypher
UNWIND $ids AS id
MATCH (d:Document {id: id})
MATCH (p:Person {id: $pid})
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(d)
OPTIONAL MATCH (d)-[:POSTED_IN]->(c:Channel)
OPTIONAL MATCH (d)-[:IN_THREAD]->(t:Thread)
RETURN d.id, d.kind, d.ts, d.title, d.text, d.sensitivity, d.doc_type, d.ticket_key, d.ticket_status,
       a.id, a.name, c.id, c.name, t.id, [(d)-[:MENTIONS]->(x) | [labels(x)[0], x.id]] AS mentions
```

### `THREAD_DOCS`

Visible documents in the given threads (conversation context around a hit).

```cypher
UNWIND $tids AS tid
MATCH (t:Thread {id: tid})<-[:IN_THREAD]-(d:Document)
MATCH (p:Person {id: $pid})
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
RETURN tid, d.id, d.ts
ORDER BY d.ts
```

### `VISIBLE_DOCS`

Which of the given document ids the persona may see.

```cypher
UNWIND $ids AS id
MATCH (d:Document {id: id})
MATCH (p:Person {id: $pid})
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
RETURN d.id
```

### `VISIBLE_DEALS`

Information barrier: an MNPI deal (codename, target) is visible only to its deal team (ON_DEAL_TEAM) and to
Compliance. Used to filter deal names and deal-derived topics out of every non-document tool output.

```cypher
MATCH (p:Person {id: $pid})
MATCH (x:Deal)
OPTIONAL MATCH (p)-[r:ON_DEAL_TEAM]->(x)
OPTIONAL MATCH (p)-[m:MEMBER_OF]->(:Team {id: 'team_compliance'})
RETURN x.id, x.codename, x.target, x.sensitivity, count(r) > 0 AS on_team, count(m) > 0 AS compliance
```

### `ENTITY_CATALOG`

Named entities used to resolve free-text names to ids (cached briefly).

```cypher
MATCH (x)
WHERE x:Person OR x:Team OR x:System OR x:Deal OR x:Channel OR x:Decision OR x:Counterparty
RETURN labels(x)[0], x.id, coalesce(x.name, x.codename, x.title), x.target, x.title
```

### `ENTITY_NAMES` (18 variants)

Display names for a batch of entity ids of one label.

Variants: `Person`, `Team`, `System`, `Client`, `Account`, `Counterparty`, `Transaction`, `Deal`, `Channel`, `Topic`, `Decision`, `PiiToken`, `Document`, `Fact`, `Thread`, `Case`, `Task`, `Action`. Example (`Person`):

```cypher
UNWIND $ids AS id
MATCH (n:Person {id: id})
RETURN n.id, coalesce(n.name, n.title, n.id)
```

### `GET_ENTITY` (18 variants)

All properties of one entity plus its relationship-type degree summary.

Variants: `Person`, `Team`, `System`, `Client`, `Account`, `Counterparty`, `Transaction`, `Deal`, `Channel`, `Topic`, `Decision`, `PiiToken`, `Document`, `Fact`, `Thread`, `Case`, `Task`, `Action`. Example (`Person`):

```cypher
MATCH (n:Person {id: $id})
OPTIONAL MATCH (n)-[r]-()
RETURN properties(n), type(r) AS rel, count(r) AS n
```

### `NEIGHBORHOOD` (18 variants)

One-hop neighbourhood of an entity, strongest edges first. Neighbouring documents are ACL-filtered;
facts and redaction events are excluded (facts are only served through ACL-checked fact search).

Variants: `Person`, `Team`, `System`, `Client`, `Account`, `Counterparty`, `Transaction`, `Deal`, `Channel`, `Topic`, `Decision`, `PiiToken`, `Document`, `Fact`, `Thread`, `Case`, `Task`, `Action`. Example (`Person`):

```cypher
MATCH (n:Person {id: $id})-[r]-(m)
MATCH (p:Person {id: $pid})
WHERE ($types IS NULL OR type(r) IN $types) AND NOT m:Fact AND NOT m:RedactionEvent
  AND (NOT m:Document OR (m.public = true OR (p)-[:CAN_ACCESS]->(m) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(m) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(m)))
RETURN type(r) AS rel, startNode(r).id = n.id AS outgoing, labels(m)[0] AS label, m.id AS id,
       coalesce(m.name, m.codename, m.title, m.id) AS name, r.weight AS weight, m.ts AS ts,
       m.kind AS kind, m.amount AS amount
ORDER BY coalesce(r.weight, 0) DESC, ts DESC
LIMIT $limit
```

## Reasoning

### `WHO_KNOWS_ENTITY` (5 variants)

Ranked experts on one entity from the derived KNOWS_ABOUT edges. The answered / asked / documented
counters explain the ranking (answering other people's questions and writing authoritative docs
outweigh volume), and end_date flags departures (bus factor).

Variants: `System`, `Client`, `Deal`, `Topic`, `Counterparty`. Example (`System`):

```cypher
MATCH (p:Person)-[k:KNOWS_ABOUT]->(x:System {id: $id})
OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
RETURN p.id, p.name, p.title, t.name, p.end_date, k.weight, k.evidence, k.answered, k.asked,
       k.documented, k.last_seen
ORDER BY k.weight DESC
LIMIT $limit
```

### `WHO_KNOWS_TOPICS`

Expertise on a free-text subject: nearest Topic nodes by vector similarity, then the KNOWS_ABOUT edges into them,
one row per (person, topic). The caller drops topics behind the information barrier, then sums weights per
person scaled by topic similarity (1 - cosine distance).

```cypher
CALL db.idx.vector.queryNodes('Topic', 'embedding', $k, vecf32($v)) YIELD node AS x, score
WITH x, score WHERE score <= $max_dist
MATCH (p:Person)-[k:KNOWS_ABOUT]->(x)
OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
RETURN p.id, p.name, p.title, t.name, p.end_date, x.id, x.name, score, k.weight, k.evidence, k.answered,
       k.asked, k.documented, k.last_seen
ORDER BY k.weight DESC
LIMIT 300
```

### `OWNERSHIP_HISTORY`

Formal ownership intervals of a system (bitemporal OWNS edges: valid_from inclusive, valid_to exclusive / open).
`who_owns(as_of)` selects the intervals containing the requested date.

```cypher
MATCH (o)-[e:OWNS]->(s:System {id: $id})
RETURN labels(o)[0], o.id, coalesce(o.name, o.id), o.title, e.valid_from, e.valid_to, e.source
ORDER BY e.valid_from, labels(o)[0]
```

### `OWNERSHIP_FACTS`

Extracted ownership/maintenance facts about a system, ACL-filtered by source.

```cypher
MATCH (f:Fact {subject_id: $id})
WHERE f.predicate IN ['owned_by', 'maintained_by', 'assigned_to']
MATCH (p:Person {id: $pid})
MATCH (f)-[:CITES]->(d:Document)
WHERE (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
RETURN f.id, f.statement, f.predicate, f.object, f.valid_from, f.valid_to, collect(DISTINCT d.id)[..3]
ORDER BY f.valid_from
```

### `LIST_DECISIONS`

All recorded decisions (small set; the agent picks one).

```cypher
MATCH (x:Decision)
RETURN x.id, x.title, x.status, x.effective, x.summary
ORDER BY x.effective
```

### `DECISION_TRAIL`

The evidence trail of a decision: where it was decided (DECIDED_IN), what justified it (JUSTIFIED_BY) and what
it cites, in time order, keeping only sources visible to the persona. OPTIONAL MATCH + WITH ... WHERE is used
because pattern comprehensions with pattern-predicate filters are rejected by FalkorDB.

```cypher
MATCH (p:Person {id: $pid})
MATCH (x:Decision {id: $id})
OPTIONAL MATCH (x)-[r:DECIDED_IN|JUSTIFIED_BY|CITES]->(d:Document)
WITH p, x, r, d WHERE d IS NULL OR (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
RETURN type(r), d.id, d.ts
ORDER BY d.ts
```

### `DECISION_SOURCE_COUNT`

Total sources of a decision (to report how many are hidden).

```cypher
MATCH (x:Decision {id: $id})-[:DECIDED_IN|JUSTIFIED_BY|CITES]->(d:Document)
RETURN count(DISTINCT d)
```

### `DECISION_LINKS`

Supersession chain and topics of a decision.

```cypher
MATCH (x:Decision {id: $id})
OPTIONAL MATCH (x)-[:SUPERSEDES]->(prev:Decision)
OPTIONAL MATCH (nxt:Decision)-[:SUPERSEDES]->(x)
OPTIONAL MATCH (x)-[:ABOUT]->(t:Topic)
RETURN collect(DISTINCT [prev.id, prev.title, prev.effective]), collect(DISTINCT [nxt.id, nxt.title, nxt.effective]),
       collect(DISTINCT t.name)
```

### `BLAST_RADIUS`

Downstream impact: every system that transitively DEPENDS_ON the failing one, with its minimum hop distance.

```cypher
MATCH path = (dep:System)-[:DEPENDS_ON*1..6]->(s:System {id: $id})
RETURN dep.id, dep.name, dep.tier, min(length(path)) AS hops
ORDER BY hops, dep.id
```

### `UPSTREAM`

Upstream dependencies of a system (what it needs to run).

```cypher
MATCH path = (s:System {id: $id})-[:DEPENDS_ON*1..4]->(up:System)
RETURN up.id, up.name, up.tier, min(length(path)) AS hops
ORDER BY hops, up.id
```

### `DEPENDENCY_EDGES`

DEPENDS_ON edges among a set of systems (evidence subgraph).

```cypher
MATCH (a:System)-[:DEPENDS_ON]->(b:System)
WHERE a.id IN $ids AND b.id IN $ids
RETURN a.id, b.id
```

### `SYSTEM_OWNERS_EXPERTS`

Current formal owners and top-2 experts per system.

```cypher
UNWIND $ids AS sid
MATCH (s:System {id: sid})
OPTIONAL MATCH (o)-[e:OWNS]->(s) WHERE e.valid_to IS NULL
WITH s, collect(DISTINCT coalesce(o.name, o.id)) AS owners
OPTIONAL MATCH (k:Person)-[kk:KNOWS_ABOUT]->(s)
WITH s, owners, k, kk ORDER BY kk.weight DESC
RETURN s.id, owners, collect(k.name)[..2], collect(k.end_date)[..2]
```

### `SYSTEM_DOCS`

Recent visible tickets and documents mentioning a system (incidents, runbooks).

```cypher
MATCH (p:Person {id: $pid})
MATCH (d:Document)-[:MENTIONS]->(s:System {id: $id})
WHERE d.kind IN ['ticket', 'doc', 'meeting'] AND (d.public = true OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d) OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d))
RETURN d.id, d.ts
ORDER BY d.ts DESC
LIMIT $limit
```

### `FIND_PATH` (5 variants)

All shortest undirected paths between two entities (FalkorDB requires both endpoints to be bound in a
preceding WITH; undirected shortestPath() is not supported, allShortestPaths is). Paths through
documents or deals the persona cannot see are dropped by the caller.

Variants: `2`, `3`, `4`, `5`, `6`. Example (`2`):

```cypher
MATCH (a) WHERE a.id = $a
MATCH (b) WHERE b.id = $b
WITH a, b
MATCH path = allShortestPaths((a)-[*..2]-(b))
RETURN [n IN nodes(path) | [labels(n)[0], n.id, coalesce(n.name, n.codename, n.title, n.id)]],
       [r IN relationships(path) | type(r)]
LIMIT 12
```

