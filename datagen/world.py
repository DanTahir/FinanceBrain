"""Deterministic world model: org chart, systems, clients, accounts, ledger, deals, channels.

The world model is the ground truth. Storyline facts (fraud ring, Project Falcon, ReconLegacy,
PricingSvc, KYC ownership, crypto pause) are fixed here; LLM writers only narrate them.
All people, companies, and identifiers are fictional.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

from datagen.config import CUTOFF, END, START, TENANTS, TenantSpec
from datagen.pii import PiiFactory

EDT = timezone(timedelta(hours=-4), "EDT")  # whole timeline (Jul-Oct 14) is in US Eastern daylight time


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(y: int, m: int, d: int, hh: int, mm: int) -> str:
    return iso(datetime(y, m, d, hh, mm, tzinfo=EDT))


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def biz_ts(rng: random.Random, day: date, start_h: int = 8, end_h: int = 19) -> str:
    return iso(datetime(day.year, day.month, day.day, rng.randint(start_h, end_h - 1), rng.randint(0, 59),
                        rng.randint(0, 59), tzinfo=EDT))


def is_live(ts: str) -> bool:
    return ts >= iso(datetime(CUTOFF.year, CUTOFF.month, CUTOFF.day, 0, 0, tzinfo=EDT))


# --------------------------------------------------------------------------- dataclasses


@dataclass
class Person:
    id: str
    name: str
    email: str
    title: str
    team_id: str
    manager_id: str | None
    start_date: str
    location: str
    salary: str
    home_address: str
    role: str | None = None  # storyline cast role
    persona: str | None = None  # demo persona key
    end_date: str | None = None
    medical_note: str | None = None


@dataclass
class Team:
    id: str
    name: str
    lead_id: str


@dataclass
class System:
    id: str
    name: str
    description: str
    tier: str
    depends_on: list[str]


@dataclass
class Ownership:
    system_id: str
    team_id: str
    person_id: str
    valid_from: str
    valid_to: str | None = None


@dataclass
class Client:
    id: str
    name: str
    kind: str  # individual | business
    segment: str
    rm_id: str
    risk_rating: str
    onboarded: str
    crypto_interest: bool
    pii: dict[str, str]


@dataclass
class Account:
    id: str
    client_id: str
    kind: str
    number: str
    routing: str
    opened: str
    status: str
    card_number: str | None = None


@dataclass
class Counterparty:
    id: str
    name: str
    country: str
    kind: str  # business | exchange
    iban: str | None = None
    swift: str | None = None
    account_number: str | None = None
    routing: str | None = None


@dataclass
class Transaction:
    id: str
    ts: str
    kind: str  # wire_in | wire_out | ach_in | ach_out | internal | card
    amount: float
    currency: str
    from_account: str | None
    to_account: str | None
    counterparty_id: str | None
    memo: str
    flags: list[str] = field(default_factory=list)
    storyline: str | None = None
    live_feed: bool = False


@dataclass
class Deal:
    id: str
    codename: str
    kind: str
    target: str
    principal: str
    value: str
    status: str
    team_ids: list[str]
    sensitivity: str = "MNPI"


@dataclass
class Channel:
    id: str
    name: str
    kind: str  # public | private
    topic: str
    member_ids: list[str]
    sensitivity: str = "internal"


@dataclass
class World:
    tenant: str
    name: str
    domain: str
    people: list[Person]
    teams: list[Team]
    systems: list[System]
    ownership: list[Ownership]
    clients: list[Client]
    accounts: list[Account]
    counterparties: list[Counterparty]
    transactions: list[Transaction]
    deals: list[Deal]
    channels: list[Channel]
    cast: dict[str, str]  # storyline role -> person id
    refs: dict[str, object]  # storyline ground-truth references (ids)

    # ----- lookups
    def __post_init__(self) -> None:
        self._idx: dict[str, dict[str, object]] = {}

    def index(self, kind: str) -> dict[str, object]:
        if kind not in self._idx:
            src = {"person": self.people, "team": self.teams, "system": self.systems, "client": self.clients,
                   "account": self.accounts, "counterparty": self.counterparties, "deal": self.deals,
                   "channel": self.channels, "transaction": self.transactions}[kind]
            self._idx[kind] = {x.id: x for x in src}  # type: ignore[attr-defined]
        return self._idx[kind]

    def person(self, pid: str) -> Person:
        return self.index("person")[pid]  # type: ignore[return-value]

    def by_role(self, role: str) -> Person:
        return self.person(self.cast[role])

    def resolve_pii(self, placeholder: str, eid: str) -> str | None:
        """Canonical synthetic value for a placeholder, or None if it does not apply."""
        c = self.index("client").get(eid)
        if c is not None:
            key = {"CLIENT_NAME": "name", "CLIENT_PHONE": "phone", "CLIENT_EMAIL": "email",
                   "CLIENT_ADDRESS": "address", "CLIENT_SSN": "ssn", "CLIENT_DOB": "dob", "CLIENT_EIN": "ein",
                   "CLIENT_DEVICE": "device_id"}.get(placeholder)
            if key == "name":
                return c.name  # type: ignore[attr-defined]
            return c.pii.get(key) if key else None  # type: ignore[attr-defined]
        a = self.index("account").get(eid)
        if a is not None:
            return {"ACCOUNT_NUMBER": a.number, "ROUTING_NUMBER": a.routing,  # type: ignore[attr-defined]
                    "CARD_NUMBER": a.card_number}.get(placeholder)  # type: ignore[attr-defined]
        cp = self.index("counterparty").get(eid)
        if cp is not None:
            return {"CP_ACCOUNT": cp.account_number, "CP_IBAN": cp.iban,  # type: ignore[attr-defined]
                    "CP_SWIFT": cp.swift}.get(placeholder)  # type: ignore[attr-defined]
        p = self.index("person").get(eid)
        if p is not None:
            return {"EMP_SALARY": p.salary, "EMP_HOME_ADDRESS": p.home_address,  # type: ignore[attr-defined]
                    "EMP_MEDICAL": p.medical_note}.get(placeholder)  # type: ignore[attr-defined]
        return None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        return d

    def save(self, root: Path) -> Path:
        out = root / self.tenant
        out.mkdir(parents=True, exist_ok=True)
        path = out / "world.json"
        path.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "World":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            tenant=d["tenant"], name=d["name"], domain=d["domain"],
            people=[Person(**x) for x in d["people"]], teams=[Team(**x) for x in d["teams"]],
            systems=[System(**x) for x in d["systems"]], ownership=[Ownership(**x) for x in d["ownership"]],
            clients=[Client(**x) for x in d["clients"]], accounts=[Account(**x) for x in d["accounts"]],
            counterparties=[Counterparty(**x) for x in d["counterparties"]],
            transactions=[Transaction(**x) for x in d["transactions"]], deals=[Deal(**x) for x in d["deals"]],
            channels=[Channel(**x) for x in d["channels"]], cast=d["cast"], refs=d["refs"],
        )


# --------------------------------------------------------------------------- tenant content

CASTS: dict[str, dict[str, tuple[str, str, str]]] = {
    "halcyon": {
        "ceo": ("Catherine Hale", "exec", "Chief Executive Officer"),
        "cfo": ("Martin Osei", "exec", "Chief Financial Officer"),
        "coo": ("Linda Brandt", "exec", "Chief Operating Officer"),
        "cco": ("Marcus Bell", "compliance", "Chief Compliance Officer"),
        "aml_analyst": ("Nadia Petrov", "compliance", "Senior AML Analyst"),
        "compliance_eng": ("Jenna Cho", "compliance", "Compliance Engineering Lead"),
        "head_risk": ("Owen Gallagher", "risk", "Head of Risk"),
        "head_it": ("Sam Okafor", "it", "Director of Engineering"),
        "recon_expert": ("Priya Raman", "it", "Staff Engineer"),
        "pricing_owner": ("Leo Martinez", "it", "Senior Engineer"),
        "junior_eng": ("Kevin Zhao", "it", "Software Engineer"),
        "head_ops": ("Ravi Patel", "ops", "Head of Operations"),
        "recon_analyst": ("Tasha Greene", "ops", "Reconciliation Analyst"),
        "head_wealth": ("Grace Lin", "wealth", "Head of Wealth Management"),
        "head_trading": ("Daniel Reyes", "trading", "Head of Trading"),
        "leaker": ("Derek Owens", "trading", "Equity Trader"),
        "head_lending": ("Brian Kowalski", "lending", "Head of Lending"),
        "falcon_lead": ("Elena Vasquez", "ma", "Managing Director"),
        "falcon_vp": ("Tom Hargreave", "ma", "Vice President"),
        "falcon_assoc": ("Aisha Karim", "ma", "Associate"),
    },
    "brightwater": {
        "ceo": ("Gloria Ibarra", "exec", "President and CEO"),
        "cco": ("Wesley Tran", "compliance", "Chief Compliance Officer"),
        "aml_analyst": ("Monica Feld", "compliance", "BSA/AML Analyst"),
        "head_it": ("Andre Dubois", "it", "IT Manager"),
        "recon_expert": ("Hector Salas", "it", "Systems Engineer"),
        "head_lending": ("Kara Nwosu", "lending", "Lending Manager"),
        "head_member": ("Paul Jensen", "member", "Member Services Manager"),
        "head_ops": ("Rita Solano", "ops", "Operations Manager"),
    },
}

TEAM_LEAD_ROLE = {"exec": "ceo", "trading": "head_trading", "wealth": "head_wealth", "lending": "head_lending",
                  "ma": "falcon_lead", "compliance": "cco", "risk": "head_risk", "ops": "head_ops", "it": "head_it",
                  "member": "head_member"}

PERSONAS = {
    "halcyon": {"cco": "compliance_officer", "aml_analyst": "aml_analyst", "falcon_lead": "ma_banker",
                "junior_eng": "engineer"},
    "brightwater": {"cco": "compliance_officer", "aml_analyst": "aml_analyst", "head_lending": "lender",
                    "head_it": "engineer"},
}

TITLES = {
    "exec": ["Chief of Staff", "HR Business Partner", "Executive Assistant"],
    "trading": ["Equity Trader", "Fixed Income Trader", "Trading Associate", "Quant Analyst", "Execution Trader",
                "Sales Trader"],
    "wealth": ["Wealth Advisor", "Senior Wealth Advisor", "Client Associate", "Portfolio Manager",
               "Relationship Manager"],
    "lending": ["Credit Analyst", "Loan Officer", "Senior Underwriter", "Relationship Manager", "Loan Closer"],
    "ma": ["Analyst", "Associate", "Vice President", "Director"],
    "compliance": ["Compliance Analyst", "AML Investigator", "KYC Specialist", "Compliance Associate"],
    "risk": ["Risk Analyst", "Market Risk Manager", "Credit Risk Analyst", "Model Validation Analyst"],
    "ops": ["Operations Analyst", "Settlements Specialist", "Payments Specialist", "Wire Room Specialist"],
    "it": ["Software Engineer", "Senior Engineer", "Site Reliability Engineer", "Data Engineer",
           "Security Engineer"],
    "member": ["Member Services Representative", "Teller", "Branch Supervisor", "Member Advocate"],
}

# (name, description, owner team, depends_on, tier, owner cast role or None)
SYSTEMS: dict[str, list[tuple[str, str, str, list[str], str, str | None]]] = {
    "halcyon": [
        ("MarketDataFeed", "Real-time market data ingestion from exchanges and vendors", "it", [], "critical", None),
        ("PricingSvc", "Instrument pricing and valuation service", "it", ["MarketDataFeed"], "critical", "pricing_owner"),
        ("PositionStore", "Positions and holdings store", "it", [], "critical", None),
        ("RiskEngine", "Intraday VaR and exposure calculations", "risk", ["PricingSvc", "PositionStore"], "critical", None),
        ("OMS", "Order management system for the trading desk", "trading", ["PricingSvc", "AuthSSO"], "critical", None),
        ("TradeBlotter", "Trader blotter and P&L view", "trading", ["OMS", "PricingSvc"], "standard", None),
        ("PortfolioMgr", "Wealth portfolio management and rebalancing", "wealth", ["PricingSvc", "PositionStore"], "standard", None),
        ("SettlementEngine", "Trade settlement and clearing", "ops", ["OMS", "GLCore"], "critical", None),
        ("GLCore", "General ledger", "ops", [], "critical", None),
        ("ReconLegacy", "Legacy end-of-day reconciliation batch (Perl on Oracle 11g)", "it", ["SettlementEngine", "GLCore"], "legacy", "head_it"),
        ("ReportingBI", "Management and regulatory reporting dashboards", "risk", ["RiskEngine", "GLCore", "DataLake"], "standard", None),
        ("DataLake", "Analytics data lake", "it", [], "standard", None),
        ("ClientHub", "CRM for client relationships", "wealth", ["AuthSSO"], "standard", None),
        ("KYCPipeline", "Client onboarding KYC/CDD screening pipeline", "ops", ["ClientHub", "DocVault", "SanctionsScreen"], "critical", "head_ops"),
        ("SanctionsScreen", "OFAC and sanctions list screening", "compliance", [], "critical", None),
        ("DocVault", "Document storage for client records", "it", ["AuthSSO"], "standard", None),
        ("PaymentsGateway", "ACH and wire payments gateway", "ops", ["GLCore", "FraudScore"], "critical", None),
        ("WireRoom", "Wire transfer operations console", "ops", ["PaymentsGateway"], "standard", None),
        ("FraudScore", "Real-time transaction fraud scoring", "compliance", ["DataLake"], "critical", None),
        ("AMLMonitor", "AML transaction monitoring and alerting", "compliance", ["DataLake", "FraudScore"], "critical", "aml_analyst"),
        ("SARFiling", "Suspicious activity report filing workflow", "compliance", ["AMLMonitor", "DocVault"], "standard", "cco"),
        ("LoanOrigination", "Loan origination system", "lending", ["ClientHub", "CreditScoring", "KYCPipeline"], "standard", None),
        ("CreditScoring", "Credit decisioning models", "lending", ["DataLake"], "standard", None),
        ("AuthSSO", "Single sign-on and identity provider", "it", [], "critical", None),
        ("HRIS", "HR information system", "exec", [], "standard", "coo"),
    ],
    "brightwater": [
        ("CoreBanking", "Core share and loan processing (vendor hosted)", "it", [], "critical", "head_it"),
        ("OnlineBanking", "Member online and mobile banking", "it", ["CoreBanking", "MemberSSO"], "critical", None),
        ("MemberSSO", "Member identity and MFA", "it", [], "critical", None),
        ("CardProcessor", "Debit card authorization feed", "ops", ["CoreBanking"], "critical", None),
        ("ACHHub", "ACH origination and receipt", "ops", ["CoreBanking"], "critical", "head_ops"),
        ("LoanDesk", "Consumer and auto loan origination", "lending", ["CoreBanking", "CreditPull"], "standard", None),
        ("CreditPull", "Bureau credit pull integration", "lending", [], "standard", None),
        ("BSAWatch", "BSA/AML monitoring", "compliance", ["CoreBanking", "ACHHub"], "critical", "aml_analyst"),
        ("ShareRecon", "Nightly share and GL reconciliation scripts", "it", ["CoreBanking"], "legacy", "recon_expert"),
        ("MemberCRM", "Member relationship notes", "member", ["MemberSSO"], "standard", None),
    ],
}

# Halcyon storyline schedule constants (EDT).
RECON_LAST_DAY = "2026-10-09"
KYC_TRANSFER_DATE = "2026-10-06"


# --------------------------------------------------------------------------- builders


class _Builder:
    def __init__(self, spec: TenantSpec):
        self.spec = spec
        self.rng = random.Random(spec.seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(spec.seed)
        self.pii = PiiFactory(self.rng, self.fake)
        self.used_names: set[str] = set()

    def unique_name(self) -> str:
        while True:
            n = f"{self.fake.first_name()} {self.fake.last_name()}"
            if n not in self.used_names:
                self.used_names.add(n)
                return n

    def unique_company(self) -> str:
        while True:
            n = self.fake.company()
            if n not in self.used_names:
                self.used_names.add(n)
                return n

    # ---- people / teams
    def people(self) -> tuple[list[Person], list[Team], dict[str, str]]:
        cast = CASTS[self.spec.id]
        self.used_names |= {v[0] for v in cast.values()}
        people: list[Person] = []
        teams: list[Team] = []
        role_to_id: dict[str, str] = {}
        n = 0
        for team_id, team_name, size in self.spec.teams:
            members = [(role, name, title) for role, (name, t, title) in cast.items() if t == team_id]
            while len(members) < size:
                members.append((None, self.unique_name(), self.rng.choice(TITLES[team_id])))
            ids = []
            for role, name, title in members:
                n += 1
                pid = f"e_{n:03d}"
                ids.append(pid)
                senior = re.search(r"Chief|Head|Director|Managing|President|Manager", title)
                mid = re.search(r"Vice|Senior|Staff|Lead|Portfolio|Quant", title)
                low, high = (240_000, 460_000) if senior else (150_000, 260_000) if mid else (78_000, 160_000)
                start = date(2014, 1, 1) + timedelta(days=self.rng.randint(0, 4400))
                if role == "recon_expert":
                    start = date(2013, 3, 4)
                if role == "junior_eng":
                    start = date(2026, 6, 15)
                people.append(Person(
                    id=pid, name=name, email=self._work_email(name), title=title, team_id=team_id, manager_id=None,
                    start_date=start.isoformat(),
                    location=self.rng.choices(["New York", "Charlotte", "Remote"], [70, 20, 10])[0],
                    salary=self.pii.salary(low, high), home_address=self.pii.address(), role=role,
                    persona=PERSONAS[self.spec.id].get(role or ""),
                ))
                if role:
                    role_to_id[role] = pid
            lead_role = TEAM_LEAD_ROLE.get(team_id)
            lead = role_to_id.get(lead_role) if lead_role else None
            teams.append(Team(id=f"team_{team_id}", name=team_name, lead_id=lead or ids[0]))
        by_id = {p.id: p for p in people}
        ceo = role_to_id["ceo"]
        for t in teams:
            for p in people:
                if f"team_{p.team_id}" != t.id:
                    continue
                p.manager_id = None if p.id == ceo else (ceo if p.id == t.lead_id or p.team_id == "exec" else t.lead_id)
        # Sensitive HR details for a few non-cast employees (pseudonymized if they appear in text).
        for p in self.rng.sample([p for p in people if not p.role], 4):
            p.medical_note = self.pii.medical_note()
        if "recon_expert" in role_to_id and self.spec.id == "halcyon":
            by_id[role_to_id["recon_expert"]].end_date = RECON_LAST_DAY
        return people, teams, role_to_id

    def _work_email(self, name: str) -> str:
        parts = [re.sub(r"[^a-z]", "", p) for p in name.lower().split()]
        return f"{parts[0]}.{parts[-1]}@{self.spec.domain}"

    # ---- systems / ownership
    def systems(self, people: list[Person], cast: dict[str, str]) -> tuple[list[System], list[Ownership]]:
        systems, owns = [], []
        by_team = defaultdict(list)
        for p in people:
            by_team[p.team_id].append(p.id)
        for name, desc, team, deps, tier, owner_role in SYSTEMS[self.spec.id]:
            sid = f"sys_{name.lower()}"
            systems.append(System(id=sid, name=name, description=desc, tier=tier,
                                  depends_on=[f"sys_{d.lower()}" for d in deps]))
            owner = cast.get(owner_role) if owner_role else None
            owner = owner or self.rng.choice(by_team[team])
            since = (date(2022, 1, 3) + timedelta(days=self.rng.randint(0, 900))).isoformat()
            if self.spec.id == "halcyon" and name == "KYCPipeline":
                owns.append(Ownership(sid, "team_ops", owner, "2025-03-03", KYC_TRANSFER_DATE))
                owns.append(Ownership(sid, "team_compliance", cast["compliance_eng"], KYC_TRANSFER_DATE, None))
            else:
                owns.append(Ownership(sid, f"team_{team}", owner, since, None))
        return systems, owns

    # ---- clients / accounts
    def clients_and_accounts(self, people: list[Person], ring_size: int) -> tuple[list[Client], list[Account], list[str]]:
        sc = self.spec.scale
        rm_pool = defaultdict(list)
        for p in people:
            if p.team_id in ("wealth", "lending", "member") and not p.title.startswith("Head"):
                rm_pool[p.team_id].append(p.id)
        if self.spec.id == "halcyon":
            seg_weights = [("wealth", 45), ("lending", 30), ("commercial", 25)]
        else:
            seg_weights = [("member", 75), ("lending", 15), ("commercial", 10)]
        ring_idx = sorted(self.rng.sample(range(40, sc.clients), ring_size))
        clients: list[Client] = []
        for i in range(sc.clients):
            cid = f"c_{i + 1:04d}"
            ring = i in ring_idx
            seg = "wealth" if (ring and self.spec.id == "halcyon") else ("member" if ring else
                  self.rng.choices([s for s, _ in seg_weights], [w for _, w in seg_weights])[0])
            kind = "business" if seg == "commercial" else "individual"
            pool = rm_pool["wealth" if seg == "wealth" else "member" if seg == "member" else "lending"] or \
                rm_pool["lending"] or rm_pool["member"]
            name = self.unique_company() if kind == "business" else self.unique_name()
            if ring:
                onboard = date(2026, 7, 6) + timedelta(days=self.rng.randint(0, 40))
            else:
                onboard = date(2017, 1, 2) + timedelta(days=self.rng.randint(0, 3400))
            pii = {"phone": self.pii.phone(), "email": self.pii.personal_email(name), "address": self.pii.address(),
                   "device_id": self.pii.device_id()}
            if kind == "individual":
                pii |= {"ssn": self.pii.ssn(), "dob": self.pii.dob()}
            else:
                pii |= {"ein": self.pii.ein()}
            clients.append(Client(
                id=cid, name=name, kind=kind, segment=seg, rm_id=self.rng.choice(pool),
                risk_rating="medium" if ring else self.rng.choices(["low", "medium", "high"], [60, 30, 10])[0],
                onboarded=onboard.isoformat(),
                crypto_interest=(seg == "wealth" and kind == "individual" and self.rng.random() < 0.12),
                pii=pii,
            ))
        ring_clients = [clients[i] for i in ring_idx]
        # Benign households: spouses share a home address (noise for ring detection).
        others = [c for c in clients if c.kind == "individual" and c not in ring_clients]
        for a, b in [self.rng.sample(others, 2) for _ in range(max(2, ring_size + 1))]:
            b.pii["address"] = a.pii["address"]
            b.name = f"{b.name.split()[0]} {a.name.split()[-1]}"
        # Fraud ring: overlapping shared identifiers connect every member.
        r = ring_clients
        if len(r) >= 5:
            for c in r[1:3]:
                c.pii["phone"] = r[0].pii["phone"]          # phone shared by 0,1,2
            for c in r[3:5]:
                c.pii["address"] = r[2].pii["address"]      # address shared by 2,3,4
            r[4].pii["device_id"] = r[0].pii["device_id"]  # device shared by 0,4
        else:
            r[1].pii["phone"] = r[0].pii["phone"]
            r[2].pii["address"] = r[1].pii["address"]
            r[2].pii["device_id"] = r[0].pii["device_id"]

        routing = self.pii.routing_number(self.spec.routing_prefix)
        accounts: list[Account] = []
        n = 0
        target = sc.accounts
        for i, c in enumerate(clients):
            remaining_clients = len(clients) - i
            if c in ring_clients:
                k = 1
            else:
                k = self.rng.choices([1, 2, 3], [24, 44, 32])[0]
                k = max(1, min(k, target - n - (remaining_clients - 1)))
            kinds = {"wealth": ["brokerage", "cash_management", "checking"], "lending": ["checking", "loan", "heloc"],
                     "commercial": ["operating", "loan", "treasury"], "member": ["share_savings", "checking", "auto_loan"]}
            for j in range(k):
                n += 1
                kind = "checking" if c in ring_clients else kinds[c.segment][j % 3]
                opened = date.fromisoformat(c.onboarded) + timedelta(days=self.rng.randint(0, 200) if j else 0)
                accounts.append(Account(
                    id=f"a_{n:05d}", client_id=c.id, kind=kind, number=self.pii.account_number(), routing=routing,
                    opened=min(opened, date(2026, 6, 30)).isoformat() if c not in ring_clients else c.onboarded,
                    status="open",
                    card_number=self.pii.card_number() if kind in ("checking", "operating") and self.rng.random() < 0.6 else None,
                ))
        return clients, accounts, [c.id for c in ring_clients]

    def counterparties(self) -> list[Counterparty]:
        out = []
        for i in range(self.spec.scale.counterparties):
            country = self.rng.choices(["US", "GB", "DE", "NL", "CY", "AE", "FR"], [55, 12, 9, 6, 6, 6, 6])[0]
            out.append(self._cp(f"cp_{i + 1:03d}", self.unique_company(), country, "business"))
        return out

    def _cp(self, cid: str, name: str, country: str, kind: str) -> Counterparty:
        if country == "US":
            return Counterparty(cid, name, country, kind, account_number=self.pii.account_number(),
                                routing=self.pii.routing_number())
        return Counterparty(cid, name, country, kind, iban=self.pii.iban(country), swift=self.pii.swift(country))


def _blast_radius(systems: list[System], root: str) -> list[dict]:
    dependents = defaultdict(list)
    for s in systems:
        for d in s.depends_on:
            dependents[d].append(s.id)
    seen, out, q = {root}, [], deque([(root, 0)])
    while q:
        cur, depth = q.popleft()
        for nxt in sorted(dependents[cur]):
            if nxt not in seen:
                seen.add(nxt)
                out.append({"system_id": nxt, "hops": depth + 1})
                q.append((nxt, depth + 1))
    return out


def _lognormal(rng: random.Random, median: float, sigma: float, lo: float, hi: float) -> float:
    return round(min(hi, max(lo, median * math.exp(rng.gauss(0, sigma)))), 2)


MEMOS = {
    "ach_in": ["Payroll deposit", "Dividend credit", "Tax refund", "Client funding", "Pension deposit", "Rental income"],
    "ach_out": ["Mortgage payment", "Utility payment", "Credit card autopay", "Insurance premium", "Tuition payment",
                "Brokerage transfer"],
    "wire_in": ["Incoming wire - invoice settlement", "Incoming wire - sale proceeds", "Incoming wire - capital call return",
                "Incoming wire - consulting fees"],
    "wire_out": ["Outgoing wire - property closing", "Outgoing wire - vendor payment", "Outgoing wire - capital call",
                 "Outgoing wire - tuition"],
    "internal": ["Transfer between own accounts", "Sweep to cash management", "Loan payment from checking"],
    "card": ["POS purchase", "Online purchase", "Travel", "Dining", "Groceries"],
}


def _transactions(b: _Builder, accounts: list[Account], cps: list[Counterparty], ring_accounts: list[str],
                  ring_cps: dict[str, str]) -> tuple[list[Transaction], dict[str, str]]:
    rng = b.rng
    days = weekdays(START, END)
    normal_accts = [a for a in accounts if a.id not in ring_accounts]
    by_client = defaultdict(list)
    for a in accounts:
        by_client[a.client_id].append(a.id)
    normal_cps = [c for c in cps if c.id not in ring_cps.values()]
    txs: list[Transaction] = []
    refs: dict[str, str] = {}

    def add(ts, kind, amount, frm, to, cp, memo, flags=None, story=None, fixed_id=None):
        t = Transaction(id=fixed_id or "", ts=ts, kind=kind, amount=round(amount, 2), currency="USD", from_account=frm,
                        to_account=to, counterparty_id=cp, memo=memo, flags=flags or [], storyline=story,
                        live_feed=is_live(ts))
        txs.append(t)
        return t

    # --- fraud ring (fixed ids so evals and demos can reference them)
    big1, big2 = (48_000.00, 31_500.00) if b.spec.id == "halcyon" else (22_000.00, 14_750.00)
    R = ring_accounts
    origin1, origin2, exit_cp = ring_cps["origin1"], ring_cps["origin2"], ring_cps["exit"]
    seq = 0

    def rid() -> str:
        nonlocal seq
        seq += 1
        return f"t_r{seq:03d}"

    # Structuring in August: sub-$10k deposits into each ring account from various small counterparties.
    for i, acct in enumerate(R):
        for k in range(3):
            day = date(2026, 8, 3 + i * 3 + k * 6)
            add(biz_ts(rng, day), "ach_in", rng.uniform(8_100, 9_900), None, acct, rng.choice(normal_cps).id,
                "Client funding", story="fraud_ring", fixed_id=rid())
    t48 = add(at(2026, 9, 25, 10, 14), "wire_in", big1, None, R[0], origin1, "Incoming wire - consulting fees",
              ["fraudscore:0.91", "alert:FRAUD-2291"], "fraud_ring", rid())
    refs["ring_wire_1"] = t48.id
    if len(R) >= 5:
        layering = [(0, 1, 9_500, (9, 25, 14, 2)), (0, 2, 9_800, (9, 25, 15, 41)), (0, 3, 9_700, (9, 28, 9, 12)),
                    (1, 4, 9_200, (9, 28, 11, 30)), (2, 4, 9_600, (9, 29, 10, 5))]
    else:
        layering = [(0, 1, 9_500, (9, 25, 14, 2)), (0, 2, 9_800, (9, 25, 15, 41)), (1, 2, 9_200, (9, 28, 11, 30))]
    for src, dst, amt, (m, d, hh, mm) in layering:
        add(at(2026, m, d, hh, mm), "internal", amt, R[src], R[dst], None, "Transfer - personal loan repayment",
            story="fraud_ring", fixed_id=rid())
    exits = [(3 if len(R) >= 5 else 2, 9_500, (9, 29, 13, 20)), (len(R) - 1, 18_700, (9, 30, 9, 45)),
             (0, 18_900, (9, 30, 10, 2))]
    for src, amt, (m, d, hh, mm) in exits:
        add(at(2026, m, d, hh, mm), "wire_out", amt, R[src], None, exit_cp, "Outgoing wire - digital asset purchase",
            story="fraud_ring", fixed_id=rid())
    # Live feed: a second large wire into the ring, then onward movement.
    t31 = add(at(2026, 10, 3, 11, 22), "wire_in", big2, None, R[2], origin2, "Incoming wire - invoice settlement",
              ["fraudscore:0.88"], "fraud_ring", rid())
    refs["ring_wire_2"] = t31.id
    add(at(2026, 10, 3, 15, 8), "internal", 9_900, R[2], R[-1], None, "Transfer - personal loan repayment",
        story="fraud_ring", fixed_id=rid())
    add(at(2026, 10, 5, 9, 31), "wire_out", 9_800, R[-1], None, exit_cp, "Outgoing wire - digital asset purchase",
        story="fraud_ring", fixed_id=rid())
    # Ordinary card activity on ring accounts so they don't look trivially synthetic.
    for acct in R:
        for _ in range(6):
            add(biz_ts(rng, rng.choice(days), 9, 22), "card", _lognormal(rng, 45, 0.8, 4, 600), acct, None, None,
                rng.choice(MEMOS["card"]), story="fraud_ring", fixed_id=rid())

    # --- background ledger
    n_bg = b.spec.scale.transactions - len(txs)
    kinds = [("card", 22), ("ach_in", 22), ("ach_out", 20), ("internal", 14), ("wire_in", 11), ("wire_out", 11)]
    for _ in range(n_bg):
        kind = rng.choices([k for k, _ in kinds], [w for _, w in kinds])[0]
        day = rng.choice(days)
        acct = rng.choice(normal_accts)
        ts = biz_ts(rng, day, 7 if kind != "card" else 8, 20 if kind != "card" else 23)
        if kind == "card":
            add(ts, kind, _lognormal(rng, 70, 1.0, 2, 4_000), acct.id, None, None, rng.choice(MEMOS["card"]))
        elif kind == "ach_in":
            add(ts, kind, _lognormal(rng, 4_500, 0.7, 200, 60_000), None, acct.id, rng.choice(normal_cps).id,
                rng.choice(MEMOS["ach_in"]))
        elif kind == "ach_out":
            add(ts, kind, _lognormal(rng, 1_800, 0.9, 25, 40_000), acct.id, None, rng.choice(normal_cps).id,
                rng.choice(MEMOS["ach_out"]))
        elif kind == "internal":
            own = by_client[acct.client_id]
            other = rng.choice([a for a in own if a != acct.id] or [rng.choice(normal_accts).id])
            add(ts, kind, _lognormal(rng, 6_000, 1.0, 100, 250_000), acct.id, other, None, rng.choice(MEMOS["internal"]))
        else:
            amt = _lognormal(rng, 35_000, 1.1, 1_000, 2_500_000)
            flags = [f"fraudscore:{rng.uniform(0.62, 0.79):.2f}"] if rng.random() < 0.02 else []
            frm, to = (None, acct.id) if kind == "wire_in" else (acct.id, None)
            add(ts, kind, amt, frm, to, rng.choice(normal_cps).id, rng.choice(MEMOS[kind]), flags)
    txs.sort(key=lambda t: (t.ts, t.id))
    n = 0
    for t in txs:
        if not t.id:
            n += 1
            t.id = f"t_{n:06d}"
    return txs, refs


def _deals(cast: dict[str, str], people: list[Person], rng: random.Random, tenant: str) -> list[Deal]:
    if tenant != "halcyon":
        return []
    ma = [p.id for p in people if p.team_id == "ma" and not p.role]
    falcon_team = [cast["falcon_lead"], cast["falcon_vp"], cast["falcon_assoc"], ma[0]]
    return [
        Deal("deal_falcon", "Project Falcon", "buy-side acquisition advisory", "Northwind Logistics Group",
             "Meridian Freight Partners", "$1.2B", "due diligence", falcon_team),
        Deal("deal_osprey", "Project Osprey", "sell-side advisory", "Bluegate Software", "Bluegate founders",
             "$340M", "marketing", [cast["falcon_lead"], ma[1], ma[2]]),
        Deal("deal_kestrel", "Project Kestrel", "debt refinancing advisory", "Harborline Utilities",
             "Harborline Utilities", "$600M", "mandate signed", [ma[3], ma[4] if len(ma) > 4 else ma[1]]),
    ]


def _channels(people: list[Person], cast: dict[str, str], deals: list[Deal], tenant: str) -> list[Channel]:
    team = defaultdict(list)
    for p in people:
        team[p.team_id].append(p.id)
    everyone = [p.id for p in people]

    def ch(name, kind, topic, members, sens="internal"):
        return Channel(f"ch_{name.strip('#')}", name, kind, topic, sorted(set(members)), sens)

    if tenant == "halcyon":
        c = cast
        falcon = next(d for d in deals if d.id == "deal_falcon")
        return [
            ch("#general", "public", "Firm-wide announcements", everyone),
            ch("#random", "public", "Water cooler", everyone),
            ch("#trading-desk", "public", "Trading desk chatter, flows, market color",
               team["trading"] + [c["head_risk"]] + team["risk"][:2]),
            ch("#wealth-team", "public", "Wealth management team", team["wealth"]),
            ch("#lending", "public", "Lending pipeline and credit", team["lending"]),
            ch("#compliance", "public", "Compliance questions and alerts", team["compliance"] + [c["head_risk"], c["coo"]]),
            ch("#risk", "public", "Risk management", team["risk"] + [c["cfo"]]),
            ch("#it-ops", "public", "Technology operations", team["it"]),
            ch("#incidents", "public", "Production incidents", team["it"] + team["ops"] + [c["head_trading"], c["head_risk"]]),
            ch("#recon", "public", "Daily reconciliation and breaks", team["ops"] + [c["recon_expert"], c["head_it"], c["junior_eng"]]),
            ch("#kyc-pipeline", "public", "KYC onboarding pipeline", team["ops"][:5] + team["compliance"] + [c["head_it"]]),
            ch("#crypto-policy", "public", "Digital asset client policy working group",
               [c["ceo"], c["cco"], c["head_wealth"], c["head_risk"], c["aml_analyst"], c["cfo"]] + team["wealth"][:4]),
            ch("#ma-team", "private", "M&A Advisory team", team["ma"], "confidential"),
            ch("#project-falcon", "private", "Project Falcon deal team (wall-crossed only)",
               falcon.team_ids + [c["cco"]], "MNPI"),
        ]
    return [
        ch("#general", "public", "Credit union announcements", everyone),
        ch("#member-services", "public", "Member services floor", team["member"] + [cast["head_ops"]]),
        ch("#lending", "public", "Loan pipeline", team["lending"]),
        ch("#bsa-aml", "private", "BSA/AML alerts", team["compliance"] + [cast["ceo"]], "confidential"),
        ch("#it", "public", "IT and systems", team["it"] + [cast["head_ops"]]),
    ]


def build_world(tenant: str) -> World:
    spec = TENANTS[tenant]
    b = _Builder(spec)
    people, teams, cast = b.people()
    systems, owns = b.systems(people, cast)
    ring_size = 5 if tenant == "halcyon" else 3
    clients, accounts, ring_clients = b.clients_and_accounts(people, ring_size)
    cps = b.counterparties()
    n = len(cps)
    special = [
        ("origin1", "Coastal Meridian Trading Ltd" if tenant == "halcyon" else "Pinecrest Remote Services LLC", "CY", "business"),
        ("origin2", "Larkspur Global FZE" if tenant == "halcyon" else "Tidewell Holdings Ltd", "AE", "business"),
        ("exit", "Bitrail Digital Exchange", "US", "exchange"),
    ]
    ring_cps = {}
    for key, name, country, kind in special:
        n += 1
        cp = b._cp(f"cp_{n:03d}", name, country, kind)
        cps.append(cp)
        ring_cps[key] = cp.id
    ring_accounts = [a.id for cid in ring_clients for a in accounts if a.client_id == cid]
    txs, tx_refs = _transactions(b, accounts, cps, ring_accounts, ring_cps)
    deals = _deals(cast, people, b.rng, tenant)
    channels = _channels(people, cast, deals, tenant)

    refs: dict[str, object] = {
        "ring_clients": ring_clients,
        "ring_accounts": ring_accounts,
        "ring_counterparties": ring_cps,
        "ring_shared": _shared_identifiers(clients, ring_clients),
        **tx_refs,
        "cutoff": CUTOFF.isoformat(),
        "personas": {p.persona: p.id for p in people if p.persona},
    }
    if tenant == "halcyon":
        refs |= {
            "falcon_deal": "deal_falcon",
            "falcon_team": next(d.team_ids for d in deals if d.id == "deal_falcon"),
            "falcon_leak_source": cast["falcon_vp"],
            "falcon_leak_recipient": cast["leaker"],
            "recon_system": "sys_reconlegacy",
            "recon_expert": cast["recon_expert"],
            "recon_last_day": RECON_LAST_DAY,
            "pricing_system": "sys_pricingsvc",
            "pricing_blast_radius": _blast_radius(systems, "sys_pricingsvc"),
            "kyc_system": "sys_kycpipeline",
            "kyc_owner_before": {"person": cast["head_ops"], "team": "team_ops"},
            "kyc_owner_after": {"person": cast["compliance_eng"], "team": "team_compliance", "from": KYC_TRANSFER_DATE},
            "crypto_decision": "dec_crypto_pause",
            "crypto_reversal": "dec_crypto_resume",
        }
    else:
        refs |= {"recon_system": "sys_sharerecon", "recon_expert": cast["recon_expert"]}
    return World(tenant=tenant, name=spec.name, domain=spec.domain, people=people, teams=teams, systems=systems,
                 ownership=owns, clients=clients, accounts=accounts, counterparties=cps, transactions=txs,
                 deals=deals, channels=channels, cast=cast, refs=refs)


def _shared_identifiers(clients: list[Client], ring_ids: list[str]) -> list[dict]:
    by_id = {c.id: c for c in clients}
    out = []
    for key in ("phone", "address", "device_id"):
        groups = defaultdict(list)
        for cid in ring_ids:
            groups[by_id[cid].pii[key]].append(cid)
        for members in groups.values():
            if len(members) > 1:
                out.append({"type": key, "clients": members})
    return out


if __name__ == "__main__":
    from backend.config import REPO_ROOT

    for t in TENANTS:
        w = build_world(t)
        p = w.save(REPO_ROOT / "data" / "world")
        live = sum(tx.live_feed for tx in w.transactions)
        print(f"{t}: people={len(w.people)} teams={len(w.teams)} systems={len(w.systems)} clients={len(w.clients)} "
              f"accounts={len(w.accounts)} cps={len(w.counterparties)} txs={len(w.transactions)} (live {live}) "
              f"deals={len(w.deals)} channels={len(w.channels)} -> {p.relative_to(REPO_ROOT)}")
        print("  ring:", w.refs["ring_clients"], w.refs["ring_shared"])
        if t == "halcyon":
            print("  blast radius:", [(x["system_id"], x["hops"]) for x in w.refs["pricing_blast_radius"]])
