"""Public-demo guardrails: per-IP rate limits, a daily USD budget fed by OpenRouter usage accounting, and a cache of
guided-question answers that keeps the showcase questions working at zero model cost (including when over budget).

All counters live in FalkorDB's Redis keyspace as plain ``fb:*`` string keys, next to the graphs.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import time
from typing import AsyncIterator

from fastapi import HTTPException, Request

from backend.config import get_settings
from backend.graph.db import execute
from backend.llm.client import COSTS

PREFIX = "fb:"


# ------------------------------------------------------------------------------------------------ rate limits
def client_ip(request: Request) -> str:
    """Viewer address. Behind CloudFront the origin request policy forwards CloudFront-Viewer-Address
    ("203.0.113.7:51234" or "[2001:db8::1]:51234"); Caddy's own X-Forwarded-For would only show the edge."""
    v = request.headers.get("cloudfront-viewer-address")
    if v:
        return v.rsplit(":", 1)[0].strip("[]")
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request, bucket: str, limit: int) -> None:
    """Fixed-window counter per (bucket, IP). Raises 429 with Retry-After when the window's quota is used up."""
    s = get_settings()
    if not s.rate_limit_enabled:
        return
    now = int(time.time())
    key = f"{PREFIX}rl:{bucket}:{client_ip(request)}:{now // s.rate_window_s}"
    n = int(await execute("INCR", key))
    if n == 1:
        await execute("EXPIRE", key, s.rate_window_s + 5)
    if n > limit:
        retry = s.rate_window_s - now % s.rate_window_s
        raise HTTPException(429, f"Rate limit: at most {limit} {bucket} requests per {s.rate_window_s // 60} minutes "
                                 f"from one address. Try again in {retry} s.", headers={"Retry-After": str(retry)})


# ------------------------------------------------------------------------------------------------ daily budget
def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


async def add_cost(usd: float) -> None:
    """COSTS hook: every model call's real USD cost (OpenRouter usage accounting) is added to today's counter."""
    if usd and usd > 0:
        key = f"{PREFIX}budget:{_today()}"
        await execute("INCRBYFLOAT", key, f"{usd:.6f}")
        await execute("EXPIRE", key, 3 * 86400)


def install_budget_hook() -> None:
    if add_cost not in COSTS.on_cost_hooks:
        COSTS.on_cost_hooks.append(add_cost)


async def budget_status() -> dict:
    s = get_settings()
    spent = float(await execute("GET", f"{PREFIX}budget:{_today()}") or 0.0)
    return {"day": _today(), "spent_usd": round(spent, 4), "limit_usd": s.daily_budget_usd,
            "remaining_usd": round(max(0.0, s.daily_budget_usd - spent), 4), "over": spent >= s.daily_budget_usd}


async def require_budget() -> None:
    b = await budget_status()
    if b["over"]:
        raise HTTPException(429, f"The public demo's daily model budget (USD {b['limit_usd']:.2f}) is used up for "
                                 f"{b['day']} (UTC). The guided questions still work from cache.")


# ------------------------------------------------------------------------------------------------ guided answer cache
_WS = re.compile(r"\s+")
REPLAY_SKIP = frozenset({"session", "memory", "memory_context", "pii_masked", "done"})


def norm_question(q: str) -> str:
    return _WS.sub(" ", q.strip().lower()).rstrip("?.! ")


def guided_key(tenant: str, persona: str, question: str, cursor: str | None) -> str:
    """Answers depend on who asks (ACL) and on how far the live feed has replayed, so both are part of the key."""
    raw = f"{tenant}|{persona}|{cursor or 'seed'}|{norm_question(question)}"
    return f"{PREFIX}guided:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


async def cached_answer(key: str) -> list[dict] | None:
    raw = await execute("GET", key)
    return json.loads(raw) if raw else None


def cacheable_events(events: list[dict]) -> list[dict] | None:
    """Only a complete, error-free run that was not shaped by the asker's private memory is shared."""
    types = {e.get("type") for e in events}
    if "final" not in types or "error" in types or "memory_context" in types:
        return None
    return [e for e in events if e.get("type") not in REPLAY_SKIP]


async def record_answer(key: str, gen: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """Pass a live run through unchanged, then cache it (a client that disconnects mid-run caches nothing)."""
    seen: list[dict] = []
    async for ev in gen:
        seen.append(ev)
        yield ev
    keep = cacheable_events(seen)
    if keep:
        await execute("SET", key, json.dumps(keep, ensure_ascii=False, default=str), "EX",
                      get_settings().guided_cache_ttl_s)


async def replay(events: list[dict]) -> AsyncIterator[dict]:
    yield {"type": "session", "session_id": None, "resumed": False, "memory_graph": None, "cached": True,
           "note": "Cached answer to a guided question (same tenant, persona and live-feed position); no model calls."}
    for ev in events:
        yield ev
        await asyncio.sleep(0.05 if ev.get("type") in ("tool_call", "tool_result", "route", "handoff") else 0)
    yield {"type": "done", "session_id": None, "cached": True}
