"""Memory tools: the caller's private memory graph (remember, recall, get_session_history) and the tenant's shared
procedural memory (save_playbook, find_playbook).

Playbooks live in the tenant brain graph (Playbook -[:HAS_STEP]-> Step -[:NEXT_STEP]-> Step) so every agent of the
tenant can reuse what one investigation taught. They hold procedure only (tool sequence and generic instructions),
never case-specific ids or personal data, and are matched by voyage-4 similarity on a FalkorDB vector index.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.graph import queries as Q
from backend.llm import embeddings
from backend.llm.guard import scrub_pii
from backend.memory.store import FACT_KINDS, SESSION_RE, MemoryStore, now_iso
from backend.tools.base import ToolContext, ToolError

# Calibrated on voyage-4 (query vs playbook text): true matches 0.37-0.63, unrelated tasks >= 0.72.
PLAYBOOK_MATCH = 0.65  # max cosine distance for find_playbook to call a playbook a match for the task
PLAYBOOK_SAME = 0.12   # a newly learned playbook this close to an existing one reinforces it instead
_TOOL_RE = re.compile(r"^[a-z][a-z_]{2,40}$")


def _store(ctx: ToolContext) -> MemoryStore:
    return MemoryStore(ctx.auth, trace=ctx.trace)


def _session(ctx: ToolContext) -> str | None:
    return ctx.session_id if ctx.session_id and SESSION_RE.match(ctx.session_id) else None


# ------------------------------------------------------------------------------------------------ private memory
async def remember(ctx: ToolContext, text: str, kind: str = "fact", about: list[str] | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ToolError("text is required")
    if len(text) > 500:
        raise ToolError("keep a memory to one or two sentences (500 characters max)")
    kind = (kind or "fact").strip().lower()
    if kind not in FACT_KINDS:
        raise ToolError(f"kind must be one of: {', '.join(FACT_KINDS)}")
    clean, masked = scrub_pii(text)
    st = _store(ctx)
    await st.ensure()
    sid = _session(ctx)
    if sid:
        await st.start_session(sid, ctx.agent)
    saved = await st.add_facts([{"text": clean, "kind": kind, "about": about or []}], sid=sid,
                               by=f"agent:{ctx.agent}")
    ctx.events.append({"kind": "memory_saved", "agent": ctx.agent, "facts": saved, "graph": st.name})
    out: dict[str, Any] = {"saved": saved, "memory_graph": st.name}
    if masked:
        out["pii_masked"] = masked
    return out


async def recall(ctx: ToolContext, query: str, k: int = 6) -> dict:
    query = (query or "").strip()
    if not query:
        raise ToolError("query is required")
    st = _store(ctx)
    if not await st.exists():
        return {"facts": [], "turns": [], "note": "Nothing remembered for this user yet."}
    res = await st.recall(scrub_pii(query)[0], k=max(1, min(int(k or 6), 12)), exclude_session=_session(ctx) or "")
    return {**res, "memory_graph": st.name}


async def get_session_history(ctx: ToolContext, session_id: str | None = None, limit: int = 5) -> dict:
    st = _store(ctx)
    if not await st.exists():
        return {"sessions": [], "note": "No earlier sessions for this user."}
    if session_id:
        if not SESSION_RE.match(session_id):
            raise ToolError("session_id looks like sess_0123456789 (from the sessions list)")
        turns = await st.turns(session_id)
        if not turns:
            raise ToolError(f"No session {session_id} in this user's memory.")
        return {"session_id": session_id,
                "turns": [{**t, "text": t["text"][:1500]} for t in turns]}
    limit = max(1, min(int(limit or 5), 20))
    current = _session(ctx)
    sessions = [s for s in await st.sessions(limit=limit + 1) if s["id"] != current][:limit]
    return {"sessions": sessions}


# ------------------------------------------------------------------------------------------------ playbooks
def _playbook_text(name: str, trigger: str, description: str, steps: list[dict]) -> str:
    return (f"{name}. Use when: {trigger}. {description} Steps: "
            + "; ".join(f"{s['tool']}: {s['instruction']}" for s in steps))


def _norm_steps(steps: Any) -> list[dict]:
    out = []
    for s in steps or []:
        if isinstance(s, str):
            tool, instr = "", s.strip()
        elif isinstance(s, dict):
            tool, instr = str(s.get("tool") or "").strip(), str(s.get("instruction") or "").strip()
        else:
            tool, instr = str(getattr(s, "tool", "") or ""), str(getattr(s, "instruction", "") or "")
        if tool and not _TOOL_RE.match(tool):
            tool = ""
        if not instr and not tool:
            continue
        out.append({"tool": tool or "reasoning", "instruction": scrub_pii(instr)[0][:400]})
    return out[:10]


def _row_to_playbook(row: list) -> dict:
    pid, name, trigger, desc, uses, created, updated, by, *rest = row
    steps = rest[-1]
    out = {"id": pid, "name": name, "trigger": trigger, "description": desc, "uses": int(uses or 0),
           "created": created, "updated": updated, "learned_by": by,
           "steps": [{"n": n, "tool": t, "instruction": i} for n, t, i in steps if n is not None]}
    if len(rest) == 2:  # search rows carry the vector score before the steps
        out["distance"] = round(float(rest[0]), 4)
    return out


def _playbook_evidence(ctx: ToolContext, p: dict) -> None:
    ctx.evidence.node(p["id"], "Playbook", p["name"])
    prev = None
    for s in p["steps"]:
        sid = f"{p['id']}_s{s['n']}"
        ctx.evidence.node(sid, "Step", f"{s['n']}. {s['tool']}")
        ctx.evidence.edge(p["id"], "HAS_STEP", sid)
        if prev:
            ctx.evidence.edge(prev, "NEXT_STEP", sid)
        prev = sid


async def store_playbook(ctx: ToolContext, name: str, trigger: str, steps: Any, description: str = "", *,
                         source: str | None = None) -> dict:
    """Create a playbook, or reinforce an existing equivalent one (vector distance <= PLAYBOOK_SAME)."""
    name = scrub_pii((name or "").strip())[0][:120]
    trigger = scrub_pii((trigger or "").strip())[0][:300]
    description = scrub_pii((description or "").strip())[0][:600]
    norm = _norm_steps(steps)
    if not name or not trigger:
        raise ToolError("name and trigger are required")
    if len(norm) < 2:
        raise ToolError("a playbook needs at least 2 steps")
    text = _playbook_text(name, trigger, description, norm)
    try:
        vec = (await embeddings.embed([text[:4000]], "document"))[0]
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"could not embed the playbook: {str(e)[:200]}") from None
    now = now_iso()
    near = await ctx.q("PLAYBOOK_SEARCH", Q.PLAYBOOK_SEARCH, {"k": 1, "v": vec})
    if near and float(near[0][8]) <= PLAYBOOK_SAME:
        existing = _row_to_playbook(near[0])
        uses = await ctx.q("PLAYBOOK_REINFORCE", Q.PLAYBOOK_REINFORCE, {"id": existing["id"], "now": now},
                           write=True)
        existing["uses"] = int(uses[0][0]) if uses else existing["uses"] + 1
        _playbook_evidence(ctx, existing)
        ctx.events.append({"kind": "playbook_reinforced", "agent": ctx.agent, "playbook_id": existing["id"],
                           "name": existing["name"], "uses": existing["uses"]})
        return {"action": "reinforced", "playbook": existing}
    pid = "pb_" + hashlib.sha1(name.lower().encode()).hexdigest()[:10]
    props = {k: v for k, v in {"name": name, "trigger": trigger, "description": description,
                               "learned_by": f"agent:{ctx.agent}", "persona": ctx.auth.persona, "source": source,
                               "kind": "procedure"}.items() if v}
    await ctx.q("PLAYBOOK_UPSERT", Q.PLAYBOOK_UPSERT, {"id": pid, "props": props, "now": now}, write=True)
    await ctx.q("PLAYBOOK_CLEAR_STEPS", Q.PLAYBOOK_CLEAR_STEPS, {"id": pid}, write=True)
    rows = [{"id": f"{pid}_s{n}", "n": n, **s} for n, s in enumerate(norm, start=1)]
    await ctx.q("PLAYBOOK_ADD_STEPS", Q.PLAYBOOK_ADD_STEPS, {"id": pid, "steps": rows}, write=True)
    pairs = [{"a": a["id"], "b": b["id"]} for a, b in zip(rows, rows[1:])]
    if pairs:
        await ctx.q("PLAYBOOK_LINK_STEPS", Q.PLAYBOOK_LINK_STEPS, {"pairs": pairs}, write=True)
    await ctx.q("PLAYBOOK_SET_VEC", Q.PLAYBOOK_SET_VEC, {"id": pid, "v": vec}, write=True)
    pb = {"id": pid, "name": name, "trigger": trigger, "description": description, "uses": 0,
          "learned_by": props["learned_by"], "steps": [{"n": r["n"], "tool": r["tool"], "instruction": r["instruction"]}
                                                     for r in rows]}
    _playbook_evidence(ctx, pb)
    ctx.events.append({"kind": "playbook_learned", "agent": ctx.agent, "playbook_id": pid, "name": name,
                       "steps": len(rows)})
    return {"action": "created", "playbook": pb}


async def save_playbook(ctx: ToolContext, name: str, trigger: str, steps: list, description: str = "") -> dict:
    ctx.require("act", "save_playbook")
    sid = _session(ctx)
    return await store_playbook(ctx, name, trigger, steps, description, source=f"session:{sid}" if sid else None)


async def find_playbook(ctx: ToolContext, query: str, k: int = 3) -> dict:
    query = scrub_pii((query or "").strip())[0]
    if not query:
        raise ToolError("query is required: describe the task in one line")
    k = max(1, min(int(k or 3), 5))
    try:
        vec = (await embeddings.embed([query[:2000]], "query"))[0]
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"playbook search unavailable: {str(e)[:200]}") from None
    rows = await ctx.q("PLAYBOOK_SEARCH", Q.PLAYBOOK_SEARCH, {"k": k, "v": vec})
    found = []
    for r in rows:
        p = _row_to_playbook(r)
        if await ctx.is_restricted(p["name"], p["trigger"], p["description"], *(s["instruction"] for s in p["steps"])):
            continue  # never surface a procedure that names a deal behind the caller's information barrier
        p["match"] = p["distance"] <= PLAYBOOK_MATCH
        found.append(p)
        _playbook_evidence(ctx, p)
    best = next((p for p in found if p["match"]), None)
    return {"playbooks": found, "best_match": best["id"] if best else None,
            "note": ("Learned procedure(s) from earlier sessions (procedural memory). Follow the best match's steps, "
                     "adapting them to this case, and say which playbook you followed.") if best else
                    ("No learned playbook matches this task; work it out step by step. The Librarian may learn a "
                     "playbook from this session.")}


async def list_playbooks(ctx: ToolContext, limit: int = 50) -> dict:
    rows = await ctx.q("PLAYBOOK_LIST", Q.PLAYBOOK_LIST, {"limit": max(1, min(int(limit or 50), 200))})
    out = []
    for pid, name, trigger, desc, uses, created, updated, by, source, steps in rows:
        p = _row_to_playbook([pid, name, trigger, desc, uses, created, updated, by, steps])
        p["source"] = source
        if not await ctx.is_restricted(p["name"], p["trigger"], p["description"]):
            out.append(p)
    return {"playbooks": out}
