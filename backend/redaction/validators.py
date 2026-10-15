"""Checksum validators and generators for financial identifiers (Luhn, ABA routing, IBAN mod-97).

Shared by the synthetic data generator, the redaction recognizers, the outbound PII guard,
and the canary scan, so every component agrees on what a "valid" identifier is.
"""

from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"\D")
_WS = re.compile(r"\s")


def digits(s: str) -> str:
    return _NON_DIGIT.sub("", s)


def luhn_checksum(number: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(digits(number))):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10


def luhn_valid(number: str) -> bool:
    d = digits(number)
    return 13 <= len(d) <= 19 and luhn_checksum(d) == 0


def luhn_check_digit(partial: str) -> str:
    """Digit to append to `partial` so the result passes Luhn."""
    return str((10 - luhn_checksum(digits(partial) + "0")) % 10)


def _aba_sum(d: str) -> int:
    n = [int(c) for c in d]
    return 3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + (n[8] if len(n) > 8 else 0))


def aba_valid(number: str) -> bool:
    d = digits(number)
    return len(d) == 9 and _aba_sum(d) % 10 == 0


def aba_check_digit(first8: str) -> str:
    d = digits(first8)
    if len(d) != 8:
        raise ValueError("need 8 digits")
    return str((-_aba_sum(d + "0")) % 10)


def _iban_numeric(s: str) -> int:
    return int("".join(str(int(c, 36)) for c in s))


def iban_valid(iban: str) -> bool:
    s = _WS.sub("", iban).upper()
    if not (15 <= len(s) <= 34) or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    return _iban_numeric(s[4:] + s[:4]) % 97 == 1


def iban_check_digits(country: str, bban: str) -> str:
    return f"{98 - _iban_numeric(bban.upper() + country.upper() + '00') % 97:02d}"


def format_iban(iban: str) -> str:
    s = _WS.sub("", iban).upper()
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))
