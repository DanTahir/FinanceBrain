"""Loaders for a tenant's source data.

- Redacted corpus (data/redacted/<tenant>/<kind>.jsonl): the only document text that ever reaches the graph.
- Structured records (data/world/<tenant>/world.json): the firm's systems of record (HR directory, CMDB, core
  banking ledger, deal register, chat membership). Raw PII fields in it are never copied into the graph; client,
  account and counterparty identifiers are replaced by their vault tokens from entities.jsonl.
- Decision log (ground_truth.json "decisions"): the governance register of formal decisions.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from backend.config import REPO_ROOT

DATA = REPO_ROOT / "data"
KINDS = ("email", "slack", "ticket", "doc", "meeting", "crm")
DEFAULT_CUTOFF = "2026-10-01"


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@lru_cache(maxsize=8)
def load_world(tenant: str) -> dict:
    return json.loads((DATA / "world" / tenant / "world.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def load_entity_tokens(tenant: str) -> dict[tuple[str, str], dict[str, str]]:
    """(entity kind, id) -> {PII type: token}, e.g. ('client', 'c_0060') -> {'PHONE': '⟨PHONE_0060⟩', ...}."""
    return {(r["entity"], r["id"]): r["tokens"] for r in _jsonl(DATA / "redacted" / tenant / "entities.jsonl")}


def load_redacted_docs(tenant: str) -> list[dict]:
    docs: list[dict] = []
    for kind in KINDS:
        docs.extend(_jsonl(DATA / "redacted" / tenant / f"{kind}.jsonl"))
    docs.sort(key=lambda d: (d["ts"], d["doc_id"]))
    return docs


@lru_cache(maxsize=8)
def load_decision_log(tenant: str) -> dict:
    p = DATA / "raw" / tenant / "ground_truth.json"
    return json.loads(p.read_text(encoding="utf-8")).get("decisions", {}) if p.is_file() else {}


def cutoff(tenant: str) -> str:
    return load_world(tenant).get("refs", {}).get("cutoff", DEFAULT_CUTOFF)
