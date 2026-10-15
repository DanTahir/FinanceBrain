"""FastAPI app: REST + SSE for the web UI, with the MCP server mounted at /mcp.

Run locally:  python -m uvicorn backend.api.app:app --port 8000
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.agents.orchestrator import SPECS, run_flow
from backend.config import mask_secrets
from backend.graph.db import get_db
from backend.mcp_server.server import http_app
from backend.tenancy import TENANTS, AuthContext, AuthError, make_auth, personas
from backend.tools import ops
from backend.tools.base import ToolContext, ToolError

log = logging.getLogger(__name__)
mcp_app = http_app()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The MCP streamable-HTTP session manager starts in the mounted app's lifespan, which a mount does not run.
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(title="FinanceBrain API", version="0.7.0", lifespan=lifespan)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    tenant: str
    persona: str
    message: str = Field(min_length=1, max_length=4000)
    agent: str = "brain"
    history: list[ChatTurn] = Field(default_factory=list)


class Decision(BaseModel):
    tenant: str
    persona: str
    approve: bool
    note: str = Field(default="", max_length=1000)


def _auth(tenant: str, persona: str) -> AuthContext:
    try:
        return make_auth(tenant, persona, user="web")
    except AuthError as e:
        raise HTTPException(400, str(e)) from None


def _tool_http_error(e: ToolError) -> HTTPException:
    msg = str(e)
    return HTTPException(403 if msg.startswith("Permission denied") else 404 if msg.startswith("No ") else 400, msg)


@app.get("/api/healthz")
async def healthz() -> dict:
    try:
        graphs = await get_db().list_graphs()
        return {"ok": True, "graphs": len(graphs)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": mask_secrets(str(e))[:200]}


@app.get("/api/tenants")
async def tenants() -> list[dict]:
    return [{"id": t, "name": name, "personas": [p.public() for p in personas(t).values()]}
            for t, name in TENANTS.items()]


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    auth = _auth(req.tenant, req.persona)
    if req.agent not in SPECS:
        raise HTTPException(400, f"unknown agent {req.agent!r}")

    async def events():
        try:
            async for ev in run_flow(req.agent, auth, req.message, [h.model_dump() for h in req.history]):
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False, default=str)}
        except Exception as e:  # noqa: BLE001
            log.exception("chat stream failed")
            yield {"event": "error", "data": json.dumps({"type": "error", "message": mask_secrets(str(e))[:500]})}

    return EventSourceResponse(events(), ping=10)  # heartbeat keeps CloudFront's 60 s origin timeout at bay


# ------------------------------------------------------------------------------------------------ investigations
@app.get("/api/cases")
async def cases(tenant: str, persona: str, status: str | None = None) -> dict:
    try:
        return await ops.list_cases(ToolContext(_auth(tenant, persona), agent="human"), status=status, limit=50)
    except ToolError as e:
        raise _tool_http_error(e) from None


@app.get("/api/cases/{case_id}")
async def case_detail(case_id: str, tenant: str, persona: str) -> dict:
    ctx = ToolContext(_auth(tenant, persona), agent="human")
    try:
        out = await ops.get_case(ctx, case_id)
    except ToolError as e:
        raise _tool_http_error(e) from None
    return {**out, "evidence_graph": ctx.evidence.to_dict(), "docs": list(ctx.docs.values())}


@app.get("/api/tasks")
async def tasks(tenant: str, persona: str, status: str | None = "any") -> dict:
    try:
        return await ops.list_tasks(ToolContext(_auth(tenant, persona), agent="human"), status=status, mine=False,
                                    limit=50)
    except ToolError as e:
        raise _tool_http_error(e) from None


@app.post("/api/actions/{action_id}/decision")
async def decide(action_id: str, body: Decision) -> dict:
    """Human-in-the-loop approval. Requires the `approve` scope (Compliance Officer persona)."""
    try:
        return await ops.decide_action(_auth(body.tenant, body.persona), action_id, body.approve, body.note)
    except ToolError as e:
        raise _tool_http_error(e) from None


app.mount("/", mcp_app)  # serves /mcp; registered last so the /api routes above take precedence
