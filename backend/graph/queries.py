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
                 "Topic", "Decision", "PiiToken", "Document", "Fact", "Thread", "Case", "Task", "Action")
_DISPLAY = {
    "Transaction": "n.kind + ' USD ' + toString(n.amount) + ' ' + substring(n.ts, 0, 10)",
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
