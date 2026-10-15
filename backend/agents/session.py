"""One chat turn with memory, end to end:

    PII scrub -> memory graph context (preferences, relevant memories, recent sessions) -> Concierge routing
    -> agent flow with handoffs (run_flow) -> episodic write (Turn nodes + USED_EVIDENCE) -> Librarian consolidation

Yields the same UI events as ``run_flow`` plus ``session``, ``pii_masked``, ``memory_context``, ``route``,
``memory`` and ``done``. Conversation history comes from the memory graph, not from the browser.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from backend.agents.concierge import route_request
from backend.agents.librarian import consolidate
from backend.agents.orchestrator import run_flow
from backend.config import mask_secrets
from backend.llm.guard import scrub_pii
from backend.memory.store import SESSION_RE, MemoryStore, entity_refs, new_session_id
from backend.tenancy import AuthContext

log = logging.getLogger(__name__)


def answer_refs(final: dict) -> list[dict]:
    """Entities an answer rests on: cited documents, non-document evidence nodes and the cases it touched."""
    cited = [c["id"] for c in final.get("citations") or []]
    nodes = (final.get("evidence") or {}).get("nodes") or []
    names = {n["id"]: n.get("name") for n in nodes}
    for c in final.get("citations") or []:
        names.setdefault(c["id"], c.get("display") or c.get("title"))
    cases = [e.get("case_id") for e in final.get("ops") or [] if e.get("case_id")]
    ids = [*cited, *cases, *(n["id"] for n in nodes if n.get("label") != "Document")]
    return entity_refs(ids[:80], names, cited)


async def run_session(auth: AuthContext, message: str, *, agent: str = "auto", session_id: str | None = None,
                      memory: bool = True, consolidate_after: bool = True, history: list[dict] | None = None,
                      max_handoffs: int = 2) -> AsyncIterator[dict]:
    resumed = bool(session_id and SESSION_RE.match(session_id))
    sid = session_id if resumed else new_session_id()
    clean, masked = scrub_pii(message)
    store = MemoryStore(auth)
    yield {"type": "session", "session_id": sid, "resumed": resumed, "memory_graph": store.name if memory else None}
    if masked:
        yield {"type": "pii_masked", "counts": masked,
               "note": "Raw personal data in the message was masked before it reached any model or graph."}

    hist = list(history or [])
    mem_text, n = None, 0
    if memory:
        try:
            await store.ensure()
            n = (await store.start_session(sid, agent))["turns"]
            if resumed:
                hist = await store.history(sid) or hist
            mem_text, info = await store.context_block(clean, exclude_session=sid)
            if info:
                yield {"type": "memory_context", **info}
        except Exception as e:  # noqa: BLE001  (never fail a chat because memory is down)
            log.exception("memory unavailable")
            memory = False
            yield {"type": "memory", "error": mask_secrets(f"memory unavailable: {e}")[:300]}

    if agent == "auto":
        route = await route_request(auth, clean, hist)
        agent = route["agent"]
        yield {"type": "route", **route}

    if memory:
        n += 1
        try:
            await store.add_turn(sid, n, "user", clean)
        except Exception:  # noqa: BLE001
            log.exception("could not record user turn")

    finals: list[dict] = []
    async for ev in run_flow(agent, auth, clean, hist, max_handoffs=max_handoffs, memory=mem_text, session_id=sid):
        if ev["type"] == "final":
            finals.append(ev)
        yield ev

    if memory and finals:
        try:
            for f in finals:
                n += 1
                await store.add_turn(sid, n, "assistant", f["text"], agent=f["agent"],
                                     tools=[c["name"] for c in f.get("tool_calls") or []],
                                     cost=f.get("cost_usd"), refs=answer_refs(f))
            if consolidate_after:
                yield {"type": "memory", **await consolidate(auth, sid, store=store)}
        except Exception as e:  # noqa: BLE001
            log.exception("memory write/consolidation failed")
            yield {"type": "memory", "error": mask_secrets(f"{type(e).__name__}: {e}")[:300]}
    yield {"type": "done", "session_id": sid, "agent": agent}
