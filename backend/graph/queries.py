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
