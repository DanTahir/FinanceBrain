"""Every Cypher query FinanceBrain runs, as named constants with docstrings.

This module is the single source of truth: docs/CYPHER.md is generated from REGISTRY with
``python -m backend.graph.queries --write``. Labels cannot be query parameters in Cypher, so
per-label variants are generated from one template each and registered under ``NAME[Label]``.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    name: str
    doc: str
    cypher: str
    group: str


REGISTRY: dict[str, Query] = {}
_GROUP = "Ingestion"


def _q(name: str, doc: str, cypher: str) -> str:
    text = textwrap.dedent(cypher).strip("\n")
    REGISTRY[name] = Query(name, textwrap.dedent(doc).strip(), text, _GROUP)
    return text


def _group(name: str) -> None:
    global _GROUP
    _GROUP = name


# ---------------------------------------------------------------------------------------------------------------------
_group("Ingestion")

NODE_LABELS = ("Person", "Team", "System", "Client", "Account", "Counterparty", "Transaction", "Deal", "Channel",
               "Document", "Thread", "Topic", "Decision", "Fact", "PiiToken", "RedactionEvent", "Agent", "Task",
               "Case", "Action", "Playbook", "Step")

UPSERT_NODES: dict[str, str] = {
    label: _q(f"UPSERT_NODES[{label}]",
              f"Batch upsert of :{label} nodes keyed by id. `r.props` is a flat map of primitive properties "
              f"(FalkorDB rejects map-valued properties, so nested data is stored as JSON strings).",
              f"""
              UNWIND $rows AS r
              MERGE (n:{label} {{id: r.id}})
              SET n += r.props
              """)
    for label in NODE_LABELS
}

# (source label, relationship type, target label) combinations written by ingestion
EDGE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("Person", "MEMBER_OF", "Team"), ("Person", "REPORTS_TO", "Person"), ("System", "DEPENDS_ON", "System"),
    ("Client", "HOLDS", "Account"), ("Person", "MANAGES", "Client"),
    ("Transaction", "FROM_ACCOUNT", "Account"), ("Transaction", "TO_ACCOUNT", "Account"),
    ("Transaction", "COUNTERPARTY", "Counterparty"),
    ("Client", "HAS_IDENTIFIER", "PiiToken"), ("Account", "HAS_IDENTIFIER", "PiiToken"),
    ("Counterparty", "HAS_IDENTIFIER", "PiiToken"),
    ("Person", "ON_DEAL_TEAM", "Deal"), ("Person", "CAN_ACCESS", "Channel"), ("Person", "CAN_ACCESS", "Document"),
    ("Team", "CAN_ACCESS", "Document"),
    ("Person", "AUTHORED", "Document"), ("Document", "SENT_TO", "Person"), ("Person", "ATTENDED", "Document"),
    ("Document", "POSTED_IN", "Channel"), ("Document", "IN_THREAD", "Thread"), ("Document", "REPLY_TO", "Document"),
    ("Document", "ASSIGNED_TO", "Person"), ("Document", "HAD_REDACTION", "RedactionEvent"),
    ("Document", "ABOUT", "Topic"), ("Document", "ABOUT", "Client"),
    *(("Document", "MENTIONS", x) for x in ("Person", "Team", "System", "Client", "Account", "Deal", "Counterparty",
                                            "Transaction", "Document", "PiiToken")),
    ("Decision", "DECIDED_IN", "Document"), ("Decision", "JUSTIFIED_BY", "Document"),
    ("Decision", "CITES", "Document"), ("Decision", "SUPERSEDES", "Decision"), ("Decision", "ABOUT", "Topic"),
    ("Fact", "CITES", "Document"), ("Fact", "SUPERSEDES", "Fact"),
    *(("Fact", "ABOUT", x) for x in ("Person", "Team", "System", "Client", "Account", "Deal", "Counterparty", "Topic")),
)

MERGE_EDGES: dict[tuple[str, str, str], str] = {
    (s, t, d): _q(f"MERGE_EDGES[{s}-{t}->{d}]",
                  f"Batch merge of ({s})-[:{t}]->({d}) edges between existing nodes; rows whose endpoints are not "
                  f"in the graph (yet) are skipped by the MATCH.",
                  f"""
                  UNWIND $rows AS r
                  MATCH (a:{s} {{id: r.src}}), (b:{d} {{id: r.dst}})
                  MERGE (a)-[e:{t}]->(b)
                  SET e += r.props
                  """)
    for s, t, d in EDGE_SPECS
}

MERGE_OWNS: dict[str, str] = {
    src: _q(f"MERGE_OWNS[{src}]",
            "Formal ownership with a validity interval. Keyed on valid_from so successive ownership periods of the "
            "same system stay separate edges (bitemporal history for `who_owns(as_of)`).",
            f"""
            UNWIND $rows AS r
            MATCH (a:{src} {{id: r.src}}), (s:System {{id: r.dst}})
            MERGE (a)-[e:OWNS {{valid_from: r.valid_from}}]->(s)
            SET e += r.props
            """)
    for src in ("Person", "Team")
}

END_OWNERSHIP = _q("END_OWNERSHIP", "Close an open ownership interval (live feed: team transfer).", """
    UNWIND $rows AS r
    MATCH (a {id: r.src})-[e:OWNS]->(s:System {id: r.dst})
    WHERE e.valid_to IS NULL AND e.valid_from < r.valid_to
    SET e.valid_to = r.valid_to
    """)

KNOWS_ABOUT: dict[str, str] = {
    label: _q(f"KNOWS_ABOUT[{label}]",
              "Incrementally accumulate derived expertise: weight += intent- and recency-weighted participation "
              "(answered / documented > assigned > authored > asked of > ... > asked), evidence += document "
              "count, answered/asked/documented counters explain the ranking, last_seen = latest activity.",
              f"""
              UNWIND $rows AS r
              MATCH (p:Person {{id: r.src}}), (x:{label} {{id: r.dst}})
              MERGE (p)-[k:KNOWS_ABOUT]->(x)
              ON CREATE SET k.weight = 0.0, k.evidence = 0, k.answered = 0, k.asked = 0, k.documented = 0
              SET k.weight = k.weight + r.w, k.evidence = k.evidence + r.n, k.answered = k.answered + r.answered,
                  k.asked = k.asked + r.asked, k.documented = k.documented + r.documented,
                  k.last_seen = CASE WHEN k.last_seen IS NULL OR r.last_seen > k.last_seen
                                     THEN r.last_seen ELSE k.last_seen END
              """)
    for label in ("System", "Client", "Deal", "Topic", "Counterparty")
}

SET_EMBEDDINGS: dict[str, str] = {
    label: _q(f"SET_EMBEDDINGS[{label}]", "Attach 1024-dim vectors (vecf32) for the vector index.", f"""
              UNWIND $rows AS r
              MATCH (n:{label} {{id: r.id}})
              SET n.embedding = vecf32(r.v)
              """)
    for label in ("Document", "Fact", "Topic")
}

FACTS_FOR_KEYS = _q("FACTS_FOR_KEYS", "All facts sharing a (subject_id, predicate) key, for supersession.", """
    UNWIND $keys AS k
    MATCH (f:Fact {subject_id: k.s, predicate: k.p})
    RETURN f.id, f.subject_id, f.predicate, f.object_key, f.valid_from, f.valid_to
    """)

SET_FACT_VALID_TO = _q("SET_FACT_VALID_TO", "Close (or reopen, with null) a fact's validity interval.", """
    UNWIND $rows AS r
    MATCH (f:Fact {id: r.id})
    SET f.valid_to = r.valid_to
    """)

ALL_TOPICS = _q("ALL_TOPICS", "Existing topics, for embedding-similarity canonicalisation of new topic phrases.", """
    MATCH (t:Topic) RETURN t.id, t.name
    """)

GET_META = _q("GET_META", "Embedding model and schema version recorded in this graph.", """
    MATCH (m:Meta {id: 'meta'}) RETURN m.embed_model, m.embed_dims, m.schema_version
    """)

SET_META = _q("SET_META", "Create or update the :Meta node.", """
    MERGE (m:Meta {id: 'meta'}) SET m += $props
    """)

LABEL_COUNTS = _q("LABEL_COUNTS", "Node count per label (seed report).", """
    MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC
    """)

REL_COUNTS = _q("REL_COUNTS", "Relationship count per type (seed report).", """
    MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS n ORDER BY n DESC
    """)

# ---------------------------------------------------------------------------------------------------------------------
_group("Derived analytics edges")

DERIVE_CLEAR: dict[str, str] = {
    rel: _q(f"DERIVE_CLEAR[{rel}]", "Drop one derived edge type before its idempotent full rebuild.",
            f"MATCH ()-[e:{rel}]->() DELETE e")
    for rel in ("FLOWS_TO", "COMMUNICATES")
}

_FLOW_DOC = """
    Derived money-flow edges from {what}, one per ordered pair: total amount, transaction count, first/last
    timestamp, highest fraud score and number of alerted transactions. The ledger stores both FROM_ACCOUNT and
    TO_ACCOUNT as edges *out of* the Transaction, so FLOWS_TO is what makes money movement traversable (fund tracing,
    ring scoring, flow betweenness). Rebuilt after the seed and after each live-feed batch."""
_FLOW_SET = """
        WITH src, dst, sum(t.amount) AS amt, count(t) AS n, min(t.ts) AS first_ts, max(t.ts) AS last_ts,
             max(coalesce(t.fraud_score, 0.0)) AS risk, collect(DISTINCT t.alert) AS alerts
        MERGE (src)-[f:FLOWS_TO]->(dst)
        SET f.amount = round(amt * 100) / 100.0, f.n = n, f.first_ts = first_ts, f.last_ts = last_ts,
            f.max_fraud_score = risk, f.alerts = size(alerts), f.kind = '{kind}'
        """

DERIVE_FLOWS: dict[str, str] = {
    "internal": _q("DERIVE_FLOWS[internal]", _FLOW_DOC.format(what="internal transfers (Account -> Account)"), """
        MATCH (t:Transaction)-[:FROM_ACCOUNT]->(src:Account)
        MATCH (t)-[:TO_ACCOUNT]->(dst:Account)""" + _FLOW_SET.format(kind="internal")),
    "inbound": _q("DERIVE_FLOWS[inbound]", _FLOW_DOC.format(what="incoming wires and ACH (Counterparty -> Account)"), """
        MATCH (t:Transaction)-[:TO_ACCOUNT]->(dst:Account)
        WHERE t.kind IN ['wire_in', 'ach_in']
        MATCH (t)-[:COUNTERPARTY]->(src:Counterparty)""" + _FLOW_SET.format(kind="inbound")),
    "outbound": _q("DERIVE_FLOWS[outbound]", _FLOW_DOC.format(what="outgoing wires and ACH (Account -> Counterparty)"), """
        MATCH (t:Transaction)-[:FROM_ACCOUNT]->(src:Account)
        WHERE t.kind IN ['wire_out', 'ach_out']
        MATCH (t)-[:COUNTERPARTY]->(dst:Counterparty)""" + _FLOW_SET.format(kind="outbound")),
}

DERIVE_COMMUNICATES: dict[str, str] = {
    "direct": _q("DERIVE_COMMUNICATES[direct]", """
        Derived Person -> Person communication edges from addressed messages (author -> each recipient). Feeds
        key_people (PageRank / betweenness). MNPI documents are excluded so the derived network never reveals
        walled-off deal activity.
        """, """
        MATCH (a:Person)-[:AUTHORED]->(d:Document)-[:SENT_TO]->(b:Person)
        WHERE a.id <> b.id AND coalesce(d.sensitivity, '') <> 'MNPI'
        WITH a, b, count(DISTINCT d) AS n, max(d.ts) AS last
        MERGE (a)-[c:COMMUNICATES]->(b)
        SET c.direct = n, c.last = last
        """),
    "replies": _q("DERIVE_COMMUNICATES[replies]", """
        Adds replies (author of a reply -> author of the message replied to) to the communication edges, again
        excluding MNPI documents on either side.
        """, """
        MATCH (a:Person)-[:AUTHORED]->(d:Document)-[:REPLY_TO]->(o:Document)<-[:AUTHORED]-(b:Person)
        WHERE a.id <> b.id AND coalesce(d.sensitivity, '') <> 'MNPI' AND coalesce(o.sensitivity, '') <> 'MNPI'
        WITH a, b, count(DISTINCT d) AS n, max(d.ts) AS last
        MERGE (a)-[c:COMMUNICATES]->(b)
        SET c.replies = n, c.last = CASE WHEN c.last IS NULL OR last > c.last THEN last ELSE c.last END
        """),
    "weight": _q("DERIVE_COMMUNICATES[weight]", "Total interaction weight per edge (messages + replies).", """
        MATCH ()-[c:COMMUNICATES]->()
        SET c.weight = coalesce(c.direct, 0) + coalesce(c.replies, 0)
        """),
}


# ---------------------------------------------------------------------------------------------------------------------
_group("Retrieval (ACL-filtered)")


def acl(d: str = "d", p: str = "p") -> str:
    """Cypher predicate: Document `d` is visible to Person `p`.

    Visibility is pure graph traversal: the document is public, or the person (directly, through their team, or
    through membership of the channel it was posted in) has a CAN_ACCESS edge to it. Pattern predicates are used
    because FalkorDB does not support `exists()` on patterns (verified in the spike).
    """
    return (f"({d}.public = true OR ({p})-[:CAN_ACCESS]->({d}) OR ({p})-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->({d}) "
            f"OR ({p})-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-({d}))")


ACL_DOC = acl()
DOC_FILTERS = ("($kinds IS NULL OR d.kind IN $kinds) AND ($since IS NULL OR d.ts >= $since) "
               "AND ($until IS NULL OR d.ts < $until)")

SEARCH_DOCS_VECTOR = _q("SEARCH_DOCS_VECTOR", """
    Semantic document search. The vector index is queried first (cosine *distance*, lower is better) and the ACL is
    applied as a post-filter, so the caller over-fetches `$k` candidates (adaptive: 80, then 400 for low-privilege
    personas) to still return `$limit` visible documents.
    """, f"""
    CALL db.idx.vector.queryNodes('Document', 'embedding', $k, vecf32($v)) YIELD node AS d, score
    MATCH (p:Person {{id: $pid}})
    WHERE {ACL_DOC} AND {DOC_FILTERS}
    RETURN d.id AS id, score
    ORDER BY score ASC
    LIMIT $limit
    """)

SEARCH_DOCS_FULLTEXT = _q("SEARCH_DOCS_FULLTEXT", """
    Keyword document search over the full-text index on Document(title, text) (score: higher is better), with the
    same ACL post-filter. Fused with the vector results by reciprocal-rank fusion in `search_knowledge`.
    """, f"""
    CALL db.idx.fulltext.queryNodes('Document', $q) YIELD node AS d, score
    MATCH (p:Person {{id: $pid}})
    WHERE {ACL_DOC} AND {DOC_FILTERS}
    RETURN d.id AS id, score
    ORDER BY score DESC
    LIMIT $limit
    """)

SEARCH_FACTS_VECTOR = _q("SEARCH_FACTS_VECTOR", """
    Semantic search over extracted bitemporal facts. A fact is visible only if at least one document it cites is
    visible to the persona, so facts never leak restricted (e.g. MNPI) sources.
    """, f"""
    CALL db.idx.vector.queryNodes('Fact', 'embedding', $k, vecf32($v)) YIELD node AS f, score
    MATCH (p:Person {{id: $pid}})
    MATCH (f)-[:CITES]->(d:Document)
    WHERE {ACL_DOC}
    RETURN f.id AS id, f.statement AS statement, f.subject_id AS subject, f.predicate AS predicate,
           f.valid_from AS valid_from, f.valid_to AS valid_to, collect(DISTINCT d.id)[..3] AS docs, min(score) AS score
    ORDER BY score ASC
    LIMIT $limit
    """)

DOC_DETAILS = _q("DOC_DETAILS", """
    Hydrate documents (ACL re-checked) with author, channel, thread and mentioned entities, for citations and the
    evidence subgraph.
    """, f"""
    UNWIND $ids AS id
    MATCH (d:Document {{id: id}})
    MATCH (p:Person {{id: $pid}})
    WHERE {ACL_DOC}
    OPTIONAL MATCH (a:Person)-[:AUTHORED]->(d)
    OPTIONAL MATCH (d)-[:POSTED_IN]->(c:Channel)
    OPTIONAL MATCH (d)-[:IN_THREAD]->(t:Thread)
    RETURN d.id, d.kind, d.ts, d.title, d.text, d.sensitivity, d.doc_type, d.ticket_key, d.ticket_status,
           a.id, a.name, c.id, c.name, t.id, [(d)-[:MENTIONS]->(x) | [labels(x)[0], x.id]] AS mentions
    """)

THREAD_DOCS = _q("THREAD_DOCS", "Visible documents in the given threads (conversation context around a hit).",
                 f"""
    UNWIND $tids AS tid
    MATCH (t:Thread {{id: tid}})<-[:IN_THREAD]-(d:Document)
    MATCH (p:Person {{id: $pid}})
    WHERE {ACL_DOC}
    RETURN tid, d.id, d.ts
    ORDER BY d.ts
    """)

VISIBLE_DOCS = _q("VISIBLE_DOCS", "Which of the given document ids the persona may see.", f"""
    UNWIND $ids AS id
    MATCH (d:Document {{id: id}})
    MATCH (p:Person {{id: $pid}})
    WHERE {ACL_DOC}
    RETURN d.id
    """)

VISIBLE_DEALS = _q("VISIBLE_DEALS", """
    Information barrier: an MNPI deal (codename, target) is visible only to its deal team (ON_DEAL_TEAM) and to
    Compliance. Used to filter deal names and deal-derived topics out of every non-document tool output.
    """, """
    MATCH (p:Person {id: $pid})
    MATCH (x:Deal)
    OPTIONAL MATCH (p)-[r:ON_DEAL_TEAM]->(x)
    OPTIONAL MATCH (p)-[m:MEMBER_OF]->(:Team {id: 'team_compliance'})
    RETURN x.id, x.codename, x.target, x.sensitivity, count(r) > 0 AS on_team, count(m) > 0 AS compliance
    """)

ENTITY_CATALOG = _q("ENTITY_CATALOG", "Named entities used to resolve free-text names to ids (cached briefly).", """
    MATCH (x)
    WHERE x:Person OR x:Team OR x:System OR x:Deal OR x:Channel OR x:Decision OR x:Counterparty
    RETURN labels(x)[0], x.id, coalesce(x.name, x.codename, x.title), x.target, x.title
    """)

ENTITY_LABELS = ("Person", "Team", "System", "Client", "Account", "Counterparty", "Transaction", "Deal", "Channel",
                 "Topic", "Decision", "PiiToken", "Document", "Fact", "Thread", "Case", "Task", "Action", "Agent")
_DISPLAY = {
    "Transaction": "n.kind + ' USD ' + toString(n.amount) + ' ' + substring(n.ts, 0, 10)",
    "Action": "n.kind + ' ' + n.target_id",
    "Deal": "coalesce(n.codename, n.id)",
    "Fact": "n.statement",
    "Document": "coalesce(n.title, n.kind + ' ' + substring(n.ts, 0, 10))",
    "Account": "coalesce(n.kind + ' account ' + n.number, n.id)",
}

ENTITY_NAMES: dict[str, str] = {
    label: _q(f"ENTITY_NAMES[{label}]", "Display names for a batch of entity ids of one label.", f"""
              UNWIND $ids AS id
              MATCH (n:{label} {{id: id}})
              RETURN n.id, {_DISPLAY.get(label, 'coalesce(n.name, n.title, n.id)')}
              """)
    for label in ENTITY_LABELS
}

GET_ENTITY: dict[str, str] = {
    label: _q(f"GET_ENTITY[{label}]", "All properties of one entity plus its relationship-type degree summary.", f"""
              MATCH (n:{label} {{id: $id}})
              OPTIONAL MATCH (n)-[r]-()
              RETURN properties(n), type(r) AS rel, count(r) AS n
              """)
    for label in ENTITY_LABELS
}

NEIGHBORHOOD: dict[str, str] = {
    label: _q(f"NEIGHBORHOOD[{label}]", """
              One-hop neighbourhood of an entity, strongest edges first. Neighbouring documents are ACL-filtered;
              facts and redaction events are excluded (facts are only served through ACL-checked fact search).
              """, f"""
              MATCH (n:{label} {{id: $id}})-[r]-(m)
              MATCH (p:Person {{id: $pid}})
              WHERE ($types IS NULL OR type(r) IN $types) AND NOT m:Fact AND NOT m:RedactionEvent
                AND (NOT m:Document OR {acl('m')})
              RETURN type(r) AS rel, startNode(r).id = n.id AS outgoing, labels(m)[0] AS label, m.id AS id,
                     coalesce(m.name, m.codename, m.title, m.id) AS name, r.weight AS weight, m.ts AS ts,
                     m.kind AS kind, m.amount AS amount
              ORDER BY coalesce(r.weight, 0) DESC, ts DESC
              LIMIT $limit
              """)
    for label in ENTITY_LABELS
}


# ---------------------------------------------------------------------------------------------------------------------
_group("Reasoning")

KNOWS_LABELS = ("System", "Client", "Deal", "Topic", "Counterparty")

WHO_KNOWS_ENTITY: dict[str, str] = {
    label: _q(f"WHO_KNOWS_ENTITY[{label}]", """
              Ranked experts on one entity from the derived KNOWS_ABOUT edges. The answered / asked / documented
              counters explain the ranking (answering other people's questions and writing authoritative docs
              outweigh volume), and end_date flags departures (bus factor).
              """, f"""
              MATCH (p:Person)-[k:KNOWS_ABOUT]->(x:{label} {{id: $id}})
              OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
              RETURN p.id, p.name, p.title, t.name, p.end_date, k.weight, k.evidence, k.answered, k.asked,
                     k.documented, k.last_seen
              ORDER BY k.weight DESC
              LIMIT $limit
              """)
    for label in KNOWS_LABELS
}

WHO_KNOWS_TOPICS = _q("WHO_KNOWS_TOPICS", """
    Expertise on a free-text subject: nearest Topic nodes by vector similarity, then the KNOWS_ABOUT edges into them,
    one row per (person, topic). The caller drops topics behind the information barrier, then sums weights per
    person scaled by topic similarity (1 - cosine distance).
    """, """
    CALL db.idx.vector.queryNodes('Topic', 'embedding', $k, vecf32($v)) YIELD node AS x, score
    WITH x, score WHERE score <= $max_dist
    MATCH (p:Person)-[k:KNOWS_ABOUT]->(x)
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
    RETURN p.id, p.name, p.title, t.name, p.end_date, x.id, x.name, score, k.weight, k.evidence, k.answered,
           k.asked, k.documented, k.last_seen
    ORDER BY k.weight DESC
    LIMIT 300
    """)

OWNERSHIP_HISTORY = _q("OWNERSHIP_HISTORY", """
    Formal ownership intervals of a system (bitemporal OWNS edges: valid_from inclusive, valid_to exclusive / open).
    `who_owns(as_of)` selects the intervals containing the requested date.
    """, """
    MATCH (o)-[e:OWNS]->(s:System {id: $id})
    RETURN labels(o)[0], o.id, coalesce(o.name, o.id), o.title, e.valid_from, e.valid_to, e.source
    ORDER BY e.valid_from, labels(o)[0]
    """)

OWNERSHIP_FACTS = _q("OWNERSHIP_FACTS", "Extracted ownership/maintenance facts about a system, ACL-filtered by source.",
                     f"""
    MATCH (f:Fact {{subject_id: $id}})
    WHERE f.predicate IN ['owned_by', 'maintained_by', 'assigned_to']
    MATCH (p:Person {{id: $pid}})
    MATCH (f)-[:CITES]->(d:Document)
    WHERE {ACL_DOC}
    RETURN f.id, f.statement, f.predicate, f.object, f.valid_from, f.valid_to, collect(DISTINCT d.id)[..3]
    ORDER BY f.valid_from
    """)

LIST_DECISIONS = _q("LIST_DECISIONS", "All recorded decisions (small set; the agent picks one).", """
    MATCH (x:Decision)
    RETURN x.id, x.title, x.status, x.effective, x.summary
    ORDER BY x.effective
    """)

DECISION_TRAIL = _q("DECISION_TRAIL", """
    The evidence trail of a decision: where it was decided (DECIDED_IN), what justified it (JUSTIFIED_BY) and what
    it cites, in time order, keeping only sources visible to the persona. OPTIONAL MATCH + WITH ... WHERE is used
    because pattern comprehensions with pattern-predicate filters are rejected by FalkorDB.
    """, f"""
    MATCH (p:Person {{id: $pid}})
    MATCH (x:Decision {{id: $id}})
    OPTIONAL MATCH (x)-[r:DECIDED_IN|JUSTIFIED_BY|CITES]->(d:Document)
    WITH p, x, r, d WHERE d IS NULL OR {ACL_DOC}
    RETURN type(r), d.id, d.ts
    ORDER BY d.ts
    """)

DECISION_SOURCE_COUNT = _q("DECISION_SOURCE_COUNT", "Total sources of a decision (to report how many are hidden).",
                           """
    MATCH (x:Decision {id: $id})-[:DECIDED_IN|JUSTIFIED_BY|CITES]->(d:Document)
    RETURN count(DISTINCT d)
    """)

DECISION_LINKS = _q("DECISION_LINKS", "Supersession chain and topics of a decision.", """
    MATCH (x:Decision {id: $id})
    OPTIONAL MATCH (x)-[:SUPERSEDES]->(prev:Decision)
    OPTIONAL MATCH (nxt:Decision)-[:SUPERSEDES]->(x)
    OPTIONAL MATCH (x)-[:ABOUT]->(t:Topic)
    RETURN collect(DISTINCT [prev.id, prev.title, prev.effective]), collect(DISTINCT [nxt.id, nxt.title, nxt.effective]),
           collect(DISTINCT t.name)
    """)

BLAST_RADIUS = _q("BLAST_RADIUS", """
    Downstream impact: every system that transitively DEPENDS_ON the failing one, with its minimum hop distance.
    """, """
    MATCH path = (dep:System)-[:DEPENDS_ON*1..6]->(s:System {id: $id})
    RETURN dep.id, dep.name, dep.tier, min(length(path)) AS hops
    ORDER BY hops, dep.id
    """)

UPSTREAM = _q("UPSTREAM", "Upstream dependencies of a system (what it needs to run).", """
    MATCH path = (s:System {id: $id})-[:DEPENDS_ON*1..4]->(up:System)
    RETURN up.id, up.name, up.tier, min(length(path)) AS hops
    ORDER BY hops, up.id
    """)

DEPENDENCY_EDGES = _q("DEPENDENCY_EDGES", "DEPENDS_ON edges among a set of systems (evidence subgraph).", """
    MATCH (a:System)-[:DEPENDS_ON]->(b:System)
    WHERE a.id IN $ids AND b.id IN $ids
    RETURN a.id, b.id
    """)

SYSTEM_OWNERS_EXPERTS = _q("SYSTEM_OWNERS_EXPERTS", "Current formal owners and top-2 experts per system.", """
    UNWIND $ids AS sid
    MATCH (s:System {id: sid})
    OPTIONAL MATCH (o)-[e:OWNS]->(s) WHERE e.valid_to IS NULL
    WITH s, collect(DISTINCT coalesce(o.name, o.id)) AS owners
    OPTIONAL MATCH (k:Person)-[kk:KNOWS_ABOUT]->(s)
    WITH s, owners, k, kk ORDER BY kk.weight DESC
    RETURN s.id, owners, collect(k.name)[..2], collect(k.end_date)[..2]
    """)

SYSTEM_DOCS = _q("SYSTEM_DOCS", "Recent visible tickets and documents mentioning a system (incidents, runbooks).",
                 f"""
    MATCH (p:Person {{id: $pid}})
    MATCH (d:Document)-[:MENTIONS]->(s:System {{id: $id}})
    WHERE d.kind IN ['ticket', 'doc', 'meeting'] AND {ACL_DOC}
    RETURN d.id, d.ts
    ORDER BY d.ts DESC
    LIMIT $limit
    """)

FIND_PATH: dict[int, str] = {
    hops: _q(f"FIND_PATH[{hops}]", """
             All shortest undirected paths between two entities (FalkorDB requires both endpoints to be bound in a
             preceding WITH; undirected shortestPath() is not supported, allShortestPaths is). Paths through
             documents or deals the persona cannot see are dropped by the caller.
             """, f"""
             MATCH (a) WHERE a.id = $a
             MATCH (b) WHERE b.id = $b
             WITH a, b
             MATCH path = allShortestPaths((a)-[*..{hops}]-(b))
             RETURN [n IN nodes(path) | [labels(n)[0], n.id, coalesce(n.name, n.codename, n.title, n.id)]],
                    [r IN relationships(path) | type(r)]
             LIMIT 12
             """)
    for hops in (2, 3, 4, 5, 6)
}


# ---------------------------------------------------------------------------------------------------------------------
_group("Investigation (graph algorithms)")

ALERTS = _q("ALERTS", """
    Transactions under suspicion: an alert id from transaction monitoring, or a fraud score at or above min_score,
    with both accounts and the counterparty. Entry point for investigations (list_alerts).
    """, """
    MATCH (t:Transaction)
    WHERE t.alert IS NOT NULL OR t.fraud_score >= $min_score
    OPTIONAL MATCH (t)-[:FROM_ACCOUNT]->(fa:Account)
    OPTIONAL MATCH (t)-[:TO_ACCOUNT]->(ta:Account)
    OPTIONAL MATCH (t)-[:COUNTERPARTY]->(cp:Counterparty)
    RETURN t.id, t.kind, t.amount, t.currency, t.ts, t.alert, t.fraud_score, fa.id, ta.id, cp.id, cp.name, cp.country
    ORDER BY coalesce(t.fraud_score, 0.0) DESC, t.ts DESC
    LIMIT $limit
    """)

TXN_PARTIES = _q("TXN_PARTIES", "Both accounts, their holders and the counterparty of transactions (seed set).", """
    UNWIND $ids AS tid
    MATCH (t:Transaction {id: tid})
    OPTIONAL MATCH (t)-[:FROM_ACCOUNT]->(fa:Account)
    OPTIONAL MATCH (fc:Client)-[:HOLDS]->(fa)
    OPTIONAL MATCH (t)-[:TO_ACCOUNT]->(ta:Account)
    OPTIONAL MATCH (tc:Client)-[:HOLDS]->(ta)
    OPTIONAL MATCH (t)-[:COUNTERPARTY]->(cp:Counterparty)
    RETURN t.id, t.kind, t.amount, t.ts, t.alert, t.fraud_score, fa.id, fc.id, ta.id, tc.id, cp.id, cp.name
    """)

ACCOUNT_HOLDERS = _q("ACCOUNT_HOLDERS", "Clients holding the given accounts.", """
    UNWIND $ids AS aid
    MATCH (c:Client)-[:HOLDS]->(a:Account {id: aid})
    RETURN a.id, c.id
    """)

TOKEN_HOLDERS = _q("TOKEN_HOLDERS", "Clients carrying the given PII tokens.", """
    UNWIND $ids AS kid
    MATCH (c:Client)-[:HAS_IDENTIFIER]->(k:PiiToken {id: kid})
    RETURN k.id, c.id
    """)

CLIENT_ACCOUNTS = _q("CLIENT_ACCOUNTS", "Clients with risk rating, KYC status and the accounts they hold.", """
    UNWIND $ids AS cid
    MATCH (c:Client {id: cid})
    OPTIONAL MATCH (c)-[:HOLDS]->(a:Account)
    RETURN c.id, c.name, c.risk_rating, c.kyc_status, a.id, a.kind, a.status
    """)

SHARED_IDENTIFIERS = _q("SHARED_IDENTIFIERS", """
    One BFS hop of identifier linking: for each frontier client, the PII tokens of the requested types that other
    clients also carry. Tokens held by more than max_share clients are hubs (an office address) and are skipped, so
    one popular value cannot glue unrelated clients together.
    """, """
    UNWIND $ids AS cid
    MATCH (c:Client {id: cid})-[:HAS_IDENTIFIER]->(k:PiiToken)
    WHERE k.type IN $types
    MATCH (k)<-[:HAS_IDENTIFIER]-(h:Client)
    WITH c, k, collect(DISTINCT h.id) AS holders
    WHERE size(holders) >= 2 AND size(holders) <= $max_share
    RETURN c.id, k.id, k.type, size(holders), [x IN holders WHERE x <> c.id]
    """)

RING_COMPONENTS = _q("RING_COMPONENTS", """
    Ring candidates: Weakly Connected Components of the client / identifier-token graph. Clients land in one
    component when any chain of shared identifiers connects them; components with at least min_clients clients are
    returned for risk scoring.
    """, """
    CALL algo.WCC({nodeLabels: ['Client', 'PiiToken'], relationshipTypes: ['HAS_IDENTIFIER']})
    YIELD node, componentId
    WITH componentId, collect(CASE WHEN labels(node)[0] = 'Client' THEN node.id END) AS clients
    WHERE size(clients) >= $min_clients
    RETURN componentId, clients
    ORDER BY size(clients) DESC
    LIMIT 60
    """)

RING_SHARED_TOKENS = _q("RING_SHARED_TOKENS", "Tokens shared by two or more of the given clients.", """
    MATCH (c:Client)-[:HAS_IDENTIFIER]->(k:PiiToken)
    WHERE c.id IN $ids
    WITH k, collect(DISTINCT c.id) AS holders
    WHERE size(holders) >= 2
    RETURN k.id, k.type, holders
    """)

ACCOUNT_ACTIVITY = _q("ACCOUNT_ACTIVITY", """
    Per account: status, transaction count, deposits between 8,000 and 10,000 (structuring just under the cash
    reporting threshold), highest fraud score and alert ids.
    """, """
    UNWIND $ids AS aid
    MATCH (a:Account {id: aid})
    OPTIONAL MATCH (t:Transaction)-[:FROM_ACCOUNT|TO_ACCOUNT]->(a)
    RETURN a.id, a.status, count(t),
           sum(CASE WHEN t.kind IN ['ach_in', 'wire_in', 'cash_in'] AND t.amount >= 8000 AND t.amount < 10000
                    THEN 1 ELSE 0 END),
           max(coalesce(t.fraud_score, 0.0)), collect(DISTINCT t.alert)
    """)

FLOWS_AROUND = _q("FLOWS_AROUND", "Derived FLOWS_TO edges into or out of the given accounts.", """
    MATCH (x)-[f:FLOWS_TO]->(y)
    WHERE x.id IN $ids OR y.id IN $ids
    RETURN labels(x)[0], x.id, coalesce(x.name, x.id), labels(y)[0], y.id, coalesce(y.name, y.id), f.amount, f.n,
           f.first_ts, f.last_ts, f.max_fraud_score, f.alerts
    """)

CP_SENDERS = _q("CP_SENDERS", """
    Firm-wide number of distinct accounts paying each counterparty. A common exit only means something when the
    counterparty is exclusive to the group, not a popular merchant that hundreds of clients pay.
    """, """
    UNWIND $ids AS cid
    MATCH (a:Account)-[:FLOWS_TO]->(cp:Counterparty {id: cid})
    RETURN cp.id, count(DISTINCT a)
    """)

FLOW_CENTRALITY = _q("FLOW_CENTRALITY", """
    Betweenness centrality over the money-flow network (accounts and counterparties via FLOWS_TO): high scores mark
    pass-through hubs. Returns score, rank and network size for the requested accounts.
    """, """
    CALL algo.betweenness({nodeLabels: ['Account', 'Counterparty'], relationshipTypes: ['FLOWS_TO']})
    YIELD node, score
    WITH node, score ORDER BY score DESC
    WITH collect([node.id, score]) AS ranked
    UNWIND range(0, size(ranked) - 1) AS i
    WITH ranked[i] AS r, i, size(ranked) AS total
    WHERE r[0] IN $ids
    RETURN r[0], r[1], i + 1, total
    """)

TRACE_FUNDS: dict[str, str] = {}
for _h in range(1, 5):
    TRACE_FUNDS[f"downstream{_h}"] = _q(f"TRACE_FUNDS[downstream{_h}]", """
        Follow money downstream from the start accounts along FLOWS_TO edges active since the given date.
        Intermediate hops must be accounts (money paid out to a counterparty ends the trail). One variant per
        maximum hop count, because variable-length bounds must be literals.
        """, f"""
        MATCH (s) WHERE s.id IN $ids AND labels(s)[0] IN ['Account', 'Counterparty']
        MATCH p = (s)-[:FLOWS_TO*1..{_h}]->(x)
        WHERE all(f IN relationships(p) WHERE f.last_ts >= $since)
          AND all(m IN nodes(p)[1..-1] WHERE labels(m)[0] = 'Account')
        RETURN [n IN nodes(p) | [labels(n)[0], n.id, coalesce(n.name, n.id)]],
               [f IN relationships(p) | [f.amount, f.n, f.first_ts, f.last_ts, f.max_fraud_score]]
        LIMIT 300
        """)
    TRACE_FUNDS[f"upstream{_h}"] = _q(f"TRACE_FUNDS[upstream{_h}]", """
        Follow money upstream into the start accounts (where did the funds come from), same rules as downstream.
        """, f"""
        MATCH (s) WHERE s.id IN $ids AND labels(s)[0] IN ['Account', 'Counterparty']
        MATCH p = (x)-[:FLOWS_TO*1..{_h}]->(s)
        WHERE all(f IN relationships(p) WHERE f.last_ts >= $since)
          AND all(m IN nodes(p)[1..-1] WHERE labels(m)[0] = 'Account')
        RETURN [n IN nodes(p) | [labels(n)[0], n.id, coalesce(n.name, n.id)]],
               [f IN relationships(p) | [f.amount, f.n, f.first_ts, f.last_ts, f.max_fraud_score]]
        LIMIT 300
        """)

KEY_PEOPLE_PAGERANK = _q("KEY_PEOPLE_PAGERANK", """
    PageRank over the derived Person-COMMUNICATES-Person network: who is most consulted. Optional team filter.
    """, """
    CALL algo.pageRank('Person', 'COMMUNICATES') YIELD node, score
    WITH node, score WHERE $team IS NULL OR node.team_id = $team
    RETURN node.id, node.name, node.title, node.team_id, node.end_date, score
    ORDER BY score DESC
    LIMIT $limit
    """)

KEY_PEOPLE_BETWEENNESS = _q("KEY_PEOPLE_BETWEENNESS", """
    Betweenness centrality over the communication network: who bridges otherwise separate groups (key-person risk).
    """, """
    CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COMMUNICATES']}) YIELD node, score
    WITH node, score WHERE $team IS NULL OR node.team_id = $team
    RETURN node.id, node.name, node.title, node.team_id, node.end_date, score
    ORDER BY score DESC
    LIMIT $limit
    """)

DEAL_WALL = _q("DEAL_WALL", """
    The information barrier around a deal: its properties, the deal team (ON_DEAL_TEAM) and the Compliance team,
    who are the only people allowed to see MNPI about it.
    """, """
    MATCH (x:Deal {id: $id})
    OPTIONAL MATCH (p:Person)-[:ON_DEAL_TEAM]->(x)
    WITH x, collect(DISTINCT p.id) AS team
    OPTIONAL MATCH (c:Person {team_id: 'team_compliance'})
    RETURN properties(x), team, collect(DISTINCT c.id)
    """)

DEAL_DOCS = _q("DEAL_DOCS", "Documents linked to a deal by extraction (MENTIONS).", """
    MATCH (d:Document)-[:MENTIONS]->(:Deal {id: $id})
    RETURN DISTINCT d.id
    """)

DEAL_DOCS_FULLTEXT = _q("DEAL_DOCS_FULLTEXT", """
    Documents naming a deal's codename or target, found with the full-text index and confirmed by substring match
    (catches leaks the extractor did not link).
    """, """
    CALL db.idx.fulltext.queryNodes('Document', $q) YIELD node AS d
    WITH d WHERE toLower(coalesce(d.text, '') + ' ' + coalesce(d.title, '')) CONTAINS $needle
    RETURN d.id
    LIMIT 400
    """)

DOC_AUDIENCE = _q("DOC_AUDIENCE", """
    Everyone a document reached: author, recipients (SENT_TO), members of the channel it was posted in, people and
    teams granted access (CAN_ACCESS), and whether it is public. Compared with the deal wall by barrier_check.
    """, """
    UNWIND $ids AS did
    MATCH (d:Document {id: did})
    OPTIONAL MATCH (a:Person)-[:AUTHORED]->(d)
    OPTIONAL MATCH (d)-[:POSTED_IN]->(ch:Channel)
    WITH d, a, ch
    OPTIONAL MATCH (d)-[:SENT_TO]->(r:Person)
    WITH d, a, ch, collect(DISTINCT r.id) AS recipients
    OPTIONAL MATCH (m:Person)-[:CAN_ACCESS]->(ch)
    WITH d, a, ch, recipients, collect(DISTINCT m.id) AS members
    OPTIONAL MATCH (g:Person)-[:CAN_ACCESS]->(d)
    WITH d, a, ch, recipients, members, collect(DISTINCT g.id) AS granted
    OPTIONAL MATCH (tm:Person)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d)
    RETURN d.id, d.kind, d.ts, d.sensitivity, d.public, a.id, ch.id, ch.name, recipients, members, granted,
           collect(DISTINCT tm.id)
    """)

DOC_SURVEILLANCE = _q("DOC_SURVEILLANCE", """
    Compliance surveillance read: the (already PII-redacted) content of documents that barrier_check flagged as
    breaches, bypassing the per-recipient document ACL. Only used for callers with the CCO 'reveal' scope, and every
    use is audit-logged as a surveillance_read ops event.
    """, """
    UNWIND $ids AS did
    MATCH (d:Document {id: did})
    OPTIONAL MATCH (a:Person)-[:AUTHORED]->(d)
    OPTIONAL MATCH (d)-[:POSTED_IN]->(c:Channel)
    RETURN d.id, d.kind, d.ts, d.title, d.text, d.sensitivity, a.id, a.name, c.id, c.name
    """)

# ---------------------------------------------------------------------------------------------------------------------
_group("Operations (cases, actions, blackboard)")

_EVIDENCE_LABELS = ("Transaction", "Account", "Client", "PiiToken", "Counterparty", "Document", "Person", "Channel",
                    "Deal", "System", "Team")
_ACTION_TARGET_LABELS = ("Account", "Case", "Person", "Team", "System", "Channel")

ENSURE_AGENTS = _q("ENSURE_AGENTS", "Register the agent roster as :Agent nodes (idempotent).", """
    UNWIND $rows AS r
    MERGE (a:Agent {id: r.id})
    SET a.name = r.name, a.role = r.role, a.model = r.model
    """)

CREATE_CASE = _q("CREATE_CASE", """
    Open a case: who opened it (agent), on whose request (persona) and the initial assignee (the opening agent).
    """, """
    MATCH (ag:Agent {id: $agent})
    MATCH (p:Person {id: $pid})
    CREATE (c:Case {id: $id, title: $title, kind: $kind, severity: $severity, status: 'open', summary: $summary,
                    created: $now, updated: $now, opened_by: $agent})
    CREATE (c)-[:OPENED_BY {ts: $now}]->(ag)
    CREATE (c)-[:REQUESTED_BY {ts: $now}]->(p)
    CREATE (c)-[:ASSIGNED_TO {ts: $now}]->(ag)
    RETURN c.id
    """)

FIND_OPEN_CASE = _q("FIND_OPEN_CASE", """
    De-duplication: an unclosed case of the same kind that already holds any of the new (non-person) evidence.
    """, """
    MATCH (c:Case)-[:EVIDENCE]->(x)
    WHERE c.kind = $kind AND c.status <> 'closed' AND x.id IN $ids
    WITH c, count(DISTINCT x) AS shared
    RETURN c.id, shared
    ORDER BY shared DESC, c.created DESC
    LIMIT 1
    """)

ADD_EVIDENCE: dict[str, str] = {
    lab: _q(f"ADD_EVIDENCE[{lab}]", "Attach evidence nodes of one label to a case (MERGE; the note is updated).", f"""
        MATCH (c:Case {{id: $case}})
        UNWIND $rows AS r
        MATCH (x:{lab} {{id: r.id}})
        MERGE (c)-[e:EVIDENCE]->(x)
        ON CREATE SET e.ts = $now, e.added_by = $agent
        SET e.note = r.note, c.updated = $now
        RETURN x.id
        """)
    for lab in _EVIDENCE_LABELS
}

GET_CASE = _q("GET_CASE", "A case with its current assignee(s) and every evidence item (label, id, note).", """
    MATCH (c:Case {id: $id})
    OPTIONAL MATCH (c)-[:ASSIGNED_TO]->(ag:Agent)
    WITH c, collect(ag.id) AS assignees
    OPTIONAL MATCH (c)-[e:EVIDENCE]->(x)
    RETURN c.id, c.title, c.kind, c.severity, c.status, c.summary, c.created, c.updated, c.opened_by, c.sar_ref,
           assignees, collect([labels(x)[0], x.id, e.note, e.added_by, e.ts])
    """)

CASE_ACTIONS = _q("CASE_ACTIONS", "Actions proposed for a case, with the human decision if any.", """
    MATCH (a:Action)-[:FOR_CASE]->(:Case {id: $id})
    OPTIONAL MATCH (a)-[d:APPROVED_BY|REJECTED_BY]->(p:Person)
    RETURN a.id, a.kind, a.status, a.target_label, a.target_id, a.rationale, a.details, a.proposed_by, a.created,
           a.effect, type(d), p.id, p.name, d.ts, d.note
    ORDER BY a.created
    """)

CASE_TASKS = _q("CASE_TASKS", "Blackboard tasks attached to a case.", """
    MATCH (t:Task)-[:FOR_CASE]->(:Case {id: $id})
    RETURN t.id, t.title, t.status, t.from_agent, t.to_agent, t.note, t.created, t.claimed, t.done, t.result
    ORDER BY t.created
    """)

CASE_HANDOFFS = _q("CASE_HANDOFFS", "Agent-to-agent handoffs for a case, in time order.", """
    MATCH (a:Agent)-[h:HANDED_OFF_TO]->(b:Agent)
    WHERE h.case_id = $id
    RETURN a.id, b.id, h.note, h.ts, h.task_id
    ORDER BY h.ts
    """)

LIST_CASES = _q("LIST_CASES", "Cases (optionally by status), most recently updated first, with counts.", """
    MATCH (c:Case)
    WHERE $status IS NULL OR c.status = $status
    OPTIONAL MATCH (c)-[:EVIDENCE]->(x)
    WITH c, count(DISTINCT x) AS evidence
    OPTIONAL MATCH (a:Action)-[:FOR_CASE]->(c)
    WITH c, evidence, count(a) AS actions, sum(CASE WHEN a.status = 'proposed' THEN 1 ELSE 0 END) AS pending
    OPTIONAL MATCH (c)-[:ASSIGNED_TO]->(ag:Agent)
    RETURN c.id, c.title, c.kind, c.severity, c.status, c.created, c.updated, evidence, actions, pending,
           collect(ag.id)
    ORDER BY c.updated DESC
    LIMIT $limit
    """)

UPDATE_CASE = _q("UPDATE_CASE", "Update case status / severity / summary (null parameters leave a field as is).", """
    MATCH (c:Case {id: $id})
    SET c.status = coalesce($status, c.status), c.severity = coalesce($severity, c.severity),
        c.summary = coalesce($summary, c.summary), c.updated = $now
    RETURN c.id, c.status, c.severity
    """)

FIND_ACTION = _q("FIND_ACTION", "An identical action still awaiting approval (de-duplication).", """
    MATCH (a:Action {case_id: $case, kind: $kind, target_id: $target})
    WHERE a.status = 'proposed'
    RETURN a.id
    LIMIT 1
    """)

CREATE_ACTION: dict[str, str] = {
    lab: _q(f"CREATE_ACTION[{lab}]", """
        Propose an action: the proposing agent (PROPOSED), the case (FOR_CASE) and the target node (TARGETS). It
        stays 'proposed' until a human decides.
        """, f"""
        MATCH (c:Case {{id: $case}})
        MATCH (ag:Agent {{id: $agent}})
        MATCH (x:{lab} {{id: $target}})
        CREATE (a:Action {{id: $id, kind: $kind, status: 'proposed', rationale: $rationale, details: $details,
                          created: $now, case_id: $case, target_id: $target, target_label: '{lab}',
                          proposed_by: $agent}})
        CREATE (ag)-[:PROPOSED {{ts: $now}}]->(a)
        CREATE (a)-[:FOR_CASE]->(c)
        CREATE (a)-[:TARGETS]->(x)
        SET c.updated = $now
        RETURN a.id
        """)
    for lab in _ACTION_TARGET_LABELS
}

GET_ACTION = _q("GET_ACTION", "One action with its target and case.", """
    MATCH (a:Action {id: $id})
    RETURN a.id, a.kind, a.status, a.target_label, a.target_id, a.case_id, a.rationale, a.details
    """)

DECIDE_ACTION: dict[str, str] = {
    rel: _q(f"DECIDE_ACTION[{rel}]", "Record a human decision on a proposed action (human-in-the-loop).", f"""
        MATCH (a:Action {{id: $id}})
        WHERE a.status = 'proposed'
        MATCH (p:Person {{id: $pid}})
        SET a.status = '{status}', a.decided = $now, a.decision_note = $note
        CREATE (a)-[:{rel} {{ts: $now, note: $note}}]->(p)
        RETURN a.id
        """)
    for rel, status in (("APPROVED_BY", "approved"), ("REJECTED_BY", "rejected"))
}

FREEZE_ACCOUNT = _q("FREEZE_ACCOUNT", "Effect of an approved freeze: remember the prior status, then freeze.", """
    MATCH (x:Account {id: $id})
    SET x.status_before_freeze = coalesce(x.status_before_freeze, x.status), x.status = 'frozen',
        x.frozen_by = $action, x.frozen_at = $now
    RETURN x.id, x.status_before_freeze
    """)

RESTRICT_CHANNEL = _q("RESTRICT_CHANNEL", "Effect of an approved channel restriction.", """
    MATCH (x:Channel {id: $id})
    SET x.restricted = true, x.restricted_by = $action, x.restricted_at = $now
    RETURN x.id, x.name
    """)

FILE_SAR = _q("FILE_SAR", "Effect of an approved SAR: stamp the (simulated) filing reference on the case.", """
    MATCH (c:Case {id: $id})
    SET c.sar_ref = $ref, c.sar_filed = $now
    RETURN c.id
    """)

MARK_EXECUTED = _q("MARK_EXECUTED", "Mark an approved action as executed with a description of its effect.", """
    MATCH (a:Action {id: $id})
    SET a.status = 'executed', a.executed = $now, a.effect = $effect
    RETURN a.id
    """)

PENDING_FOR_CASE = _q("PENDING_FOR_CASE", "How many actions on a case still await a decision.", """
    MATCH (c:Case {id: $case})
    OPTIONAL MATCH (a:Action {case_id: $case})
    RETURN sum(CASE WHEN a.status = 'proposed' THEN 1 ELSE 0 END)
    """)

CREATE_HANDOFF = _q("CREATE_HANDOFF", """
    Agent-to-agent handoff on the shared blackboard: a Task for the receiving agent (ASSIGNED_TO), a HANDED_OFF_TO
    edge between the agents carrying the note, and the case moves to in_review.
    """, """
    MATCH (c:Case {id: $case})
    MATCH (src:Agent {id: $from})
    MATCH (dst:Agent {id: $to})
    CREATE (t:Task {id: $task, title: $title, kind: 'case_review', status: 'open', note: $note, created: $now,
                    case_id: $case, from_agent: $from, to_agent: $to})
    CREATE (t)-[:FOR_CASE]->(c)
    CREATE (t)-[:ASSIGNED_TO {ts: $now}]->(dst)
    CREATE (src)-[:HANDED_OFF_TO {ts: $now, note: $note, task_id: $task, case_id: $case}]->(dst)
    SET c.status = 'in_review', c.updated = $now
    RETURN t.id
    """)

UNASSIGN_CASE = _q("UNASSIGN_CASE", "Drop a case's current assignment(s) before reassigning it.", """
    MATCH (:Case {id: $case})-[old:ASSIGNED_TO]->(:Agent)
    DELETE old
    """)

ASSIGN_CASE = _q("ASSIGN_CASE", "Assign a case to an agent.", """
    MATCH (c:Case {id: $case})
    MATCH (ag:Agent {id: $agent})
    MERGE (c)-[e:ASSIGNED_TO]->(ag)
    SET e.ts = $now
    """)

CLAIM_TASK = _q("CLAIM_TASK", """
    Claim an open task. Only the agent it is assigned to may claim it (agent null = an external MCP client).
    """, """
    MATCH (t:Task {id: $id})
    WHERE t.status = 'open' AND ($agent IS NULL OR t.to_agent = $agent)
    MATCH (ag:Agent {id: t.to_agent})
    SET t.status = 'claimed', t.claimed = $now
    CREATE (t)-[:CLAIMED_BY {ts: $now}]->(ag)
    RETURN t.id, t.case_id, t.from_agent, t.note
    """)

TASK_STATUS = _q("TASK_STATUS", "Status and assignee of a task (for error messages).", """
    MATCH (t:Task {id: $id})
    RETURN t.id, t.status, t.to_agent, t.case_id
    """)

COMPLETE_TASK = _q("COMPLETE_TASK", "Complete a task with a result summary.", """
    MATCH (t:Task {id: $id})
    WHERE t.status IN ['open', 'claimed']
    SET t.status = 'done', t.done = $now, t.result = $result
    RETURN t.id, t.case_id
    """)

LIST_TASKS = _q("LIST_TASKS", "Blackboard tasks, optionally for one agent and status, newest first.", """
    MATCH (t:Task)
    WHERE ($agent IS NULL OR t.to_agent = $agent) AND ($status IS NULL OR t.status = $status)
    RETURN t.id, t.title, t.status, t.case_id, t.from_agent, t.to_agent, t.note, t.created
    ORDER BY t.created DESC
    LIMIT $limit
    """)

CLEAR_OPS: dict[str, str] = {
    "accounts": _q("CLEAR_OPS[accounts]", "Reset demo: unfreeze accounts frozen by approved actions.", """
        MATCH (x:Account) WHERE x.status_before_freeze IS NOT NULL
        SET x.status = x.status_before_freeze, x.status_before_freeze = NULL, x.frozen_by = NULL, x.frozen_at = NULL
        """),
    "channels": _q("CLEAR_OPS[channels]", "Reset demo: lift channel restrictions.", """
        MATCH (x:Channel) WHERE x.restricted = true
        SET x.restricted = NULL, x.restricted_by = NULL, x.restricted_at = NULL
        """),
    "handoffs": _q("CLEAR_OPS[handoffs]", "Reset demo: remove handoff edges between agents.", """
        MATCH ()-[h:HANDED_OFF_TO]->() DELETE h
        """),
    "nodes": _q("CLEAR_OPS[nodes]", "Reset demo: delete all cases, actions and tasks with their edges.", """
        MATCH (n) WHERE n:Case OR n:Action OR n:Task DETACH DELETE n
        """),
}


# ---------------------------------------------------------------------------------------------------------------------
_group("Memory graphs (one private graph per tenant, persona and visitor: mem__<tenant>__<persona>__<visitor>)")

MEM_USER = _q("MEM_USER", "Create or touch the owner of a private memory graph.", """
    MERGE (u:User {id: $uid})
    ON CREATE SET u.created = $now, u.tenant = $tenant, u.persona = $persona, u.person_id = $pid
    SET u.last_seen = $now
    RETURN u.id
    """)

MEM_SESSION = _q("MEM_SESSION", "Start or resume a conversation session (episodic memory).", """
    MATCH (u:User {id: $uid})
    MERGE (s:Session {id: $sid})
    ON CREATE SET s.started = $now, s.turns = 0, s.agent = $agent
    SET s.updated = $now
    MERGE (u)-[:HAD_SESSION]->(s)
    RETURN s.id, s.turns, s.consolidated_n
    """)

MEM_ADD_TURN = _q("MEM_ADD_TURN", "Append one turn (user message or agent answer) to a session.", """
    MATCH (s:Session {id: $sid})
    CREATE (t:Turn {id: $id, n: $n, ts: $ts, role: $role, text: $text})
    SET t += $props
    CREATE (s)-[:HAS_TURN]->(t)
    SET s.turns = $n, s.updated = $ts
    RETURN t.id
    """)

MEM_LINK_TURN = _q("MEM_LINK_TURN", "Chain consecutive turns of a session (previous turn by its number).", """
    MATCH (s:Session {id: $sid})-[:HAS_TURN]->(a:Turn {n: $prev})
    MATCH (s)-[:HAS_TURN]->(b:Turn {id: $id})
    MERGE (a)-[:NEXT]->(b)
    """)

MEM_SESSION_INFO = _q("MEM_SESSION_INFO", "One session's header (Librarian watermark: consolidated_n).", """
    MATCH (s:Session {id: $sid})
    RETURN s.id, s.title, s.summary, s.started, s.updated, s.turns, s.agent, s.consolidated_n
    """)

MEM_TURN_REFS = _q("MEM_TURN_REFS", """
    Record which company entities an answer used (USED_EVIDENCE to EntityRef stubs carrying only the stable entity
    id), so a later session can answer 'why did you tell me X?' by re-opening the same evidence.
    """, """
    MATCH (t:Turn {id: $tid})
    UNWIND $refs AS r
    MERGE (e:EntityRef {id: r.id})
    ON CREATE SET e.label = r.label, e.name = r.name
    MERGE (t)-[u:USED_EVIDENCE]->(e)
    SET u.cited = r.cited
    """)

MEM_SET_EMBEDDINGS: dict[str, str] = {
    label: _q(f"MEM_SET_EMBEDDINGS[{label}]", f"Attach voyage-4 vectors to {label} nodes.", f"""
        UNWIND $rows AS r
        MATCH (n:{label} {{id: r.id}})
        SET n.embedding = vecf32(r.v)
        """)
    for label in ("Turn", "MemoryFact")
}

MEM_SESSIONS = _q("MEM_SESSIONS", "A user's sessions, most recent first.", """
    MATCH (:User {id: $uid})-[:HAD_SESSION]->(s:Session)
    RETURN s.id, s.title, s.summary, s.started, s.updated, s.turns, s.agent, s.consolidated_n
    ORDER BY s.updated DESC
    LIMIT $limit
    """)

MEM_TURNS = _q("MEM_TURNS", "Turns of one session after turn number $after, with the entities each answer used.", """
    MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn)
    WHERE t.n > $after
    OPTIONAL MATCH (t)-[:USED_EVIDENCE]->(e:EntityRef)
    WITH t, collect(e.id) AS refs
    RETURN t.id AS id, t.n AS n, t.role AS role, t.agent AS agent, t.text AS text, t.ts AS ts, t.tools AS tools,
           t.cost_usd AS cost, refs
    ORDER BY n
    """)

MEM_FACTS = _q("MEM_FACTS", "Semantic memories (current unless $all), newest first, with the entities they are about.", """
    MATCH (f:MemoryFact)
    WHERE $all OR f.valid_to IS NULL
    OPTIONAL MATCH (f)-[:ABOUT]->(e:EntityRef)
    WITH f, collect(e.id) AS about
    RETURN f.id AS id, f.kind AS kind, f.text AS text, f.created AS created, f.valid_to AS valid_to,
           f.source_session AS session, f.by AS by, f.superseded_by AS superseded_by, about
    ORDER BY created DESC
    LIMIT $limit
    """)

MEM_FACT_SEARCH = _q("MEM_FACT_SEARCH", "Vector recall over current memories (score is cosine distance).", """
    CALL db.idx.vector.queryNodes('MemoryFact', 'embedding', $k, vecf32($v)) YIELD node, score
    WHERE node.valid_to IS NULL
    RETURN node.id, node.kind, node.text, node.created, node.source_session, score
    ORDER BY score ASC
    """)

MEM_TURN_SEARCH = _q("MEM_TURN_SEARCH", "Vector recall over past conversation turns, excluding the current session.", """
    CALL db.idx.vector.queryNodes('Turn', 'embedding', $k, vecf32($v)) YIELD node, score
    MATCH (s:Session)-[:HAS_TURN]->(node)
    WHERE s.id <> $exclude
    RETURN node.id, node.role, node.agent, node.text, node.ts, s.id, s.title, score
    ORDER BY score ASC
    """)

MEM_ADD_FACT = _q("MEM_ADD_FACT", "Store one semantic memory owned by the user.", """
    MATCH (u:User {id: $uid})
    CREATE (f:MemoryFact {id: $id, kind: $kind, text: $text, created: $now, by: $by})
    SET f += $props
    CREATE (u)-[:REMEMBERS]->(f)
    RETURN f.id
    """)

MEM_FACT_SOURCE = _q("MEM_FACT_SOURCE", "Provenance: the session a memory was learned from.", """
    MATCH (f:MemoryFact {id: $id})
    MATCH (s:Session {id: $sid})
    MERGE (f)-[:LEARNED_FROM]->(s)
    """)

MEM_FACT_ABOUT = _q("MEM_FACT_ABOUT", "Link a memory to the company entities it is about (by stable id).", """
    MATCH (f:MemoryFact {id: $id})
    UNWIND $refs AS r
    MERGE (e:EntityRef {id: r.id})
    ON CREATE SET e.label = r.label, e.name = r.name
    MERGE (f)-[:ABOUT]->(e)
    """)

MEM_SUPERSEDE = _q("MEM_SUPERSEDE", """
    A newer memory replaces an older one: the old one is closed (valid_to) and linked by SUPERSEDES, never deleted,
    so the memory graph keeps its own history.
    """, """
    MATCH (n:MemoryFact {id: $new})
    MATCH (o:MemoryFact {id: $old})
    WHERE o.valid_to IS NULL AND o.id <> n.id
    SET o.valid_to = $now, o.superseded_by = $new
    MERGE (n)-[:SUPERSEDES]->(o)
    RETURN o.id
    """)

MEM_RETIRE = _q("MEM_RETIRE", "Close memories that are no longer true (e.g. a resolved open thread).", """
    MATCH (f:MemoryFact)
    WHERE f.id IN $ids AND f.valid_to IS NULL
    SET f.valid_to = $now, f.retired = true
    RETURN f.id
    """)

MEM_CAP = _q("MEM_CAP", "Keep at most $keep current memories: the oldest beyond the cap are retired.", """
    MATCH (f:MemoryFact)
    WHERE f.valid_to IS NULL
    WITH f ORDER BY f.created DESC
    SKIP $keep
    SET f.valid_to = $now, f.retired = true
    RETURN count(f)
    """)

MEM_FORGET_FACT = _q("MEM_FORGET_FACT", "Forget one memory (hard delete, user-initiated).", """
    MATCH (f:MemoryFact {id: $id})
    WITH f, f.id AS id
    DETACH DELETE f
    RETURN id
    """)

MEM_DELETE_TURNS = _q("MEM_DELETE_TURNS", "Forget a session, step 1: its turns.", """
    MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn)
    DETACH DELETE t
    """)

MEM_DELETE_SESSION = _q("MEM_DELETE_SESSION", "Forget a session, step 2: the session node.", """
    MATCH (s:Session {id: $sid})
    WITH s, s.id AS id
    DETACH DELETE s
    RETURN id
    """)

MEM_SET_SESSION = _q("MEM_SET_SESSION", "Librarian output on a session: title, summary, consolidation watermark.", """
    MATCH (s:Session {id: $sid})
    SET s += $props
    RETURN s.id
    """)

MEM_COUNTS = _q("MEM_COUNTS", "Node counts per label in a memory graph (Memory Inspector header).", """
    MATCH (n)
    RETURN labels(n)[0] AS label, count(n) AS n
    """)


# ---------------------------------------------------------------------------------------------------------------------
_group("Procedural memory (playbooks in the shared tenant graph)")

PLAYBOOK_SEARCH = _q("PLAYBOOK_SEARCH", """
    Find learned procedures similar to a task description (vector index on Playbook.embedding; score is cosine
    distance), with their ordered steps.
    """, """
    CALL db.idx.vector.queryNodes('Playbook', 'embedding', $k, vecf32($v)) YIELD node, score
    OPTIONAL MATCH (node)-[:HAS_STEP]->(s:Step)
    WITH node, score, s ORDER BY s.n
    WITH node, score, collect([s.n, s.tool, s.instruction]) AS steps
    RETURN node.id AS id, node.name AS name, node.trigger AS trigger, node.description AS description,
           node.uses AS uses, node.created AS created, node.updated AS updated, node.learned_by AS learned_by,
           score, steps
    ORDER BY score ASC
    """)

PLAYBOOK_LIST = _q("PLAYBOOK_LIST", "All playbooks with their steps, most recently updated first.", """
    MATCH (p:Playbook)
    OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step)
    WITH p, s ORDER BY s.n
    WITH p, collect([s.n, s.tool, s.instruction]) AS steps
    RETURN p.id AS id, p.name AS name, p.trigger AS trigger, p.description AS description, p.uses AS uses,
           p.created AS created, p.updated AS updated, p.learned_by AS learned_by, p.source AS source, steps
    ORDER BY updated DESC
    LIMIT $limit
    """)

PLAYBOOK_UPSERT = _q("PLAYBOOK_UPSERT", "Create or update a playbook header.", """
    MERGE (p:Playbook {id: $id})
    ON CREATE SET p.created = $now, p.uses = 0
    SET p += $props, p.updated = $now
    RETURN p.id
    """)

PLAYBOOK_CLEAR_STEPS = _q("PLAYBOOK_CLEAR_STEPS", "Drop a playbook's steps before rewriting them.", """
    MATCH (:Playbook {id: $id})-[:HAS_STEP]->(s:Step)
    DETACH DELETE s
    """)

PLAYBOOK_ADD_STEPS = _q("PLAYBOOK_ADD_STEPS", "Write a playbook's ordered steps (HAS_STEP from the playbook).", """
    MATCH (p:Playbook {id: $id})
    UNWIND $steps AS st
    CREATE (s:Step {id: st.id, n: st.n, tool: st.tool, instruction: st.instruction, playbook_id: $id})
    CREATE (p)-[:HAS_STEP {n: st.n}]->(s)
    """)

PLAYBOOK_LINK_STEPS = _q("PLAYBOOK_LINK_STEPS", "Chain steps in order with NEXT_STEP.", """
    UNWIND $pairs AS r
    MATCH (a:Step {id: r.a})
    MATCH (b:Step {id: r.b})
    MERGE (a)-[:NEXT_STEP]->(b)
    """)

PLAYBOOK_SET_VEC = _q("PLAYBOOK_SET_VEC", "Embed a playbook (name + trigger + description + tool sequence).", """
    MATCH (p:Playbook {id: $id})
    SET p.embedding = vecf32($v)
    """)

PLAYBOOK_REINFORCE = _q("PLAYBOOK_REINFORCE", "A session re-confirmed a playbook: count the use.", """
    MATCH (p:Playbook {id: $id})
    SET p.uses = coalesce(p.uses, 0) + 1, p.last_used = $now, p.updated = $now
    RETURN p.uses
    """)

CASE_FOLLOWS_PLAYBOOK = _q("CASE_FOLLOWS_PLAYBOOK", "Record that a case was worked by following a playbook.", """
    MATCH (c:Case {id: $case})
    MATCH (p:Playbook {id: $id})
    MERGE (c)-[e:FOLLOWS_PLAYBOOK]->(p)
    SET e.ts = $now
    """)


# ---------------------------------------------------------------------------------------------------------------------
_group("Live feed and Sentinel")

LIVE_CURSOR = _q("LIVE_CURSOR", "Last live-feed day replayed into this graph (stored on :Meta).", """
    MATCH (m:Meta)
    RETURN m.live_cursor, m.live_batches, m.live_updated
    LIMIT 1
    """)

SET_LIVE_CURSOR = _q("SET_LIVE_CURSOR", "Advance the live-feed cursor after a day's batch is ingested.", """
    MATCH (m:Meta)
    SET m.live_cursor = $day, m.live_batches = coalesce(m.live_batches, 0) + 1, m.live_updated = $now
    RETURN m.live_cursor
    """)

SENTINEL_TXNS = _q("SENTINEL_TXNS", """
    Sentinel, detector 1: transactions in the new batch that carry a monitoring alert or a fraud score at or above
    $min_score, with both accounts (and whether either is already frozen) and the counterparty.
    """, """
    MATCH (t:Transaction)
    WHERE t.id IN $ids AND (t.alert IS NOT NULL OR t.fraud_score >= $min_score)
    OPTIONAL MATCH (t)-[:FROM_ACCOUNT]->(fa:Account)
    OPTIONAL MATCH (t)-[:TO_ACCOUNT]->(ta:Account)
    OPTIONAL MATCH (t)-[:COUNTERPARTY]->(cp:Counterparty)
    RETURN t.id, t.kind, t.amount, t.ts, t.alert, t.fraud_score, fa.id, fa.status, ta.id, ta.status, cp.id, cp.name,
           cp.country
    ORDER BY t.ts
    """)

SENTINEL_MNPI_DEALS = _q("SENTINEL_MNPI_DEALS", "Sentinel, detector 2: the walled-off (MNPI) deals to barrier-check.", """
    MATCH (d:Deal)
    WHERE d.sensitivity = 'MNPI'
    RETURN d.id, d.codename
    ORDER BY d.id
    """)

SENTINEL_DEPARTURE_RISK = _q("SENTINEL_DEPARTURE_RISK", """
    Sentinel, detector 3 (bus factor): systems a departing person knows well (KNOWS_ABOUT weight), with the best
    remaining active expert's weight as backup coverage.
    """, """
    MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System)
    WHERE p.id IN $ids AND k.weight >= $min_weight
    OPTIONAL MATCH (o:Person)-[k2:KNOWS_ABOUT]->(s)
    WHERE o.id <> p.id AND coalesce(o.active, true)
    WITH p, s, k.weight AS w, max(k2.weight) AS backup
    RETURN p.id AS pid, p.name AS name, s.id AS sid, s.name AS system, w, backup
    ORDER BY w DESC
    LIMIT 12
    """)

TASK_INFO = _q("TASK_INFO", "One blackboard task (who it is for, which case) so the UI can run its assigned agent.", """
    MATCH (t:Task {id: $id})
    RETURN t.id, t.status, t.to_agent, t.from_agent, t.case_id, t.note, t.title
    """)

RECENT_HANDOFFS = _q("RECENT_HANDOFFS", "Agent-to-agent handoff timeline (HANDED_OFF_TO edges), newest first.", """
    MATCH (a:Agent)-[h:HANDED_OFF_TO]->(b:Agent)
    RETURN a.id, b.id, h.ts, h.note, h.task_id, h.case_id
    ORDER BY h.ts DESC
    LIMIT $limit
    """)

# ------------------------------------------------------------------------------------------------ redaction view
DOC_REDACTION = _q("DOC_REDACTION", "Redaction audit event of one document: PII counts per type (values are never stored).", """
    MATCH (:Document {id: $id})-[:HAD_REDACTION]->(r:RedactionEvent)
    RETURN r.counts, r.total
    """)

REDACTION_TOTALS = _q("REDACTION_TOTALS", "Every redaction audit event in the tenant graph; the caller sums the per-type counts.", """
    MATCH (r:RedactionEvent)
    RETURN r.counts, r.total
    """)

RECENT_REDACTIONS = _q("RECENT_REDACTIONS", "Newest documents the person may read that had PII redacted, with their counts.", f"""
    MATCH (p:Person {{id: $pid}})
    MATCH (d:Document)-[:HAD_REDACTION]->(r:RedactionEvent)
    WHERE {ACL_DOC}
    RETURN d.id, d.kind, d.ts, d.title, r.counts, r.total
    ORDER BY d.ts DESC
    LIMIT $limit
    """)

TOKEN_TYPE_COUNTS = _q("TOKEN_TYPE_COUNTS", "Pseudonymous PII tokens per type, and how many are shared by more than one owner.", """
    MATCH (k:PiiToken)
    OPTIONAL MATCH (o)-[:HAS_IDENTIFIER]->(k)
    WITH k, count(o) AS owners
    RETURN k.type, count(k) AS tokens, sum(CASE WHEN owners > 1 THEN 1 ELSE 0 END) AS shared
    ORDER BY tokens DESC
    """)


def to_markdown() -> str:
    out = ["# Cypher reference", "",
           "Generated from `backend/graph/queries.py` (`python -m backend.graph.queries --write`). "
           "Every query FinanceBrain runs is listed here; per-label variants are shown once with one example label.",
           ""]
    seen_templates: set[str] = set()
    groups: dict[str, list[Query]] = {}
    for q in REGISTRY.values():
        groups.setdefault(q.group, []).append(q)
    for group, qs in groups.items():
        out += [f"## {group}", ""]
        for q in qs:
            base = q.name.split("[")[0]
            if "[" in q.name:
                if base in seen_templates:
                    continue
                seen_templates.add(base)
                variants = [x.name[len(base) + 1:-1] for x in qs if x.name.startswith(base + "[")]
                title = f"### `{base}` ({len(variants)} variants)"
                extra = f"Variants: {', '.join(f'`{v}`' for v in variants)}. Example (`{variants[0]}`):"
            else:
                title, extra = f"### `{q.name}`", ""
            out += [title, "", q.doc, ""] + ([extra, ""] if extra else []) + ["```cypher", q.cypher, "```", ""]
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if "--write" in sys.argv:
        target = Path(__file__).resolve().parents[2] / "docs" / "CYPHER.md"
        target.write_text(to_markdown() + "\n", encoding="utf-8")
        print(f"wrote {target} ({len(REGISTRY)} queries)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        print(to_markdown())
