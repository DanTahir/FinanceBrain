"""FalkorDB concurrency benchmark (Track 2: high-concurrency reads and writes, separate graphs per user/tenant).

What it measures: throughput and latency (p50/p95/p99/max, nearest-rank) of the graph operations FinanceBrain
issues, under concurrent asyncio workers sharing the process-wide connection pool from backend/graph/db.py.
No LLM or embedding API calls are made.

Setup: `pristine__<tenant>` (or `brain__<tenant>` if pristine is missing) is copied with GRAPH.COPY into the
scratch graph `test__eval_load_brain`, so reads hit realistic data and writes never touch real graphs. Every
`test__eval_*` graph this eval creates is deleted in a `finally`.

Read ops (GRAPH.RO_QUERY, round-robin): neighborhood, blast_radius, fulltext, ring_hops, vector_knn (if a stored
Document embedding is available and the vector query works). Write op: write_merge, an UNWIND/MERGE batch of
:LoadProbe nodes plus a NEXT relationship between consecutive probes.

Scenarios:
  a) shared_graph    W writers + R readers on the single scratch brain copy (writes serialize on one graph)
  b) graph_per_user  W writers each on their own graph test__eval_load_mem_<i> + R readers on the brain copy
  c) read_only       R readers only (baseline)

Run:
    PYTHONPATH=. .venv/Scripts/python -m evals.load_test --seconds 20 --writers 8 --readers 16 --batch 50

Outputs evals/results/<out>.json and .md. Exit 1 if setup fails or any scenario's error rate exceeds 1%.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.config import REPO_ROOT, get_settings, mask_secrets
from backend.graph.db import brain_graph, copy_graph, delete_graph, get_db, graph_exists, pristine_graph

SCRATCH_BRAIN = "test__eval_load_brain"
MEM_PREFIX = "test__eval_load_mem_"
RING_TYPES = ["PHONE", "ADDRESS", "DEVICE_ID"]
FULLTEXT_TERMS = ["reconciliation", "pricing", "fraud", "onboarding", "Pric*"]
ERROR_RATE_GATE = 0.01

SCENARIOS = {
    "a": ("shared_graph", "W writers + R readers all on the single scratch brain copy (writes serialize on one graph)"),
    "b": ("graph_per_user", "W writers each on their own graph test__eval_load_mem_<i> + R readers on the brain copy"),
    "c": ("read_only", "R readers only on the scratch brain copy (baseline read throughput/latency)"),
}
NAME_TO_KEY = {v[0]: k for k, v in SCENARIOS.items()}

WRITE_CYPHER = """
UNWIND $rows AS r
MERGE (n:LoadProbe {id: r.id})
SET n.v = r.v, n.ts = r.ts
WITH n, r WHERE r.prev <> ''
MERGE (p:LoadProbe {id: r.prev})
MERGE (p)-[:NEXT]->(n)
"""


# ---------------------------------------------------------------- stats

def pct(sorted_vals: list[float], p: float) -> float | None:
    """Nearest-rank percentile on an already sorted list."""
    if not sorted_vals:
        return None
    k = max(1, math.ceil(p / 100.0 * len(sorted_vals)))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


class OpStats:
    def __init__(self) -> None:
        self.lat: list[float] = []
        self.errors = 0
        self.messages: list[str] = []


class Recorder:
    def __init__(self) -> None:
        self.ops: dict[str, OpStats] = {}

    def _get(self, op: str) -> OpStats:
        if op not in self.ops:
            self.ops[op] = OpStats()
        return self.ops[op]

    def ok(self, op: str, ms: float) -> None:
        self._get(op).lat.append(ms)

    def err(self, op: str, e: BaseException) -> None:
        s = self._get(op)
        s.errors += 1
        if len(s.messages) < 3:
            s.messages.append(mask_secrets(f"{type(e).__name__}: {e}")[:300])


def summarize(lats: list[float], errors: int, elapsed: float) -> dict:
    v = sorted(lats)
    r = lambda x: round(x, 2) if x is not None else None  # noqa: E731
    return {"count": len(v), "ops_per_s": round(len(v) / elapsed, 2) if elapsed > 0 else 0.0,
            "p50_ms": r(pct(v, 50)), "p95_ms": r(pct(v, 95)), "p99_ms": r(pct(v, 99)),
            "max_ms": r(v[-1] if v else None), "errors": errors}


# ---------------------------------------------------------------- setup helpers

async def _rows(g, cypher: str, params: dict | None = None) -> list[list]:
    return (await g.ro_query(cypher, params or {})).result_set


async def _exists(g, label: str, node_id: str) -> bool:
    rows = await _rows(g, f"MATCH (n:{label} {{id: $id}}) RETURN count(n)", {"id": node_id})
    return bool(rows) and rows[0][0] > 0


async def _create_probe_index(graph: str) -> None:
    g = get_db().select_graph(graph)
    try:
        await g.query("CREATE INDEX FOR (n:LoadProbe) ON (n.id)")
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "already" not in msg and "exists" not in msg:
            raise


def _ground_truth_refs(tenant: str) -> dict:
    p = REPO_ROOT / "data" / "raw" / tenant / "ground_truth.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("refs", {})
    except Exception:  # noqa: BLE001
        return {}


async def resolve_targets(tenant: str) -> dict:
    """Pick realistic ids (ground truth refs, then documented defaults, then any matching node)."""
    g = get_db().select_graph(SCRATCH_BRAIN)
    refs = _ground_truth_refs(tenant)
    notes: list[str] = []

    client = (refs.get("ring_clients") or ["c_0060"])[0]
    if not await _exists(g, "Client", client):
        rows = await _rows(g, "MATCH (c:Client)-[:HAS_IDENTIFIER]-() RETURN c.id LIMIT 1")
        rows = rows or await _rows(g, "MATCH (c:Client) RETURN c.id LIMIT 1")
        notes.append(f"client {client} not found; using {rows[0][0] if rows else None}")
        client = rows[0][0] if rows else None

    system = refs.get("pricing_system") or "sys_pricingsvc"
    if not await _exists(g, "System", system):
        rows = await _rows(g, "MATCH (:System)-[:DEPENDS_ON]->(s:System) WITH s, count(*) AS n "
                              "RETURN s.id ORDER BY n DESC LIMIT 1")
        rows = rows or await _rows(g, "MATCH (s:System) RETURN s.id LIMIT 1")
        notes.append(f"system {system} not found; using {rows[0][0] if rows else None}")
        system = rows[0][0] if rows else None

    embedding = None
    try:
        rows = await _rows(g, "MATCH (d:Document) WHERE d.embedding IS NOT NULL RETURN d.id, d.embedding LIMIT 1")
        if rows and isinstance(rows[0][1], (list, tuple)) and rows[0][1]:
            embedding = [float(x) for x in rows[0][1]]
            notes.append(f"vector_knn query vector: stored embedding of {rows[0][0]} ({len(embedding)} dims)")
        else:
            notes.append("no usable stored Document embedding; vector_knn disabled")
    except Exception as e:  # noqa: BLE001
        notes.append(f"could not fetch a stored embedding ({type(e).__name__}); vector_knn disabled")
    return {"client": client, "system": system, "embedding": embedding, "notes": notes}


def build_read_ops(t: dict) -> list[dict]:
    """Each op: name, cypher, list of param dicts (rotated round-robin)."""
    ops: list[dict] = []
    neigh = []
    if t["client"]:
        neigh.append(("MATCH (a:Client {id: $id})-[*1..2]-(m) RETURN DISTINCT m.id LIMIT 100", {"id": t["client"]}))
    if t["system"]:
        neigh.append(("MATCH (a:System {id: $id})-[*1..2]-(m) RETURN DISTINCT m.id LIMIT 100", {"id": t["system"]}))
    if neigh:
        ops.append({"name": "neighborhood", "variants": neigh})
    if t["system"]:
        ops.append({"name": "blast_radius", "variants": [(
            "MATCH p = (s:System)-[:DEPENDS_ON*1..4]->(:System {id: $id}) RETURN s.id, min(length(p)) ORDER BY s.id",
            {"id": t["system"]})]})
    ops.append({"name": "fulltext", "variants": [(
        "CALL db.idx.fulltext.queryNodes('Document', $term) YIELD node, score "
        "RETURN node.id, score ORDER BY score DESC LIMIT 10", {"term": term}) for term in FULLTEXT_TERMS]})
    if t["client"]:
        ops.append({"name": "ring_hops", "variants": [(
            "MATCH (a:Client {id: $id})-[:HAS_IDENTIFIER]-(tok:PiiToken)-[:HAS_IDENTIFIER]-(b:Client) "
            "WHERE tok.type IN $types AND b.id <> a.id RETURN DISTINCT b.id LIMIT 100",
            {"id": t["client"], "types": RING_TYPES})]})
    if t["embedding"]:
        ops.append({"name": "vector_knn", "variants": [(
            "CALL db.idx.vector.queryNodes('Document', 'embedding', 10, vecf32($v)) YIELD node, score "
            "RETURN node.id, score", {"v": t["embedding"]})]})
    return ops


async def probe_read_ops(ops: list[dict]) -> tuple[list[dict], list[str]]:
    """Run each read op once; drop ops that cannot run on this graph (noted, not counted as load errors)."""
    g = get_db().select_graph(SCRATCH_BRAIN)
    keep, notes = [], []
    for op in ops:
        cypher, params = op["variants"][0]
        try:
            await g.ro_query(cypher, params)
            keep.append(op)
        except Exception as e:  # noqa: BLE001
            notes.append(f"read op {op['name']} disabled: {mask_secrets(str(e))[:200]}")
    return keep, notes


def cap_concurrency(writers: int, readers: int, pool: int) -> tuple[int, int, str | None]:
    if writers + readers <= pool:
        return writers, readers, None
    if writers == 0:
        return 0, pool, f"readers capped {readers}->{pool} (pool size {pool})"
    if readers == 0:
        return pool, 0, f"writers capped {writers}->{pool} (pool size {pool})"
    w = max(1, min(pool - 1, math.floor(writers * pool / (writers + readers))))
    r = pool - w
    return w, r, f"writers+readers {writers}+{readers} > pool size {pool}; capped to {w}+{r}"


# ---------------------------------------------------------------- workers

async def reader(idx: int, ops: list[dict], deadline: float, rec: Recorder) -> None:
    g = get_db().select_graph(SCRATCH_BRAIN)
    k = idx
    while time.perf_counter() < deadline:
        op = ops[k % len(ops)]
        cypher, params = op["variants"][(k // len(ops)) % len(op["variants"])]
        k += 1
        t0 = time.perf_counter()
        try:
            await g.ro_query(cypher, params)
            rec.ok(op["name"], (time.perf_counter() - t0) * 1000.0)
        except Exception as e:  # noqa: BLE001
            rec.err(op["name"], e)


async def writer(idx: int, graph: str, scen: str, batch: int, deadline: float, rec: Recorder) -> None:
    g = get_db().select_graph(graph)
    seq, prev = 0, ""
    while time.perf_counter() < deadline:
        now = int(time.time() * 1000)
        rows = []
        for j in range(batch):
            pid = f"lp_{scen}_{idx}_{seq}_{j}"
            rows.append({"id": pid, "v": seq * batch + j, "ts": now, "prev": prev})
            prev = pid
        seq += 1
        t0 = time.perf_counter()
        try:
            await g.query(WRITE_CYPHER, {"rows": rows})
            rec.ok("write_merge", (time.perf_counter() - t0) * 1000.0)
        except Exception as e:  # noqa: BLE001
            rec.err("write_merge", e)


async def run_scenario(key: str, writers: int, readers: int, seconds: float, batch: int, read_ops: list[dict],
                       created: set[str], cap_note: str | None) -> dict:
    name, desc = SCENARIOS[key]
    if key == "c":
        writers = 0
    write_graphs: list[str] = []
    if writers:
        if key == "a":
            write_graphs = [SCRATCH_BRAIN] * writers
        else:
            for i in range(writers):
                gname = f"{MEM_PREFIX}{i}"
                await delete_graph(gname)
                created.add(gname)
                await _create_probe_index(gname)
                write_graphs.append(gname)
    if not read_ops:
        readers = 0

    rec = Recorder()
    start = time.perf_counter()
    deadline = start + seconds
    tasks = [reader(i, read_ops, deadline, rec) for i in range(readers)]
    tasks += [writer(i, write_graphs[i], key, batch, deadline, rec) for i in range(writers)]
    await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.perf_counter() - start

    ops = {op: summarize(s.lat, s.errors, elapsed) for op, s in rec.ops.items()}
    error_messages = {op: s.messages for op, s in rec.ops.items() if s.messages}
    total_ops = sum(o["count"] for o in ops.values())
    errors = sum(o["errors"] for o in ops.values())
    read_lat = [x for op, s in rec.ops.items() if op != "write_merge" for x in s.lat]
    read_err = sum(s.errors for op, s in rec.ops.items() if op != "write_merge")
    reads = summarize(read_lat, read_err, elapsed)
    writes = None
    if "write_merge" in rec.ops:
        ws = rec.ops["write_merge"]
        writes = summarize(ws.lat, ws.errors, elapsed)
        writes["rows_per_s"] = round(len(ws.lat) * batch / elapsed, 1) if elapsed > 0 else 0.0
    attempts = total_ops + errors
    return {"name": name, "description": desc, "writers": writers, "readers": readers, "seconds": seconds,
            "elapsed_s": round(elapsed, 3), "batch": batch, "total_ops": total_ops,
            "total_ops_per_s": round(total_ops / elapsed, 2) if elapsed > 0 else 0.0, "errors": errors,
            "error_rate": round(errors / attempts, 5) if attempts else 0.0,
            "ops": dict(sorted(ops.items())), "reads": reads, "writes": writes,
            "write_graphs": sorted(set(write_graphs)), "error_messages": error_messages,
            "concurrency_note": cap_note}


# ---------------------------------------------------------------- reporting

def interpret(s: dict, by_name: dict[str, dict]) -> str:
    base = by_name.get("read_only")
    r = s["reads"]
    parts: list[str] = []
    if s["name"] == "read_only":
        parts.append(f"Baseline: {s['readers']} readers sustain {r['ops_per_s']} reads/s, "
                     f"p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms.")
    else:
        w = s["writes"] or {}
        where = "one shared graph (writes serialize on its lock)" if s["name"] == "shared_graph" \
            else f"{s['writers']} separate graphs"
        line = (f"{s['writers']} writers on {where}: {w.get('ops_per_s', 0)} writes/s "
                f"({w.get('rows_per_s', 0)} rows/s), write p95 {w.get('p95_ms')} ms")
        if s["name"] == "graph_per_user" and by_name.get("shared_graph", {}).get("writes"):
            sw = by_name["shared_graph"]["writes"]["ops_per_s"]
            if sw:
                line += f" ({(w.get('ops_per_s', 0) / sw):.2f}x shared_graph write throughput)"
        parts.append(line + ".")
        if s["readers"]:
            rl = f"Concurrent reads: {r['ops_per_s']} reads/s, p95 {r['p95_ms']} ms"
            if base and base["reads"]["p95_ms"] and r["p95_ms"] is not None:
                rl += f" ({r['p95_ms'] / base['reads']['p95_ms']:.2f}x read-only baseline p95)"
            parts.append(rl + ".")
    if s["errors"]:
        parts.append(f"{s['errors']} errors ({s['error_rate'] * 100:.2f}%).")
    return " ".join(parts)


def write_reports(out: str, data: dict) -> tuple[Path, Path]:
    d = REPO_ROOT / "evals" / "results"
    d.mkdir(parents=True, exist_ok=True)
    jp, mp = d / f"{out}.json", d / f"{out}.md"
    jp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    f = lambda x: "-" if x is None else f"{x}"  # noqa: E731
    lines = [f"# FalkorDB load test ({data['tenant']})", "",
             f"Generated {data['generated_at']} · env `{data['env']}` · host `{data['falkordb_host']}` · "
             f"source graph `{data['source_graph']}` · pool size {data['pool_size']} · "
             f"clients {', '.join(f'{k} {v}' for k, v in data['client_versions'].items())}", "",
             "## Summary", "",
             "| Scenario | Writers | Readers | Seconds | Total ops | Ops/s | Errors | Error rate | Gate |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in data["scenarios"]:
        gate = "PASS" if s["gate_passed"] else "FAIL"
        lines.append(f"| {s['name']} | {s['writers']} | {s['readers']} | {s['seconds']} | {s['total_ops']} | "
                     f"{s['total_ops_per_s']} | {s['errors']} | {s['error_rate'] * 100:.2f}% | {gate} |")
    if data["notes"]:
        lines += ["", "Notes:", ""] + [f"- {n}" for n in data["notes"]]
    for s in data["scenarios"]:
        lines += ["", f"## {s['name']}", "", s["description"], "",
                  "| Op | Count | Ops/s | p50 ms | p95 ms | p99 ms | Max ms | Errors |",
                  "|---|---|---|---|---|---|---|---|"]
        for op, o in s["ops"].items():
            lines.append(f"| {op} | {o['count']} | {o['ops_per_s']} | {f(o['p50_ms'])} | {f(o['p95_ms'])} | "
                         f"{f(o['p99_ms'])} | {f(o['max_ms'])} | {o['errors']} |")
        lines += ["", f"**Interpretation:** {s['interpretation']}"]
        if s.get("concurrency_note"):
            lines += ["", f"_Concurrency: {s['concurrency_note']}_"]
        for op, msgs in s.get("error_messages", {}).items():
            for m in msgs:
                lines.append(f"- `{op}` error: {m}")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def client_versions() -> dict:
    out: dict[str, str] = {}
    try:
        from importlib.metadata import version
        for pkg in ("redis", "falkordb"):
            try:
                out[pkg] = version(pkg)
            except Exception:  # noqa: BLE001
                out[pkg] = "unknown"
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------- main

def parse_scenarios(spec: str) -> list[str]:
    keys: list[str] = []
    for tok in [t.strip() for t in spec.split(",") if t.strip()]:
        k = tok if tok in SCENARIOS else NAME_TO_KEY.get(tok)
        if k is None:
            raise SystemExit(f"unknown scenario {tok!r}; use a,b,c or {', '.join(NAME_TO_KEY)}")
        if k not in keys:
            keys.append(k)
    return keys


async def main() -> int:
    ap = argparse.ArgumentParser(description="FalkorDB concurrency benchmark (no LLM/embedding calls)")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--writers", type=int, default=8)
    ap.add_argument("--readers", type=int, default=16)
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--tenant", default="halcyon")
    ap.add_argument("--scenarios", default="a,b,c")
    ap.add_argument("--out", default="load_test")
    args = ap.parse_args()
    keys = parse_scenarios(args.scenarios)

    s = get_settings()
    db = get_db()
    pool_size = int(getattr(db.connection.connection_pool, "max_connections", 32) or 32)
    created: set[str] = set()
    notes: list[str] = []
    results: list[dict] = []
    source = None
    try:
        try:
            # leftovers from a crashed earlier run
            for gname in await db.list_graphs():
                if gname == SCRATCH_BRAIN or gname.startswith(MEM_PREFIX):
                    await delete_graph(gname)
            source = pristine_graph(args.tenant)
            if not await graph_exists(source):
                source = brain_graph(args.tenant)
                notes.append(f"{pristine_graph(args.tenant)} missing; copied {source}")
                if not await graph_exists(source):
                    raise RuntimeError(f"neither pristine nor brain graph exists for tenant {args.tenant}")
            t0 = time.perf_counter()
            created.add(SCRATCH_BRAIN)
            await copy_graph(source, SCRATCH_BRAIN)
            copy_ms = (time.perf_counter() - t0) * 1000.0
            g = db.select_graph(SCRATCH_BRAIN)
            n_nodes = (await g.ro_query("MATCH (n) RETURN count(n)")).result_set[0][0]
            notes.append(f"GRAPH.COPY {source} -> {SCRATCH_BRAIN}: {n_nodes} nodes in {copy_ms:.0f} ms")
            await _create_probe_index(SCRATCH_BRAIN)
            targets = await resolve_targets(args.tenant)
            notes += targets["notes"]
            read_ops, pnotes = await probe_read_ops(build_read_ops(targets))
            notes += pnotes
            if not read_ops:
                raise RuntimeError("no read operation could run on the scratch graph")
        except Exception as e:  # noqa: BLE001
            print(f"SETUP FAILED: {mask_secrets(f'{type(e).__name__}: {e}')}")
            return 1

        print(f"load test on {SCRATCH_BRAIN} (copy of {source}); read ops: "
              f"{', '.join(o['name'] for o in read_ops)}; pool size {pool_size}")
        for k in keys:
            w = 0 if k == "c" else args.writers
            w, r, cap_note = cap_concurrency(w, args.readers, pool_size)
            if cap_note:
                notes.append(f"{SCENARIOS[k][0]}: {cap_note}")
            print(f"-> {SCENARIOS[k][0]}: writers={w} readers={r} for {args.seconds:g}s ...", flush=True)
            res = await run_scenario(k, w, r, args.seconds, args.batch, read_ops, created, cap_note)
            results.append(res)
    finally:
        for gname in sorted(created):
            try:
                await delete_graph(gname)
            except Exception as e:  # noqa: BLE001
                print(f"warning: could not delete {gname}: {type(e).__name__}: {e}")

    by_name = {x["name"]: x for x in results}
    failed = False
    for x in results:
        x["interpretation"] = interpret(x, by_name)
        x["gate_passed"] = x["error_rate"] <= ERROR_RATE_GATE and x["total_ops"] > 0
        failed |= not x["gate_passed"]

    data = {"generated_at": datetime.now(timezone.utc).isoformat(), "env": s.env, "falkordb_host": s.falkordb_host,
            "tenant": args.tenant, "source_graph": source, "scratch_graph": SCRATCH_BRAIN, "pool_size": pool_size,
            "client_versions": client_versions(),
            "config": {"seconds": args.seconds, "writers": args.writers, "readers": args.readers,
                       "batch": args.batch, "scenarios": [SCENARIOS[k][0] for k in keys]},
            "read_ops": [o["name"] for o in read_ops], "error_rate_gate": ERROR_RATE_GATE,
            "notes": notes, "scenarios": results}
    jp, mp = write_reports(args.out, data)

    print()
    for x in results:
        print(f"[{'PASS' if x['gate_passed'] else 'FAIL'}] {x['name']}: W={x['writers']} R={x['readers']} "
              f"{x['total_ops']} ops, {x['total_ops_per_s']} ops/s, errors {x['errors']} "
              f"({x['error_rate'] * 100:.2f}%)")
        for op, o in x["ops"].items():
            print(f"      {op:<13} {o['ops_per_s']:>9} ops/s  p50 {o['p50_ms']} ms  p95 {o['p95_ms']} ms  "
                  f"p99 {o['p99_ms']} ms  err {o['errors']}")
        print(f"      {x['interpretation']}")
    for n in notes:
        print(f"note: {n}")
    print(f"wrote {jp} and {mp}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(asyncio.run(main()))