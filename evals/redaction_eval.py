"""Redaction eval: precision / recall of the PII analyzer against the injected-span ground truth.

    python -m evals.redaction_eval [halcyon brightwater]

Matching: a ground-truth span counts as detected when detections cover >= 50% of its characters ("any type"
recall, the privacy-relevant number) and as typed-correct when a covering detection has the same type. A detection
is a true positive when it overlaps any ground-truth span. Writes evals/results/redaction.json and .md.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict

from backend.config import REPO_ROOT
from backend.redaction.pipeline import analyzer_for_world
from datagen.world import World

KINDS = ("slack", "email", "ticket", "doc", "meeting", "crm")
OUT = REPO_ROOT / "evals" / "results"


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def evaluate(tenant: str, show_fp: int = 0) -> dict:
    raw = REPO_ROOT / "data" / "raw" / tenant
    w = World.load(REPO_ROOT / "data" / "world" / tenant / "world.json")
    an = analyzer_for_world(w)
    docs = [json.loads(line) for k in KINDS for line in (raw / f"{k}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    gt: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for line in (raw / "pii_spans.jsonl").read_text(encoding="utf-8").splitlines():
        if line:
            s = json.loads(line)
            gt[(s["doc_id"], s.get("field", "text"))].append(s)
    gt_total, gt_found, gt_typed = Counter(), Counter(), Counter()
    det_total, det_tp = Counter(), Counter()
    fps: dict[str, list[str]] = defaultdict(list)
    t0 = time.time()
    for d in docs:
        for field in ("text", "title"):
            text = d.get(field) or ""
            dets = an.detect(text, d.get("title" if field == "text" else "text") or "")
            spans = gt.get((d["doc_id"], field), [])
            for s in spans:
                ln = s["end"] - s["start"]
                cov = sum(_overlap(s["start"], s["end"], x.start, x.end) for x in dets)
                typed = sum(_overlap(s["start"], s["end"], x.start, x.end) for x in dets if x.type == s["type"])
                gt_total[s["type"]] += 1
                gt_found[s["type"]] += cov >= 0.5 * ln
                gt_typed[s["type"]] += typed >= 0.5 * ln
            for x in dets:
                det_total[x.type] += 1
                if any(_overlap(s["start"], s["end"], x.start, x.end) for s in spans):
                    det_tp[x.type] += 1
                elif len(fps[x.type]) < show_fp:
                    fps[x.type].append(f"{d['doc_id']}: {text[max(0, x.start - 25):x.end + 25]!r}")
    types = sorted(set(gt_total) | set(det_total))
    rows = {}
    for t in types:
        rows[t] = {"gt": gt_total[t], "recall_any": round(gt_found[t] / gt_total[t], 4) if gt_total[t] else None,
                   "recall_typed": round(gt_typed[t] / gt_total[t], 4) if gt_total[t] else None,
                   "detections": det_total[t],
                   "precision": round(det_tp[t] / det_total[t], 4) if det_total[t] else None}
    tot_gt, tot_det = sum(gt_total.values()), sum(det_total.values())
    summary = {"gt_spans": tot_gt, "detections": tot_det,
               "recall_any": round(sum(gt_found.values()) / tot_gt, 4) if tot_gt else None,
               "recall_typed": round(sum(gt_typed.values()) / tot_gt, 4) if tot_gt else None,
               "precision": round(sum(det_tp.values()) / tot_det, 4) if tot_det else None,
               "docs": len(docs), "seconds": round(time.time() - t0, 1)}
    if show_fp:
        for t, ex in fps.items():
            print(f"  FP sample {t}:", *ex, sep="\n    ")
    return {"tenant": tenant, "summary": summary, "by_type": rows}


def to_markdown(results: list[dict]) -> str:
    lines = ["# Redaction eval", ""]
    for r in results:
        s = r["summary"]
        lines += [f"## {r['tenant']}", "",
                  f"{s['gt_spans']} injected PII spans across {s['docs']} documents. Overall recall (any type) "
                  f"**{s['recall_any']:.1%}**, typed recall {s['recall_typed']:.1%}, precision **{s['precision']:.1%}**.",
                  "", "| Type | Ground-truth spans | Recall (any type) | Recall (typed) | Detections | Precision |",
                  "|---|---|---|---|---|---|"]
        for t, row in r["by_type"].items():
            f = lambda v: "-" if v is None else f"{v:.1%}"  # noqa: E731
            lines.append(f"| {t} | {row['gt']} | {f(row['recall_any'])} | {f(row['recall_typed'])} | "
                         f"{row['detections']} | {f(row['precision'])} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show = 8 if "--show-fp" in sys.argv else 0
    results = [evaluate(t, show) for t in (args or ["halcyon", "brightwater"])]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "redaction.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (OUT / "redaction.md").write_text(to_markdown(results), encoding="utf-8")
    for r in results:
        print(r["tenant"], json.dumps(r["summary"]))
        for t, row in r["by_type"].items():
            print(f"  {t:15s} {json.dumps(row)}")


if __name__ == "__main__":
    main()
