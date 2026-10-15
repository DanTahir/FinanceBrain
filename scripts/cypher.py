"""Run ad-hoc Cypher against local FalkorDB (dev helper).

    python scripts/cypher.py brain__halcyon "MATCH (n:System) RETURN n.id LIMIT 5" ['{"param": 1}'] [--rw]

Read-only (GRAPH.RO_QUERY) unless --rw is given.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from backend.graph.db import get_db


async def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--rw"]
    rw = "--rw" in sys.argv
    graph, cypher = args[0], args[1]
    params = json.loads(args[2]) if len(args) > 2 else {}
    g = get_db().select_graph(graph)
    t = time.perf_counter()
    res = await (g.query if rw else g.ro_query)(cypher, params)
    ms = (time.perf_counter() - t) * 1000
    if res.header:
        print(" | ".join(str(h[1]) for h in res.header))
    for row in res.result_set[:200]:
        print(" | ".join(json.dumps(v, default=str, ensure_ascii=False)[:160] for v in row))
    print(f"-- {len(res.result_set)} rows, {ms:.1f} ms")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    asyncio.run(main())
