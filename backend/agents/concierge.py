"""Concierge: routes each request to the right specialist agent (Haiku 5.5, JSON-schema output).

Personas without the ``act`` scope always get the Brain (they cannot run investigations), without an LLM call.
If the router call fails, a keyword heuristic decides.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.llm.client import chat_json, system_block
from backend.tenancy import AuthContext

log = logging.getLogger(__name__)

ROUTER_PROMPT = """You route requests inside FinanceBrain, the company brain of a financial firm. Pick the specialist:
- brain: questions about the company and its knowledge: who knows or owns what, why something was decided, what a
  document or system says, dependencies and impact, people, history, earlier conversations. The default.
- investigator: investigate suspicious activity: flagged or alerted transactions, wires, fraud rings, AML, linked
  accounts, money flows, freezing accounts, or investigating whether deal information (MNPI) leaked.
- compliance: review or progress an existing case: SAR drafting, policy and regulatory review of findings,
  preparing actions for approval, working a task handed over on the blackboard.
Return JSON with the agent and a one-sentence reason."""

_INVESTIGATE = re.compile(r"\b(investigat\w*|fraud|ring|wire|alert\w*|flagged|aml|launder\w*|freeze|suspicious|"
                          r"leak\w*|mnpi)\b", re.I)
_COMPLIANCE = re.compile(r"\b(sar|case_\w+|task_\w+|review the case|policy review|approval package)\b", re.I)


class Route(BaseModel):
    agent: Literal["brain", "investigator", "compliance"]
    reason: str = Field(description="One short sentence.")


def heuristic(message: str) -> str:
    if _COMPLIANCE.search(message):
        return "compliance"
    if _INVESTIGATE.search(message):
        return "investigator"
    return "brain"


async def route_request(auth: AuthContext, message: str, history: list[dict] | None = None) -> dict:
    if not auth.can("act"):
        return {"agent": "brain", "reason": "Read-only persona: company Q&A (investigations need the act permission).",
                "by": "policy"}
    recent = [f"{h['role']}: {str(h.get('content') or '')[:300]}" for h in (history or [])[-2:]]
    content = ("Earlier in this conversation:\n" + "\n".join(recent) + "\n\n" if recent else "") + f"Request: {message}"
    try:
        r: Route = await chat_json([system_block(ROUTER_PROMPT), {"role": "user", "content": content[:3000]}], Route,
                                   model=get_settings().model_fast, name="route", max_tokens=200, purpose="concierge")
        return {"agent": r.agent, "reason": r.reason.strip()[:300], "by": "concierge"}
    except Exception as e:  # noqa: BLE001
        log.warning("concierge routing failed, using heuristic: %s", str(e)[:200])
        return {"agent": heuristic(message), "reason": "Keyword routing (router unavailable).", "by": "heuristic"}
