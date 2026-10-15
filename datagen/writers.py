"""LLM content writers. Prompts carry placeholders, never PII values; outputs are cached on disk."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.config import REPO_ROOT
from backend.llm.client import chat_json, system_block
from datagen.world import World

log = logging.getLogger(__name__)

CACHE_ROOT = REPO_ROOT / "data" / "gen_cache"

_FIRM_DESC = {
    "halcyon": "a mid-sized New York capital markets, wealth management, lending, and M&A advisory firm",
    "brightwater": "a community credit union in North Carolina",
}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


S = {"type": "string"}
I = {"type": "integer"}

SCHEMAS: dict[str, dict] = {
    "beats": _obj({"items": _arr(_obj({"beat_id": S, "title": S, "text": S}))}),
    "slack": _obj({"messages": _arr(_obj({"author_id": S, "minutes_after_start": I, "reply_to": I, "text": S}))}),
    "email": _obj({"emails": _arr(_obj({"from_id": S, "to_ids": _arr(S), "cc_ids": _arr(S), "subject": S, "body": S,
                                        "reply_to_previous": {"type": "boolean"}}))}),
    "ticket": _obj({"tickets": _arr(_obj({
        "title": S, "description": S, "reporter_id": S, "assignee_id": S,
        "status": {"type": "string", "enum": ["open", "in_progress", "resolved", "closed"]},
        "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
        "comments": _arr(_obj({"author_id": S, "text": S})),
    }))}),
    "doc": _obj({"title": S, "body": S}),
    "meeting": _obj({"title": S, "transcript": S}),
    "crm": _obj({"notes": _arr(_obj({"client_id": S, "text": S}))}),
}

_SYSTEM_CACHE: dict[str, str] = {}


def system_prompt(w: World) -> str:
    if w.tenant in _SYSTEM_CACHE:
        return _SYSTEM_CACHE[w.tenant]
    teams = {t.id: t.name for t in w.teams}
    hist: dict[str, list] = {}
    for o in sorted(w.ownership, key=lambda o: o.valid_from):
        hist.setdefault(o.system_id, []).append(o)

    def owner(sid: str) -> str:
        rows = hist.get(sid, [])
        if not rows:
            return "owner team unassigned"
        if len(rows) == 1:
            return f"owner team {teams[rows[0].team_id]}"
        return "; ".join(f"owner team {teams[o.team_id]} " + (f"until {o.valid_to}" if o.valid_to else f"from {o.valid_from}")
                         for o in rows)

    directory = "\n".join(f"{p.id} | {p.name} | {p.title} | {teams[f'team_{p.team_id}']}"
                          + (f" | leaves {p.end_date}" if p.end_date else "") for p in w.people)
    sysname = {s.id: s.name for s in w.systems}
    systems = "\n".join(
        f"{s.name} ({s.tier}; {owner(s.id)}): {s.description}. "
        f"Depends on: {', '.join(sysname[d] for d in s.depends_on) or 'nothing'}" for s in w.systems)
    channels = "\n".join(f"{c.name} ({c.kind}): {c.topic}" for c in w.channels)
    text = f"""You write realistic internal workplace communications for {w.name}, {_FIRM_DESC[w.tenant]}. \
The company and every person, client, and number in it are fictional; the output tests an enterprise knowledge graph.

COMPANY DIRECTORY (employee id | name | title | team). You may name employees freely:
{directory}

SYSTEMS:
{systems}

SLACK CHANNELS:
{channels}

STRICT PRIVACY RULES (most important):
- Never write personal data values: no client names, phone numbers, personal emails, street addresses, SSNs, dates of \
birth, account/card/routing numbers, IBANs, SWIFT/BIC codes, EINs, device IDs, salaries, or medical details.
- Where such a detail naturally appears, write the exact placeholder offered in the request's ALLOWED PLACEHOLDERS, \
e.g. {{{{CLIENT_NAME:c_0042}}}}. Copy placeholders character for character. Never invent placeholder ids or types.
- Refer to clients only through {{{{CLIENT_NAME:<id>}}}} placeholders or neutral phrases such as "the client". Never \
invent a client's name.
- Do not write any other digit strings that look like phone, card, account, or identity numbers. Dollar amounts, \
dates, times, percentages, ticket keys, and internal ids such as c_0042 are fine.

STYLE:
- Sound like real colleagues: specific, concrete, varied in tone and length, with realistic financial and technical \
jargon. Slack is short and informal; email has a greeting and sign-off; no markdown headings except plain-text section \
titles in documents.
- Only employees from the directory appear as staff. External parties are named by organization only.
- Keep everything consistent with the dates you are given and with the system dependencies above.
- Never say the content is fictional or synthetic."""
    _SYSTEM_CACHE[w.tenant] = text
    return text


def cache_path(tenant: str, key: str) -> Path:
    return CACHE_ROOT / tenant / f"{key}.json"


async def run_job(w: World, key: str, schema: str, prompt: str, model: str, max_tokens: int) -> dict:
    path = cache_path(w.tenant, key)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    msgs = [system_block(system_prompt(w)), {"role": "user", "content": prompt}]
    out = await chat_json(msgs, SCHEMAS[schema], model=model, name=schema, max_tokens=max_tokens,
                          purpose=f"datagen_{schema}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
