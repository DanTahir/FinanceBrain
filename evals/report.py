"""Aggregate every eval result file into a single README-ready benchmark summary.

What it measures
----------------
Nothing new: this script reads the machine-readable outputs already produced by the
other evals in ``evals/results/`` and renders one Markdown section, ``SUMMARY.md``,
that is meant to be pasted into the project README. It summarises:

1. a headline table (one row per eval with its key numbers),
2. golden questions by track (T1/T2/T3) and by phase (seed/live),
3. retrieval Recall@k / hit@k / MRR plus the optional modes ablation,
4. redaction recall/precision per tenant and per PII type (+ canary/outbound-guard
   information when present),
5. graph checks per tenant with a pass/fail mark per check,
6. tenant isolation per category,
7. load-test per-scenario op tables,
8. a footer with each source file's ``generated_at`` and ``env``.

Every input is optional; missing or unreadable files are skipped with a note.
No database or network access is performed.

How to run
----------
    PYTHONPATH=. .venv/Scripts/python -m evals.report
    PYTHONPATH=. .venv/Scripts/python -m evals.report --results evals/results \
        --out evals/results/SUMMARY.md --stdout
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCES: list[str] = [
    "golden",
    "retrieval",
    "redaction",
    "graph_checks_halcyon",
    "graph_checks_brightwater",
    "isolation",
    "load_test",
]

DASH = "—"


# --------------------------------------------------------------------------- formatting


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def pct(x: Any) -> str:
    if not _is_num(x):
        return DASH
    return f"{float(x) * 100:.1f}%"


def ms(x: Any) -> str:
    if not _is_num(x):
        return DASH
    return f"{float(x):.1f}"


def cost(x: Any) -> str:
    if not _is_num(x):
        return DASH
    return f"${float(x):.3f}"


def num(x: Any, digits: int = 1) -> str:
    if not _is_num(x):
        return DASH
    if isinstance(x, int):
        return f"{x:,}"
    if float(x).is_integer():
        return f"{int(x):,}"
    return f"{float(x):,.{digits}f}"


def frac(passed: Any, total: Any) -> str:
    if not _is_num(passed) or not _is_num(total):
        return DASH
    return f"{int(passed)}/{int(total)}"


def cell(x: Any) -> str:
    s = DASH if x is None else str(x)
    return s.replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(cell(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(cell(c) for c in r) + " |")
    return out


def compact(v: Any, limit: int = 160) -> str:
    if isinstance(v, dict):
        s = ", ".join(f"{k}={compact(val, 40)}" for k, val in v.items())
    elif isinstance(v, list):
        s = f"{len(v)} item(s)" if len(v) > 5 else ", ".join(compact(i, 40) for i in v)
    elif _is_num(v):
        s = num(v, 4) if isinstance(v, float) else str(v)
    else:
        s = str(v)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# --------------------------------------------------------------------------- loading


class Loaded:
    def __init__(self, results: Path) -> None:
        self.results = results
        self.data: dict[str, Any] = {}
        self.notes: dict[str, str] = {}
        self.mtimes: dict[str, str] = {}

    def load(self) -> None:
        for name in SOURCES:
            path = self.results / f"{name}.json"
            if not path.exists():
                self.notes[name] = f"`{path.name}` not found — skipped"
                continue
            try:
                self.data[name] = json.loads(path.read_text(encoding="utf-8"))
                mt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                self.mtimes[name] = mt.isoformat(timespec="seconds")
            except Exception as exc:  # noqa: BLE001
                self.notes[name] = f"`{path.name}` unreadable ({type(exc).__name__}) — skipped"

    def get(self, name: str) -> Any:
        return self.data.get(name)

    def link(self, name: str) -> str:
        md = self.results / f"{name}.md"
        rel = f"{name}.md"  # SUMMARY.md is written next to the result files, so links are relative to that folder
        if md.exists():
            return f"Details: [{rel}]({rel})"
        return f"Details: `{rel}` (not generated)"

    def skipped(self, name: str) -> list[str]:
        return [f"_{self.notes.get(name, 'no data')}._", ""]


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, list) else []


# --------------------------------------------------------------------------- redaction helpers


def redaction_entries(data: Any) -> list[dict]:
    """Normalise redaction.json into a list of {tenant, summary, by_type, ...}."""
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        for key in ("tenants", "results"):
            if isinstance(data.get(key), list):
                return [e for e in data[key] if isinstance(e, dict)]
        out = []
        for k, v in data.items():
            if isinstance(v, dict) and ("by_type" in v or "summary" in v):
                out.append({"tenant": k, **v})
        return out
    return []


def redaction_extras(entry: dict) -> dict[str, Any]:
    """Canary / outbound-guard style keys, if the redaction eval emitted any."""
    found: dict[str, Any] = {}
    for scope in (entry, _d(entry.get("summary"))):
        for k, v in scope.items():
            lk = str(k).lower()
            if any(t in lk for t in ("canary", "guard", "outbound", "block")):
                found[str(k)] = v
    return found


def canary_value(entry: dict) -> str:
    for k, v in redaction_extras(entry).items():
        if "canary" not in k.lower():
            continue
        if isinstance(v, dict):
            for hk in ("hits", "leaks", "leaked", "found"):
                if hk in v:
                    hv = v[hk]
                    return str(len(hv)) if isinstance(hv, list) else compact(hv)
            return compact(v, 60)
        if isinstance(v, list):
            return str(len(v))
        return compact(v, 60)
    return "n/a"


# --------------------------------------------------------------------------- graph checks helpers


def graph_check_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, dict) and isinstance(data.get("checks"), list):
        return [c for c in data["checks"] if isinstance(c, dict)]
    return []


def check_passed(c: dict) -> bool:
    v = c.get("pass", c.get("passed"))
    return bool(v)


# --------------------------------------------------------------------------- load test helpers


def read_p95(scn: dict) -> str:
    ops = _d(scn.get("ops"))
    reads = {k: _d(v) for k, v in ops.items() if "read" in str(k).lower()}
    if not reads:
        reads = {k: _d(v) for k, v in ops.items()
                 if not any(t in str(k).lower() for t in ("write", "ingest", "upsert", "insert"))}
    vals = [(k, v.get("p95_ms")) for k, v in reads.items() if _is_num(v.get("p95_ms"))]
    if not vals:
        return DASH
    if len(vals) == 1:
        return f"{ms(vals[0][1])} ms"
    worst = max(vals, key=lambda kv: kv[1])
    return f"{ms(worst[1])} ms (worst: {worst[0]})"


# --------------------------------------------------------------------------- sections


def section_headline(L: Loaded) -> list[str]:
    out = ["## Headline results", "",
           "One row per eval with its key number(s); see the sections below for breakdowns.", ""]
    rows: list[list[Any]] = []

    g = L.get("golden")
    if g is not None:
        s = _d(_d(g).get("summary"))
        rows.append(["Golden questions",
                     f"pass {pct(s.get('pass_rate'))} ({frac(s.get('passed'), s.get('n'))}), "
                     f"judge mean {num(s.get('judge_mean'), 2)}, cost {cost(s.get('cost_usd'))}",
                     "[golden.md](golden.md)"])
    else:
        rows.append(["Golden questions", "not run", DASH])

    r = L.get("retrieval")
    if r is not None:
        s = _d(_d(r).get("summary"))
        rows.append(["Retrieval",
                     f"Recall@5 {pct(s.get('recall@5'))}, Recall@10 {pct(s.get('recall@10'))}, "
                     f"MRR {num(s.get('mrr'), 3)} (n={num(s.get('n'))})",
                     "[retrieval.md](retrieval.md)"])
    else:
        rows.append(["Retrieval", "not run", DASH])

    red = L.get("redaction")
    entries = redaction_entries(red) if red is not None else []
    if entries:
        for e in entries:
            s = _d(e.get("summary"))
            rows.append([f"Redaction ({e.get('tenant', '?')})",
                         f"recall {pct(s.get('recall_any'))} (typed {pct(s.get('recall_typed'))}), "
                         f"precision {pct(s.get('precision'))}, canary hits {canary_value(e)}",
                         "[redaction.md](redaction.md)"])
    else:
        rows.append(["Redaction", "not run", DASH])

    for tenant in ("halcyon", "brightwater"):
        name = f"graph_checks_{tenant}"
        data = L.get(name)
        if data is None:
            rows.append([f"Graph checks ({tenant})", "not run", DASH])
            continue
        checks = graph_check_list(data)
        p = sum(1 for c in checks if check_passed(c))
        rows.append([f"Graph checks ({tenant})", f"{p}/{len(checks)} passed",
                     f"[{name}.md]({name}.md)"])

    iso = L.get("isolation")
    if iso is not None:
        s = _d(_d(iso).get("summary"))
        checks = _l(_d(iso).get("checks"))
        n = s.get("n", len(checks))
        p = s.get("passed", sum(1 for c in checks if _d(c).get("passed")))
        rows.append(["Tenant isolation", f"{frac(p, n)} passed",
                     "[isolation.md](isolation.md)"])
    else:
        rows.append(["Tenant isolation", "not run", DASH])

    lt = L.get("load_test")
    scns = _l(_d(lt).get("scenarios")) if lt is not None else []
    if scns:
        for scn in scns:
            scn = _d(scn)
            rows.append([f"Load test ({scn.get('name', '?')})",
                         f"{num(scn.get('total_ops_per_s'))} ops/s total, read p95 {read_p95(scn)}, "
                         f"errors {num(scn.get('errors'))}",
                         "[load_test.md](load_test.md)"])
    else:
        rows.append(["Load test", "not run", DASH])

    out += table(["Eval", "Key result", "Details"], rows)
    out.append("")
    return out


def section_golden(L: Loaded) -> list[str]:
    out = ["## Golden questions", "",
           "End-to-end agent answers on curated questions, scored on entity recall, cited-document hits, "
           "citation validity, barrier violations and an LLM judge.", "", L.link("golden"), ""]
    g = L.get("golden")
    if g is None:
        return out + L.skipped("golden")
    g = _d(g)
    s = _d(g.get("summary"))
    out += [f"Judge model: `{g.get('judge_model') or 'n/a'}`", ""]
    out += table(
        ["n", "Passed", "Pass rate", "Entity recall", "Doc hit rate", "Citation validity",
         "Barrier violations", "Judge mean", "Judge correct", "Cost", "p50 ms", "p95 ms"],
        [[num(s.get("n")), num(s.get("passed")), pct(s.get("pass_rate")), pct(s.get("entity_recall")),
          pct(s.get("doc_hit_rate")), pct(s.get("citation_validity")), num(s.get("barrier_violations")),
          num(s.get("judge_mean"), 2), pct(s.get("judge_correct_rate")), cost(s.get("cost_usd")),
          ms(s.get("p50_ms")), ms(s.get("p95_ms"))]])
    out.append("")

    tracks = _d(g.get("by_track"))
    if tracks:
        order = [t for t in ("T1", "T2", "T3") if t in tracks] + \
                sorted(t for t in tracks if t not in ("T1", "T2", "T3"))
        out += ["### By track", ""]
        out += table(["Track", "n", "Passed", "Pass rate", "Entity recall", "Doc hit rate", "Judge mean"],
                     [[t, num(_d(tracks[t]).get("n")), num(_d(tracks[t]).get("passed")),
                       pct(_d(tracks[t]).get("pass_rate")), pct(_d(tracks[t]).get("entity_recall")),
                       pct(_d(tracks[t]).get("doc_hit_rate")), num(_d(tracks[t]).get("judge_mean"), 2)]
                      for t in order])
        out.append("")

    phases = _d(g.get("by_phase"))
    if phases:
        order = [p for p in ("seed", "live") if p in phases] + \
                sorted(p for p in phases if p not in ("seed", "live"))
        out += ["### By phase", ""]
        out += table(["Phase", "n", "Passed", "Pass rate"],
                     [[p, num(_d(phases[p]).get("n")), num(_d(phases[p]).get("passed")),
                       pct(_d(phases[p]).get("pass_rate"))] for p in order])
        out.append("")
    return out


def section_retrieval(L: Loaded) -> list[str]:
    out = ["## Retrieval", "",
           "Ranking quality of document retrieval against expected source documents (Recall@k, hit@k, MRR).",
           "", L.link("retrieval"), ""]
    r = L.get("retrieval")
    if r is None:
        return out + L.skipped("retrieval")
    r = _d(r)
    out += [f"Embedding model: `{r.get('embed_model') or 'n/a'}`", ""]
    hdr = ["n", "Recall@5", "Recall@10", "Hit@5", "Hit@10", "MRR"]

    def row(s: dict) -> list[Any]:
        return [num(s.get("n")), pct(s.get("recall@5")), pct(s.get("recall@10")),
                pct(s.get("hit@5")), pct(s.get("hit@10")), num(s.get("mrr"), 3)]

    out += table(hdr, [row(_d(r.get("summary")))])
    out.append("")
    modes = _d(r.get("modes"))
    if modes:
        out += ["### Modes ablation", ""]
        out += table(["Mode"] + hdr, [[m] + row(_d(v)) for m, v in modes.items()])
        out.append("")
    return out


def section_redaction(L: Loaded) -> list[str]:
    out = ["## Redaction", "",
           "Detection of injected PII spans in the synthetic corpus, per tenant and per PII type "
           "(recall over ground-truth spans, precision over detections).", "", L.link("redaction"), ""]
    red = L.get("redaction")
    if red is None:
        return out + L.skipped("redaction")
    entries = redaction_entries(red)
    if not entries:
        return out + ["_redaction.json has no recognisable tenant entries._", ""]
    for e in entries:
        s = _d(e.get("summary"))
        out += [f"### {e.get('tenant', '?')}", ""]
        out.append(
            f"{num(s.get('gt_spans'))} ground-truth spans across {num(s.get('docs'))} documents, "
            f"{num(s.get('detections'))} detections; recall (any type) **{pct(s.get('recall_any'))}**, "
            f"typed recall {pct(s.get('recall_typed'))}, precision **{pct(s.get('precision'))}**"
            + (f", {num(s.get('seconds'))} s." if _is_num(s.get("seconds")) else "."))
        out.append("")
        by_type = _d(e.get("by_type"))
        if by_type:
            out += table(["Type", "GT spans", "Recall (any)", "Recall (typed)", "Detections", "Precision"],
                         [[t, num(_d(v).get("gt")), pct(_d(v).get("recall_any")),
                           pct(_d(v).get("recall_typed")), num(_d(v).get("detections")),
                           pct(_d(v).get("precision"))] for t, v in sorted(by_type.items())])
            out.append("")
        extras = redaction_extras(e)
        if extras:
            out += ["Canary / outbound guard:", ""]
            out += [f"- **{k}**: {compact(v)}" for k, v in extras.items()]
        else:
            out.append("_No canary or outbound-guard results recorded for this tenant._")
        out.append("")
    return out


def section_graph(L: Loaded) -> list[str]:
    out = ["## Graph checks", "",
           "Structural assertions on each tenant's knowledge graph (fraud rings, blast radius, ownership, "
           "experts, decision trails, access restrictions, vector and full-text indexes).", ""]
    for tenant in ("halcyon", "brightwater"):
        name = f"graph_checks_{tenant}"
        out += [f"### {tenant}", "", L.link(name), ""]
        data = L.get(name)
        if data is None:
            out += L.skipped(name)
            continue
        checks = graph_check_list(data)
        p = sum(1 for c in checks if check_passed(c))
        out += [f"**{p}/{len(checks)}** checks passed.", ""]
        out += table(["", "Check"],
                     [["✓" if check_passed(c) else "✗", c.get("check", c.get("name", "?"))] for c in checks])
        out.append("")
    return out


def section_isolation(L: Loaded) -> list[str]:
    out = ["## Tenant isolation", "",
           "Verifies that tenants, personas and per-user memory graphs cannot read each other's data.",
           "", L.link("isolation"), ""]
    iso = L.get("isolation")
    if iso is None:
        return out + L.skipped("isolation")
    iso = _d(iso)
    s = _d(iso.get("summary"))
    checks = [_d(c) for c in _l(iso.get("checks"))]
    n = s.get("n", len(checks))
    p = s.get("passed", sum(1 for c in checks if c.get("passed")))
    out += [f"**{frac(p, n)}** checks passed.", ""]
    cats: dict[str, list[int]] = {}
    for c in checks:
        cat = str(c.get("category") or "uncategorised")
        bucket = cats.setdefault(cat, [0, 0])
        bucket[1] += 1
        if c.get("passed"):
            bucket[0] += 1
    if cats:
        out += table(["Category", "Passed/total"], [[k, frac(v[0], v[1])] for k, v in sorted(cats.items())])
        out.append("")
    failed = [c for c in checks if not c.get("passed")]
    if failed:
        out += ["Failed checks:", ""]
        out += [f"- ✗ {c.get('check', '?')} ({c.get('category', '?')}): {compact(c.get('detail'))}"
                for c in failed]
        out.append("")
    return out


def section_load(L: Loaded) -> list[str]:
    out = ["## Load test", "",
           "Concurrent read/write throughput and latency percentiles against FalkorDB.",
           "", L.link("load_test"), ""]
    lt = L.get("load_test")
    if lt is None:
        return out + L.skipped("load_test")
    lt = _d(lt)
    scns = [_d(s) for s in _l(lt.get("scenarios"))]
    if not scns:
        return out + ["_load_test.json contains no scenarios._", ""]
    for scn in scns:
        out += [f"### {scn.get('name', '?')}", ""]
        if scn.get("description"):
            out += [str(scn["description"]), ""]
        out.append(f"{num(scn.get('writers'))} writers, {num(scn.get('readers'))} readers, "
                   f"{num(scn.get('seconds'))} s — {num(scn.get('total_ops'))} ops, "
                   f"**{num(scn.get('total_ops_per_s'))} ops/s**, {num(scn.get('errors'))} errors.")
        out.append("")
        ops = _d(scn.get("ops"))
        if ops:
            out += table(["Op", "Count", "ops/s", "p50 ms", "p95 ms", "p99 ms", "max ms", "Errors"],
                         [[k, num(_d(v).get("count")), num(_d(v).get("ops_per_s")),
                           ms(_d(v).get("p50_ms")), ms(_d(v).get("p95_ms")), ms(_d(v).get("p99_ms")),
                           ms(_d(v).get("max_ms")), num(_d(v).get("errors"))] for k, v in ops.items()])
            out.append("")
    return out


def section_footer(L: Loaded) -> list[str]:
    out = ["## Sources", "",
           "Provenance of each result file used to build this summary.", ""]
    rows = []
    for name in SOURCES:
        data = L.get(name)
        if data is None:
            rows.append([f"{name}.json", "missing", DASH])
            continue
        d = _d(data)
        gen = d.get("generated_at")
        if not gen:
            gen = f"{L.mtimes.get(name, DASH)} (file mtime)"
        env = d.get("env") or "n/a"
        if name == "load_test" and d.get("falkordb_host"):
            env = f"{env} (host: {d['falkordb_host']})"
        rows.append([f"{name}.json", gen, env])
    out += table(["File", "generated_at", "env"], rows)
    out += ["", f"_Summary generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}._", ""]
    return out


def render(L: Loaded) -> str:
    lines: list[str] = ["# Evaluation & benchmarks", ""]
    for fn in (section_headline, section_golden, section_retrieval, section_redaction,
               section_graph, section_isolation, section_load, section_footer):
        lines += fn(L)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- main


async def main() -> int:
    ap = argparse.ArgumentParser(description="Build evals/results/SUMMARY.md from eval result JSON files.")
    ap.add_argument("--results", default="evals/results", help="directory containing result JSON files")
    ap.add_argument("--out", default="evals/results/SUMMARY.md", help="output Markdown path")
    ap.add_argument("--stdout", action="store_true", help="also print the summary to stdout")
    args = ap.parse_args()

    results = Path(args.results)
    L = Loaded(results)
    L.load()
    md = render(L)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")

    if args.stdout:
        print(md)
    print(f"report: loaded {len(L.data)}/{len(SOURCES)} result files from {results}")
    for name, note in L.notes.items():
        print(f"  note: {note}")
    print(f"report: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))