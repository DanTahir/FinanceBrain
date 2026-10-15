"""Retrieval eval: Recall@k of the expected source documents for the golden questions.

What it measures
    For every golden question (evals/golden.json) with non-empty ``expect_docs``, the production hybrid retrieval
    (``backend.tools.retrieval.search_knowledge``: vector + full-text + fact vectors, RRF-fused) is called directly
    as the question's persona (``ToolContext(make_auth(tenant, persona))``), so ACLs and the information barrier
    apply exactly as in production. The ranked primary hits (thread-neighbour context entries, i.e. results with
    ``context_for`` set, are excluded from the ranking) are scored against the expected documents:

    * recall@k = |expected ∩ top-k| / min(k, |expected|)   (normalised: a perfect ranking scores 1.0)
    * hit@k    = 1 if any expected document is in the top-k
    * rr       = 1 / rank of the first expected document (0 if none); mrr is its mean

    Expected documents are filtered to ids that exist as Document nodes in brain__<tenant> AND are visible to the
    persona (``ToolContext.visible_docs``, the same ACL-checked Cypher the tools use). Questions whose expected set
    becomes empty are recorded as skipped.

    Ablation (unless --no-ablation): the same question is also scored with vector-only and full-text-only ranking,
    reusing the exact ACL-filtered Cypher constants search_knowledge runs (``Q.SEARCH_DOCS_VECTOR`` and
    ``Q.SEARCH_DOCS_FULLTEXT``) with the same parameters. The query is embedded once per question (via
    ``retrieval.query_vector``, the tool's own embedding function) and passed as ``vector=`` to every mode.

How to run
    PYTHONPATH=. .venv/Scripts/python -m evals.retrieval_eval [--phase seed|live|all] [--k 10] [--only id1,id2]
                                                              [--no-ablation]

Outputs evals/results/retrieval.json and evals/results/retrieval.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from backend.config import REPO_ROOT, get_settings
from backend.graph import queries as Q
from backend.tenancy import make_auth
from backend.tools import retrieval
from backend.tools.base import ToolContext, ToolError

RESULTS_DIR = REPO_ROOT / "evals" / "results"
GOLDEN = REPO_ROOT / "evals" / "golden.json"
METRIC_KEYS = ("recall@5", "recall@10", "hit@5", "hit@10")


# ------------------------------------------------------------------------------------------------ metrics
def score(ranked: list[str], expected: list[str]) -> dict[str, Any]:
    exp = set(expected)
    out: dict[str, Any] = {}
    for k in (5, 10):
        top = set(ranked[:k])
        inter = len(exp & top)
        out[f"recall@{k}"] = inter / min(k, len(exp)) if exp else 0.0
        out[f"hit@{k}"] = 1.0 if inter else 0.0
    first = next((i + 1 for i, d in enumerate(ranked) if d in exp), None)
    out["rr"] = 1.0 / first if first else 0.0
    out["first_rank"] = first
    out["missed"] = [d for d in expected if d not in set(ranked)]
    return out


def aggregate(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    out: dict[str, Any] = {"n": n}
    for key in METRIC_KEYS:
        out[key] = round(sum(r[key] for r in rows) / n, 4) if n else 0.0
    out["mrr"] = round(sum(r["rr"] for r in rows) / n, 4) if n else 0.0
    return out


def dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


# ------------------------------------------------------------------------------------------------ retrieval modes
def _base(ctx: ToolContext) -> dict:
    return {"pid": ctx.pid, "kinds": None, "since": None, "until": None}


async def vector_only(ctx: ToolContext, v: list[float], k: int) -> list[str]:
    """Same loop and Cypher as search_knowledge's vec() branch (no kinds/since/until filters)."""
    want = k * 3
    over = max(80, want * 8)
    while True:
        rows = await ctx.q("SEARCH_DOCS_VECTOR", Q.SEARCH_DOCS_VECTOR, {**_base(ctx), "v": v, "k": over, "limit": want})
        if len(rows) >= want or over >= 400:
            break
        over = 400
    return dedupe([r[0] for r in rows])[:k]


async def fulltext_only(ctx: ToolContext, query: str, k: int) -> list[str]:
    """Same Cypher and query-word construction as search_knowledge's fulltext() branch."""
    words = retrieval.query_words(query)
    if not words:
        return []
    try:
        rows = await ctx.q("SEARCH_DOCS_FULLTEXT", Q.SEARCH_DOCS_FULLTEXT,
                           {**_base(ctx), "q": " | ".join(words), "limit": k * 3})
    except ToolError:
        return []
    return dedupe([r[0] for r in rows])[:k]


# ------------------------------------------------------------------------------------------------ main eval
def select_questions(args: argparse.Namespace) -> list[dict]:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    out = []
    for q in golden["questions"]:
        if not q.get("expect_docs"):
            continue
        if only and q["id"] not in only:
            continue
        if args.phase != "all" and q.get("phase", "seed") != args.phase:
            continue
        out.append(q)
    return out


async def eval_question(q: dict, k: int, ablation: bool) -> dict:
    ctx = ToolContext(make_auth(q["tenant"], q["persona"]))
    want = dedupe(list(q["expect_docs"]))
    rows = await ctx.q("eval:doc_exists", "MATCH (d:Document) WHERE d.id IN $ids RETURN d.id", {"ids": want})
    existing = {r[0] for r in rows}
    visible = await ctx.visible_docs([d for d in want if d in existing])
    expected = [d for d in want if d in visible]
    rec: dict[str, Any] = {
        "id": q["id"], "tenant": q["tenant"], "persona": q["persona"], "phase": q.get("phase", "seed"),
        "query": q["question"], "expected": expected,
        "dropped_missing": [d for d in want if d not in existing],
        "dropped_invisible": [d for d in want if d in existing and d not in visible],
    }
    if not expected:
        rec["skipped"] = True
        rec["reason"] = "no expected document exists in the graph and is visible to the persona"
        return rec

    v = await retrieval.query_vector(q["question"])  # one embedding call per question, shared by all modes
    res = await retrieval.search_knowledge(ctx, q["question"], k=k, vector=v)
    results = res.get("results") or []
    ranked = dedupe([r["id"] for r in results if not r.get("context_for")])
    context = dedupe([r["id"] for r in results if r.get("context_for") and r["id"] not in set(ranked)])
    s = score(ranked, expected)
    rec.update({"ranked": ranked, "context": context, **{m: s[m] for m in METRIC_KEYS}, "rr": s["rr"],
                "first_rank": s["first_rank"], "missed": s["missed"],
                "context_hits": [d for d in expected if d in set(context)]})
    if ablation:
        modes: dict[str, dict] = {"hybrid": {m: s[m] for m in (*METRIC_KEYS, "rr")}}
        vr = await vector_only(ctx, v, k)
        fr = await fulltext_only(ctx, q["question"], k)
        for name, rk in (("vector", vr), ("fulltext", fr)):
            ms = score(rk, expected)
            modes[name] = {**{m: ms[m] for m in (*METRIC_KEYS, "rr")}, "ranked": rk, "first_rank": ms["first_rank"]}
        rec["modes"] = modes
    return rec


def fmt(x: float) -> str:
    return f"{x:.3f}"


def write_md(data: dict) -> str:
    lines = ["# Retrieval eval (Recall@k of expected source documents)", "",
             f"- generated_at: {data['generated_at']}", f"- env: {data['env']}",
             f"- embed_model: {data['embed_model']}", f"- phase: {data['phase']}, search depth k={data['k']}", "",
             "## Summary", "", "| Mode | n | R@5 | R@10 | hit@5 | hit@10 | MRR |", "|---|---|---|---|---|---|---|"]
    rows = [("hybrid (search_knowledge)", data["summary"])]
    for name, m in (data.get("modes") or {}).items():
        if name != "hybrid":
            rows.append((name, m))
    for name, m in rows:
        lines.append(f"| {name} | {m['n']} | {fmt(m['recall@5'])} | {fmt(m['recall@10'])} | {fmt(m['hit@5'])} | "
                     f"{fmt(m['hit@10'])} | {fmt(m['mrr'])} |")
    lines += ["", "Notes:",
              "- recall@k = |expected ∩ top-k| / min(k, |expected|); thread-neighbour context results are excluded "
              "from the ranking.",
              "- Expected documents are filtered to ids that exist in brain__<tenant> and are visible to the persona "
              "(ToolContext.visible_docs).",
              "- vector / fulltext modes reuse the exact ACL-filtered Cypher constants of search_knowledge "
              "(Q.SEARCH_DOCS_VECTOR, Q.SEARCH_DOCS_FULLTEXT) with the same parameters; fact-vector boosts and RRF "
              "fusion apply only to hybrid." if data.get("modes") else
              "- Ablation disabled (--no-ablation)."]
    lines += ["", "## Per question", "",
              "| id | persona | |expected| | R@5 | R@10 | hit@5 | RR | first expected rank | missed |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in data["questions"]:
        missed = ", ".join(r["missed"]) or "-"
        lines.append(f"| {r['id']} | {r['persona']} | {len(r['expected'])} | {fmt(r['recall@5'])} | "
                     f"{fmt(r['recall@10'])} | {int(r['hit@5'])} | {fmt(r['rr'])} | {r['first_rank'] or '-'} | "
                     f"{missed} |")
    if data.get("modes"):
        lines += ["", "## Per question by mode (R@10 / RR)", "", "| id | hybrid | vector | fulltext |",
                  "|---|---|---|---|"]
        for r in data["questions"]:
            m = r.get("modes") or {}
            cells = [f"{fmt(m[x]['recall@10'])} / {fmt(m[x]['rr'])}" if x in m else "-"
                     for x in ("hybrid", "vector", "fulltext")]
            lines.append(f"| {r['id']} | " + " | ".join(cells) + " |")
    dropped = [r for r in data["questions"] + data["skipped"] if r.get("dropped_missing") or r.get("dropped_invisible")]
    if dropped:
        lines += ["", "## Dropped expected documents", "", "| id | missing from graph | not visible to persona |",
                  "|---|---|---|"]
        for r in dropped:
            lines.append(f"| {r['id']} | {', '.join(r.get('dropped_missing') or []) or '-'} | "
                         f"{', '.join(r.get('dropped_invisible') or []) or '-'} |")
    if data["skipped"]:
        lines += ["", "## Skipped", "", "| id | phase | reason |", "|---|---|---|"]
        for r in data["skipped"]:
            lines.append(f"| {r['id']} | {r.get('phase', '')} | {r.get('reason') or r.get('error', '')} |")
    return "\n".join(lines) + "\n"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Recall@k of expected source documents via search_knowledge.")
    ap.add_argument("--phase", choices=["seed", "live", "all"], default="seed")
    ap.add_argument("--k", type=int, default=10, help="search depth (clamped to 1..15 like the tool)")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--no-ablation", action="store_true")
    args = ap.parse_args()
    k = max(1, min(int(args.k), 15))
    ablation = not args.no_ablation
    s = get_settings()

    scored: list[dict] = []
    skipped: list[dict] = []
    for q in select_questions(args):
        try:
            rec = await eval_question(q, k, ablation)
        except Exception as e:  # noqa: BLE001
            skipped.append({"id": q["id"], "tenant": q["tenant"], "persona": q["persona"],
                            "phase": q.get("phase", "seed"), "skipped": True, "error": f"{type(e).__name__}: {e}"[:300]})
            print(f"[ERR ] {q['id']}: {type(e).__name__}: {str(e)[:200]}")
            continue
        if rec.get("skipped"):
            skipped.append(rec)
            print(f"[SKIP] {q['id']}: {rec['reason']}")
        else:
            scored.append(rec)
            print(f"[{'HIT ' if rec['hit@10'] else 'MISS'}] {rec['id']}: R@5={rec['recall@5']:.2f} "
                  f"R@10={rec['recall@10']:.2f} RR={rec['rr']:.2f}")

    data: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "env": s.env, "embed_model": s.embed_model, "phase": args.phase, "k": k,
        "summary": aggregate(scored),
    }
    if ablation and scored:
        data["modes"] = {name: aggregate([r["modes"][name] for r in scored]) for name in ("hybrid", "vector", "fulltext")}
    data["questions"] = scored
    data["skipped"] = skipped

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "retrieval.json").write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
                                                encoding="utf-8")
    (RESULTS_DIR / "retrieval.md").write_text(write_md(data), encoding="utf-8")

    sm = data["summary"]
    print(f"\nretrieval ({args.phase}, k={k}): n={sm['n']} skipped={len(skipped)} R@5={sm['recall@5']:.3f} "
          f"R@10={sm['recall@10']:.3f} hit@5={sm['hit@5']:.3f} hit@10={sm['hit@10']:.3f} MRR={sm['mrr']:.3f}")
    for name, m in (data.get("modes") or {}).items():
        print(f"  {name:9s} R@5={m['recall@5']:.3f} R@10={m['recall@10']:.3f} hit@10={m['hit@10']:.3f} "
              f"MRR={m['mrr']:.3f}")
    print(f"wrote {RESULTS_DIR / 'retrieval.json'} and {RESULTS_DIR / 'retrieval.md'}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(asyncio.run(main()))