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

from backend.agents.brain import brain_spec
from backend.agents.runtime import run_agent
from backend.config import mask_secrets
from backend.graph.db import get_db
from backend.mcp_server.server import http_app
from backend.tenancy import TENANTS, AuthError, make_auth, personas

log = logging.getLogger(__name__)
mcp_app = http_app()
AGENTS = {"brain": brain_spec}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The MCP streamable-HTTP session manager starts in the mounted app's lifespan, which a mount does not run.
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(title="FinanceBrain API", version="0.6.0", lifespan=lifespan)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    tenant: str
    persona: str
    message: str = Field(min_length=1, max_length=4000)
    agent: str = "brain"
    history: list[ChatTurn] = Field(default_factory=list)


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
    try:
        auth = make_auth(req.tenant, req.persona, user="web")
    except AuthError as e:
        raise HTTPException(400, str(e)) from None
    if req.agent not in AGENTS:
        raise HTTPException(400, f"unknown agent {req.agent!r}")
    spec = AGENTS[req.agent]()

    async def events():
        try:
            async for ev in run_agent(spec, auth, req.message, [h.model_dump() for h in req.history]):
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False, default=str)}
        except Exception as e:  # noqa: BLE001
            log.exception("chat stream failed")
            yield {"event": "error", "data": json.dumps({"type": "error", "message": mask_secrets(str(e))[:500]})}

    return EventSourceResponse(events(), ping=10)  # heartbeat keeps CloudFront's 60 s origin timeout at bay


app.mount("/", mcp_app)  # serves /mcp; registered last so the /api routes above take precedence
