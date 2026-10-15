"""Synthetic PII values and placeholder injection with span-level ground truth.

LLM content writers never see PII values. They write placeholders such as
``{{CLIENT_PHONE:c_0042}}``; :func:`fill_placeholders` swaps in the synthetic value locally and
records every injected span (offset, type, value) for the redaction eval.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Callable

from faker import Faker

from backend.redaction.validators import (
    aba_check_digit,
    digits,
    format_iban,
    iban_check_digits,
    luhn_check_digit,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z_]+)\s*:\s*([A-Za-z0-9_\-]+)\s*\}\}")

# placeholder name -> (canonical PII type, entity kind it takes, description for prompts)
PLACEHOLDERS: dict[str, tuple[str, str, str]] = {
    "CLIENT_NAME": ("CLIENT_NAME", "client", "client's full name (person or business)"),
    "CLIENT_PHONE": ("PHONE", "client", "client's phone number"),
    "CLIENT_EMAIL": ("EMAIL", "client", "client's personal email"),
    "CLIENT_ADDRESS": ("ADDRESS", "client", "client's home or business address"),
    "CLIENT_SSN": ("SSN", "client", "client's social security number (individuals only)"),
    "CLIENT_DOB": ("DOB", "client", "client's date of birth (individuals only)"),
    "CLIENT_EIN": ("EIN", "client", "business client's EIN (businesses only)"),
    "CLIENT_DEVICE": ("DEVICE_ID", "client", "device fingerprint seen on the client's online banking login"),
    "ACCOUNT_NUMBER": ("ACCOUNT_NUMBER", "account", "full account number"),
    "ROUTING_NUMBER": ("ROUTING_NUMBER", "account", "ABA routing number of the account"),
    "CARD_NUMBER": ("CARD_NUMBER", "account", "debit card number on the account"),
    "CP_ACCOUNT": ("ACCOUNT_NUMBER", "counterparty", "external counterparty account number"),
    "CP_IBAN": ("IBAN", "counterparty", "external counterparty IBAN"),
    "CP_SWIFT": ("SWIFT", "counterparty", "external counterparty bank SWIFT/BIC"),
    "EMP_SALARY": ("SALARY", "employee", "employee's salary"),
    "EMP_HOME_ADDRESS": ("ADDRESS", "employee", "employee's home address"),
    "EMP_MEDICAL": ("MEDICAL", "employee", "employee's medical leave detail"),
}

PII_TYPES = sorted({v[0] for v in PLACEHOLDERS.values()})

FALLBACK_TEXT = {
    "client": "the client",
    "account": "the account",
    "counterparty": "the counterparty",
    "employee": "the employee",
}

_MEDICAL_NOTES = [
    "medical leave for knee surgery",
    "short-term disability leave for a back injury",
    "medical leave following a cardiac procedure",
    "intermittent FMLA leave for migraine treatment",
    "medical leave for chemotherapy",
    "medical leave after a hip replacement",
]

_EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "icloud.com", "proton.me", "aol.com"]

_IBAN_FORMATS = {  # country -> list of (kind, length) segments of the BBAN
    "GB": [("A", 4), ("9", 14)],
    "DE": [("9", 18)],
    "NL": [("A", 4), ("9", 10)],
    "CY": [("9", 8), ("9", 16)],
    "AE": [("9", 19)],
    "FR": [("9", 23)],
}


class PiiFactory:
    """Deterministic generator of synthetic identifiers. Never reuses a value unless asked."""

    def __init__(self, rng: random.Random, fake: Faker):
        self.rng = rng
        self.fake = fake
        self._used: set[str] = set()

    def _unique(self, gen: Callable[[], str]) -> str:
        for _ in range(1000):
            v = gen()
            if v not in self._used:
                self._used.add(v)
                return v
        raise RuntimeError("could not generate a unique value")

    def _d(self, n: int, first_nonzero: bool = False) -> str:
        s = "".join(str(self.rng.randint(0, 9)) for _ in range(n))
        if first_nonzero and s[0] == "0":
            s = str(self.rng.randint(1, 9)) + s[1:]
        return s

    def ssn(self) -> str:
        def g() -> str:
            area = self.rng.randint(1, 899)
            if area == 666:
                area = 667
            return f"{area:03d}-{self.rng.randint(1, 99):02d}-{self.rng.randint(1, 9999):04d}"

        return self._unique(g)

    def dob(self, min_age: int = 23, max_age: int = 86) -> str:
        ref = date(2026, 7, 1)
        days = self.rng.randint(min_age * 365, max_age * 365)
        return (ref - timedelta(days=days)).isoformat()

    def phone(self) -> str:
        def g() -> str:
            while True:
                area = f"{self.rng.randint(2, 9)}{self.rng.randint(0, 9)}{self.rng.randint(0, 9)}"
                if area[1:] != "11" and area not in {"555", "800", "888", "877", "866", "900"}:
                    break
            exch = f"{self.rng.randint(2, 9)}{self.rng.randint(0, 9)}{self.rng.randint(0, 9)}"
            if exch[1:] == "11":
                exch = exch[0] + "23"
            return f"({area}) {exch}-{self._d(4)}"

        return self._unique(g)

    def personal_email(self, name: str) -> str:
        parts = [p for p in re.split(r"[^a-z]+", name.lower()) if p]
        first, last = (parts[0], parts[-1]) if len(parts) > 1 else (parts[0] if parts else "user", "x")

        def g() -> str:
            style = self.rng.randint(0, 3)
            local = [f"{first}.{last}", f"{first[0]}{last}", f"{first}{last[0]}", f"{last}.{first}"][style]
            return f"{local}{self.rng.randint(1, 99)}@{self.rng.choice(_EMAIL_DOMAINS)}"

        return self._unique(g)

    def address(self) -> str:
        return self._unique(
            lambda: f"{self.fake.street_address()}, {self.fake.city()}, {self.fake.state_abbr()} {self.fake.zipcode()}"
        )

    def account_number(self) -> str:
        return self._unique(lambda: self._d(self.rng.choice([10, 11, 12]), first_nonzero=True))

    def routing_number(self, prefix8: str | None = None) -> str:
        def g() -> str:
            p = prefix8 or f"{self.rng.choice(list(range(1, 13)) + list(range(21, 33))):02d}{self._d(6)}"
            return p + aba_check_digit(p)

        if prefix8:
            return prefix8 + aba_check_digit(prefix8)
        return self._unique(g)

    def card_number(self) -> str:
        def g() -> str:
            partial = "4" + self._d(14)
            return partial + luhn_check_digit(partial)

        return self._unique(g)

    def iban(self, country: str = "GB") -> str:
        segs = _IBAN_FORMATS[country]

        def g() -> str:
            bban = "".join(
                "".join(self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(n)) if kind == "A" else self._d(n)
                for kind, n in segs
            )
            return format_iban(country + iban_check_digits(country, bban) + bban)

        return self._unique(g)

    def swift(self, country: str) -> str:
        def g() -> str:
            bank = "".join(self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4))
            loc = self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") + self.rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ")
            return bank + country + loc + self.rng.choice(["", "XXX"])

        return self._unique(g)

    def ein(self) -> str:
        return self._unique(lambda: f"{self.rng.randint(10, 98):02d}-{self._d(7)}")

    def device_id(self) -> str:
        return self._unique(lambda: "DEV-" + "".join(self.rng.choice("0123456789ABCDEF") for _ in range(12)))

    def salary(self, low: int, high: int) -> str:
        return f"${round(self.rng.randint(low, high), -3):,}"

    def medical_note(self) -> str:
        return self.rng.choice(_MEDICAL_NOTES)


@dataclass
class Span:
    doc_id: str
    start: int
    end: int
    type: str
    placeholder: str
    entity_id: str
    value: str  # as rendered in the text
    canonical: str  # canonical stored value

    def to_dict(self) -> dict:
        return asdict(self)


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]


def render_value(pii_type: str, canonical: str, variant: int) -> str:
    """Render a canonical value in one of several realistic surface formats."""
    if pii_type == "PHONE":
        d = digits(canonical)
        a, e, l = d[:3], d[3:6], d[6:]
        return [f"({a}) {e}-{l}", f"{a}-{e}-{l}", f"{a}.{e}.{l}", f"+1 {a} {e} {l}"][variant % 4]
    if pii_type == "CARD_NUMBER":
        d = digits(canonical)
        groups = [d[i : i + 4] for i in range(0, len(d), 4)]
        return [" ".join(groups), "-".join(groups), d][variant % 3]
    if pii_type == "DOB":
        y, m, dd = canonical.split("-")
        return [canonical, f"{m}/{dd}/{y}", f"{_MONTHS[int(m) - 1]} {int(dd)}, {y}"][variant % 3]
    if pii_type == "IBAN":
        return [canonical, canonical.replace(" ", "")][variant % 2]
    return canonical


def _variant(doc_id: str, idx: int) -> int:
    return int(hashlib.sha256(f"{doc_id}#{idx}".encode()).hexdigest()[:8], 16)


def fill_placeholders(
    text: str, doc_id: str, resolve: Callable[[str, str], str | None]
) -> tuple[str, list[Span], list[str]]:
    """Replace ``{{TYPE:entity}}`` placeholders. Returns (text, spans, unresolved placeholders)."""
    out: list[str] = []
    spans: list[Span] = []
    unresolved: list[str] = []
    pos = 0
    cursor = 0
    for idx, m in enumerate(PLACEHOLDER_RE.finditer(text)):
        out.append(text[pos : m.start()])
        cursor += m.start() - pos
        ph, eid = m.group(1), m.group(2)
        spec = PLACEHOLDERS.get(ph)
        canonical = resolve(ph, eid) if spec else None
        if spec is None or canonical is None:
            unresolved.append(m.group(0))
            repl = FALLBACK_TEXT.get(spec[1], "") if spec else ""
            out.append(repl)
            cursor += len(repl)
        else:
            rendered = render_value(spec[0], canonical, _variant(doc_id, idx))
            spans.append(Span(doc_id, cursor, cursor + len(rendered), spec[0], ph, eid, rendered, canonical))
            out.append(rendered)
            cursor += len(rendered)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), spans, unresolved
