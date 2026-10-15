"""Outbound PII guard: last line of defence before any text leaves for OpenRouter.

Every prompt and embedding input is scanned for high-confidence raw PII patterns (SSN,
Luhn-valid card numbers, mod-97-valid IBANs, ABA routing numbers in context, US phone numbers).
If anything is found the call is blocked. Findings carry types and offsets only, never values.
The same detector powers the post-ingestion canary scan.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Iterator

from backend.redaction.validators import aba_valid, iban_valid, luhn_valid

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Finding:
    type: str
    start: int
    end: int


class OutboundPIIBlocked(Exception):
    def __init__(self, where: str, counts: dict[str, int]):
        self.where = where
        self.counts = counts
        super().__init__(f"outbound PII guard blocked {where}: {counts}")


_SSN = re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])")
_CARD = re.compile(r"(?<!\d)(?<!\d[ -])\d(?:[ -]?\d){12,18}(?![ -]?\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_ROUTING = re.compile(r"(?<!\d)\d{9}(?!\d)")
_ROUTING_CTX = re.compile(r"routing|aba|rtn|transit", re.I)
_PHONE = re.compile(
    r"(?<![\w+])(?:\+1[ .-]?)?(?:\([2-9]\d{2}\) ?|[2-9]\d{2}[ .-])[2-9]\d{2}[ .-]\d{4}(?![\d])"
)

BLOCKED_TOTAL = 0
_BLOCKED_BY_TYPE: Counter[str] = Counter()


def _iban_findings(text: str) -> Iterator[Finding]:
    for m in _IBAN.finditer(text):
        s = m.group(0)
        # The greedy pattern can swallow a following word; shrink until a valid IBAN remains.
        for end in range(len(s), 14, -1):
            if s[end - 1].isalnum() and iban_valid(s[:end]):
                yield Finding("IBAN", m.start(), m.start() + end)
                break


def find_pii(text: str) -> list[Finding]:
    """Detect raw high-confidence PII. Returns non-overlapping findings sorted by offset."""
    if not text:
        return []
    found: list[Finding] = [Finding("SSN", m.start(), m.end()) for m in _SSN.finditer(text)]
    found += [Finding("CARD_NUMBER", m.start(), m.end()) for m in _CARD.finditer(text) if luhn_valid(m.group(0))]
    found += list(_iban_findings(text))
    for m in _ROUTING.finditer(text):
        if aba_valid(m.group(0)) and _ROUTING_CTX.search(text[max(0, m.start() - 40) : m.start()]):
            found.append(Finding("ROUTING_NUMBER", m.start(), m.end()))
    found += [Finding("PHONE", m.start(), m.end()) for m in _PHONE.finditer(text)]
    kept: list[Finding] = []
    for f in sorted(found, key=lambda f: (-(f.end - f.start), f.start)):
        if all(f.end <= k.start or f.start >= k.end for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda f: f.start)


def guard_enabled() -> bool:
    return os.environ.get("FB_OUTBOUND_GUARD", "1") != "0"


def guard_stats() -> dict:
    return {"blocked_total": BLOCKED_TOTAL, "by_type": dict(_BLOCKED_BY_TYPE)}


def check_outbound(texts: Iterable[str], where: str) -> None:
    """Raise OutboundPIIBlocked if any text contains raw PII."""
    global BLOCKED_TOTAL
    if not guard_enabled():
        return
    counts: Counter[str] = Counter()
    for t in texts:
        if t:
            counts.update(f.type for f in find_pii(t))
    if counts:
        BLOCKED_TOTAL += 1
        _BLOCKED_BY_TYPE.update(counts)
        log.error("outbound PII guard blocked %s: %s", where, dict(counts))
        raise OutboundPIIBlocked(where, dict(counts))


def iter_message_texts(messages: list[dict]) -> Iterator[str]:
    """Yield every string carried by OpenAI-format chat messages."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    yield part["text"]
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if fn and isinstance(fn.get("arguments"), str):
                yield fn["arguments"]


if __name__ == "__main__":
    positives = {
        "card 4111 1111 1111 1111 on file": "CARD_NUMBER",
        "card 4111-1111-1111-1111": "CARD_NUMBER",
        "ssn 123-45-6789.": "SSN",
        "pay to GB82 WEST 1234 5698 7654 32 today": "IBAN",
        "pay to GB82WEST12345698765432": "IBAN",
        "call (415) 829-3301": "PHONE",
        "call 415-829-3301": "PHONE",
        "call 415.829.3301": "PHONE",
        "call +1 415 829 3301": "PHONE",
        "call +1-415-829-3301": "PHONE",
        "routing 021000021": "ROUTING_NUMBER",
    }
    for text, typ in positives.items():
        types = [f.type for f in find_pii(text)]
        assert types == [typ], (text, types)
    negatives = [
        "token ⟨PHONE_0042⟩ and ⟨SSN_0007⟩", "client c_0042 account a_00017 tx t_000123", "see COMP-112",
        "on 2026-10-14 at 10:14", "wire of $48,000.00", "bad card 4111 1111 1111 1112", "account 021000021",
        "EIN-like 12-3456789", "ticket FRAUD-2291 score 0.91", "DEV-7F3A9C21B4E0",
    ]
    for text in negatives:
        assert find_pii(text) == [], (text, find_pii(text))
    try:
        check_outbound(["ok text", "ssn 123-45-6789"], where="selftest")
        raise AssertionError("guard did not block")
    except OutboundPIIBlocked as e:
        assert e.counts == {"SSN": 1}
    print("guard self-test OK")
