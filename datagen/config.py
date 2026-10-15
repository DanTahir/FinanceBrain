"""Tenant specs, scale, and timeline for the synthetic corpus. Everything here is fictional."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

START = date(2026, 7, 1)
CUTOFF = date(2026, 10, 1)  # initial load: events before this date; live feed: on/after
END = date(2026, 10, 14)


@dataclass(frozen=True)
class Scale:
    clients: int
    accounts: int
    transactions: int
    counterparties: int
    slack: int
    email: int
    tickets: int
    docs: int
    meetings: int
    crm: int


@dataclass(frozen=True)
class TenantSpec:
    id: str
    name: str
    domain: str
    seed: int
    scale: Scale
    teams: tuple[tuple[str, str, int], ...]  # (team_id, display name, headcount)
    routing_prefix: str  # first 8 digits of the tenant's own ABA routing number


TENANTS: dict[str, TenantSpec] = {
    "halcyon": TenantSpec(
        id="halcyon",
        name="Halcyon Ridge Capital",
        domain="halcyonridge.com",
        seed=1729,
        scale=Scale(
            clients=300, accounts=600, transactions=5000, counterparties=60,
            slack=1500, email=400, tickets=150, docs=40, meetings=20, crm=150,
        ),
        teams=(
            ("exec", "Executive Office", 4),
            ("trading", "Trading", 12),
            ("wealth", "Wealth Management", 12),
            ("lending", "Lending", 10),
            ("ma", "M&A Advisory", 8),
            ("compliance", "Compliance", 9),
            ("risk", "Risk", 7),
            ("ops", "Operations", 9),
            ("it", "Technology", 10),
        ),
        routing_prefix="02100917",
    ),
    "brightwater": TenantSpec(
        id="brightwater",
        name="Brightwater Credit Union",
        domain="brightwatercu.org",
        seed=4242,
        scale=Scale(
            clients=90, accounts=180, transactions=1500, counterparties=18,
            slack=450, email=120, tickets=45, docs=12, meetings=6, crm=45,
        ),
        teams=(
            ("exec", "Executive Office", 2),
            ("member", "Member Services", 8),
            ("lending", "Lending", 5),
            ("compliance", "Compliance", 3),
            ("it", "Information Technology", 4),
            ("ops", "Operations", 3),
        ),
        routing_prefix="32227316",
    ),
}
