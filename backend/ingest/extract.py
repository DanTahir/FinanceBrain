"""LLM knowledge extraction over redacted documents (Haiku 5.5, JSON-schema structured output).

Results are cached in data/extracted/<tenant>/extractions.jsonl, keyed by a hash of the model, prompt version,
system prompt and the exact redacted user message, so seeding and "Reset demo" make no LLM calls. Only redacted
text is sent (and the outbound guard in backend.llm re-checks every prompt).

    python -m backend.ingest.extract --tenant halcyon --tenant brightwater [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from backend.config import REPO_ROOT, get_settings
from backend.ingest.sources import load_redacted_docs, load_world
from backend.llm.client import COSTS, chat_json, system_block

log = logging.getLogger(__name__)

PROMPT_VERSION = "x1"
MIN_CHARS = 120  # shorter messages ("+1", "thanks!") carry no extractable knowledge
MAX_TEXT = 8000

SubjectType = Literal["person", "team", "system", "client", "account", "deal", "counterparty", "policy",
                      "process", "project", "other"]
Predicate = Literal["owned_by", "maintained_by", "expert_on", "depends_on", "status", "policy", "decision", "risk",
                    "deadline", "role", "assigned_to", "metric", "incident", "other"]


class XFact(BaseModel):
    subject: str = Field(description="Directory name, PII token, or short noun phrase the claim is about")
    subject_type: SubjectType
    predicate: Predicate
    object: str = Field(description="Short value of the claim: a name, token, status, date or phrase")
    statement: str = Field(description="One self-contained sentence stating the claim and naming the subject")
    valid_from: str = Field(description="YYYY-MM-DD when the claim became true; default to the document date")


class XDecision(BaseModel):
    summary: str
    status: Literal["made", "proposed", "reversed", "deferred"]
    effective: str = Field(description="YYYY-MM-DD, or an empty string if unknown")


class Extraction(BaseModel):
    topics: list[str]
    systems: list[str]
    people: list[str]
    facts: list[XFact]
    decisions: list[XDecision]


def empty() -> Extraction:
    return Extraction(topics=[], systems=[], people=[], facts=[], decisions=[])


def build_system_prompt(world: dict) -> str:
    teams = {t["id"]: t["name"] for t in world["teams"]}
    short = {tid.removeprefix("team_"): name for tid, name in teams.items()}
    lines = [
        f"You extract structured knowledge from the internal communications of {world['name']}, a financial "
        "firm, to build its company knowledge graph.",
        "",
        "Personal data has already been replaced by tokens such as ⟨NAME_0042⟩, ⟨PHONE_0042⟩ or ⟨ACCT_0007⟩. "
        "Copy tokens verbatim when they are the subject or object of a claim. Never guess what a token stands for.",
        "",
        "Return JSON with:",
        "- topics: 1-4 short lowercase noun phrases naming what the document is about (for example "
        "\"wire fraud alert\", \"kyc pipeline ownership\", \"crypto client onboarding\"). Prefer general, "
        "reusable phrasing over document-specific detail.",
        "- systems: names from the Systems directory that the document discusses.",
        "- people: names from the Employees directory mentioned in the body (not just the author).",
        "- facts: 0-6 durable, self-contained claims a colleague would want to look up later: ownership, who "
        "maintains or is the expert on a system, dependencies, statuses, policies, decisions, risks, deadlines, "
        "role changes, assignments, incidents. Skip greetings, chit-chat and speculation. For each fact give "
        "subject (a directory name, token, or short noun phrase), subject_type, predicate (from the allowed list), "
        "object (short), statement (one full sentence that stands alone and names the subject), and valid_from "
        "(YYYY-MM-DD when it became true; default to the document date).",
        "- decisions: 0-2 decisions made, proposed, reversed or deferred in this document, each with summary, "
        "status and effective date (YYYY-MM-DD or \"\").",
        "Use empty lists when nothing applies.",
        "",
        "Systems:",
        *(f"- {s['name']}: {s['description']}" for s in world["systems"]),
        "",
        "Teams:",
        *(f"- {name}" for name in teams.values()),
        "",
        "Deals / projects:",
        *(f"- {d['codename']}" for d in world["deals"]),
        "",
        "Employees:",
        *(f"- {p['name']}: {p['title']}, {short.get(p['team_id'], p['team_id'])}" for p in world["people"]),
    ]
    return "\n".join(lines)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Extractor:
    def __init__(self, tenant: str, world: dict | None = None, *, model: str | None = None):
        self.tenant = tenant
        w = world or load_world(tenant)
        self.model = model or get_settings().model_fast
        self.system = build_system_prompt(w)
        self._sys_hash = _sha(self.system)[:16]
        self.people = {p["id"]: p for p in w["people"]}
        self.path = REPO_ROOT / "data" / "extracted" / tenant / "extractions.jsonl"
        self._cache: dict[str, dict] | None = None
        self._lock = asyncio.Lock()
        self.stats: Counter = Counter()

    def _load(self) -> dict[str, dict]:
        if self._cache is None:
            self._cache = {}
            if self.path.is_file():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:  # a concurrent writer's partial last line
                            continue
                        self._cache[rec["k"]] = rec["v"]
        return self._cache

    @staticmethod
    def eligible(doc: dict) -> bool:
        return len((doc.get("text") or "").strip()) >= MIN_CHARS

    def message(self, doc: dict) -> str:
        kind = doc["kind"]
        tk = doc.get("ticket") or {}
        if tk:
            kind += f" ({tk.get('key')}, status {tk.get('status')})"
        elif doc.get("doc_type"):
            kind += f" ({doc['doc_type']})"
        author = self.people.get(doc.get("author_id") or "")
        who = f"{author['name']} ({author['title']})" if author else (doc.get("external_author") or "unknown")
        parts = [f"Document date: {doc['ts'][:10]}", f"Kind: {kind}", f"Author: {who}"]
        if doc.get("title"):
            parts.append(f"Title: {doc['title']}")
        return "\n".join(parts) + "\n\n" + (doc.get("text") or "")[:MAX_TEXT]

    def key(self, msg: str) -> str:
        return _sha(f"{self.model}|{PROMPT_VERSION}|{self._sys_hash}|{msg}")

    def cached(self, doc: dict) -> Extraction | None:
        if not self.eligible(doc):
            return empty()
        v = self._load().get(self.key(self.message(doc)))
        return None if v is None else Extraction.model_validate(v)

    async def extract(self, doc: dict, *, offline: bool = False) -> Extraction:
        if not self.eligible(doc):
            self.stats["skipped_short"] += 1
            return empty()
        msg = self.message(doc)
        k = self.key(msg)
        hit = self._load().get(k)
        if hit is not None:
            self.stats["cache_hit"] += 1
            return Extraction.model_validate(hit)
        if offline:
            self.stats["cache_miss_offline"] += 1
            log.warning("extraction cache miss for %s in offline mode", doc["doc_id"])
            return empty()
        try:
            res: Extraction = await chat_json([system_block(self.system), {"role": "user", "content": msg}],
                                              Extraction, model=self.model, name="extraction", max_tokens=2000,
                                              purpose="extract")
        except Exception as e:  # LLMError, outbound-guard block, validation failure: never abort a bulk run
            self.stats["error"] += 1
            log.warning("extraction failed for %s: %s", doc["doc_id"], str(e)[:300])
            return empty()
        res = Extraction(topics=res.topics[:4], systems=res.systems[:8], people=res.people[:8],
                         facts=res.facts[:6], decisions=res.decisions[:2])
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"k": k, "doc_id": doc["doc_id"], "v": res.model_dump()}, ensure_ascii=False)
                        + "\n")
            self._load()[k] = res.model_dump()
        self.stats["extracted"] += 1
        return res

    async def extract_many(self, docs: list[dict], *, offline: bool = False,
                           concurrency: int | None = None) -> dict[str, Extraction]:
        sem = asyncio.Semaphore(concurrency or get_settings().llm_concurrency)
        done = 0
        out: dict[str, Extraction] = {}

        async def one(d: dict) -> None:
            nonlocal done
            async with sem:
                out[d["doc_id"]] = await self.extract(d, offline=offline)
            done += 1
            if done % 200 == 0:
                log.info("extract[%s] %d/%d %s $%.3f", self.tenant, done, len(docs), dict(self.stats),
                         COSTS.total_usd())

        await asyncio.gather(*(one(d) for d in docs))
        return out


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", action="append", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=0)
    args = ap.parse_args()
    for tenant in args.tenant:
        docs = load_redacted_docs(tenant)
        if args.limit:
            docs = docs[: args.limit]
        ex = Extractor(tenant)
        t0 = time.perf_counter()
        res = await ex.extract_many(docs, concurrency=args.concurrency or None)
        n_facts = sum(len(x.facts) for x in res.values())
        n_topics = len({t for x in res.values() for t in x.topics})
        print(f"{tenant}: {len(docs)} docs in {time.perf_counter() - t0:.0f}s {dict(ex.stats)} facts={n_facts} "
              f"distinct_topics={n_topics} total_cost=${COSTS.total_usd():.3f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpx2", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    asyncio.run(_main())
