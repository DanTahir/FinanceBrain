"""Verify invariants of a generated corpus in data/raw/<tenant>/ (no LLM calls).

    python scripts/verify_corpus.py halcyon brightwater

Checks:
  1. every storyline beat produced exactly one document
  2. every PII span's offsets point at exactly its value
  3. no PII-like string (SSN/card/IBAN/routing/phone) exists outside a recorded span
  4. author / recipient / attendee / channel ids resolve; thread_id and reply_to point at existing docs
  5. MNPI containment: deal codenames appear only in MNPI-classified docs, and the only MNPI docs visible
     outside the deal team are the planted leak beats
  6. decision ground truth is present (crypto pause + resume, with justification docs)
Exit code 1 if any check fails.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.llm.guard import find_pii  # noqa: E402
from datagen.storylines import build_beats  # noqa: E402
from datagen.world import World  # noqa: E402

CODENAME_RE = re.compile(r"\bProject Falcon\b|\bNorthwind\b")


def verify(tenant: str) -> list[str]:
    errs: list[str] = []
    d = ROOT / "data" / "raw" / tenant
    w = World.load(ROOT / "data" / "world" / tenant / "world.json")
    docs = [json.loads(line) for k in ("slack", "email", "ticket", "doc", "meeting", "crm")
            for line in (d / f"{k}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {x["doc_id"]: x for x in docs}
    spans = [json.loads(line) for line in (d / "pii_spans.jsonl").read_text(encoding="utf-8").splitlines() if line]
    gt = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
    people = {p.id for p in w.people}
    chans = {c.id for c in w.channels}

    # 1. beats
    beats = build_beats(w)
    got = Counter(x["beat_id"] for x in docs if x.get("beat_id"))
    missing = [b.id for b in beats if got[b.id] != 1]
    if missing:
        errs.append(f"beats missing or duplicated: {missing}")

    # 2. span offsets
    bad = 0
    for s in spans:
        doc = by_id.get(s["doc_id"])
        field = s.get("field", "text")
        if doc is None:
            bad += 1
            continue
        txt = doc.get("title", "") if field == "title" else doc["text"]
        if txt[s["start"]: s["end"]] != s["value"]:
            bad += 1
    if bad:
        errs.append(f"{bad} spans whose offsets do not match their value")

    # 3. PII outside spans
    covered: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for s in spans:
        covered.setdefault((s["doc_id"], s.get("field", "text")), []).append((s["start"], s["end"]))
    stray = []
    for x in docs:
        for field in ("text", "title"):
            for f in find_pii(x.get(field, "")):
                rng = covered.get((x["doc_id"], field), [])
                if not any(a <= f.start and f.end <= b for a, b in rng):
                    stray.append(f"{x['doc_id']}:{field}:{f.type}")
    if stray:
        errs.append(f"{len(stray)} PII-like strings outside recorded spans, e.g. {stray[:5]}")

    # 4. references
    ref_errs = Counter()
    for x in docs:
        for pid in [x.get("author_id"), *x.get("recipients", []), *x.get("cc", []), *x.get("attendees", [])]:
            if pid and pid not in people:
                ref_errs["person"] += 1
        if x.get("channel_id") and x["channel_id"] not in chans:
            ref_errs["channel"] += 1
        for k in ("thread_id", "reply_to"):
            if x.get(k) and x[k] not in by_id:
                ref_errs[k] += 1
        if not x.get("author_id") and not x.get("external_author"):
            ref_errs["no_author"] += 1
    if ref_errs:
        errs.append(f"dangling references: {dict(ref_errs)}")

    # 5. MNPI containment
    leaks = set(gt.get("mnpi_outside_deal_team", []))
    falcon = next((x for x in w.deals if x.id == "deal_falcon"), None)
    deal_team = set(falcon.team_ids) | {w.cast["cco"]} if falcon else set()
    falcon_ch = {c.id for c in w.channels if c.sensitivity == "MNPI"}
    for x in docs:
        if CODENAME_RE.search(x["text"] + x.get("title", "")) and x.get("sensitivity") != "MNPI":
            errs.append(f"{x['doc_id']} mentions a deal codename but is not classified MNPI")
        if x.get("sensitivity") == "MNPI" and x["doc_id"] not in leaks:
            acl = x["acl"]
            if acl.get("public") or acl.get("teams"):
                errs.append(f"{x['doc_id']} is MNPI but broadly accessible: {acl}")
            elif acl.get("channel") and acl["channel"] not in falcon_ch:
                errs.append(f"{x['doc_id']} is MNPI in non-MNPI channel {acl['channel']}")
            elif acl.get("people") and not set(acl["people"]) <= deal_team:
                errs.append(f"{x['doc_id']} is MNPI and shared outside the deal team: {acl['people']}")
    if tenant == "halcyon" and len(leaks) != 2:
        errs.append(f"expected 2 planted Falcon leak docs, got {sorted(leaks)}")

    # 6. decisions
    if tenant == "halcyon":
        for dec in ("dec_crypto_pause", "dec_crypto_resume"):
            g = gt["decisions"].get(dec)
            if not g or not g.get("decided_in") or not g.get("justified_by"):
                errs.append(f"decision {dec} incomplete in ground truth: {g}")

    kinds = Counter(x["kind"] for x in docs)
    live = sum(1 for x in docs if x["live_feed"])
    print(f"[{tenant}] {len(docs)} docs {dict(kinds)}; live-feed {live}; {len(spans)} PII spans; "
          f"{len(beats)} beats; MNPI docs {len(gt['mnpi_docs'])}; checks failed: {len(errs)}")
    return errs


def main() -> None:
    tenants = sys.argv[1:] or ["halcyon", "brightwater"]
    failed = False
    for t in tenants:
        for e in verify(t):
            failed = True
            print(f"  FAIL [{t}] {e}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
