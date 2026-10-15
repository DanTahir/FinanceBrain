"""Redact the synthetic corpus for one tenant and canary-scan the result.

    python -m backend.redaction.run --tenant halcyon [--reset-vault]

Order is deterministic (structured CRM/KYC records first, then documents by (ts, doc_id)), so token numbers are
reproducible. Writes data/redacted/<tenant>/: <kind>.jsonl, entities.jsonl (tokens per client/account/
counterparty), manifest.json (counts, timing, canary result). Exit code 1 if the canary finds anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import Counter

from backend.config import REPO_ROOT
from backend.redaction.canary import CanaryScanner
from backend.redaction.pipeline import Redactor, analyzer_for_world
from backend.redaction.vault import Vault, redis_client
from datagen.config import TENANTS
from datagen.pii import PLACEHOLDERS
from datagen.world import World

log = logging.getLogger("redaction.run")
KINDS = ("slack", "email", "ticket", "doc", "meeting", "crm")


def load_raw(tenant: str) -> list[dict]:
    d = REPO_ROOT / "data" / "raw" / tenant
    docs = [json.loads(line) for k in KINDS for line in (d / f"{k}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return sorted(docs, key=lambda x: (x["ts"], x["doc_id"]))


async def tokenize_entities(w: World, vault: Vault) -> list[dict]:
    """Tokens for identifiers held in structured records (these become PiiToken nodes in the graph)."""
    groups = (("client", sorted(w.clients, key=lambda c: c.id)), ("account", sorted(w.accounts, key=lambda a: a.id)),
              ("counterparty", sorted(w.counterparties, key=lambda c: c.id)))
    out = []
    for kind, items in groups:
        phs = [(ph, spec[0]) for ph, spec in PLACEHOLDERS.items() if spec[1] == kind]
        for it in items:
            toks = {}
            for ph, typ in phs:
                v = w.resolve_pii(ph, it.id)
                if v:
                    toks[typ] = await vault.tokenize(typ, v)
            out.append({"entity": kind, "id": it.id, "tokens": toks})
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, choices=sorted(TENANTS))
    ap.add_argument("--reset-vault", action="store_true", help="drop existing tokens first (deterministic rebuild)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    t0 = time.time()
    w = World.load(REPO_ROOT / "data" / "world" / args.tenant / "world.json")
    r = redis_client()
    if args.reset_vault:
        await Vault.reset(r, args.tenant)
    vault = await Vault.open(r, args.tenant)
    red = Redactor(analyzer_for_world(w), vault)
    ents = await tokenize_entities(w, vault)
    docs = load_raw(args.tenant)
    out: dict[str, list[dict]] = {k: [] for k in KINDS}
    totals: Counter[str] = Counter()
    for i, d in enumerate(docs):
        body = await red.redact(d["text"], hint=d.get("title", ""))
        title = await red.redact(d.get("title", ""), hint=d["text"])
        counts = body.counts + title.counts
        totals.update(counts)
        rec = {k: v for k, v in d.items() if k not in ("text", "title")}
        rec.update(title=title.text, text=body.text, redactions=body.items,
                   title_redactions=title.items, redaction_counts=dict(counts))
        out[d["kind"]].append(rec)
        if (i + 1) % 500 == 0:
            log.info("redacted %d/%d docs (%.0fs)", i + 1, len(docs), time.time() - t0)
    od = REPO_ROOT / "data" / "redacted" / args.tenant
    od.mkdir(parents=True, exist_ok=True)
    for k, rows in out.items():
        with (od / f"{k}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (od / "entities.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for e in ents:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    scanner = CanaryScanner([TENANTS[args.tenant].domain], [c.name for c in w.clients])
    for k, rows in out.items():
        for row in rows:
            scanner.scan_obj(f"{k}:{row['doc_id']}", row)
    for e in ents:
        scanner.scan_obj(f"entity:{e['id']}", e)
    canary = scanner.report.as_dict()
    manifest = {"tenant": args.tenant, "docs": len(docs), "redactions_by_type": dict(totals.most_common()),
                "redactions_total": sum(totals.values()), "entities": len(ents), "vault": await vault.stats(),
                "canary": canary, "seconds": round(time.time() - t0, 1)}
    (od / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    await r.aclose()
    log.info("redactions: %s", manifest["redactions_by_type"])
    log.info("vault: %s", manifest["vault"])
    log.info("canary: %s", canary)
    if not canary["clean"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
