"""Redact documents: detect PII, replace each span with its consistent vault token, record a redaction event."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from backend.redaction.recognizers import Detection, PiiAnalyzer
from backend.redaction.vault import Vault


@dataclass
class Redacted:
    text: str
    items: list[dict] = field(default_factory=list)  # {type, token, start, end} in redacted-text offsets
    counts: Counter = field(default_factory=Counter)


def analyzer_for_world(w) -> PiiAnalyzer:
    """Build the tenant analyzer from the world model: CRM client names, employee directory, system names."""
    from datagen.config import TENANTS

    keep = ([s.name for s in w.systems] + [t.name for t in w.teams] + [c.name for c in w.counterparties]
            + [d.codename for d in w.deals] + [d.target for d in w.deals] + [ch.name for ch in w.channels])
    return PiiAnalyzer(TENANTS[w.tenant].domain, [c.name for c in w.clients], [p.name for p in w.people], keep)


class Redactor:
    def __init__(self, analyzer: PiiAnalyzer, vault: Vault):
        self.analyzer = analyzer
        self.vault = vault

    def detect(self, text: str, hint: str = "") -> list[Detection]:
        return self.analyzer.detect(text, hint)

    async def redact(self, text: str, hint: str = "") -> Redacted:
        if not text:
            return Redacted(text or "")
        out: list[str] = []
        res = Redacted("")
        pos = cur = 0
        for d in self.analyzer.detect(text, hint):
            out.append(text[pos:d.start])
            cur += d.start - pos
            tok = await self.vault.tokenize(d.type, text[d.start:d.end])
            res.items.append({"type": d.type, "token": tok, "start": cur, "end": cur + len(tok)})
            res.counts[d.type] += 1
            out.append(tok)
            cur += len(tok)
            pos = d.end
        out.append(text[pos:])
        res.text = "".join(out)
        return res
