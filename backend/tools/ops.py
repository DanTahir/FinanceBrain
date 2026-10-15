"""Operations tools: cases, evidence, proposed actions, agent handoffs and the task blackboard.

All of it is graph state in brain__<tenant> (Case, Action, Task and Agent nodes; EVIDENCE, OPENED_BY, REQUESTED_BY,
ASSIGNED_TO, PROPOSED, FOR_CASE, TARGETS, HANDED_OFF_TO, CLAIMED_BY, APPROVED_BY / REJECTED_BY edges), so every
agent, the UI and later sessions see the same state. Agents can only *propose* actions: `decide_action` is called by
a human-facing REST endpoint, requires the `approve` scope, and executes the approved effect in the graph.

    python -m backend.tools.ops --tenant halcyon --clear     # remove all cases/actions/tasks, undo their effects
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import defaultdict
from typing import Any

from backend.config import get_settings
from backend.graph import queries as Q
from backend.graph.db import brain_graph, get_db
from backend.tenancy import AuthContext
from backend.tools.base import ToolContext, ToolError, fetch_docs, name_entities, resolve

AGENTS: dict[str, tuple[str, str, str]] = {  # name -> (display name, role, model setting)
    "brain": ("Brain", "Company Q&A with cited sources", "model_default"),
    "investigator": ("Investigator", "Fraud, AML and information-barrier investigations", "model_deep"),
    "compliance": ("Compliance", "Case review, SAR drafting, barrier and access checks", "model_default"),
    "sentinel": ("Sentinel", "Watches newly ingested data and raises alerts", "model_fast"),
    "librarian": ("Librarian", "Consolidates sessions into memory and playbooks", "model_fast"),
    "comms": ("Comms", "Drafts emails and Slack messages", "model_default"),
    "concierge": ("Concierge", "Routes requests to specialists", "model_default"),
    "external": ("External MCP client", "Any MCP client holding a signed token", ""),
    "human": ("Human reviewer", "Approves or rejects proposed actions", ""),
}
HANDOFF_TARGETS = ("investigator", "compliance")
CASE_KINDS = ("fraud_ring", "suspicious_activity", "mnpi_breach", "access_review", "other")
SEVERITIES = ("low", "medium", "high", "critical")
CASE_STATUSES = ("open", "in_review", "pending_approval", "escalated", "actioned", "closed")
ACTION_TARGETS: dict[str, tuple[str, ...]] = {
    "freeze_account": ("Account",), "file_sar": ("Case",), "notify_owner": ("Person", "Team", "System"),
    "restrict_channel": ("Channel",)}
EVIDENCE_LABELS = ("Transaction", "Account", "Client", "PiiToken", "Counterparty", "Document", "Person", "Channel",
                   "Deal", "System", "Team")


def agent_id(name: str) -> str:
    return f"agent_{name}"


def _me(ctx: ToolContext) -> str:
    return ctx.agent if ctx.agent in AGENTS else "external"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _case_id(case_id: str | None) -> str:
    c = (case_id or "").strip()
    if not c.startswith("case_"):
        raise ToolError("case_id must be a case id like case_1a2b3c4d5e (see list_cases)")
    return c


def _choice(value: str | None, allowed: tuple[str, ...], name: str, default: str | None = None) -> str | None:
    v = (value or "").strip().lower() or default
    if v is not None and v not in allowed:
        raise ToolError(f"{name} must be one of: {', '.join(allowed)}")
    return v


async def ensure_agents(ctx: ToolContext) -> None:
    s = get_settings()
    rows = [{"id": agent_id(n), "name": d, "role": r, "model": getattr(s, m, "") if m else ""}
            for n, (d, r, m) in AGENTS.items()]
    await ctx.q("ENSURE_AGENTS", Q.ENSURE_AGENTS, {"rows": rows}, write=True)


# ------------------------------------------------------------------------------------------------ case views
async def _resolve_items(ctx: ToolContext, items: Any) -> tuple[list[dict], list[str]]:
    out, skipped = [], []
    for it in (items or [])[:30]:
        if isinstance(it, str):
            ref, note = it, ""
        elif isinstance(it, dict):
            ref, note = str(it.get("entity") or it.get("id") or ""), str(it.get("note") or "")
        else:
            continue
        ent = await resolve(ctx, ref, set(EVIDENCE_LABELS))
        if not ent:
            skipped.append(ref)
            continue
        out.append({**ent, "note": note[:500]})
    return out, skipped


async def _add_evidence(ctx: ToolContext, case_id: str, items: list[dict]) -> None:
    by_label: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_label[it["label"]].append({"id": it["id"], "note": it["note"]})
    for label, rows in by_label.items():
        await ctx.q(f"ADD_EVIDENCE[{label}]", Q.ADD_EVIDENCE[label],
                    {"case": case_id, "rows": rows, "now": ctx.now(), "agent": agent_id(_me(ctx))}, write=True)


async def _case_view(ctx: ToolContext, case_id: str) -> dict:
    p = {"id": case_id}
    rows, actions, tasks, handoffs = await asyncio.gather(
        ctx.q("GET_CASE", Q.GET_CASE, p), ctx.q("CASE_ACTIONS", Q.CASE_ACTIONS, p),
        ctx.q("CASE_TASKS", Q.CASE_TASKS, p), ctx.q("CASE_HANDOFFS", Q.CASE_HANDOFFS, p))
    if not rows or await ctx.is_restricted(rows[0][1]):
        raise ToolError(f"No case {case_id!r} is visible to you (see list_cases).")
    (cid, title, kind, sev, status, summary, created, updated, opened_by, sar_ref, assignees, ev) = rows[0]
    evidence = [e for e in ev if e and e[1]]
    names = await name_entities(ctx, [(e[0], e[1]) for e in evidence])
    doc_ids = [e[1] for e in evidence if e[0] == "Document"]
    visible = await ctx.visible_docs(doc_ids) if doc_ids else set()
    if visible:
        await fetch_docs(ctx, sorted(visible), size=300)
    ctx.evidence.node(cid, "Case", title, status=status, severity=sev)
    ev_out = []
    for lab, eid, note, by, ts in evidence:
        if lab == "Document" and eid not in visible:
            ev_out.append({"label": lab, "id": "⟨restricted⟩", "note": "outside your access"})
            continue
        name = names.get(eid, eid)
        ev_out.append({k: v for k, v in {"label": lab, "id": eid, "name": name if name != eid else None,
                                         "note": note or None, "added_by": by, "ts": ts}.items() if v})
        ctx.evidence.node(eid, lab, name)
        ctx.evidence.edge(cid, "EVIDENCE", eid)
    act_out = []
    for (aid, akind, astatus, tlabel, tid, rationale, details, by, acreated, effect, dtype, dpid, dname, dts,
         dnote) in actions:
        a = {"id": aid, "kind": akind, "status": astatus, "target": f"{tlabel}:{tid}", "rationale": rationale,
             "details": (details[:1500] + "…") if details and len(details) > 1500 else (details or None),
             "proposed_by": by, "created": acreated, "effect": effect}
        if dtype:
            a["decision"] = {"by": dname or dpid, "approved": dtype == "APPROVED_BY", "ts": dts, "note": dnote or None}
        act_out.append({k: v for k, v in a.items() if v is not None})
        ctx.evidence.node(aid, "Action", f"{akind} {tid}", status=astatus)
        ctx.evidence.edge(aid, "FOR_CASE", cid)
        if tid:
            ctx.evidence.edge(aid, "TARGETS", tid)
    task_out = [{k: v for k, v in {"id": t[0], "title": t[1], "status": t[2], "from_agent": t[3], "to_agent": t[4],
                                   "note": t[5], "created": t[6], "claimed": t[7], "done": t[8],
                                   "result": t[9]}.items() if v is not None} for t in tasks]
    ho_out = [{"from_agent": h[0], "to_agent": h[1], "note": h[2], "ts": h[3], "task_id": h[4]} for h in handoffs]
    for h in ho_out:
        ctx.evidence.node(h["from_agent"], "Agent", h["from_agent"].removeprefix("agent_"))
        ctx.evidence.node(h["to_agent"], "Agent", h["to_agent"].removeprefix("agent_"))
        ctx.evidence.edge(h["from_agent"], "HANDED_OFF_TO", h["to_agent"])
    return {k: v for k, v in {"id": cid, "title": title, "kind": kind, "severity": sev, "status": status,
                              "summary": summary or None, "created": created, "updated": updated,
                              "opened_by": opened_by, "assigned_to": assignees, "sar_reference": sar_ref,
                              "evidence": ev_out, "actions": act_out, "tasks": task_out,
                              "handoffs": ho_out}.items() if v is not None}


# ------------------------------------------------------------------------------------------------ cases
async def open_case(ctx: ToolContext, title: str, kind: str, severity: str = "medium", summary: str = "",
                    evidence: list | None = None) -> dict:
    ctx.require("act", "open_case")
    title = (title or "").strip()[:200]
    if not title:
        raise ToolError("title is required")
    kind = _choice(kind, CASE_KINDS, "kind", "other")
    severity = _choice(severity, SEVERITIES, "severity", "medium")
    items, skipped = await _resolve_items(ctx, evidence)
    await ensure_agents(ctx)
    now, me = ctx.now(), _me(ctx)
    case_id, reused = None, False
    if items:
        rows = await ctx.q("FIND_OPEN_CASE", Q.FIND_OPEN_CASE, {"kind": kind, "ids": [i["id"] for i in items]})
        if rows:
            case_id, reused = rows[0][0], True
    if case_id is None:
        case_id = _new_id("case")
        created = await ctx.q("CREATE_CASE", Q.CREATE_CASE, {
            "id": case_id, "title": title, "kind": kind, "severity": severity, "summary": (summary or "")[:4000],
            "now": now, "agent": agent_id(me), "pid": ctx.pid}, write=True)
        if not created:
            raise ToolError("could not create the case")
    elif summary:
        await ctx.q("UPDATE_CASE", Q.UPDATE_CASE, {"id": case_id, "status": None, "severity": None,
                                                   "summary": summary[:4000], "now": now}, write=True)
    if items:
        await _add_evidence(ctx, case_id, items)
    ctx.events.append({"kind": "case_updated" if reused else "case_opened", "case_id": case_id, "title": title,
                       "severity": severity, "evidence_added": len(items), "agent": me})
    out: dict[str, Any] = {"case": await _case_view(ctx, case_id), "reused_existing_case": reused}
    if skipped:
        out["unresolved_evidence"] = skipped
    return out


async def add_case_evidence(ctx: ToolContext, case_id: str, evidence: list) -> dict:
    ctx.require("act", "add_case_evidence")
    case_id = _case_id(case_id)
    await _case_view(ctx, case_id)
    items, skipped = await _resolve_items(ctx, evidence)
    if not items:
        raise ToolError("none of the evidence items resolved to a visible entity")
    await _add_evidence(ctx, case_id, items)
    ctx.events.append({"kind": "evidence_added", "case_id": case_id, "count": len(items), "agent": _me(ctx)})
    out: dict[str, Any] = {"case": await _case_view(ctx, case_id), "added": len(items)}
    if skipped:
        out["unresolved_evidence"] = skipped
    return out


async def get_case(ctx: ToolContext, case_id: str) -> dict:
    ctx.require("act", "get_case")
    return {"case": await _case_view(ctx, _case_id(case_id))}


async def list_cases(ctx: ToolContext, status: str | None = None, limit: int = 20) -> dict:
    ctx.require("act", "list_cases")
    status = _choice(status, CASE_STATUSES, "status") if status and status != "any" else None
    limit = max(1, min(int(limit or 20), 50))
    rows = await ctx.q("LIST_CASES", Q.LIST_CASES, {"status": status, "limit": limit})
    cases = []
    for cid, title, kind, sev, st, created, updated, ev, acts, pending, assigned in rows:
        if await ctx.is_restricted(title):
            continue
        cases.append({"id": cid, "title": title, "kind": kind, "severity": sev, "status": st, "created": created,
                      "updated": updated, "evidence": ev, "actions": acts, "pending_approval": int(pending or 0),
                      "assigned_to": [a for a in assigned if a]})
    return {"cases": cases}


async def update_case(ctx: ToolContext, case_id: str, status: str | None = None, severity: str | None = None,
                      summary: str | None = None) -> dict:
    ctx.require("act", "update_case")
    case_id = _case_id(case_id)
    status = _choice(status, CASE_STATUSES, "status") if status else None
    severity = _choice(severity, SEVERITIES, "severity") if severity else None
    if status is None and severity is None and not summary:
        raise ToolError("nothing to update: give status, severity or summary")
    await _case_view(ctx, case_id)
    await ctx.q("UPDATE_CASE", Q.UPDATE_CASE, {"id": case_id, "status": status, "severity": severity,
                                               "summary": (summary or None) and summary[:4000], "now": ctx.now()},
                write=True)
    ctx.events.append({"kind": "case_updated", "case_id": case_id, "status": status, "severity": severity,
                       "agent": _me(ctx)})
    return {"case": await _case_view(ctx, case_id)}


# ------------------------------------------------------------------------------------------------ actions
async def propose_action(ctx: ToolContext, case_id: str, kind: str, target: str | None = None, rationale: str = "",
                         details: str | None = None) -> dict:
    ctx.require("act", "propose_action")
    case_id = _case_id(case_id)
    kind = _choice(kind, tuple(ACTION_TARGETS), "kind")
    case = await _case_view(ctx, case_id)
    labels = ACTION_TARGETS[kind]
    if kind == "file_sar":
        tgt = {"label": "Case", "id": case_id, "name": case["title"]}
    else:
        if not target or not str(target).strip():
            raise ToolError(f"target is required for {kind}: a {'/'.join(labels)} id or name")
        tgt = await resolve(ctx, str(target), set(labels))
        if not tgt:
            raise ToolError(f"No {'/'.join(labels)} matching {target!r} is visible to you.")
    rationale = (rationale or "").strip()[:2000]
    if not rationale:
        raise ToolError("rationale is required: say why, referring to the evidence")
    dup = await ctx.q("FIND_ACTION", Q.FIND_ACTION, {"case": case_id, "kind": kind, "target": tgt["id"]})
    if dup:
        return {"action": {"id": dup[0][0], "kind": kind, "status": "proposed", "target": tgt, "case_id": case_id},
                "duplicate": True, "note": "An identical action is already awaiting approval."}
    await ensure_agents(ctx)
    aid = _new_id("act")
    rows = await ctx.q(f"CREATE_ACTION[{tgt['label']}]", Q.CREATE_ACTION[tgt["label"]], {
        "case": case_id, "agent": agent_id(_me(ctx)), "target": tgt["id"], "id": aid, "kind": kind,
        "rationale": rationale, "details": (details or "")[:8000], "now": ctx.now()}, write=True)
    if not rows:
        raise ToolError("could not create the action (case or target missing)")
    ctx.events.append({"kind": "action_proposed", "action_id": aid, "action": kind, "target": tgt["id"],
                       "target_name": tgt["name"], "case_id": case_id, "agent": _me(ctx)})
    ctx.evidence.node(aid, "Action", f"{kind} {tgt['id']}", status="proposed")
    ctx.evidence.node(tgt["id"], tgt["label"], tgt["name"])
    ctx.evidence.edge(aid, "TARGETS", tgt["id"])
    ctx.evidence.edge(aid, "FOR_CASE", case_id)
    return {"action": {"id": aid, "kind": kind, "status": "proposed", "target": tgt, "case_id": case_id},
            "note": "Proposed only. A human with approval rights must approve it in the Investigations view before "
                    "anything happens; agents cannot approve."}


async def _execute(ctx: ToolContext, kind: str, tlabel: str, tid: str, aid: str, case_id: str) -> str:
    now = ctx.now()
    if kind == "freeze_account":
        rows = await ctx.q("FREEZE_ACCOUNT", Q.FREEZE_ACCOUNT, {"id": tid, "action": aid, "now": now}, write=True)
        before = rows[0][1] if rows else "unknown"
        return f"Account {tid} frozen (status was {before})."
    if kind == "restrict_channel":
        rows = await ctx.q("RESTRICT_CHANNEL", Q.RESTRICT_CHANNEL, {"id": tid, "action": aid, "now": now}, write=True)
        name = rows[0][1] if rows else tid
        return f"Channel {name} restricted: flagged for membership review, new posts held for Compliance."
    if kind == "file_sar":
        ref = f"SAR-{now[:4]}-{aid[-6:].upper()}"
        await ctx.q("FILE_SAR", Q.FILE_SAR, {"id": case_id, "ref": ref, "now": now}, write=True)
        return f"SAR {ref} filed from the drafted narrative (simulated regulator e-filing)."
    names = await name_entities(ctx, [(tlabel, tid)])
    return f"Notification sent to {names.get(tid, tid)} ({tid}) (simulated)."


async def decide_action(auth: AuthContext, action_id: str, approve: bool, note: str = "") -> dict:
    """Human-in-the-loop decision (REST only, never an MCP tool). Approval executes the effect."""
    ctx = ToolContext(auth, agent="human")
    if not auth.can("approve"):
        raise ToolError(f"Permission denied: deciding on actions requires the 'approve' permission, which "
                        f"{auth.persona_info.name} ({auth.persona}) does not have.")
    rows = await ctx.q("GET_ACTION", Q.GET_ACTION, {"id": (action_id or "").strip()})
    if not rows:
        raise ToolError(f"No action {action_id!r}.")
    aid, kind, status, tlabel, tid, case_id, _rationale, _details = rows[0]
    if status != "proposed":
        raise ToolError(f"Action {aid} is already {status}.")
    rel = "APPROVED_BY" if approve else "REJECTED_BY"
    done = await ctx.q(f"DECIDE_ACTION[{rel}]", Q.DECIDE_ACTION[rel],
                       {"id": aid, "pid": ctx.pid, "now": ctx.now(), "note": (note or "")[:1000]}, write=True)
    if not done:
        raise ToolError(f"Action {aid} could not be decided (already decided?).")
    effect = None
    if approve:
        effect = await _execute(ctx, kind, tlabel, tid, aid, case_id)
        await ctx.q("MARK_EXECUTED", Q.MARK_EXECUTED, {"id": aid, "now": ctx.now(), "effect": effect}, write=True)
    pending = await ctx.q("PENDING_FOR_CASE", Q.PENDING_FOR_CASE, {"case": case_id})
    if pending and not pending[0][0]:
        await ctx.q("UPDATE_CASE", Q.UPDATE_CASE, {"id": case_id, "status": "actioned", "severity": None,
                                                   "summary": None, "now": ctx.now()}, write=True)
    return {"action_id": aid, "kind": kind, "decision": "approved" if approve else "rejected", "effect": effect,
            "decided_by": auth.persona_info.name, "case": await _case_view(ctx, case_id), "trace": ctx.trace}


# ------------------------------------------------------------------------------------------------ blackboard
async def handoff(ctx: ToolContext, case_id: str, to_agent: str, note: str) -> dict:
    ctx.require("act", "handoff")
    case_id = _case_id(case_id)
    to = (to_agent or "").strip().lower().removeprefix("agent_")
    if to not in HANDOFF_TARGETS:
        raise ToolError(f"to_agent must be one of: {', '.join(HANDOFF_TARGETS)}")
    me = _me(ctx)
    if to == me:
        raise ToolError("You cannot hand a case off to yourself.")
    note = (note or "").strip()[:3000]
    if not note:
        raise ToolError("note is required: summarise the findings and what the next agent should do")
    case = await _case_view(ctx, case_id)
    await ensure_agents(ctx)
    tid, now = _new_id("task"), ctx.now()
    rows = await ctx.q("CREATE_HANDOFF", Q.CREATE_HANDOFF, {
        "case": case_id, "from": agent_id(me), "to": agent_id(to), "task": tid,
        "title": f"Review case: {case['title']}"[:200], "note": note, "now": now}, write=True)
    if not rows:
        raise ToolError("could not create the handoff task")
    await ctx.q("UNASSIGN_CASE", Q.UNASSIGN_CASE, {"case": case_id}, write=True)
    await ctx.q("ASSIGN_CASE", Q.ASSIGN_CASE, {"case": case_id, "agent": agent_id(to), "now": now}, write=True)
    ctx.events.append({"kind": "handoff", "from_agent": me, "to_agent": to, "task_id": tid, "case_id": case_id,
                       "note": note})
    ctx.evidence.node(agent_id(me), "Agent", AGENTS[me][0])
    ctx.evidence.node(agent_id(to), "Agent", AGENTS[to][0])
    ctx.evidence.edge(agent_id(me), "HANDED_OFF_TO", agent_id(to))
    return {"task_id": tid, "case_id": case_id, "to_agent": to, "status": "open",
            "note": f"Task posted to the blackboard; the {AGENTS[to][0]} agent will claim it."}


async def claim_task(ctx: ToolContext, task_id: str) -> dict:
    ctx.require("act", "claim_task")
    tid, me = (task_id or "").strip(), _me(ctx)
    rows = await ctx.q("CLAIM_TASK", Q.CLAIM_TASK, {"id": tid, "agent": None if me == "external" else agent_id(me),
                                                   "now": ctx.now()}, write=True)
    if not rows:
        st = await ctx.q("TASK_STATUS", Q.TASK_STATUS, {"id": tid})
        if not st:
            raise ToolError(f"No task {tid!r} (see list_tasks).")
        raise ToolError(f"Task {tid} is {st[0][1]} and assigned to {st[0][2]}; only an open task assigned to you "
                        "can be claimed.")
    _, case_id, from_agent, note = rows[0]
    ctx.events.append({"kind": "task_claimed", "task_id": tid, "case_id": case_id, "agent": me})
    return {"task": {"id": tid, "status": "claimed", "from_agent": from_agent, "note": note},
            "case": await _case_view(ctx, case_id)}


async def complete_task(ctx: ToolContext, task_id: str, result: str) -> dict:
    ctx.require("act", "complete_task")
    tid = (task_id or "").strip()
    result = (result or "").strip()[:3000]
    if not result:
        raise ToolError("result is required: one paragraph on what you did and what awaits approval")
    rows = await ctx.q("COMPLETE_TASK", Q.COMPLETE_TASK, {"id": tid, "result": result, "now": ctx.now()}, write=True)
    if not rows:
        raise ToolError(f"No open or claimed task {tid!r}.")
    ctx.events.append({"kind": "task_done", "task_id": tid, "case_id": rows[0][1], "agent": _me(ctx)})
    return {"task_id": tid, "status": "done", "case_id": rows[0][1]}


async def list_tasks(ctx: ToolContext, status: str | None = "open", mine: bool = True, limit: int = 20) -> dict:
    ctx.require("act", "list_tasks")
    st = None if not status or status == "any" else _choice(status, ("open", "claimed", "done"), "status")
    me = _me(ctx)
    rows = await ctx.q("LIST_TASKS", Q.LIST_TASKS, {"agent": agent_id(me) if mine and me != "external" else None,
                                                   "status": st, "limit": max(1, min(int(limit or 20), 50))})
    return {"tasks": [{k: v for k, v in {"id": r[0], "title": r[1], "status": r[2], "case_id": r[3],
                                         "from_agent": r[4], "to_agent": r[5], "note": r[6],
                                         "created": r[7]}.items() if v is not None} for r in rows]}


# ------------------------------------------------------------------------------------------------ reset
async def clear_ops(graph_name: str) -> dict:
    """Delete every case, action and task (and handoff edge) and undo their effects (frozen accounts, restricted
    channels)."""
    g = get_db().select_graph(graph_name)
    out = {}
    for key in ("accounts", "channels", "handoffs", "nodes"):
        res = await g.query(Q.CLEAR_OPS[key])
        out[key] = {"properties_set": getattr(res, "properties_set", 0),
                    "nodes_deleted": getattr(res, "nodes_deleted", 0),
                    "relationships_deleted": getattr(res, "relationships_deleted", 0)}
    return out


async def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()
    if args.clear:
        print(json.dumps(await clear_ops(brain_graph(args.tenant)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
