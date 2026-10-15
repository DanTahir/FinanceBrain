"""Librarian: consolidates a conversation into long-term memory after each answer (Haiku 5.5, JSON-schema output).

- Episodic -> semantic: a session title and summary, plus 0-4 durable MemoryFacts about the user (preferences,
  goals, open threads, facts they told us). A memory that updates an older one SUPERSEDES it; resolved ones retire.
- Episodic -> procedural: when an ``act`` persona ran a real multi-tool investigation, the Librarian may distil the
  tool sequence into a generic Playbook in the shared tenant graph, so the next investigation can follow it.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.graph import queries as Q
from backend.llm.client import COSTS, chat_json, system_block
from backend.memory.store import MemoryStore, now_iso
from backend.tenancy import AuthContext
from backend.tools import memory as memtools
from backend.tools.base import ToolContext, ToolError

log = logging.getLogger(__name__)

INVESTIGATION_TOOLS = frozenset({"list_alerts", "linked_accounts", "detect_rings", "trace_funds", "barrier_check",
                                 "key_people", "open_case", "propose_action"})
MAX_FACTS_PER_SESSION = 4


class LFact(BaseModel):
    kind: Literal["preference", "fact", "goal", "open_thread"]
    text: str = Field(description="One self-contained third-person sentence, e.g. 'Prefers answers as a table.'")
    about: list[str] = Field(description="Entity ids the memory concerns (c_0060, a_00128, case_..., deal_...), or [].")
    replaces: str = Field(description="Id (mf_...) of an existing memory this one updates or contradicts, else ''.")


class LStep(BaseModel):
    tool: str = Field(description="Exact tool name, as listed in tool_sequence.")
    instruction: str = Field(description="What to do with the tool, generalised: no specific ids, names or amounts.")


class LPlaybook(BaseModel):
    name: str = Field(description="Short imperative name, e.g. 'Investigate a flagged inbound wire for ring activity'.")
    trigger: str = Field(description="When to use it, in generic terms.")
    description: str = Field(description="One or two sentences on the approach and what it concludes.")
    steps: list[LStep]


class Consolidation(BaseModel):
    title: str = Field(description="Session title, at most 8 words.")
    summary: str = Field(description="1-3 sentences: what was asked and concluded, with key entity ids.")
    facts: list[LFact]
    retire: list[str] = Field(description="Ids of existing memories that are no longer true, else [].")
    playbooks: list[LPlaybook] = Field(description="At most one; [] unless playbook_eligible is true.")


LIBRARIAN_PROMPT = """You are the Librarian of FinanceBrain, the company brain of a financial firm (synthetic data).
After each conversation you consolidate it into long-term memory for ONE user. You receive JSON with the user's
existing memories, the new conversation turns, the tools the agents used, and whether a playbook may be learned.

Return:
- title / summary of the session (with key entity ids such as t_r016, a_00128, case_..., deal_...).
- facts: 0-4 durable memories worth having next time: the user's preferences (format, depth, focus), goals,
  open threads (an investigation still awaiting approval, a question they want to come back to), or facts the user
  stated about themselves or their work. Not company knowledge that is already in the company graph, not one-off
  chit-chat, nothing already in existing_memories. Third person, one sentence each.
- If a new memory updates or contradicts an existing one, set replaces to that memory's id. Put ids of existing
  memories that are no longer true (e.g. an open thread that is now resolved) in retire.
- playbooks: only if playbook_eligible is true and the conversation shows a successful multi-step procedure worth
  reusing. Generalise it: no ids, amounts, names or tokens; refer to roles ("the flagged transaction", "the linked
  clients", "the exit counterparty"). 3-8 steps in order, each using a tool name exactly as in tool_sequence.
  Otherwise return [].
Never include personal data; privacy tokens like ⟨PHONE_0042⟩ must not appear in playbooks."""


async def consolidate(auth: AuthContext, sid: str, *, store: MemoryStore | None = None,
                      graph: str | None = None) -> dict:
    st = store or MemoryStore(auth)
    info = await st.session_info(sid)
    if not info:
        return {"session_id": sid, "skipped": "no such session"}
    done_n = int(info.get("consolidated_n") or 0)
    turns = await st.turns(sid)
    new = [t for t in turns if t["n"] > done_n]
    if not any(t["role"] == "assistant" for t in new):
        return {"session_id": sid, "skipped": "nothing new to consolidate"}
    existing = await st.facts(limit=40)
    tool_seq = [x for t in turns if t["role"] == "assistant" for x in t["tools"]]
    eligible = auth.can("act") and len(tool_seq) >= 4 and bool(INVESTIGATION_TOOLS & set(tool_seq))
    payload = {
        "user": {"persona": auth.persona, "title": auth.persona_info.title},
        "existing_memories": [{"id": f["id"], "kind": f["kind"], "text": f["text"]} for f in existing],
        "previous_summary": info.get("summary"),
        "conversation": [{"n": t["n"], "role": t["role"], "agent": t.get("agent"), "text": t["text"][:2500],
                          "tools": t["tools"]} for t in new][-10:],
        "tool_sequence": tool_seq[:40],
        "playbook_eligible": eligible,
    }
    c0 = COSTS.total_usd()
    out: Consolidation = await chat_json(
        [system_block(LIBRARIAN_PROMPT), {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        Consolidation, model=get_settings().model_fast, name="consolidation", max_tokens=1500, purpose="librarian")
    existing_ids = {f["id"] for f in existing}
    items = [{"text": f.text, "kind": f.kind, "about": f.about,
              "replaces": f.replaces if f.replaces in existing_ids else None}
             for f in out.facts[:MAX_FACTS_PER_SESSION]]
    saved = await st.add_facts(items, sid=sid, by="librarian")
    superseded = {f["replaced"] for f in saved if f.get("replaced")}
    retired = await st.retire([i for i in out.retire if i in existing_ids and i not in superseded])

    playbook = None
    if eligible and out.playbooks:
        p = out.playbooks[0]
        ctx = ToolContext(auth, agent="librarian", graph=graph)
        try:
            playbook = await memtools.store_playbook(ctx, p.name, p.trigger, [s.model_dump() for s in p.steps],
                                                     p.description, source=f"session:{sid}")
            pid = playbook["playbook"]["id"]
            case_ids = sorted({r for t in new for r in t["refs"] if r.startswith("case_")})
            for cid in case_ids:  # the case this session worked now FOLLOWS_PLAYBOOK the procedure it taught
                await ctx.q("CASE_FOLLOWS_PLAYBOOK", Q.CASE_FOLLOWS_PLAYBOOK, {"case": cid, "id": pid, "now": now_iso()},
                            write=True)
            playbook["cases"] = case_ids
        except ToolError as e:
            playbook = {"error": str(e)}

    await st.set_session(sid, title=out.title.strip()[:120], summary=out.summary.strip()[:1000],
                         consolidated_n=max(t["n"] for t in turns), consolidated=now_iso())
    return {"session_id": sid, "memory_graph": st.name, "title": out.title.strip()[:120],
            "summary": out.summary.strip()[:1000],
            "facts_added": [f for f in saved if not f.get("duplicate_of")],
            "facts_duplicate": [f["duplicate_of"] for f in saved if f.get("duplicate_of")],
            "facts_superseded": sorted(superseded), "facts_retired": retired,
            "playbook_eligible": eligible, "playbook": playbook,
            "cost_usd": round(COSTS.total_usd() - c0, 5)}
