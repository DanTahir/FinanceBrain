"""Generate the synthetic multi-channel corpus for one tenant.

    python -m datagen.generate --tenant halcyon             # full run (LLM outputs cached in data/gen_cache)
    python -m datagen.generate --tenant halcyon --dry-run   # plan only: job counts + one sample prompt per kind
    python -m datagen.generate --tenant halcyon --kinds beats,slack --limit 3

Outputs in data/raw/<tenant>/: <kind>.jsonl (slack, email, ticket, doc, meeting, crm), pii_spans.jsonl,
ground_truth.json, manifest.json. Every person, client, and value is fictional. LLM prompts contain only
placeholders; synthetic PII values are injected locally after generation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from backend.config import REPO_ROOT, get_settings
from backend.llm.client import COSTS
from backend.llm.guard import find_pii
from datagen import writers
from datagen.config import END, START, TENANTS
from datagen.pii import PLACEHOLDER_RE, PLACEHOLDERS, fill_placeholders
from datagen.storylines import Beat, build_beats, ph
from datagen.topics import CHANNELS, CRM_SEEDS, DOCS, EMAIL_TEAMS, MEETINGS, TICKET_PROJECTS
from datagen.world import EDT, World, is_live, iso, weekdays

log = logging.getLogger("datagen.generate")

RAW_ROOT = REPO_ROOT / "data" / "raw"
WORLD_ROOT = REPO_ROOT / "data" / "world"
KINDS = ("beats", "slack", "email", "ticket", "doc", "meeting", "crm")
CONV_SIZE = {"halcyon": 15, "brightwater": 12}
EMAIL_BATCH, TICKET_BATCH, CRM_BATCH, BEAT_CHUNK = 5, 5, 10, 6
MAX_TOKENS = {"beats": 16000, "slack": 4000, "email": 5000, "ticket": 5000, "doc": 3500, "meeting": 4500, "crm": 4000}
TICKET_BASE = {"halcyon": {"ITOPS": 300, "INC": 900, "COMP": 200, "KYC": 100, "OPS": 400, "RISK": 50, "FRAUD": 2240},
               "brightwater": {"IT": 100, "BSA": 380, "OPS": 200, "LOAN": 50}}
TICKET_WIDTH = {"INC": 4, "BSA": 4, "KYC": 3}
EMAIL_HOOKS = {"wealth": ["clients:wealth"], "lending": ["clients:lending"],
               "compliance": ["clients_kyc:any", "counterparties"], "ops": ["counterparties", "clients:any"],
               "trading": ["systems:trading"], "it": ["systems:it"], "risk": ["systems:risk"],
               "member": ["clients:member"], "exec": [], "ma": []}
NAMED_SYSTEMS = {"recon": ["sys_reconlegacy", "sys_settlementengine", "sys_glcore", "sys_sharerecon", "sys_corebanking"],
                 "kyc": ["sys_kycpipeline", "sys_sanctionsscreen", "sys_docvault"]}
CRM_TEAMS = {"halcyon": ["wealth", "lending", "compliance"], "brightwater": ["member", "lending", "compliance"]}
STORY_TITLES = {"fraud_ring": "suspicious wires and a possible fraud ring", "falcon": "Project Falcon (M&A, MNPI)",
                "crypto": "digital-asset client onboarding decision", "recon": "ReconLegacy / reconciliation expertise",
                "pricing": "PricingSvc incident and dependencies", "kyc": "KYC pipeline ownership"}
FORMAT_RULES = {
    "slack": "Slack message: 1-4 sentences, informal. Return an empty title.",
    "email": "Email body with greeting and sign-off (first name). The title is the subject line.",
    "ticket": "Ticket description first, then each comment as its own block starting with a line "
              "'--- Comment by <Full Name> ---'. The title is the ticket title.",
    "doc": "Plain-text document with short section titles on their own lines (no markdown symbols). "
           "The title is the document title.",
    "meeting": "Meeting transcript, one utterance per line as 'Full Name: text'. The title is the meeting title.",
    "crm": "CRM note in the relationship manager's voice about the client. The title is a short note subject.",
}
MNPI_RE = re.compile(r"\bProject Falcon\b|\bFalcon\b|\bNorthwind\b|\bNWLG\b")
TICKET_KEY_RE = re.compile(r"\b[A-Z]{2,6}-\d{2,5}\b")


# ------------------------------------------------------------------------------------------- helpers
def sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def human(ts: str) -> str:
    return datetime.fromisoformat(ts).strftime("%a %Y-%m-%d %H:%M ET")


def ts_on(rng: random.Random, day: date, h0: int = 8, h1: int = 18) -> str:
    return iso(datetime(day.year, day.month, day.day, rng.randint(h0, h1 - 1), rng.randint(0, 59), tzinfo=EDT))


def allocate(total: int, weights: dict[str, int]) -> dict[str, int]:
    s = sum(weights.values())
    raw = {k: total * v / s for k, v in weights.items()}
    out = {k: int(x) for k, x in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: total - sum(out.values())]:
        out[k] += 1
    return out


@dataclass
class Job:
    key: str
    kind: str
    schema: str
    prompt: str
    model: str
    max_tokens: int
    meta: dict = field(default_factory=dict)


class Ctx:
    """World lookups shared by planners and assembly."""

    def __init__(self, w: World, rng: random.Random):
        self.w, self.rng = w, rng
        self.people = w.index("person")
        self.clients = w.index("client")
        self.accounts = w.index("account")
        self.cps = w.index("counterparty")
        ring = set(w.refs["ring_clients"])  # type: ignore[arg-type]
        ring_cps = set(w.refs["ring_counterparties"].values())  # type: ignore[union-attr]
        self.bg_clients = [c for c in w.clients if c.id not in ring]
        self.bg_cps = [c for c in w.counterparties if c.id not in ring_cps]
        self.acct_by_client: dict[str, list] = defaultdict(list)
        for a in w.accounts:
            self.acct_by_client[a.client_id].append(a)
        self.flagged = [t for t in w.transactions if t.flags and not t.storyline and t.to_account]
        self.owner_team = {o.system_id: o.team_id.removeprefix("team_") for o in w.ownership if o.valid_to is None}
        self.team_members: dict[str, list[str]] = defaultdict(list)
        for p in w.people:
            self.team_members[p.team_id].append(p.id)
        self.team_name = {t.id.removeprefix("team_"): t.name for t in w.teams}
        self.team_lead = {t.id.removeprefix("team_"): t.lead_id for t in w.teams}
        self.days = weekdays(START, END)
        self.sys_ids = {s.id for s in w.systems}
        names = sorted(((p.name, p.id) for p in w.people), key=lambda x: -len(x[0]))
        self.person_re = re.compile(r"\b(" + "|".join(re.escape(n) for n, _ in names) + r")\b")
        self.person_by_name = dict(names)
        sysn = sorted(((s.name, s.id) for s in w.systems), key=lambda x: -len(x[0]))
        self.system_re = re.compile(r"\b(" + "|".join(re.escape(n) for n, _ in sysn) + r")\b")
        self.system_by_name = dict(sysn)
        self.deal_by_word = {}
        for d in w.deals:
            self.deal_by_word[d.codename] = d.id
            self.deal_by_word[d.codename.replace("Project ", "")] = d.id
        if self.deal_by_word:
            self.deal_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(self.deal_by_word, key=len,
                                                                                     reverse=True)) + r")\b")
        else:
            self.deal_re = None

    def active(self, pid: str, day: date) -> bool:
        p = self.people[pid]
        return p.end_date is None or day.isoformat() <= p.end_date  # type: ignore[attr-defined]

    def desc(self, pid: str) -> str:
        if pid.startswith("ext:"):
            return f"{pid[4:]} (external sender)"
        p = self.people[pid]
        return f"{p.id} {p.name} ({p.title})"  # type: ignore[attr-defined]

    def name(self, pid: str) -> str:
        return pid[4:] if pid.startswith("ext:") else self.people[pid].name  # type: ignore[attr-defined]

    # ---- hooks: entities a writer may reference, with the exact placeholders allowed for them
    def hooks(self, pools: list[str]) -> tuple[str, list[str]]:
        rng = self.rng
        lines: list[str] = []
        allowed: list[str] = []
        for pool in pools:
            kind, _, sel = pool.partition(":")
            if kind == "systems":
                if sel in NAMED_SYSTEMS:
                    cands = [s for s in self.w.systems if s.id in NAMED_SYSTEMS[sel]]
                elif sel in ("all", ""):
                    cands = list(self.w.systems)
                else:
                    cands = [s for s in self.w.systems if self.owner_team.get(s.id) == sel] or list(self.w.systems)
                for s in rng.sample(cands, min(2, len(cands))):
                    lines.append(f"System {s.name}: {s.description}")
            elif kind in ("clients", "clients_kyc"):
                cands = [c for c in self.bg_clients if sel in ("any", "") or c.segment == sel] or self.bg_clients
                for c in rng.sample(cands, min(2 if kind == "clients" else 1, len(cands))):
                    accts = self.acct_by_client.get(c.id, [])[:2]
                    rm = self.name(c.rm_id)
                    lines.append(f"Client {c.id}: {c.kind}, {c.segment} segment, risk {c.risk_rating}, RM {rm}, client "
                                 f"since {c.onboarded}; accounts: "
                                 + (", ".join(f"{a.id} ({a.kind}, {a.status})" for a in accts) or "none"))
                    allowed += [ph("CLIENT_NAME", c.id), ph("CLIENT_PHONE", c.id), ph("CLIENT_EMAIL", c.id)]
                    allowed += [ph("ACCOUNT_NUMBER", a.id) for a in accts]
                    allowed += [ph("CARD_NUMBER", a.id) for a in accts if a.card_number]
                    if kind == "clients_kyc":
                        allowed.append(ph("CLIENT_ADDRESS", c.id))
                        allowed += ([ph("CLIENT_SSN", c.id), ph("CLIENT_DOB", c.id)] if c.kind == "individual"
                                    else [ph("CLIENT_EIN", c.id)])
                        if accts:
                            allowed.append(ph("ROUTING_NUMBER", accts[0].id))
            elif kind == "counterparties":
                cp = rng.choice(self.bg_cps)
                lines.append(f"Counterparty {cp.id}: {cp.name} ({cp.country}, {cp.kind})")
                allowed += ([ph("CP_IBAN", cp.id), ph("CP_SWIFT", cp.id)] if cp.iban else [ph("CP_ACCOUNT", cp.id)])
            elif kind == "flagged" and self.flagged:
                t = rng.choice(self.flagged)
                a = self.accounts[t.to_account]
                cl = self.clients[a.client_id]  # type: ignore[attr-defined]
                cpn = self.cps[t.counterparty_id].name if t.counterparty_id else "internal"  # type: ignore[attr-defined]
                lines.append(f"Transaction {t.id}: {t.kind} ${t.amount:,.2f} on {t.ts[:10]} into account {a.id} "  # type: ignore[attr-defined]
                             f"(client {cl.id}, {cl.segment}) from {cpn}; flags: {', '.join(t.flags)}")  # type: ignore[attr-defined]
                allowed += [ph("ACCOUNT_NUMBER", a.id), ph("CLIENT_NAME", cl.id)]  # type: ignore[attr-defined]
        allowed = list(dict.fromkeys(allowed))
        if not lines:
            return "ALLOWED PLACEHOLDERS: none. Do not mention any client or personal details.", []
        block = "ENTITIES YOU MAY REFERENCE (use where natural):\n" + "\n".join(f"- {x}" for x in lines)
        block += ("\nALLOWED PLACEHOLDERS (copy exactly; use them for any personal or account detail you mention, "
                  "and use at least one where natural):\n" + (", ".join(allowed) if allowed else "none"))
        return block, allowed

    # ---- ground-truth mentions from the placeholder text
    def mentions(self, text: str, extra: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
        m: dict[str, set[str]] = defaultdict(set)
        for pm in PLACEHOLDER_RE.finditer(text):
            spec = PLACEHOLDERS.get(pm.group(1))
            if not spec:
                continue
            ent = spec[1]
            eid = pm.group(2)
            if ent == "client" and eid in self.clients:
                m["clients"].add(eid)
            elif ent == "account" and eid in self.accounts:
                m["accounts"].add(eid)
                m["clients"].add(self.accounts[eid].client_id)  # type: ignore[attr-defined]
            elif ent == "counterparty" and eid in self.cps:
                m["counterparties"].add(eid)
            elif ent == "employee" and eid in self.people:
                m["people"].add(eid)
        for nm in self.person_re.findall(text):
            m["people"].add(self.person_by_name[nm])
        for sn in self.system_re.findall(text):
            m["systems"].add(self.system_by_name[sn])
        if self.deal_re is not None:
            for dw in self.deal_re.findall(text):
                m["deals"].add(self.deal_by_word[dw])
        for tk in TICKET_KEY_RE.findall(text):
            m["tickets"].add(tk)
        for k, vs in (extra or {}).items():
            m[k].update(vs)
        return {k: sorted(v) for k, v in m.items() if v}


# ------------------------------------------------------------------------------------------- planners
def plan_beats(cx: Ctx, beats: list[Beat]) -> list[Job]:
    w = cx.w
    by_story: dict[str, list[Beat]] = defaultdict(list)
    for b in beats:
        by_story[b.storyline].append(b)
    ch = {c.id: c for c in w.channels}
    jobs = []
    for story, bs in by_story.items():
        for ci in range(0, len(bs), BEAT_CHUNK):
            chunk, earlier = bs[ci: ci + BEAT_CHUNK], bs[:ci]
            parts = [f'Write {len(chunk)} items for the "{STORY_TITLES.get(story, story)}" storyline at {w.name}. '
                     "Each brief is ground truth: include every fact in it, add realistic surrounding detail, and "
                     "never contradict it. Keep the items consistent with each other and with earlier items."]
            if earlier:
                parts.append("EARLIER ITEMS IN THIS STORYLINE (already written):\n" + "\n".join(
                    f"- {human(b.ts)} {b.kind} by {cx.name(b.author)}: {b.brief[:220]}" for b in earlier))
            kinds = sorted({b.kind for b in chunk})
            parts.append("FORMAT RULES:\n" + "\n".join(f"- {k}: {FORMAT_RULES[k]}" for k in kinds))
            blocks = []
            for b in chunk:
                lines = [f"### beat_id: {b.id}",
                         f"Kind: {b.kind}" + (f" ({b.ticket_key}, status {b.ticket_status})" if b.ticket_key else ""),
                         f"Date: {human(b.ts)}", f"Author: {cx.desc(b.author)}"]
                if b.channel:
                    lines.append(f"Channel: {ch[b.channel].name}" + (" (reply in an existing thread)"
                                                                     if b.thread and b != _first_in_thread(bs, b) else ""))
                if b.recipients:
                    lines.append("To: " + ", ".join(cx.desc(p) for p in b.recipients))
                if b.cc:
                    lines.append("Cc: " + ", ".join(cx.desc(p) for p in b.cc))
                if b.attendees:
                    lines.append("Attendees: " + ", ".join(cx.desc(p) for p in b.attendees))
                if b.assignee:
                    lines.append(f"Assignee: {cx.desc(b.assignee)}")
                if b.client_id:
                    cl = cx.clients[b.client_id]
                    lines.append(f"Client: {cl.id} ({cl.kind}, {cl.segment}, onboarded {cl.onboarded})")  # type: ignore[attr-defined]
                lines.append(f"Title: {b.title}" if b.title else
                             ("Title: (leave empty)" if b.kind == "slack" else "Title: (write a fitting title)"))
                if b.placeholders:
                    lines.append("Required placeholders (use each, copied exactly): " + ", ".join(b.placeholders))
                lines.append(f"Brief: {b.brief}")
                blocks.append("\n".join(lines))
            parts.append("ITEMS:\n\n" + "\n\n".join(blocks))
            parts.append('Return JSON {"items": [{"beat_id", "title", "text"}]} with exactly these beat_ids in this '
                         "order: " + ", ".join(b.id for b in chunk))
            prompt = "\n\n".join(parts)
            jobs.append(Job(f"beats_{story}_{ci // BEAT_CHUNK}_{sha8(prompt)}", "beats", "beats", prompt,
                            get_settings().model_deep, MAX_TOKENS["beats"], {"beat_ids": [b.id for b in chunk]}))
    return jobs


def _first_in_thread(bs: list[Beat], b: Beat) -> Beat:
    return next(x for x in bs if x.thread == b.thread)


def plan_slack(cx: Ctx, n_beats: int) -> list[Job]:
    w, rng = cx.w, cx.rng
    cfg = CHANNELS[w.tenant]
    size = CONV_SIZE[w.tenant]
    target = TENANTS[w.tenant].scale.slack - n_beats
    alloc = allocate(max(1, round(target / size)), {k: v[0] for k, v in cfg.items()})
    chans = {c.name: c for c in w.channels}
    jobs = []
    for cname, n_conv in alloc.items():
        c = chans[cname]
        _, seeds, pools, note = cfg[cname]
        seeds = seeds[:]
        rng.shuffle(seeds)
        days = [d for d in cx.days if cname != "#project-falcon" or d >= date(2026, 8, 4)]
        for i in range(n_conv):
            day = rng.choice(days)
            pool = [p for p in c.member_ids if cx.active(p, day)]
            parts = rng.sample(pool, min(len(pool), rng.randint(3, 6)))
            hooks, allowed = cx.hooks(pools)
            n = max(4, size + rng.randint(-4, 4))
            hh = rng.randint(8, 15)
            prompt = (f"Write a realistic Slack conversation in {cname} ({c.topic}) at {w.name} on "
                      f"{day:%A %Y-%m-%d}, starting around {hh}:00 ET.\nTopic: {seeds[i % len(seeds)]}.\n"
                      + (f"Context: {note}\n" if note else "")
                      + "Participants (author_id must be one of these):\n" + "\n".join(f"- {cx.desc(p)}" for p in parts)
                      + f"\n{hooks}\n"
                      f"Write exactly {n} messages. minutes_after_start is non-decreasing, between 0 and 300. reply_to "
                      "is the 0-based index of an earlier message this one replies to in a thread, or -1 for a "
                      "top-level channel message. Mix top-level messages and thread replies. Each message is 1-60 "
                      "words in natural Slack style.")
            jobs.append(Job(f"slack_{c.id}_{i:03d}_{sha8(prompt)}", "slack", "slack", prompt,
                            get_settings().model_default, MAX_TOKENS["slack"],
                            {"channel": c.id, "day": day.isoformat(), "hour": hh, "participants": parts}))
    return jobs


def plan_email(cx: Ctx, n_beats: int) -> list[Job]:
    w, rng = cx.w, cx.rng
    cfg = EMAIL_TEAMS[w.tenant]
    target = TENANTS[w.tenant].scale.email - n_beats - 4  # 4 HR emails below
    n_batches = max(1, math.ceil(target / EMAIL_BATCH))
    alloc = allocate(n_batches, {k: v[0] for k, v in cfg.items()})
    leads = [cx.team_lead[t] for t in cx.team_lead]
    jobs = []
    for team, nb in alloc.items():
        seeds = cfg[team][1]
        for i in range(nb):
            dates = sorted(rng.sample(cx.days, EMAIL_BATCH))
            senders = [p for p in cx.team_members[team] if cx.active(p, dates[-1])]
            recips = list(dict.fromkeys(senders + [x for x in leads if cx.active(x, dates[-1])]))
            hooks, _ = cx.hooks(EMAIL_HOOKS.get(team, []))
            topics = [rng.choice(seeds) for _ in dates]
            prompt = (f"Write {EMAIL_BATCH} distinct internal emails at {w.name} involving the {cx.team_name[team]} "
                      "team. Email i is sent on the date shown, about the topic shown (adapt freely, make each "
                      "specific):\n" + "\n".join(f"{k + 1}. {d:%a %Y-%m-%d} - {t}" for k, (d, t) in
                                                  enumerate(zip(dates, topics)))
                      + "\nPossible senders (from_id):\n" + "\n".join(f"- {cx.desc(p)}" for p in senders)
                      + "\nPossible recipients (to_ids / cc_ids, 1-4 recipients, cc optional):\n"
                      + "\n".join(f"- {cx.desc(p)}" for p in recips)
                      + f"\n{hooks}\nSet reply_to_previous=true only when an email replies to the email immediately "
                      "before it in this list (its subject then starts with 'Re: ' and it is sent by one of that "
                      "email's recipients). Bodies are 60-250 words.")
            jobs.append(Job(f"email_{team}_{i:03d}_{sha8(prompt)}", "email", "email", prompt,
                            get_settings().model_default, MAX_TOKENS["email"],
                            {"team": team, "dates": [d.isoformat() for d in dates]}))
    # HR batch: the only place employee salary / medical / home address placeholders appear.
    hr_people = [p for p in w.people if p.medical_note][:2] + rng.sample(
        [p for p in w.people if not p.medical_note and p.manager_id], 2)
    sender = w.cast.get("coo") or w.cast["ceo"]
    dates = sorted(rng.sample(cx.days, 4))
    lines, allowed = [], []
    for k, (p, d) in enumerate(zip(hr_people, dates)):
        if p.medical_note:
            topic, phs = "coordinating an approved medical leave and coverage", [ph("EMP_MEDICAL", p.id)]
        elif k == 2:
            topic, phs = "compensation adjustment after the mid-year review", [ph("EMP_SALARY", p.id)]
        else:
            topic, phs = "updating payroll records after a home move", [ph("EMP_HOME_ADDRESS", p.id)]
        mgr = p.manager_id or sender
        lines.append(f"{k + 1}. {d:%a %Y-%m-%d} - to {cx.desc(p.id)}, cc their manager {cx.desc(mgr)}: {topic}. "
                     f"Allowed placeholders: {', '.join(phs)}")
        allowed += phs
    prompt = (f"Write 4 short confidential HR emails at {w.name}, all sent by {cx.desc(sender)}:\n" + "\n".join(lines)
              + "\nUse the listed placeholders for the sensitive details (copy exactly). to_ids/cc_ids must use the "
              "employee ids shown. reply_to_previous is false. Bodies are 60-160 words, discreet and professional.")
    jobs.append(Job(f"email_hr_000_{sha8(prompt)}", "email", "email", prompt, get_settings().model_default,
                    MAX_TOKENS["email"], {"team": "exec", "dates": [d.isoformat() for d in dates], "hr": True,
                                          "sender": sender}))
    return jobs


def plan_ticket(cx: Ctx, n_beats: int) -> list[Job]:
    w, rng = cx.w, cx.rng
    cfg = TICKET_PROJECTS[w.tenant]
    target = TENANTS[w.tenant].scale.tickets - n_beats
    alloc = allocate(max(1, math.ceil(target / TICKET_BATCH)), {k: v[1] for k, v in cfg.items()})
    jobs = []
    for proj, nb in alloc.items():
        teams, _, seeds, pools = cfg[proj]
        for i in range(nb):
            dates = sorted(rng.sample(cx.days, TICKET_BATCH))
            pool = [p for t in teams for p in cx.team_members[t] if cx.active(p, dates[-1])]
            hooks, _ = cx.hooks(pools)
            extra = ""
            if proj in ("FRAUD", "BSA"):
                extra = ("These are automated alerts on the transactions listed above; after review the analyst "
                         "concludes each is legitimate (false positive) and explains why.\n")
            prompt = (f"Write {TICKET_BATCH} distinct {proj} tickets at {w.name} ("
                      + ", ".join(cx.team_name[t] for t in teams) + " teams). Ticket i is created on the date shown "
                      "with the seed topic shown (make each concrete and different):\n"
                      + "\n".join(f"{k + 1}. {d:%a %Y-%m-%d} - {rng.choice(seeds)}" for k, d in enumerate(dates))
                      + "\nreporter_id, assignee_id, and comment author_id must come from:\n"
                      + "\n".join(f"- {cx.desc(p)}" for p in pool)
                      + f"\n{hooks}\n{extra}0-4 comments per ticket. Tickets older than three weeks before "
                      f"{END.isoformat()} are usually resolved or closed; recent ones may be open or in progress. "
                      "Descriptions are 40-180 words; comments 10-80 words. Do not put ticket keys in titles.")
            jobs.append(Job(f"ticket_{proj}_{i:03d}_{sha8(prompt)}", "ticket", "ticket", prompt,
                            get_settings().model_default, MAX_TOKENS["ticket"],
                            {"project": proj, "teams": teams, "dates": [d.isoformat() for d in dates], "pool": pool}))
    return jobs


def plan_doc(cx: Ctx) -> list[Job]:
    w, rng = cx.w, cx.rng
    jobs = []
    for i, (title, dtype, team, pool) in enumerate(DOCS[w.tenant]):
        day = rng.choice([d for d in cx.days if d < date(2026, 10, 1)])
        cands = [p for p in cx.team_members[team] if cx.active(p, day)]
        author = cx.team_lead[team] if rng.random() < 0.4 else rng.choice(cands)
        hooks, _ = cx.hooks([pool] if pool else [])
        prompt = (f'Write the internal document "{title}" ({dtype}) for {w.name}, authored by {cx.desc(author)}, last '
                  f"updated {day.isoformat()}.\n{hooks}\n300-800 words, plain text with short section titles on "
                  "their own lines (no markdown symbols). Be specific to the company's teams, systems, and processes. "
                  "Return the title (you may refine it slightly) and the body.")
        jobs.append(Job(f"doc_{i:03d}_{sha8(prompt)}", "doc", "doc", prompt, get_settings().model_default,
                        MAX_TOKENS["doc"], {"author": author, "day": day.isoformat(), "doc_type": dtype,
                                            "team": team, "title": title}))
    return jobs


def plan_meeting(cx: Ctx) -> list[Job]:
    w, rng = cx.w, cx.rng
    jobs = []
    for i, (title, team) in enumerate(MEETINGS[w.tenant]):
        day = rng.choice(cx.days)
        if team in ("all", "exec"):  # leadership meetings: exec office plus every team lead
            pool = list(dict.fromkeys(cx.team_members["exec"] + [cx.team_lead[t] for t in cx.team_lead]))
        else:
            pool = list(dict.fromkeys(cx.team_members[team] + [cx.team_lead[team]]))
        pool = [p for p in pool if cx.active(p, day)]
        att = rng.sample(pool, min(len(pool), rng.randint(4, 7)))
        hooks, _ = cx.hooks(EMAIL_HOOKS.get(team, []))
        hh = rng.randint(9, 16)
        prompt = (f'Write a transcript of the meeting "{title}" at {w.name} on {day:%A %Y-%m-%d} at {hh}:00 ET.\n'
                  "Attendees:\n" + "\n".join(f"- {cx.desc(p)}" for p in att)
                  + f"\n{hooks}\nFormat: one utterance per line as 'Full Name: text'. 400-900 words. Cover concrete "
                  "agenda items, decisions, and action items with owners.")
        jobs.append(Job(f"meeting_{i:03d}_{sha8(prompt)}", "meeting", "meeting", prompt, get_settings().model_default,
                        MAX_TOKENS["meeting"], {"attendees": att, "day": day.isoformat(), "hour": hh, "title": title}))
    return jobs


def plan_crm(cx: Ctx, n_beats: int) -> list[Job]:
    w, rng = cx.w, cx.rng
    target = TENANTS[w.tenant].scale.crm - n_beats
    clients = rng.sample(cx.bg_clients, min(target, len(cx.bg_clients)))
    while len(clients) < target:
        clients.append(rng.choice(cx.bg_clients))
    jobs = []
    for bi in range(0, len(clients), CRM_BATCH):
        batch = clients[bi: bi + CRM_BATCH]
        dates = [rng.choice([d for d in cx.days if d.isoformat() >= c.onboarded] or cx.days) for c in batch]
        lines = []
        for k, (c, d) in enumerate(zip(batch, dates)):
            accts = cx.acct_by_client.get(c.id, [])[:2]
            phs = [ph("CLIENT_NAME", c.id), ph("CLIENT_PHONE", c.id), ph("CLIENT_EMAIL", c.id),
                   ph("CLIENT_ADDRESS", c.id)] + [ph("ACCOUNT_NUMBER", a.id) for a in accts]
            lines.append(f"{k + 1}. client_id {c.id} - RM {cx.desc(c.rm_id)}; {c.kind}, {c.segment}, risk "
                         f"{c.risk_rating}, client since {c.onboarded}"
                         + (", has asked about crypto" if c.crypto_interest else "")
                         + f"; accounts: {', '.join(f'{a.id} ({a.kind})' for a in accts) or 'none'}; date "
                         f"{d.isoformat()}; topic: {rng.choice(CRM_SEEDS)}; allowed placeholders: {', '.join(phs)}")
        prompt = (f"Write one CRM note per client below at {w.name}, each by that client's relationship manager on the "
                  "date shown. 40-120 words each, written as the RM's note about the interaction. Refer to the client "
                  "as {{CLIENT_NAME:<id>}} and use the allowed placeholders for any personal detail (copy exactly).\n"
                  + "\n".join(lines) + "\nReturn notes with client_id exactly as listed, in this order.")
        jobs.append(Job(f"crm_{bi // CRM_BATCH:03d}_{sha8(prompt)}", "crm", "crm", prompt,
                        get_settings().model_default, MAX_TOKENS["crm"],
                        {"clients": [c.id for c in batch], "dates": [d.isoformat() for d in dates]}))
    return jobs


def plan_all(cx: Ctx, beats: list[Beat]) -> list[Job]:
    nb = Counter(b.kind for b in beats)
    return (plan_beats(cx, beats) + plan_slack(cx, nb["slack"]) + plan_email(cx, nb["email"])
            + plan_ticket(cx, nb["ticket"]) + plan_doc(cx) + plan_meeting(cx) + plan_crm(cx, nb["crm"]))


# ------------------------------------------------------------------------------------------- execution
async def run_jobs(w: World, jobs: list[Job], max_cost: float) -> tuple[dict[str, dict], list[str]]:
    results: dict[str, dict] = {}
    failed: list[str] = []
    done = 0

    async def one(job: Job) -> None:
        nonlocal done
        if not writers.cache_path(w.tenant, job.key).is_file() and COSTS.snapshot()["total_usd"] > max_cost:
            failed.append(job.key + " (skipped: max cost)")
            return
        try:
            results[job.key] = await writers.run_job(w, job.key, job.schema, job.prompt, job.model, job.max_tokens)
        except Exception as e:  # noqa: BLE001 - one failed batch must not sink the run
            failed.append(job.key)
            log.error("job %s failed: %s", job.key, str(e)[:300])
        done += 1
        if done % 10 == 0 or done == len(jobs):
            log.info("progress %d/%d, spend $%.3f", done, len(jobs), COSTS.snapshot()["total_usd"])

    await asyncio.gather(*(one(j) for j in jobs))
    return results, failed


# ------------------------------------------------------------------------------------------- assembly
class Assembler:
    def __init__(self, cx: Ctx, beats: list[Beat]):
        self.cx = cx
        self.beats = {b.id: b for b in beats}
        self.raw: list[dict] = []
        self.stats: Counter[str] = Counter()

    def add(self, **d) -> dict:
        d.setdefault("_thread", d["_k"])
        self.raw.append(d)
        return d

    def _pid(self, pid: str, fallback: str) -> str:
        return pid if pid in self.cx.people else fallback

    def beats_out(self, out: dict) -> None:
        for it in out.get("items", []):
            b = self.beats.get(it.get("beat_id", ""))
            if b is None:
                self.stats["beat_unknown_id"] += 1
                continue
            text = it.get("text", "").strip()
            missing = [p for p in b.placeholders if p not in text]
            if missing:  # required facts the writer dropped: append them so ground truth holds
                self.stats["beat_placeholder_appended"] += len(missing)
                text += "\n" + " ".join(missing)
            title = (b.title or it.get("title") or "").strip() if b.kind != "slack" else ""
            ext = b.author if b.author.startswith("ext:") else None
            body = text
            ticket = None
            if b.kind == "ticket":
                ticket = {"key": b.ticket_key, "status": b.ticket_status, "priority": "P2" if "FRAUD" in (
                    b.ticket_key or "") else "P1" if "INC" in (b.ticket_key or "") else "P3",
                          "reporter_id": b.author, "assignee_id": b.assignee, "project": b.ticket_key.split("-")[0]}  # type: ignore[union-attr]
                body = self._ticket_text(ticket, title, text)
            self.add(_k=f"beat:{b.id}", _beat=b, kind=b.kind, ts=b.ts, author_id=None if ext else b.author,
                     external_author=ext[4:] if ext else None, title=title, text=body, channel_id=b.channel,
                     recipients=b.recipients, cc=b.cc, attendees=b.attendees, ticket=ticket, client_id=b.client_id,
                     doc_type=b.doc_type, sensitivity=b.sensitivity, storyline=b.storyline, beat_id=b.id,
                     decision=b.decision, _extra=b.mentions)

    def _ticket_text(self, t: dict, title: str, body: str) -> str:
        cx = self.cx
        head = (f"[{t['key']}] {title}\nStatus: {t['status']} | Priority: {t['priority']} | Reporter: "
                f"{cx.name(t['reporter_id'])} | Assignee: {cx.name(t['assignee_id']) if t['assignee_id'] else '-'}")
        return f"{head}\n\n{body.strip()}"

    def slack_out(self, job: Job, out: dict) -> None:
        msgs = sorted(enumerate(out.get("messages", [])), key=lambda x: (x[1].get("minutes_after_start", 0), x[0]))
        day = date.fromisoformat(job.meta["day"])
        parts = job.meta["participants"]
        roots: dict[int, str] = {}
        for idx, msg in msgs:
            k = f"{job.key}#{idx}"
            minutes = max(0, min(int(msg.get("minutes_after_start", 0)), 600))
            hh, mm = divmod(job.meta["hour"] * 60 + minutes, 60)
            ts = iso(datetime(day.year, day.month, day.day, min(hh, 23), mm, tzinfo=EDT))
            r = msg.get("reply_to", -1)
            if isinstance(r, int) and 0 <= r < idx and r in roots:
                thread, reply = roots[r], f"{job.key}#{r}"
            else:
                thread, reply = k, None
            roots[idx] = thread
            self.add(_k=k, _thread=thread, _reply=reply, kind="slack", ts=ts,
                     author_id=self._pid(msg.get("author_id", ""), parts[idx % len(parts)]), title="",
                     text=msg.get("text", "").strip(), channel_id=job.meta["channel"])

    def email_out(self, job: Job, out: dict) -> None:
        dates = job.meta["dates"]
        lead = self.cx.team_lead.get(job.meta["team"], next(iter(self.cx.people)))
        prev = None
        for i, e in enumerate(out.get("emails", [])[: len(dates)]):
            k = f"{job.key}#{i}"
            sender = job.meta.get("sender") or self._pid(e.get("from_id", ""), lead)
            to = [p for p in e.get("to_ids", []) if p in self.cx.people and p != sender] or [lead]
            cc = [p for p in e.get("cc_ids", []) if p in self.cx.people and p not in to and p != sender]
            ts = ts_on(self.cx.rng, date.fromisoformat(dates[i]), 7, 19)
            thread, reply = k, None
            if e.get("reply_to_previous") and prev is not None:
                thread, reply = prev["_thread"], prev["_k"]
                ts = max(ts, prev["ts"])
            prev = self.add(_k=k, _thread=thread, _reply=reply, kind="email", ts=ts, author_id=sender,
                            title=e.get("subject", "").strip(), text=e.get("body", "").strip(), recipients=to, cc=cc,
                            sensitivity="confidential" if job.meta.get("hr") else None)

    def ticket_out(self, job: Job, out: dict) -> None:
        pool = job.meta["pool"]
        for i, t in enumerate(out.get("tickets", [])[: len(job.meta["dates"])]):
            rep = t.get("reporter_id") if t.get("reporter_id") in pool else pool[i % len(pool)]
            asg = t.get("assignee_id") if t.get("assignee_id") in pool else rep
            comments = "".join(f"\n\n--- Comment by {self.cx.name(self._pid(c.get('author_id', ''), asg))} ---\n"
                               f"{c.get('text', '').strip()}" for c in t.get("comments", []))
            ticket = {"key": None, "status": t.get("status", "open"), "priority": t.get("priority", "P3"),
                      "reporter_id": rep, "assignee_id": asg, "project": job.meta["project"]}
            self.add(_k=f"{job.key}#{i}", kind="ticket",
                     ts=ts_on(self.cx.rng, date.fromisoformat(job.meta["dates"][i])), author_id=rep,
                     title=t.get("title", "").strip(), text=t.get("description", "").strip() + comments,
                     ticket=ticket, _teams=job.meta["teams"])

    def doc_out(self, job: Job, out: dict) -> None:
        m = job.meta
        self.add(_k=job.key, kind="doc", ts=ts_on(self.cx.rng, date.fromisoformat(m["day"]), 9, 18),
                 author_id=m["author"], title=(out.get("title") or m["title"]).strip(), text=out.get("body", "").strip(),
                 doc_type=m["doc_type"])

    def meeting_out(self, job: Job, out: dict) -> None:
        m = job.meta
        d = date.fromisoformat(m["day"])
        self.add(_k=job.key, kind="meeting", ts=iso(datetime(d.year, d.month, d.day, m["hour"], 0, tzinfo=EDT)),
                 author_id=m["attendees"][0], title=(out.get("title") or m["title"]).strip(),
                 text=out.get("transcript", "").strip(), attendees=m["attendees"])

    def crm_out(self, job: Job, out: dict) -> None:
        notes = {n.get("client_id"): n.get("text", "") for n in out.get("notes", [])}
        for i, (cid, d) in enumerate(zip(job.meta["clients"], job.meta["dates"])):
            if cid not in notes:
                self.stats["crm_missing_note"] += 1
                continue
            cl = self.cx.clients[cid]
            self.add(_k=f"{job.key}#{i}", kind="crm", ts=ts_on(self.cx.rng, date.fromisoformat(d), 9, 18),
                     author_id=cl.rm_id, title="", text=notes[cid].strip(), client_id=cid)  # type: ignore[attr-defined]

    # ---- finalize: ids, threads, ticket keys, scrub, fill, ACLs
    def finalize(self) -> tuple[list[dict], list[dict]]:
        cx, w = self.cx, self.cx.w
        falcon_ch = next((c.id for c in w.channels if c.name == "#project-falcon"), None)
        chan = {c.id: c for c in w.channels}
        kept = []
        for d in self.raw:
            if not d["text"]:
                self.stats["dropped_empty"] += 1
                continue
            if "_beat" not in d and d.get("channel_id") != falcon_ch and MNPI_RE.search(d["text"] + d["title"]):
                if not (d.get("channel_id") and chan[d["channel_id"]].name == "#ma-team"
                        and "Falcon" not in d["text"]):
                    self.stats["dropped_bg_mnpi_mention"] += 1
                    continue
            kept.append(d)
        kept.sort(key=lambda d: (d["ts"], d["_k"]))
        # ticket keys for background tickets, numbered in time order per project
        seq: Counter[str] = Counter()
        for d in kept:
            if d["kind"] == "ticket" and d["ticket"]["key"] is None:
                proj = d["ticket"]["project"]
                n = TICKET_BASE[w.tenant][proj] + seq[proj]
                seq[proj] += 1
                d["ticket"]["key"] = f"{proj}-{n:0{TICKET_WIDTH.get(proj, 3)}d}"
                d["text"] = self._ticket_text(d["ticket"], d["title"], d["text"])
        counters: Counter[str] = Counter()
        idmap: dict[str, str] = {}
        for d in kept:
            counters[d["kind"]] += 1
            idmap[d["_k"]] = f"{d['kind']}-{counters[d['kind']]:05d}"
        # beat threads: chain beats sharing a thread key
        beat_thread_root: dict[str, str] = {}
        beat_thread_last: dict[str, str] = {}
        docs, spans = [], []
        for d in kept:
            doc_id = idmap[d["_k"]]
            b: Beat | None = d.get("_beat")
            thread_id, reply_to = idmap.get(d.get("_thread", ""), doc_id), idmap.get(d.get("_reply") or "")
            if b is not None and b.thread:
                if b.thread in beat_thread_root:
                    thread_id, reply_to = beat_thread_root[b.thread], beat_thread_last[b.thread]
                else:
                    beat_thread_root[b.thread] = doc_id
                beat_thread_last[b.thread] = doc_id
            text = self._scrub(d["text"])
            title = self._scrub(d["title"])
            mentions = cx.mentions(title + "\n" + text, d.get("_extra"))
            filled, sp, unresolved = fill_placeholders(text, doc_id, w.resolve_pii)
            tfilled, tsp, tun = fill_placeholders(title, doc_id + ":title", w.resolve_pii)
            self.stats["unresolved_placeholders"] += len(unresolved) + len(tun)
            for s in sp:
                spans.append(s.to_dict() | {"field": "text"})
            for s in tsp:
                spans.append(s.to_dict() | {"doc_id": doc_id, "field": "title"})
            author = d.get("author_id")
            if author:
                mentions.setdefault("people", [])
                if author not in mentions["people"]:
                    mentions["people"] = sorted(mentions["people"] + [author])
            sens = d.get("sensitivity") or self._sensitivity(d, text)
            doc = {
                "doc_id": doc_id, "tenant": w.tenant, "kind": d["kind"], "ts": d["ts"], "live_feed": is_live(d["ts"]),
                "author_id": author, "external_author": d.get("external_author"), "title": tfilled, "text": filled,
                "channel_id": d.get("channel_id"), "thread_id": thread_id if d["kind"] in ("slack", "email") else None,
                "reply_to": reply_to if d["kind"] in ("slack", "email") else None,
                "recipients": d.get("recipients") or [], "cc": d.get("cc") or [], "attendees": d.get("attendees") or [],
                "ticket": d.get("ticket"), "client_id": d.get("client_id"), "doc_type": d.get("doc_type"),
                "sensitivity": sens, "storyline": d.get("storyline"), "beat_id": d.get("beat_id"),
                "decision": d.get("decision"), "mentions": mentions,
            }
            doc["acl"] = self._acl(doc, d)
            docs.append({k: v for k, v in doc.items() if v not in (None, [], {})} | {"live_feed": doc["live_feed"]})
        return docs, spans

    def _scrub(self, text: str) -> str:
        """Remove PII-like digit strings the LLM invented (placeholders are untouched: they contain no digits runs)."""
        found = find_pii(text)
        if not found:
            return text
        self.stats["llm_invented_pii_scrubbed"] += len(found)
        for f in reversed(found):
            text = text[: f.start] + "(on file)" + text[f.end:]
        return text

    def _sensitivity(self, d: dict, text: str) -> str:
        chan = {c.id: c for c in self.cx.w.channels}
        if d.get("channel_id") and chan[d["channel_id"]].sensitivity == "MNPI":
            return "MNPI"
        if MNPI_RE.search(text) and "Falcon" in text + d["title"]:
            return "MNPI"
        if d.get("channel_id") and chan[d["channel_id"]].sensitivity == "confidential":
            return "confidential"
        return "internal"

    def _acl(self, doc: dict, d: dict) -> dict:
        w = self.cx.w
        kind = doc["kind"]
        if kind == "slack":
            return {"channel": doc["channel_id"]}
        if kind == "email":
            return {"people": sorted({p for p in [doc.get("author_id"), *doc["recipients"], *doc["cc"]] if p})}
        if kind == "meeting":
            return {"people": sorted(doc["attendees"])}
        if kind == "ticket":
            teams = d.get("_teams") or self._ticket_teams(doc["ticket"]["project"])
            return {"teams": teams, "people": sorted({doc["ticket"]["reporter_id"], doc["ticket"]["assignee_id"]})}
        if kind == "crm":
            return {"teams": CRM_TEAMS[w.tenant]}
        if doc["sensitivity"] == "MNPI":
            falcon = next((x for x in w.deals if x.id == "deal_falcon"), None)
            ppl = set(falcon.team_ids) if falcon else set()
            return {"people": sorted(ppl | {w.cast["cco"]})}
        return {"public": True}

    def _ticket_teams(self, proj: str) -> list[str]:
        cfg = TICKET_PROJECTS[self.cx.w.tenant]
        if proj in cfg:
            return cfg[proj][0]
        return {"FRAUD": ["compliance"], "BSA": ["compliance"], "INC": ["it", "ops"], "ITOPS": ["it"],
                "COMP": ["compliance"], "KYC": ["ops", "compliance"]}.get(proj, ["it"])


def ground_truth(w: World, docs: list[dict]) -> dict:
    beat_doc = {d["beat_id"]: d["doc_id"] for d in docs if d.get("beat_id")}
    decisions: dict[str, dict] = {}
    for d in docs:
        dec = d.get("decision")
        if not dec:
            continue
        g = decisions.setdefault(dec["id"], {"decided_in": [], "documents": []})
        g["documents"].append(d["doc_id"])
        if dec.get("role") == "decided_in":
            g["decided_in"].append(d["doc_id"])
        else:
            g.update({k: v for k, v in dec.items() if k not in ("id", "justified_by", "role")})
            g["justified_by"] = [beat_doc[b] for b in dec.get("justified_by", []) if b in beat_doc]
    story_docs: dict[str, list[str]] = defaultdict(list)
    for d in docs:
        if d.get("storyline"):
            story_docs[d["storyline"]].append(d["doc_id"])
    return {"tenant": w.tenant, "refs": w.refs, "cast": w.cast, "beat_docs": beat_doc,
            "storyline_docs": dict(story_docs), "decisions": decisions,
            "mnpi_docs": [d["doc_id"] for d in docs if d.get("sensitivity") == "MNPI"],
            "mnpi_outside_deal_team": [d["doc_id"] for d in docs if d.get("beat_id") in ("fa_leak_email", "fa_leak_slack")]}


def write_outputs(w: World, docs: list[dict], spans: list[dict], stats: Counter, failed: list[str]) -> None:
    out = RAW_ROOT / w.tenant
    out.mkdir(parents=True, exist_ok=True)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_kind[d["kind"]].append(d)
    for kind in ("slack", "email", "ticket", "doc", "meeting", "crm"):
        with (out / f"{kind}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for d in by_kind.get(kind, []):
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with (out / "pii_spans.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for s in spans:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    (out / "ground_truth.json").write_text(json.dumps(ground_truth(w, docs), indent=1, ensure_ascii=False),
                                           encoding="utf-8")
    manifest = {
        "tenant": w.tenant, "synthetic": True,
        "note": "All people, organizations, clients, identifiers, and events are fictional.",
        "counts": {k: len(v) for k, v in sorted(by_kind.items())},
        "live_feed_counts": {k: sum(1 for d in v if d["live_feed"]) for k, v in sorted(by_kind.items())},
        "pii_spans_by_type": dict(Counter(s["type"] for s in spans).most_common()),
        "stats": dict(stats), "failed_jobs": failed, "llm_cost": COSTS.snapshot(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log.info("wrote %s: %s", out, manifest["counts"])
    log.info("pii spans: %s", manifest["pii_spans_by_type"])
    log.info("stats: %s", dict(stats))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True, choices=sorted(TENANTS))
    ap.add_argument("--kinds", default=",".join(KINDS), help="comma list of job kinds to run")
    ap.add_argument("--limit", type=int, default=0, help="max jobs per kind (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--max-cost", type=float, default=30.0, help="stop starting new LLM jobs above this USD spend")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    get_settings().llm_concurrency = args.concurrency

    w = World.load(WORLD_ROOT / args.tenant / "world.json")
    cx = Ctx(w, random.Random(TENANTS[args.tenant].seed + 101))
    beats = build_beats(w)
    jobs = plan_all(cx, beats)
    kinds = set(args.kinds.split(","))
    selected, per_kind = [], Counter()
    for j in jobs:
        if j.kind in kinds and (not args.limit or per_kind[j.kind] < args.limit):
            selected.append(j)
            per_kind[j.kind] += 1
    cached = sum(1 for j in selected if writers.cache_path(w.tenant, j.key).is_file())
    log.info("%s: %d beats, %d jobs planned %s; selected %d (%d cached)", w.tenant, len(beats), len(jobs),
             dict(Counter(j.kind for j in jobs)), len(selected), cached)
    if args.dry_run:
        seen = set()
        for j in jobs:
            if j.kind not in seen:
                seen.add(j.kind)
                print(f"\n===== sample {j.kind} prompt ({j.key}, {j.model}) =====\n{j.prompt[:2500]}")
        return

    results, failed = await run_jobs(w, selected, args.max_cost)
    # assemble from everything available in the cache (previous runs included)
    asm = Assembler(cx, beats)
    for j in jobs:
        out = results.get(j.key)
        if out is None and writers.cache_path(w.tenant, j.key).is_file():
            out = json.loads(writers.cache_path(w.tenant, j.key).read_text(encoding="utf-8"))
        if out is None:
            asm.stats[f"missing_{j.kind}_jobs"] += 1
            continue
        getattr(asm, f"{j.kind}_out")(j, out) if j.kind != "beats" else asm.beats_out(out)
    docs, spans = asm.finalize()
    write_outputs(w, docs, spans, asm.stats, failed)
    log.info("LLM cost this run: %s", COSTS.snapshot())


if __name__ == "__main__":
    asyncio.run(main())
