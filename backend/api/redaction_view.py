"""Ingestion & Redaction view: redaction audit totals, the canary scan, redaction-eval results and a no-persist
redaction preview for text a visitor types in (nothing is tokenized, stored or sent to a model)."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
import time
from collections import Counter
from functools import lru_cache

from backend.config import REPO_ROOT
from backend.graph.db import brain_graph, execute, get_db
from backend.ingest.sources import load_world
from backend.redaction.canary import CanaryScanner, scan_graphs

POLICY = [
    {"category": "Employee name, work email, title, team", "handling": "kept (business directory)"},
    {"category": "Client name, SSN, DOB, home address, phone, personal email",
     "handling": "pseudonymized to a consistent token, e.g. \u27e8PHONE_0042\u27e9"},
    {"category": "Account, routing and card numbers, IBAN, SWIFT, EIN",
     "handling": "pseudonymized; detected with checksum validators (Luhn, ABA, IBAN mod-97)"},
    {"category": "Employee salary, medical leave, home address", "handling": "pseudonymized"},
    {"category": "MNPI (deal codenames, unannounced terms)",
     "handling": "not redacted: classified sensitivity=MNPI and restricted to the deal team"},
]


def counts_of(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v:
        try:
            out = json.loads(v)
            return out if isinstance(out, dict) else {}
        except ValueError:
            return {}
    return {}


@lru_cache(maxsize=1)
def _eval_results() -> list:
    p = REPO_ROOT / "evals" / "results" / "redaction.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def eval_for(tenant: str) -> dict | None:
    return next((r for r in _eval_results() if r.get("tenant") == tenant), None)


def _canary_key(tenant: str) -> str:
    return f"fb:canary:{tenant}"


async def run_canary(tenant: str) -> dict:
    """Scan every string property in the tenant's brain graph and all of its memory graphs for raw PII."""
    w = load_world(tenant)
    scanner = CanaryScanner([w["domain"]], [c["name"] for c in w["clients"]])
    graphs = [g for g in await get_db().list_graphs()
              if g == brain_graph(tenant) or g.startswith(f"mem__{tenant}__")]
    t0 = time.perf_counter()
    rep = (await scan_graphs(get_db(), scanner, graphs)).as_dict()
    out = {**rep, "graphs": len(graphs), "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "seconds": round(time.perf_counter() - t0, 1)}
    await execute("SET", _canary_key(tenant), json.dumps(out))
    return out


async def last_canary(tenant: str) -> dict | None:
    raw = await execute("GET", _canary_key(tenant))
    return json.loads(raw) if raw else None


_ANALYZERS: dict = {}
_ANALYZER_LOCK = threading.Lock()


def _analyzer(tenant: str):
    with _ANALYZER_LOCK:
        if tenant not in _ANALYZERS:
            from backend.redaction.pipeline import analyzer_for_world  # Presidio + spaCy: load lazily
            from datagen.world import World

            _ANALYZERS[tenant] = analyzer_for_world(World.load(REPO_ROOT / "data" / "world" / tenant / "world.json"))
        return _ANALYZERS[tenant]


def _preview_sync(tenant: str, text: str) -> dict:
    dets = sorted(_analyzer(tenant).detect(text), key=lambda d: (d.start, -d.end))
    out: list[str] = []
    spans: list[dict] = []
    counts: Counter = Counter()
    pos = 0
    for d in dets:
        if d.start < pos:
            continue
        out.append(text[pos:d.start])
        out.append(f"\u27e8{d.type}\u27e9")
        spans.append({"type": d.type, "start": d.start, "end": d.end, "score": round(float(d.score), 2),
                      "recognizer": d.recognizer})
        counts[d.type] += 1
        pos = d.end
    out.append(text[pos:])
    return {"redacted": "".join(out), "spans": spans, "counts": dict(counts),
            "note": "Preview only: nothing was tokenized, stored or sent to a model."}


async def preview(tenant: str, text: str) -> dict:
    return await asyncio.to_thread(_preview_sync, tenant, text)
