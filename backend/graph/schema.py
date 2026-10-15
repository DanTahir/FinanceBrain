"""Graph schema: range indexes, unique constraints, full-text and vector indexes, and the :Meta guard.

Idempotent: re-applying to an existing graph skips indexes that already exist.
"""

from __future__ import annotations

import asyncio
import logging

from redis.exceptions import ResponseError

from backend.config import get_settings
from backend.graph import queries as Q
from backend.graph.db import execute, get_db

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# label -> properties with a range index (every id, plus the properties we filter or sort on)
BRAIN_RANGE = {
    "Person": ["id", "name", "team_id"], "Team": ["id"], "System": ["id", "name"], "Client": ["id"],
    "Account": ["id"], "Counterparty": ["id"], "Transaction": ["id", "ts"], "Deal": ["id"], "Channel": ["id"],
    "Document": ["id", "ts", "kind", "thread_id", "ticket_key"], "Thread": ["id"], "Topic": ["id"],
    "Decision": ["id"], "Fact": ["id", "subject_id", "predicate", "valid_from"], "PiiToken": ["id", "type"],
    "RedactionEvent": ["id"], "Agent": ["id"], "Task": ["id", "status"], "Case": ["id", "status"],
    "Action": ["id", "status"], "Playbook": ["id"], "Step": ["id"], "Meta": ["id"],
}
BRAIN_EDGE_RANGE = {"OWNS": ["valid_from"], "KNOWS_ABOUT": ["weight"]}
BRAIN_FULLTEXT = {"Document": ["title", "text"], "Person": ["name", "title"], "System": ["name", "description"],
                  "Topic": ["name"], "Deal": ["codename", "target"], "Fact": ["statement"]}
BRAIN_VECTOR = {"Document": "embedding", "Fact": "embedding", "Topic": "embedding"}

MEMORY_RANGE = {"User": ["id"], "Session": ["id", "started"], "Turn": ["id", "ts"], "MemoryFact": ["id"],
                "EntityRef": ["id"], "Preference": ["id"], "Meta": ["id"]}
MEMORY_FULLTEXT = {"MemoryFact": ["text"], "Turn": ["text"]}
MEMORY_VECTOR = {"MemoryFact": "embedding", "Turn": "embedding"}


async def _try(g, cypher: str) -> bool:
    try:
        await g.query(cypher)
        return True
    except ResponseError as e:
        msg = str(e).lower()
        if "already" in msg or "exists" in msg:
            return False
        raise


async def apply_schema(graph: str, kind: str = "brain") -> dict:
    """Create indexes and constraints; returns counts of newly created objects."""
    g = get_db().select_graph(graph)
    rng, ft, vec = ((BRAIN_RANGE, BRAIN_FULLTEXT, BRAIN_VECTOR) if kind == "brain"
                    else (MEMORY_RANGE, MEMORY_FULLTEXT, MEMORY_VECTOR))
    dims = get_settings().embed_dims
    made = {"range": 0, "fulltext": 0, "vector": 0, "unique": 0}
    for label, props in rng.items():
        for p in props:
            made["range"] += await _try(g, f"CREATE INDEX FOR (n:{label}) ON (n.{p})")
    if kind == "brain":
        for rel, props in BRAIN_EDGE_RANGE.items():
            for p in props:
                made["range"] += await _try(g, f"CREATE INDEX FOR ()-[e:{rel}]-() ON (e.{p})")
    for label, props in ft.items():
        args = ", ".join(f"'{p}'" for p in props)
        made["fulltext"] += await _try(g, f"CALL db.idx.fulltext.createNodeIndex('{label}', {args})")
    for label, prop in vec.items():
        made["vector"] += await _try(g, f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.{prop}) "
                                        f"OPTIONS {{dimension: {dims}, similarityFunction: 'cosine'}}")
    for label in rng:
        try:
            await execute("GRAPH.CONSTRAINT", "CREATE", graph, "UNIQUE", "NODE", label, "PROPERTIES", "1", "id")
            made["unique"] += 1
        except ResponseError as e:
            if "already" not in str(e).lower() and "exists" not in str(e).lower():
                raise
    return made


async def ensure_meta(graph: str, tenant: str, kind: str = "brain") -> dict:
    """Record (or verify) the embedding model on the :Meta node. Mixing vector spaces is refused."""
    s = get_settings()
    g = get_db().select_graph(graph)
    res = await g.query(Q.GET_META)
    if res.result_set:
        model, dims = res.result_set[0][0], res.result_set[0][1]
        if model and (model != s.embed_model or int(dims) != s.embed_dims):
            raise RuntimeError(f"{graph} holds {model}/{dims}-dim vectors but config is {s.embed_model}/"
                               f"{s.embed_dims}; re-embed (seed --drop) instead of mixing vector spaces")
    await g.query(Q.SET_META, {"props": {"embed_model": s.embed_model, "embed_dims": s.embed_dims,
                                         "schema_version": SCHEMA_VERSION, "tenant": tenant, "kind": kind}})
    return {"embed_model": s.embed_model, "embed_dims": s.embed_dims, "schema_version": SCHEMA_VERSION}


async def wait_constraints(graph: str, timeout: float = 30.0) -> None:
    """Unique constraints start PENDING and build asynchronously."""
    g = get_db().select_graph(graph)
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        res = await g.query("CALL db.constraints() YIELD status RETURN collect(status)")
        statuses = res.result_set[0][0] if res.result_set else []
        if all(s == "OPERATIONAL" for s in statuses):
            return
        await asyncio.sleep(0.25)
    log.warning("constraints on %s not all OPERATIONAL after %.0fs", graph, timeout)
