"""Canary scan: prove that no raw PII survived redaction, in output files or in any FalkorDB graph.

Checks every string for checksum-validated identifiers (SSN, card/Luhn, IBAN/mod-97, ABA routing, phone; the same
validators the outbound guard uses), plus device fingerprints, non-work email addresses, US street addresses, and
the tenant's known client names. Reports locations and types only, never values.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from backend.llm.guard import find_pii

_DEVICE = re.compile(r"\bDEV-[0-9A-F]{12}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_ADDRESS = re.compile(r"\b\d{1,6} [A-Z][A-Za-z'.]*(?: [A-Z][A-Za-z'.]*){0,4}(?: (?:Apt\.|Suite) \d{1,5})?, "
                      r"[A-Z][A-Za-z'. ]{1,30}?, [A-Z]{2} \d{5}\b")


@dataclass
class CanaryReport:
    strings_scanned: int = 0
    hits: Counter = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)  # "<location>:<type>" only

    @property
    def clean(self) -> bool:
        return not self.hits

    def as_dict(self) -> dict:
        return {"strings_scanned": self.strings_scanned, "hits": sum(self.hits.values()),
                "by_type": dict(self.hits), "examples": self.examples[:20], "clean": self.clean}


class CanaryScanner:
    def __init__(self, work_domains: Iterable[str] = (), client_names: Iterable[str] = ()):
        self.work_domains = {d.lower() for d in work_domains}
        names = sorted({n for n in client_names if n}, key=len, reverse=True)
        self.names_re = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b", re.I) if names else None
        self.report = CanaryReport()

    def _hit(self, where: str, typ: str) -> None:
        self.report.hits[typ] += 1
        if len(self.report.examples) < 50:
            self.report.examples.append(f"{where}:{typ}")

    def scan(self, where: str, s: str) -> None:
        if not isinstance(s, str) or not s:
            return
        self.report.strings_scanned += 1
        for f in find_pii(s):
            self._hit(where, f.type)
        if _DEVICE.search(s):
            self._hit(where, "DEVICE_ID")
        for m in _EMAIL.finditer(s):
            if m.group(1).lower() not in self.work_domains:
                self._hit(where, "EMAIL")
        if _ADDRESS.search(s):
            self._hit(where, "ADDRESS")
        if self.names_re is not None and self.names_re.search(s):
            self._hit(where, "CLIENT_NAME")

    def scan_obj(self, where: str, obj) -> None:
        """Recursively scan every string inside a JSON-like object."""
        if isinstance(obj, str):
            self.scan(where, obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                self.scan_obj(f"{where}.{k}", v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                self.scan_obj(where, v)


async def scan_graphs(db, scanner: CanaryScanner, graphs: list[str] | None = None, page: int = 2000) -> CanaryReport:
    """Scan every string property of every node and edge in the given FalkorDB graphs (default: all)."""
    names = graphs if graphs is not None else await db.list_graphs()
    for gname in names:
        g = db.select_graph(gname)
        for what, q in (("node", "MATCH (n) RETURN n SKIP $skip LIMIT $lim"),
                        ("edge", "MATCH ()-[e]->() RETURN e SKIP $skip LIMIT $lim")):
            skip = 0
            while True:
                res = await g.ro_query(q, {"skip": skip, "lim": page})
                rows = res.result_set
                for (item,) in rows:
                    label = getattr(item, "labels", None) or [getattr(item, "relation", what)]
                    scanner.scan_obj(f"{gname}:{label[0] if label else what}:{getattr(item, 'id', '?')}",
                                     dict(item.properties))
                if len(rows) < page:
                    break
                skip += page
    return scanner.report
