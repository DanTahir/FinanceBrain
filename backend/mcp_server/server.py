"""FinanceBrain MCP tool server (MCP Python SDK v2 ``MCPServer``; streamable HTTP at ``/mcp``).

In-process agents connect through the SDK's in-memory transport and put an HMAC-signed auth token in each request's
``_meta`` (``fb_auth``). External clients (e.g. Claude Desktop) send ``Authorization: Bearer <token>`` with a signed
token or the read-only public demo token. The token, never the model, decides tenant graph, persona and ACL.

Every tool returns ``{"result": ..., "_trace": [...cypher...], "_evidence": {nodes, edges}, "_docs": [...]}``; the
agent runtime feeds only ``result`` to the model and streams the rest to the UI (Cypher trace panel, evidence graph,
citations).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field

from backend.tenancy import AuthContext, AuthError, auth_from_bearer, verify_auth
from backend.tools import investigation, ops, reasoning, retrieval
from backend.tools.base import ToolContext, ToolError

log = logging.getLogger(__name__)

INSTRUCTIONS = """FinanceBrain is the knowledge graph (FalkorDB) of a finance company: email, Slack, tickets, docs,
meetings and CRM notes, plus people, teams, systems, ownership, expertise, decisions, clients, accounts and
transactions. Personal data appears only as pseudonymous tokens like ⟨PHONE_0042⟩. Every result is filtered by the
caller's access rights. Start with search_knowledge; use who_knows / who_owns / trace_decision / blast_radius /
find_path for expertise, ownership, decision rationale, dependency impact and connections, and key_people for
PageRank / betweenness over the communication network. Compliance and AML personas can also investigate
(list_alerts, detect_rings, linked_accounts, trace_funds, barrier_check) and run cases on the shared blackboard
(open_case, propose_action, handoff, claim_task, ...). Actions are only proposed: a human approves them in the
FinanceBrain UI."""

server = MCPServer(name="financebrain", title="FinanceBrain", version="0.7.0", instructions=INSTRUCTIONS)


def _meta_value(ctx: Context, key: str) -> str | None:
    meta = getattr(ctx.request_context, "meta", None)
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get(key)
    return getattr(meta, key, None) or (getattr(meta, "model_extra", None) or {}).get(key)


def _meta_token(ctx: Context) -> str | None:
    return _meta_value(ctx, "fb_auth")


def auth_of(ctx: Context) -> AuthContext:
    token = _meta_token(ctx)
    if token:
        return verify_auth(token)
    headers = {k.lower(): v for k, v in (ctx.headers or {}).items()}
    if headers.get("x-fb-auth"):
        return verify_auth(headers["x-fb-auth"])
    authz = headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return auth_from_bearer(authz[7:])
    raise AuthError("missing credentials (send Authorization: Bearer <token>)")


async def _run(ctx: Context, fn: Callable[..., Awaitable[Any]], **kwargs: Any) -> dict:
    """Authenticate, run one tool, and wrap its result. Expected failures become MCP tool errors (is_error result
    with the message), so the model can read and react to them; anything else surfaces as a generic error."""
    try:
        auth = auth_of(ctx)
    except AuthError as e:
        raise MCPToolError(f"unauthorized: {e}") from None
    agent = _meta_value(ctx, "fb_agent")
    tc = ToolContext(auth, agent=agent if agent in ops.AGENTS else "external")
    try:
        result = await fn(tc, **kwargs)
    except ToolError as e:
        raise MCPToolError(str(e)) from None
    return tc.envelope(result)


# ------------------------------------------------------------------------------------------------ retrieval
@server.tool(title="Search company knowledge")
async def search_knowledge(
    ctx: Context,
    query: Annotated[str, Field(description="Natural-language question or keywords.")],
    k: Annotated[int, Field(description="How many documents to return (1-15, default 8).")] = 8,
    kinds: Annotated[list[str] | None, Field(
        description="Optional filter, any of: email, slack, ticket, doc, meeting, crm.")] = None,
    since: Annotated[str | None, Field(description="Optional ISO date; only documents on or after it.")] = None,
    until: Annotated[str | None, Field(description="Optional ISO date; only documents before it.")] = None,
) -> dict:
    """Hybrid search over every document the caller may see: semantic vectors (voyage-4) + full-text keywords +
    extracted facts, fused by reciprocal rank and expanded through the graph (author, channel, neighbouring thread
    replies, mentioned entities). Returns documents with ids to cite and relevant dated facts (current or
    superseded). Use for most questions."""
    return await _run(ctx, retrieval.search_knowledge, query=query, k=k, kinds=kinds, since=since, until=until)


@server.tool(title="Get entity")
async def get_entity(
    ctx: Context,
    entity: Annotated[str, Field(description="An id (e_073, sys_reconlegacy, c_0060, email-00084, ...) or a name.")],
) -> dict:
    """All properties of one entity (person, team, system, client, account, transaction, deal, document, ...) and
    a count of its relationships by type. Documents are returned in full if the caller may see them."""
    return await _run(ctx, retrieval.get_entity, entity=entity)


@server.tool(title="Neighbourhood")
async def neighborhood(
    ctx: Context,
    entity: Annotated[str, Field(description="An entity id or name.")],
    rel_types: Annotated[list[str] | None, Field(
        description="Optional relationship types to follow, e.g. ['HOLDS','HAS_IDENTIFIER'].")] = None,
    limit: Annotated[int, Field(description="Max neighbours (1-60, default 30).")] = 30,
) -> dict:
    """One-hop neighbours of an entity with relationship type and direction, strongest first (ACL-filtered)."""
    return await _run(ctx, retrieval.neighborhood, entity=entity, rel_types=rel_types, limit=limit)


@server.tool(title="Graph schema")
async def graph_schema(ctx: Context) -> dict:
    """Node labels and relationship types with counts, the main patterns and id conventions. Call before writing
    Cypher for run_cypher_readonly."""
    return await _run(ctx, retrieval.graph_schema)


@server.tool(title="Read-only Cypher")
async def run_cypher_readonly(
    ctx: Context,
    cypher: Annotated[str, Field(description="A read-only openCypher query (FalkorDB dialect).")],
    params: Annotated[dict[str, Any] | None, Field(description="Optional query parameters.")] = None,
    limit: Annotated[int, Field(description="Max rows returned (1-100, default 50).")] = 50,
) -> dict:
    """Run a read-only Cypher query (GRAPH.RO_QUERY, 5 s timeout) for structural or aggregate questions the other
    tools cannot answer (counts, groupings, degrees). Writes and access to document/fact content properties are
    rejected; use search_knowledge / get_entity for content."""
    return await _run(ctx, retrieval.run_cypher_readonly, cypher=cypher, params=params, limit=limit)


# ------------------------------------------------------------------------------------------------ reasoning
@server.tool(title="Who knows")
async def who_knows(
    ctx: Context,
    about: Annotated[str, Field(description="A system, client, deal or counterparty name/id, or a free-text subject.")],
    limit: Annotated[int, Field(description="Max experts (1-15, default 6).")] = 6,
) -> dict:
    """Who actually knows about something, ranked by derived KNOWS_ABOUT expertise (answering colleagues' questions
    and writing authoritative docs count most), with an explanation per person, the formal owners for comparison,
    and a bus-factor assessment (knowledge concentration, departures)."""
    return await _run(ctx, reasoning.who_knows, about=about, limit=limit)


@server.tool(title="Who owns")
async def who_owns(
    ctx: Context,
    system: Annotated[str, Field(description="System name or id, e.g. 'KYC pipeline' or sys_kycpipeline.")],
    as_of: Annotated[str | None, Field(description="Optional ISO date to ask who owned it then (default today).")] = None,
) -> dict:
    """Formal ownership of a system as of a date, the full ownership history (bitemporal OWNS edges) and extracted
    ownership facts with sources."""
    return await _run(ctx, reasoning.who_owns, system=system, as_of=as_of)


@server.tool(title="Trace decision")
async def trace_decision(
    ctx: Context,
    decision: Annotated[str | None, Field(
        description="Decision id (dec_...) or words from it, e.g. 'crypto onboarding'. Omit to list decisions.")] = None,
) -> dict:
    """The rationale trail of a decision: where it was decided, the documents that justified it in time order,
    and what it supersedes / was superseded by."""
    return await _run(ctx, reasoning.trace_decision, decision=decision)


@server.tool(title="Blast radius")
async def blast_radius(
    ctx: Context,
    system: Annotated[str, Field(description="System name or id, e.g. PricingSvc.")],
    depth: Annotated[int, Field(description="Max dependency hops (1-6, default 4).")] = 4,
) -> dict:
    """What breaks if a system goes down: every system that transitively depends on it (by hop distance) with
    owners and experts, its own upstream dependencies, and related tickets/docs."""
    return await _run(ctx, reasoning.blast_radius, system=system, depth=depth)


@server.tool(title="Find path")
async def find_path(
    ctx: Context,
    source: Annotated[str, Field(description="Start entity id or name.")],
    target: Annotated[str, Field(description="End entity id or name.")],
    max_hops: Annotated[int, Field(description="Max path length (2-6, default 4).")] = 4,
) -> dict:
    """All shortest connections between two entities (e.g. two clients sharing a phone token, a person and a
    system). Paths through documents or deals outside the caller's access are omitted."""
    return await _run(ctx, reasoning.find_path, source=source, target=target, max_hops=max_hops)


@server.tool(title="Key people")
async def key_people(
    ctx: Context,
    team: Annotated[str | None, Field(description="Optional team name or id to rank within.")] = None,
    limit: Annotated[int, Field(description="Max people (1-20, default 8).")] = 8,
) -> dict:
    """The most central people in the firm's communication network (derived from who messages and replies to
    whom, MNPI excluded): PageRank (most consulted) and betweenness (bridges between groups, key-person risk),
    with departure dates."""
    return await _run(ctx, investigation.key_people, team=team, limit=limit)


# ------------------------------------------------------------------------------------------------ investigation
@server.tool(title="List alerts")
async def list_alerts(
    ctx: Context,
    min_score: Annotated[float, Field(description="Also include unalerted transactions with fraud_score at or "
                                                  "above this (0-1, default 0.8).")] = 0.8,
    limit: Annotated[int, Field(description="Max transactions (1-50, default 15).")] = 15,
) -> dict:
    """Flagged transactions (monitoring alerts and high fraud scores) with accounts, holders and counterparty,
    highest risk first. Requires investigation rights."""
    return await _run(ctx, investigation.list_alerts, min_score=min_score, limit=limit)


@server.tool(title="Detect rings")
async def detect_rings(
    ctx: Context,
    min_clients: Annotated[int, Field(description="Minimum clients per group (2-10, default 3).")] = 3,
    limit: Annotated[int, Field(description="Max groups returned (1-10, default 5).")] = 5,
) -> dict:
    """Find groups of clients tied together by shared identifiers (phone, address, device, email, SSN) using
    Weakly Connected Components, then risk-score each group from identifier strength, money moving between member
    accounts, common exit counterparties, structuring and alerts (with plain-language signals). Requires
    investigation rights."""
    return await _run(ctx, investigation.detect_rings, min_clients=min_clients, limit=limit)


@server.tool(title="Linked accounts")
async def linked_accounts(
    ctx: Context,
    entity: Annotated[str, Field(description="Start: a transaction (t_r016), account, client or PII token.")],
    hops: Annotated[int, Field(description="Identifier hops to expand (1-3, default 2).")] = 2,
    identifier_types: Annotated[list[str] | None, Field(
        description="Optional identifier types to follow, e.g. ['PHONE','DEVICE_ID'] (default: all strong ones plus "
                    "ADDRESS; DOB is excluded).")] = None,
) -> dict:
    """Multi-hop identifier linking: every client connected to the start through shared PII tokens, how each was
    reached, their accounts, and money flows among those accounts. Requires investigation rights."""
    return await _run(ctx, investigation.linked_accounts, entity=entity, hops=hops,
                      identifier_types=identifier_types)


@server.tool(title="Trace funds")
async def trace_funds(
    ctx: Context,
    start: Annotated[str, Field(description="A transaction, account, client or counterparty id.")],
    direction: Annotated[str, Field(description="'downstream' (where money went, default) or 'upstream'.")] =
    "downstream",
    hops: Annotated[int, Field(description="Max hops (1-4, default 3).")] = 3,
    since: Annotated[str | None, Field(description="Optional ISO date; only flows active since then (defaults to "
                                                   "the transaction date when starting from a transaction).")] = None,
) -> dict:
    """Follow money through accounts along aggregated FLOWS_TO edges: paths, exit (or source) counterparties with
    amounts, and the clients holding each account. Requires investigation rights."""
    return await _run(ctx, investigation.trace_funds, start=start, direction=direction, hops=hops, since=since)


@server.tool(title="Information-barrier check")
async def barrier_check(
    ctx: Context,
    deal: Annotated[str, Field(description="Deal id or codename, e.g. 'Project Falcon'.")],
) -> dict:
    """Audit a deal's information barrier: the wall (deal team + Compliance), every document that references the
    deal, and which of them reached people outside the wall (recipients, channel members, access grants).
    Requires investigation rights."""
    return await _run(ctx, investigation.barrier_check, deal=deal)


# ------------------------------------------------------------------------------------------------ cases
class EvidenceItem(BaseModel):
    entity: str = Field(description="Entity id or name: transaction, account, client, PII token, document, ...")
    note: str = Field(default="", description="Why it matters, one line.")


def _items(evidence: list[EvidenceItem | str] | None) -> list:
    return [e.model_dump() if isinstance(e, BaseModel) else e for e in (evidence or [])]


@server.tool(title="Open case")
async def open_case(
    ctx: Context,
    title: Annotated[str, Field(description="Short case title.")],
    kind: Annotated[str, Field(description="fraud_ring, suspicious_activity, mnpi_breach, access_review or other.")],
    severity: Annotated[str, Field(description="low, medium, high or critical.")] = "medium",
    summary: Annotated[str, Field(description="What was found and why it matters.")] = "",
    evidence: Annotated[list[EvidenceItem | str] | None, Field(
        description="Key evidence entities with optional notes.")] = None,
) -> dict:
    """Open an investigation case in the shared graph with linked evidence. Reuses an open case of the same kind
    that already holds any of this evidence. Requires investigation rights."""
    return await _run(ctx, ops.open_case, title=title, kind=kind, severity=severity, summary=summary,
                      evidence=_items(evidence))


@server.tool(title="Add case evidence")
async def add_case_evidence(
    ctx: Context,
    case_id: Annotated[str, Field(description="Case id (case_...).")],
    evidence: Annotated[list[EvidenceItem | str], Field(description="Evidence entities with optional notes.")],
) -> dict:
    """Link more evidence to a case."""
    return await _run(ctx, ops.add_case_evidence, case_id=case_id, evidence=_items(evidence))


@server.tool(title="Get case")
async def get_case(ctx: Context, case_id: Annotated[str, Field(description="Case id (case_...).")]) -> dict:
    """A case with its evidence, proposed actions (and human decisions), tasks and handoff history."""
    return await _run(ctx, ops.get_case, case_id=case_id)


@server.tool(title="List cases")
async def list_cases(
    ctx: Context,
    status: Annotated[str | None, Field(description="Optional: open, in_review, pending_approval, escalated, "
                                                    "actioned, closed.")] = None,
    limit: Annotated[int, Field(description="Max cases (1-50, default 20).")] = 20,
) -> dict:
    """Cases on the blackboard, most recently updated first, with evidence and pending-approval counts."""
    return await _run(ctx, ops.list_cases, status=status, limit=limit)


@server.tool(title="Update case")
async def update_case(
    ctx: Context,
    case_id: Annotated[str, Field(description="Case id (case_...).")],
    status: Annotated[str | None, Field(description="Optional new status (e.g. pending_approval, escalated).")] = None,
    severity: Annotated[str | None, Field(description="Optional new severity.")] = None,
    summary: Annotated[str | None, Field(description="Optional replacement summary.")] = None,
) -> dict:
    """Change a case's status, severity or summary."""
    return await _run(ctx, ops.update_case, case_id=case_id, status=status, severity=severity, summary=summary)


@server.tool(title="Propose action")
async def propose_action(
    ctx: Context,
    case_id: Annotated[str, Field(description="Case id (case_...).")],
    kind: Annotated[str, Field(description="freeze_account, file_sar, notify_owner or restrict_channel.")],
    target: Annotated[str | None, Field(description="Account for freeze_account, channel for restrict_channel, "
                                                    "person/team/system for notify_owner; omit for file_sar.")] = None,
    rationale: Annotated[str, Field(description="Why, referring to the evidence.")] = "",
    details: Annotated[str | None, Field(description="Optional payload, e.g. the SAR narrative draft.")] = None,
) -> dict:
    """Propose an action on a case. It is NOT executed: it waits for a human with approval rights."""
    return await _run(ctx, ops.propose_action, case_id=case_id, kind=kind, target=target, rationale=rationale,
                      details=details)


# ------------------------------------------------------------------------------------------------ blackboard
@server.tool(title="Hand off")
async def handoff(
    ctx: Context,
    case_id: Annotated[str, Field(description="Case id (case_...).")],
    to_agent: Annotated[str, Field(description="Receiving agent: 'compliance' or 'investigator'.")],
    note: Annotated[str, Field(description="Findings so far and what the receiving agent should do.")],
) -> dict:
    """Hand a case to another agent: posts a task on the shared blackboard and reassigns the case."""
    return await _run(ctx, ops.handoff, case_id=case_id, to_agent=to_agent, note=note)


@server.tool(title="Claim task")
async def claim_task(ctx: Context, task_id: Annotated[str, Field(description="Task id (task_...).")]) -> dict:
    """Claim an open task assigned to you; returns the task and its case."""
    return await _run(ctx, ops.claim_task, task_id=task_id)


@server.tool(title="List tasks")
async def list_tasks(
    ctx: Context,
    status: Annotated[str | None, Field(description="open (default), claimed, done or any.")] = "open",
    mine: Annotated[bool, Field(description="Only tasks assigned to the calling agent (default true).")] = True,
) -> dict:
    """Tasks on the shared blackboard."""
    return await _run(ctx, ops.list_tasks, status=status, mine=mine)


@server.tool(title="Complete task")
async def complete_task(
    ctx: Context,
    task_id: Annotated[str, Field(description="Task id (task_...).")],
    result: Annotated[str, Field(description="One paragraph: what was done and what awaits approval.")],
) -> dict:
    """Mark a task done with a result summary."""
    return await _run(ctx, ops.complete_task, task_id=task_id, result=result)


def http_app():
    """Streamable-HTTP Starlette app serving /mcp (stateless JSON responses; mounted by the FastAPI app).

    DNS-rebinding protection is off because the origin only accepts CloudFront traffic carrying a secret header
    (checked by Caddy) and every request is authenticated by token.
    """
    return server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
