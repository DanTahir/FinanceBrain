"""Seed brain__<tenant> from the redacted corpus and the committed extraction / embedding caches.

    python -m backend.ingest.seed --tenant halcyon --tenant brightwater --drop [--offline] [--limit N]

Steps: schema + :Meta guard -> structured records as of the live-feed cutoff -> pre-cutoff documents in
chronological chunks through the shared ingestion pipeline -> label / relationship counts -> canary scan of every
string property in the graph (must be 0 hits) -> GRAPH.COPY to pristine__<tenant> for "Reset demo".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from backend.config import REPO_ROOT
from backend.graph import queries as Q
from backend.graph.db import brain_graph, copy_graph, delete_graph, get_db, pristine_graph
from backend.graph.schema import apply_schema, ensure_meta, wait_constraints
from backend.ingest.pipeline import Ingestor
from backend.ingest.sources import load_redacted_docs, load_world
from backend.llm.client import COSTS
from backend.redaction.canary import CanaryScanner, scan_graphs

log = logging.getLogger("seed")


async def seed(tenant: str, *, drop: bool = False, offline: bool = False, limit: int = 0, chunk: int = 300,
               pristine: bool = True) -> dict:
    gname = brain_graph(tenant)
    if drop:
        await delete_graph(gname)
    made = await apply_schema(gname)
    meta = await ensure_meta(gname, tenant)
    await wait_constraints(gname)

    ing = Ingestor(tenant, offline=offline)
    t0 = time.perf_counter()
    await ing.seed_world()
    t_world = time.perf_counter() - t0
    docs = [d for d in load_redacted_docs(tenant) if not d.get("live_feed") and d["ts"][:10] < ing.cutoff]
    if limit:
        docs = docs[:limit]
    for i in range(0, len(docs), chunk):
        await ing.ingest_docs(docs[i : i + chunk])
        log.info("%s: %d/%d docs, %.0fs, $%.3f", tenant, min(i + chunk, len(docs)), len(docs),
                 time.perf_counter() - t0, COSTS.total_usd())
    t_total = time.perf_counter() - t0

    g = get_db().select_graph(gname)
    labels = {r[0]: r[1] for r in (await g.query(Q.LABEL_COUNTS)).result_set}
    rels = {r[0]: r[1] for r in (await g.query(Q.REL_COUNTS)).result_set}
    w = load_world(tenant)
    scanner = CanaryScanner([w["domain"]], [c["name"] for c in w["clients"]])
    canary = await scan_graphs(get_db(), scanner, [gname])
    if pristine:
        await copy_graph(gname, pristine_graph(tenant))

    report = {
        "tenant": tenant, "graph": gname, "cutoff": ing.cutoff, "docs_ingested": len(docs), "schema_created": made,
        "meta": meta, "seconds": {"world": round(t_world, 1), "total": round(t_total, 1)},
        "extraction": dict(ing.extractor.stats), "pipeline": dict(sorted(ing.stats.items())),
        "labels": labels, "relationships": rels,
        "canary": {"strings_scanned": canary.strings_scanned, "hits": sum(canary.hits.values()),
                   "by_type": dict(canary.hits), "examples": canary.examples[:10]},
        "pristine_copy": pristine_graph(tenant) if pristine else None,
    }
    if not limit:
        out = REPO_ROOT / "data" / "extracted" / tenant / "seed_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", action="append", required=True)
    ap.add_argument("--drop", action="store_true", help="delete the brain graph first")
    ap.add_argument("--offline", action="store_true", help="never call the LLM for extraction (cache only)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pre-cutoff documents (smoke test)")
    ap.add_argument("--chunk", type=int, default=300)
    ap.add_argument("--no-pristine", action="store_true")
    args = ap.parse_args()
    for tenant in args.tenant:
        rep = await seed(tenant, drop=args.drop, offline=args.offline, limit=args.limit, chunk=args.chunk,
                         pristine=not args.no_pristine)
        print(json.dumps({k: rep[k] for k in ("tenant", "docs_ingested", "seconds", "extraction", "labels",
                                              "relationships", "canary")}, indent=1, ensure_ascii=False))
    print(f"total LLM/embedding cost this run: ${COSTS.total_usd():.3f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpx2", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    asyncio.run(_main())
