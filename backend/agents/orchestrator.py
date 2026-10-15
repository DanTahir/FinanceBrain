"""Agent orchestration: run the requested agent and follow agent-to-agent handoffs within one event stream.

A handoff is graph state (a Task plus a HANDED_OFF_TO edge, written by the `handoff` tool). The orchestrator only
notices the resulting `ops` event and starts the receiving agent with the task id; that agent then claims the task
from the blackboard like any other worker would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from backend.agents.brain import brain_spec
from backend.agents.compliance import compliance_spec
from backend.agents.investigator import investigator_spec
from backend.agents.runtime import AgentSpec, run_agent
from backend.tenancy import AuthContext

SPECS: dict[str, Callable[[], AgentSpec]] = {
    "brain": brain_spec, "investigator": investigator_spec, "compliance": compliance_spec}


async def run_flow(agent: str, auth: AuthContext, message: str, history: list[dict] | None = None,
                   max_handoffs: int = 2, *, memory: str | None = None,
                   session_id: str | None = None) -> AsyncIterator[dict]:
    """``memory`` (the user's long-term memory block) and ``session_id`` go to every agent in the flow."""
    if agent not in SPECS:
        raise ValueError(f"unknown agent {agent!r}")
    current, msg, hist = agent, message, history
    for hop in range(max_handoffs + 1):
        handoff = None
        async for ev in run_agent(SPECS[current](), auth, msg, hist, memory=memory, session_id=session_id):
            if ev["type"] == "ops" and ev.get("kind") == "handoff":
                handoff = ev
            yield ev
        if handoff is None or hop == max_handoffs:
            return
        nxt = handoff.get("to_agent")
        if nxt not in SPECS or nxt == current:
            return
        yield {"type": "handoff", "from": current, "to": nxt, "task_id": handoff.get("task_id"),
               "case_id": handoff.get("case_id"), "note": handoff.get("note")}
        msg = (f"The {current} agent handed you task {handoff.get('task_id')} for case {handoff.get('case_id')}.\n"
               f"Handoff note: {handoff.get('note')}\n\nThe user's original request was: {message}\n\n"
               "Claim the task, review the case and complete your part.")
        current, hist = nxt, None
