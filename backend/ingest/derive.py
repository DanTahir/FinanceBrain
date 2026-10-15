"""Derived analytics edges: FLOWS_TO (money movement) and COMMUNICATES (who talks to whom).

A full, idempotent recompute (drop, then rebuild with a handful of aggregate queries; about a second per graph). Run
after the seed and after each live-feed batch:

    python -m backend.ingest.derive --tenant halcyon [--pristine]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from backend.graph import queries as Q
from backend.graph.db import brain_graph, copy_graph, get_db, pristine_graph


async def derive_edges(graph_name: str) -> dict:
    g = get_db().select_graph(graph_name)
    t0 = time.perf_counter()
    for rel in ("FLOWS_TO", "COMMUNICATES"):
        await g.query(Q.DERIVE_CLEAR[rel], timeout=60_000)
    for key in ("internal", "inbound", "outbound"):
        await g.query(Q.DERIVE_FLOWS[key], timeout=60_000)
    for key in ("direct", "replies", "weight"):
        await g.query(Q.DERIVE_COMMUNICATES[key], timeout=60_000)
    rels = {r[0]: r[1] for r in (await g.ro_query(Q.REL_COUNTS)).result_set}
    return {"graph": graph_name, "FLOWS_TO": rels.get("FLOWS_TO", 0), "COMMUNICATES": rels.get("COMMUNICATES", 0),
            "ms": round((time.perf_counter() - t0) * 1000)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--pristine", action="store_true", help="refresh pristine__<tenant> from the result")
    args = ap.parse_args()
    out = await derive_edges(brain_graph(args.tenant))
    if args.pristine:
        await copy_graph(brain_graph(args.tenant), pristine_graph(args.tenant))
        out["pristine"] = pristine_graph(args.tenant)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
