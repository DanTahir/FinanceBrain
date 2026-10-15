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
from pydantic import Field

from backend.tenancy import AuthContext, AuthError, auth_from_bearer, verify_auth
from backend.tools import reasoning, retrieval
from backend.tools.base import ToolContext, ToolError

log = logging.getLogger(__name__)

INSTRUCTIONS = """FinanceBrain is the knowledge graph (FalkorDB) of a finance company: email, Slack, tickets, docs,
meetings and CRM notes, plus people, teams, systems, ownership, expertise, decisions, clients, accounts and
transactions. Personal data appears only as pseudonymous tokens like ⟨PHONE_0042⟩. Every result is filtered by the
caller's access rights. Start with search_knowledge; use who_knows / who_owns / trace_decision / blast_radius /
find_path for expertise, ownership, decision rationale, dependency impact and connections."""

server = MCPServer(name="financebrain", title="FinanceBrain", version="0.6.0", instructions=INSTRUCTIONS)


def _meta_token(ctx: Context) -> str | None:
    meta = getattr(ctx.request_context, "meta", None)
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get("fb_auth")
    return getattr(meta, "fb_auth", None) or (getattr(meta, "model_extra", None) or {}).get("fb_auth")


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
    tc = ToolContext(auth)
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


def http_app():
    """Streamable-HTTP Starlette app serving /mcp (stateless JSON responses; mounted by the FastAPI app).

    DNS-rebinding protection is off because the origin only accepts CloudFront traffic carrying a secret header
    (checked by Caddy) and every request is authenticated by token.
    """
    return server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
