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

