"""Live feed: replay the post-cutoff part of the synthetic timeline into the brain graph one day at a time, through
the real ingestion pipeline (redacted documents -> Haiku extraction on cache miss -> entity resolution -> voyage-4
embeddings -> UNWIND/MERGE), then let the Sentinel act on the new batch.

Per day: new transactions, ownership intervals opening/closing (OWNS valid_from/valid_to), departures (Person
active=false), the day's documents, a rebuild of the derived FLOWS_TO / COMMUNICATES edges, a Sentinel scan, and
the cursor on :Meta. "Reset demo" copies the pristine snapshot back (GRAPH.COPY).

    python -m backend.ingest.live --tenant halcyon --status
    python -m backend.ingest.live --tenant halcyon --next [--days 3] [--no-llm]
    python -m backend.ingest.live --tenant halcyon --reset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from functools import lru_cache

from backend.agents import sentinel as sentinel_agent
from backend.graph import queries as Q
from backend.graph.db import brain_graph, copy_graph, get_db, graph_exists, pristine_graph
from backend.ingest.derive import derive_edges
from backend.ingest.pipeline import Batch, Ingestor, clean_props, team_id
from backend.ingest.sources import cutoff, load_redacted_docs, load_world
from backend.memory.store import now_iso

log = logging.getLogger(__name__)
_INGESTORS: dict[tuple[str, str, bool], Ingestor] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


@lru_cache(maxsize=4)
def _docs(tenant: str) -> dict[str, dict]:
    return {d["doc_id"]: d for d in load_redacted_docs(tenant)}


@lru_cache(maxsize=4)
def timeline(tenant: str) -> tuple[dict, ...]:
    """Every post-cutoff event grouped by day (documents, transactions, ownership changes, joiners, leavers)."""
    w, c = load_world(tenant), cutoff(tenant)
    days: dict[str, dict] = defaultdict(lambda: {"docs": [], "txns": [], "ownership": [], "joiners": [],
                                                 "leavers": []})
    for d in _docs(tenant).values():
        if d.get("live_feed") or d["ts"][:10] >= c:
            days[d["ts"][:10]]["docs"].append(d["doc_id"])
    for t in w["transactions"]:
        if t.get("live_feed") or t["ts"][:10] >= c:
            days[t["ts"][:10]]["txns"].append(t["id"])
    for o in w["ownership"]:
        if o.get("valid_to") and o["valid_to"] >= c:
            days[o["valid_to"]]["ownership"].append({"event": "end", **o})
        if o["valid_from"] >= c:
            days[o["valid_from"]]["ownership"].append({"event": "start", **o})
    for p in w["people"]:
        if p.get("start_date") and p["start_date"] >= c:
            days[p["start_date"]]["joiners"].append(p["id"])
        if p.get("end_date") and p["end_date"] >= c:
            days[p["end_date"]]["leavers"].append(p["id"])
    return tuple({"day": d, **days[d]} for d in sorted(days))


def get_ingestor(tenant: str, graph: str | None = None, offline: bool = False) -> Ingestor:
    """One cached Ingestor per (tenant, graph): its topic index and resolver state carry across batches."""
    key = (tenant, graph or brain_graph(tenant), offline)
    if key not in _INGESTORS:
        _INGESTORS[key] = Ingestor(tenant, graph=key[1], offline=offline)
    return _INGESTORS[key]


def drop_ingestors(tenant: str | None = None, graph: str | None = None) -> None:
    """Forget cached ingestors (after a graph reset their topic index would be stale)."""
    for k in list(_INGESTORS):
        if (tenant is None or k[0] == tenant) and (graph is None or k[1] == graph):
            del _INGESTORS[k]


def _lock(tenant: str, graph: str | None) -> asyncio.Lock:
    return _LOCKS.setdefault(f"{tenant}:{graph or brain_graph(tenant)}", asyncio.Lock())


async def cursor(tenant: str, graph: str | None = None) -> str | None:
    res = await get_db().select_graph(graph or brain_graph(tenant)).ro_query(Q.LIVE_CURSOR)
    return res.result_set[0][0] if res.result_set else None


async def status(tenant: str, graph: str | None = None) -> dict:
    cur = await cursor(tenant, graph)
    days = [{"day": d["day"], "docs": len(d["docs"]), "txns": len(d["txns"]), "ownership": len(d["ownership"]),
             "joiners": len(d["joiners"]), "leavers": len(d["leavers"]), "done": bool(cur and d["day"] <= cur)}
            for d in timeline(tenant)]
    pending = [d for d in days if not d["done"]]
    return {"tenant": tenant, "graph": graph or brain_graph(tenant), "cutoff": cutoff(tenant), "cursor": cur,
            "next_day": pending[0]["day"] if pending else None, "remaining_days": len(pending),
            "remaining_docs": sum(d["docs"] for d in pending), "remaining_txns": sum(d["txns"] for d in pending),
            "days": days}


def _ownership_writes(b: Batch, day: str, events: list[dict]) -> list[dict]:
    for o in events:
        if o["event"] == "end":
            props = {"valid_from": o["valid_from"], "valid_to": o["valid_to"], "current": False}
        else:
            vt = o.get("valid_to") if o.get("valid_to") and o["valid_to"] <= day else None
            props = clean_props({"valid_from": o["valid_from"], "valid_to": vt, "current": vt is None})
        for label, src in (("Team", o.get("team_id")), ("Person", o.get("person_id"))):
            if src:
                b.owns[label].append({"src": src, "dst": o["system_id"], "valid_from": o["valid_from"], "props": props})
    changes = []
    for s in (o for o in events if o["event"] == "start"):
        prev = next((o for o in events if o["event"] == "end" and o["system_id"] == s["system_id"]), None)
        changes.append({k: v for k, v in {
            "system": s["system_id"], "from_team": prev and prev.get("team_id"),
            "from_person": prev and prev.get("person_id"), "to_team": s.get("team_id"),
            "to_person": s.get("person_id"), "valid_from": s["valid_from"]}.items() if v})
    return changes


async def replay_day(tenant: str, day: str, *, graph: str | None = None, offline: bool = False,
                     sentinel: bool = True, use_llm: bool = True) -> dict:
    t0 = time.perf_counter()
    entry = next((d for d in timeline(tenant) if d["day"] == day), None)
    if entry is None:
        raise ValueError(f"no live-feed events on {day} for {tenant}")
    ing = get_ingestor(tenant, graph, offline)
    stats0, ex0 = Counter(ing.stats), Counter(ing.extractor.stats)
    w, b = ing.world, Batch()
    txn_ids = set(entry["txns"])
    for t in w["transactions"]:
        if t["id"] in txn_ids:
            ing._transaction(b, t)
    for p in w["people"]:
        if p["id"] in entry["joiners"]:
            b.node("Person", p["id"], name=p["name"], email=p["email"], title=p["title"], team_id=team_id(p["team_id"]),
                   location=p.get("location"), start_date=p.get("start_date"), role=p.get("role"), active=True)
            b.edge("Person", "MEMBER_OF", "Team", p["id"], team_id(p["team_id"]))
            b.edge("Person", "REPORTS_TO", "Person", p["id"], p.get("manager_id"))
    for pid in entry["leavers"]:
        b.node("Person", pid, end_date=day, active=False)
    changes = _ownership_writes(b, day, entry["ownership"])
    await ing.write(b)
    docs = sorted((_docs(tenant)[i] for i in entry["docs"]), key=lambda d: d["ts"])
    await ing.ingest_docs(docs)
    derived = await derive_edges(ing.graph_name)
    found = None
    if sentinel:
        found = await sentinel_agent.scan(tenant, day=day, txn_ids=sorted(txn_ids), doc_ids=[d["doc_id"] for d in docs],
                                          leavers=entry["leavers"], ownership=changes, graph=ing.graph_name,
                                          use_llm=use_llm)
    await ing.g.query(Q.SET_LIVE_CURSOR, {"day": day, "now": now_iso()})
    stats, ex = Counter(ing.stats), Counter(ing.extractor.stats)
    stats.subtract(stats0)
    ex.subtract(ex0)
    return {"day": day, "docs": len(docs), "txns": len(txn_ids), "joiners": entry["joiners"],
            "leavers": entry["leavers"], "ownership_changes": changes,
            "ingest": {k: v for k, v in stats.items() if v}, "extraction": {k: v for k, v in ex.items() if v},
            "derived": derived, "sentinel": found, "ms": round((time.perf_counter() - t0) * 1000)}


async def advance(tenant: str, days: int = 1, *, graph: str | None = None, offline: bool = False,
                  sentinel: bool = True, use_llm: bool = True) -> dict:
    """Replay the next `days` pending days (serialised per graph)."""
    async with _lock(tenant, graph):
        st = await status(tenant, graph)
        todo = [d["day"] for d in st["days"] if not d["done"]][:max(1, min(int(days or 1), 31))]
        out = []
        for day in todo:
            out.append(await replay_day(tenant, day, graph=graph, offline=offline, sentinel=sentinel,
                                        use_llm=use_llm))
        return {"replayed": out, "status": await status(tenant, graph)}


async def reset_tenant(tenant: str) -> dict:
    """Reset demo: copy pristine__<tenant> over brain__<tenant> (data and indexes) and drop cached ingestors."""
    src, dst = pristine_graph(tenant), brain_graph(tenant)
    if not await graph_exists(src):
        raise RuntimeError(f"{src} does not exist; run the seed with --pristine first")
    async with _lock(tenant, None):
        t0 = time.perf_counter()
        await copy_graph(src, dst)
        drop_ingestors(tenant, dst)
    return {"tenant": tenant, "graph": dst, "restored_from": src, "ms": round((time.perf_counter() - t0) * 1000)}


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", default="halcyon")
    ap.add_argument("--graph", help="target graph (default brain__<tenant>)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--next", action="store_true", help="replay the next pending day(s)")
    g.add_argument("--reset", action="store_true", help="restore the brain graph from its pristine snapshot")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--offline", action="store_true", help="extraction cache only (no live Haiku calls)")
    ap.add_argument("--no-llm", action="store_true", help="Sentinel uses template write-ups")
    ap.add_argument("--no-sentinel", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.status:
        out = await status(args.tenant, args.graph)
    elif args.reset:
        out = await reset_tenant(args.tenant)
    else:
        out = await advance(args.tenant, args.days, graph=args.graph, offline=args.offline,
                            sentinel=not args.no_sentinel, use_llm=not args.no_llm)
        for r in out["replayed"]:
            s = r.get("sentinel") or {}
            print(f"{r['day']}: {r['docs']} docs, {r['txns']} txns, ownership {r['ownership_changes']}, leavers "
                  f"{r['leavers']}, extraction {r['extraction']}, {r['ms']} ms")
            for c in s.get("cases") or []:
                print(f"   case {c.get('case_id')} [{c.get('kind')}/{c.get('severity')}] {c.get('title')} "
                      f"handoff={c.get('handoff')} {c.get('error', '')}")
            for a in s.get("alerts") or []:
                what = (f"{a.get('person')} {a.get('name')}: {a.get('systems')}" if a.get("person") else
                        f"{a.get('deal')} {a.get('document')} ({a.get('outsider_count')} outsiders)")
                print(f"   alert {a['kind']}/{a.get('severity')} {what}")
        out = out["status"]
    print(json.dumps({k: v for k, v in out.items() if k != "days"}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
