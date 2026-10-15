"""Print a quick sample of a generated corpus (read-only QA helper).

    python scripts/peek_raw.py brightwater [--n 2] [--chars 700]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "raw"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tenant")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--chars", type=int, default=700)
    ap.add_argument("--beats-only", action="store_true")
    args = ap.parse_args()
    d = ROOT / args.tenant
    for kind in ("slack", "email", "ticket", "doc", "meeting", "crm"):
        p = d / f"{kind}.jsonl"
        if not p.is_file():
            continue
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.beats_only:
            rows = [r for r in rows if r.get("beat_id")]
        print(f"===== {kind}: {len(rows)} rows, sensitivity {dict(Counter(r.get('sensitivity') for r in rows))}")
        for r in rows[: args.n]:
            text = r.pop("text")
            print(json.dumps(r, ensure_ascii=False)[: args.chars])
            print("TEXT:", text[: args.chars].replace("\n", " / "))
            print()
    spans = d / "pii_spans.jsonl"
    if spans.is_file():
        first = spans.read_text(encoding="utf-8").splitlines()[:2]
        print("spans sample:", *first, sep="\n")
    m = d / "manifest.json"
    if m.is_file():
        man = json.loads(m.read_text(encoding="utf-8"))
        print("counts:", man["counts"], "live:", man["live_feed_counts"])


if __name__ == "__main__":
    main()
