"""FalkorDB connection pool and graph naming.

Graph topology (FalkorDB multi-graph):
  brain__<tenant>                  company knowledge, PII tokens, ACLs, cases, tasks, blackboard, :Meta
  pristine__<tenant>               seeded snapshot used by "Reset demo" (GRAPH.COPY)
  mem__<tenant>__<user>            private per-user (per-visitor) memory graph
"""

from __future__ import annotations

import asyncio
import re

from falkordb.asyncio import FalkorDB
from redis.asyncio import BlockingConnectionPool
from redis.exceptions import ResponseError

from backend.config import get_settings

_SAFE = re.compile(r"[^a-z0-9_]+")


def brain_graph(tenant: str) -> str:
    return f"brain__{tenant}"


def pristine_graph(tenant: str) -> str:
    return f"pristine__{tenant}"


def memory_graph(tenant: str, user: str) -> str:
    return f"mem__{tenant}__{_SAFE.sub('_', user.lower()).strip('_')}"


_db: FalkorDB | None = None


def get_db() -> FalkorDB:
    """Process-wide async client over a bounded connection pool (verified in the Phase 0 spike)."""
    global _db
    if _db is None:
        s = get_settings()
        pool = BlockingConnectionPool(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password,
                                      max_connections=32, timeout=30, decode_responses=True)
        _db = FalkorDB(connection_pool=pool)
    return _db


async def execute(*args):
    """Raw command on the shared pool (GRAPH.COPY, GRAPH.CONSTRAINT, GRAPH.DELETE, ...)."""
    return await get_db().connection.execute_command(*args)


async def graph_exists(name: str) -> bool:
    return name in await get_db().list_graphs()


async def delete_graph(name: str) -> None:
    if await graph_exists(name):
        await execute("GRAPH.DELETE", name)


async def copy_graph(src: str, dst: str, *, wait_s: float = 30.0) -> None:
    """Replace dst with a copy of src (data and indexes).

    GRAPH.COPY forks the server, and Redis allows one fork child at a time: while an AOF auto-rewrite or BGSAVE is
    running (copying a large graph is itself enough to trigger the AOF rewrite) it fails with "could not fork".
    That is transient, so retry with backoff for up to ``wait_s`` seconds."""
    await delete_graph(dst)
    loop = asyncio.get_running_loop()
    deadline, delay = loop.time() + wait_s, 0.2
    while True:
        try:
            await execute("GRAPH.COPY", src, dst)
            return
        except ResponseError as e:
            if "fork" not in str(e).lower() or loop.time() + delay > deadline:
                raise
        await asyncio.sleep(delay)
        delay = min(delay * 2, 2.0)
