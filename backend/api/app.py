"""FastAPI app: REST + SSE for the web UI, with the MCP server mounted at /mcp.

Each browser gets a random visitor id cookie; with tenant and persona it names the visitor's private memory graph
(``mem__<tenant>__<persona>__<visitor>``), so demo visitors never see each other's memories.

Run locally:  python -m uvicorn backend.api.app:app --port 8000
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from http.cookies import CookieError, SimpleCookie

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.agents.orchestrator import SPECS
from backend.agents.session import run_session
from backend.config import mask_secrets
from backend.graph import queries as Q
from backend.graph.db import get_db
from backend.ingest import live
from backend.mcp_server.server import http_app
from backend.memory.store import SESSION_RE, MemoryStore
from backend.tenancy import TENANTS, AuthContext, AuthError, make_auth, personas
from backend.tools import memory as memtools
from backend.tools import ops
from backend.tools.base import ToolContext, ToolError

log = logging.getLogger(__name__)
mcp_app = http_app()

VISITOR_COOKIE = "fb_vid"
_VID_RE = re.compile(r"^[0-9a-f]{12}$")
AGENT_CHOICES = ("auto", *SPECS)


class VisitorMiddleware:
    """Pure ASGI middleware: read or mint the visitor id cookie for /api requests. The Set-Cookie header is added
    to the response-start message, so streaming (SSE) responses are not buffered."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        vid, https = None, False
        for k, v in scope.get("headers") or []:
            if k == b"cookie" and vid is None:
                c = SimpleCookie()
                try:
                    c.load(v.decode("latin-1"))
                except CookieError:
                    continue
                if VISITOR_COOKIE in c and _VID_RE.match(c[VISITOR_COOKIE].value):
                    vid = c[VISITOR_COOKIE].value
            elif k in (b"cloudfront-forwarded-proto", b"x-forwarded-proto") and v == b"https":
                https = True
        minted = vid is None
        if minted:
            vid = secrets.token_hex(6)
        scope.setdefault("state", {})["visitor"] = vid
        cookie = (f"{VISITOR_COOKIE}={vid}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax"
                  + ("; Secure" if https else "")).encode()

        async def send_with_cookie(msg):
            if minted and msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*(msg.get("headers") or []), (b"set-cookie", cookie)]}
            await send(msg)

        await self.app(scope, receive, send_with_cookie)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The MCP streamable-HTTP session manager starts in the mounted app's lifespan, which a mount does not run.
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(title="FinanceBrain API", version="0.8.0", lifespan=lifespan)
app.add_middleware(VisitorMiddleware)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    tenant: str
    persona: str
    message: str = Field(min_length=1, max_length=4000)
    agent: str = "auto"
    session_id: str | None = None
    memory: bool = True
    history: list[ChatTurn] = Field(default_factory=list)


class Decision(BaseModel):
    tenant: str
    persona: str
    approve: bool
    note: str = Field(default="", max_length=1000)


class PersonaRef(BaseModel):
    tenant: str
    persona: str


class LiveAdvance(BaseModel):
    tenant: str
    days: int = Field(default=1, ge=1, le=5)


class TenantRef(BaseModel):
    tenant: str


def visitor(request: Request) -> str:
    return getattr(request.state, "visitor", None) or "anon"


def _auth(request: Request, tenant: str, persona: str) -> AuthContext:
    try:
        return make_auth(tenant, persona, user=visitor(request))
    except AuthError as e:
        raise HTTPException(400, str(e)) from None


def _tenant(tenant: str) -> str:
    if tenant not in TENANTS:
        raise HTTPException(400, f"unknown tenant {tenant!r}")
    return tenant


def _tool_http_error(e: ToolError) -> HTTPException:
    msg = str(e)
    return HTTPException(403 if msg.startswith("Permission denied") else 404 if msg.startswith("No ") else 400, msg)


def _sse(gen) -> EventSourceResponse:
    async def events():
        try:
            async for ev in gen:
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False, default=str)}
        except Exception as e:  # noqa: BLE001
            log.exception("stream failed")
            yield {"event": "error", "data": json.dumps({"type": "error", "message": mask_secrets(str(e))[:500]})}

    return EventSourceResponse(events(), ping=10)  # heartbeat keeps CloudFront's 60 s origin timeout at bay


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


@app.get("/api/whoami")
async def whoami(request: Request, tenant: str, persona: str) -> dict:
    auth = _auth(request, tenant, persona)
    return {"visitor": visitor(request), "tenant": tenant, "persona": persona, "scopes": list(auth.scopes),
            "memory_graph": MemoryStore(auth).name}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    auth = _auth(request, req.tenant, req.persona)
    if req.agent not in AGENT_CHOICES:
        raise HTTPException(400, f"unknown agent {req.agent!r}")
    if req.session_id and not SESSION_RE.match(req.session_id):
        raise HTTPException(400, "bad session_id")
    return _sse(run_session(auth, req.message, agent=req.agent, session_id=req.session_id, memory=req.memory,
                            history=[h.model_dump() for h in req.history]))


# ------------------------------------------------------------------------------------------------ memory
@app.get("/api/memory")
async def memory_overview(request: Request, tenant: str, persona: str) -> dict:
    st = MemoryStore(_auth(request, tenant, persona))
    return {**await st.overview(), "trace": st.trace}


@app.get("/api/memory/sessions/{session_id}")
async def memory_session(session_id: str, request: Request, tenant: str, persona: str) -> dict:
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "bad session_id")
    st = MemoryStore(_auth(request, tenant, persona))
    if not await st.exists():
        raise HTTPException(404, "no memory yet")
    info = await st.session_info(session_id)
    if not info:
        raise HTTPException(404, f"No session {session_id}")
    return {"session": info, "turns": await st.turns(session_id)}


@app.delete("/api/memory/sessions/{session_id}")
async def forget_session(session_id: str, request: Request, tenant: str, persona: str) -> dict:
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "bad session_id")
    st = MemoryStore(_auth(request, tenant, persona))
    if not await st.exists() or not await st.forget_session(session_id):
        raise HTTPException(404, f"No session {session_id}")
    return {"forgotten": session_id}


@app.delete("/api/memory/facts/{fact_id}")
async def forget_fact(fact_id: str, request: Request, tenant: str, persona: str) -> dict:
    if not re.match(r"^mf_[0-9a-f]{10}$", fact_id):
        raise HTTPException(400, "bad fact id")
    st = MemoryStore(_auth(request, tenant, persona))
    if not await st.exists() or not await st.forget_fact(fact_id):
        raise HTTPException(404, f"No memory {fact_id}")
    return {"forgotten": fact_id}


@app.delete("/api/memory")
async def forget_everything(request: Request, tenant: str, persona: str) -> dict:
    st = MemoryStore(_auth(request, tenant, persona))
    await st.forget_all()
    return {"deleted_graph": st.name}


@app.get("/api/playbooks")
async def playbooks(request: Request, tenant: str, persona: str) -> dict:
    return await memtools.list_playbooks(ToolContext(_auth(request, tenant, persona), agent="human"))


# ------------------------------------------------------------------------------------------------ live feed
@app.get("/api/live")
async def live_status(tenant: str) -> dict:
    return await live.status(_tenant(tenant))


@app.post("/api/live/advance")
async def live_advance(body: LiveAdvance) -> dict:
    """Replay the next day(s) of the live feed through the real pipeline; the Sentinel reacts to each batch."""
    try:
        return await live.advance(_tenant(body.tenant), body.days)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.post("/api/demo/reset")
async def demo_reset(body: TenantRef) -> dict:
    """Reset demo: restore the tenant graph from its pristine snapshot (GRAPH.COPY)."""
    try:
        return await live.reset_tenant(_tenant(body.tenant))
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None


# ------------------------------------------------------------------------------------------------ investigations
@app.get("/api/cases")
async def cases(request: Request, tenant: str, persona: str, status: str | None = None) -> dict:
    try:
        return await ops.list_cases(ToolContext(_auth(request, tenant, persona), agent="human"), status=status,
                                    limit=50)
    except ToolError as e:
        raise _tool_http_error(e) from None


@app.get("/api/cases/{case_id}")
async def case_detail(case_id: str, request: Request, tenant: str, persona: str) -> dict:
    ctx = ToolContext(_auth(request, tenant, persona), agent="human")
    try:
        out = await ops.get_case(ctx, case_id)
    except ToolError as e:
        raise _tool_http_error(e) from None
    return {**out, "evidence_graph": ctx.evidence.to_dict(), "docs": list(ctx.docs.values())}


@app.get("/api/tasks")
async def tasks(request: Request, tenant: str, persona: str, status: str | None = "any") -> dict:
    try:
        return await ops.list_tasks(ToolContext(_auth(request, tenant, persona), agent="human"), status=status,
                                    mine=False, limit=50)
    except ToolError as e:
        raise _tool_http_error(e) from None


@app.get("/api/blackboard")
async def blackboard(request: Request, tenant: str, persona: str) -> dict:
    """Agents view: blackboard tasks plus the HANDED_OFF_TO timeline between agents."""
    ctx = ToolContext(_auth(request, tenant, persona), agent="human")
    try:
        task_list = await ops.list_tasks(ctx, status="any", mine=False, limit=50)
        ctx.require("act", "the agent blackboard")
    except ToolError as e:
        raise _tool_http_error(e) from None
    rows = await ctx.q("RECENT_HANDOFFS", Q.RECENT_HANDOFFS, {"limit": 50})
    handoffs = [{"from": a, "to": b, "ts": ts, "note": note, "task_id": tid, "case_id": cid}
                for a, b, ts, note, tid, cid in rows]
    return {**task_list, "handoffs": handoffs}


@app.post("/api/tasks/{task_id}/run")
async def run_task(task_id: str, body: PersonaRef, request: Request) -> EventSourceResponse:
    """Run the agent a blackboard task is assigned to (e.g. a Sentinel handoff) and stream its work."""
    auth = _auth(request, body.tenant, body.persona)
    ctx = ToolContext(auth, agent="human")
    try:
        ctx.require("act", "running agent tasks")
    except ToolError as e:
        raise _tool_http_error(e) from None
    rows = await ctx.q("TASK_INFO", Q.TASK_INFO, {"id": task_id})
    if not rows:
        raise HTTPException(404, f"No task {task_id}")
    tid, status, to_agent, from_agent, case_id, note, _title = rows[0]
    agent = str(to_agent or "").removeprefix("agent_")
    if status != "open":
        raise HTTPException(409, f"task {tid} is {status}")
    if agent not in SPECS:
        raise HTTPException(400, f"task {tid} is assigned to {agent!r}, which cannot be run from the UI")
    msg = (f"Task {tid} for case {case_id} was handed to you by the {str(from_agent or '').removeprefix('agent_')} "
           f"agent.\nHandoff note: {note}\n\nClaim the task, review the case and complete your part.")
    return _sse(run_session(auth, msg, agent=agent))


@app.post("/api/actions/{action_id}/decision")
async def decide(action_id: str, body: Decision, request: Request) -> dict:
    """Human-in-the-loop approval. Requires the `approve` scope (Compliance Officer persona)."""
    try:
        return await ops.decide_action(_auth(request, body.tenant, body.persona), action_id, body.approve, body.note)
    except ToolError as e:
        raise _tool_http_error(e) from None


app.mount("/", mcp_app)  # serves /mcp; registered last so the /api routes above take precedence
