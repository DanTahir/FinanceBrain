"""Item 6: signed auth tokens, ACL / information barrier, tenant isolation, read-only Cypher guard, MCP surface.

DB-dependent tests skip when FalkorDB is unreachable. Searches reuse a stored document embedding (``vector=``), so
the suite makes no LLM or embedding calls.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
import redis

from backend import tenancy
from backend.config import get_settings
from backend.graph import db
from backend.tenancy import TENANTS, AuthError, auth_from_bearer, make_auth, personas, sign_auth, verify_auth
from backend.tools import reasoning, retrieval
from backend.tools.base import ToolContext, ToolError, resolve

BRAIN_TOOLS = {"search_knowledge", "get_entity", "neighborhood", "graph_schema", "run_cypher_readonly", "who_knows",
               "who_owns", "trace_decision", "blast_radius", "find_path"}


# ------------------------------------------------------------------------------------------------ no database
def test_token_roundtrip_tamper_and_forgery():
    a = make_auth("halcyon", "aml_analyst", user="u1")
    tok = sign_auth(a)
    assert verify_auth(tok) == a
    _, _, sig = tok.split(".")
    escalated = tenancy._b64(json.dumps({"t": "halcyon", "p": "compliance_officer", "u": "u1",
                                         "s": ["read", "reveal"], "exp": int(time.time()) + 900}).encode())
    with pytest.raises(AuthError):
        verify_auth(f"fb1.{escalated}.{sig}")
    with pytest.raises(AuthError):
        verify_auth(tok[:-3] + ("xyz" if not tok.endswith("xyz") else "abc"))
    with pytest.raises(AuthError):
        verify_auth("not-a-token")


def test_token_expiry():
    with pytest.raises(AuthError, match="expired"):
        verify_auth(sign_auth(make_auth("halcyon", "engineer"), ttl=-5))


def test_demo_token_maps_to_read_only_engineer(monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mcp_token", "demo-token-123")
    a = auth_from_bearer("demo-token-123")
    assert (a.tenant, a.persona, a.scopes) == ("halcyon", "engineer", ("read",))
    with pytest.raises(AuthError):
        auth_from_bearer("demo-token-124")


def test_personas_expose_directory_fields_only():
    for t in TENANTS:
        ps = personas(t)
        assert {"compliance_officer", "aml_analyst", "engineer"} <= set(ps)
        for p in ps.values():
            assert set(p.public()) == {"key", "person_id", "name", "title", "team", "label"}
    with pytest.raises(AuthError):
        make_auth("halcyon", "ceo")
    with pytest.raises(AuthError):
        make_auth("acme", "engineer")


@pytest.mark.parametrize("cypher", [
    "MATCH (p:Person) SET p.x = 1", "CREATE (:X)", "MATCH (n) DETACH DELETE n", "MERGE (a:A {id: 1})",
    "MATCH (d:Document) RETURN d.text", "MATCH (d:Deal) RETURN d.codename", "MATCH (f:Fact) RETURN f.statement",
    "MATCH (d:Document) RETURN properties(d)", "CALL db.idx.fulltext.queryNodes('Document', 'falcon')",
    "MATCH (d:Document) RETURN d.`title`", "MATCH (p:Person) RETURN p.title",  # .title is blocked for every label
])
def test_cypher_guard_rejects(cypher):
    assert retrieval._BLOCK.search(cypher)


@pytest.mark.parametrize("cypher", [
    "MATCH (s:System)<-[:DEPENDS_ON]-(d) RETURN s.name, count(d)", "MATCH (p:Person) RETURN p.name, p.team_id",
    "MATCH (a:Account) RETURN toSet(a.kind)", "MATCH (t:Transaction) RETURN t.amount ORDER BY t.ts SKIP 1 LIMIT 5",
])
def test_cypher_guard_allows(cypher):
    assert not retrieval._BLOCK.search(cypher)


# ------------------------------------------------------------------------------------------------ database
@pytest.fixture
def live_db():
    s = get_settings()
    try:
        redis.Redis(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password, socket_timeout=2).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("FalkorDB not reachable")
    db._db = None  # the async pool is bound to the event loop that created it; each test has its own loop
    yield
    db._db = None


def ctx_for(tenant: str, persona: str) -> ToolContext:
    return ToolContext(make_auth(tenant, persona))


async def doc_vector(ctx: ToolContext, doc_id: str) -> list[float]:
    rows = await ctx.q("test:embedding", "MATCH (d:Document {id: $id}) RETURN d.embedding", {"id": doc_id})
    return [float(x) for x in rows[0][0]]


async def test_mnpi_documents_only_reach_deal_team(live_db):
    probe = ctx_for("halcyon", "compliance_officer")
    v = await doc_vector(probe, "meeting-00012")  # the Project Falcon diligence meeting (MNPI)
    eng = await retrieval.search_knowledge(ctx_for("halcyon", "engineer"), "Project Falcon diligence", k=12, vector=v)
    assert eng["results"]
    for r in eng["results"]:
        assert r.get("sensitivity") != "MNPI"
        assert "falcon" not in (str(r.get("title", "")) + r.get("text", "")).lower()
    banker = await retrieval.search_knowledge(ctx_for("halcyon", "ma_banker"), "Project Falcon diligence", k=12,
                                              vector=v)
    assert "meeting-00012" in {r["id"] for r in banker["results"]}


async def test_walled_deal_is_unresolvable_for_outsiders(live_db):
    with pytest.raises(ToolError):
        await retrieval.get_entity(ctx_for("halcyon", "engineer"), "deal_falcon")
    assert await resolve(ctx_for("halcyon", "engineer"), "Project Falcon") is None
    cco = await retrieval.get_entity(ctx_for("halcyon", "compliance_officer"), "deal_falcon")
    assert cco["properties"]["codename"] != "⟨restricted⟩"
    assert await resolve(ctx_for("halcyon", "compliance_officer"), "#project-falcon") is not None


async def test_deal_names_do_not_leak_through_structure(live_db):
    eng = ctx_for("halcyon", "engineer")
    # the deal team's private channel is named after the codename; a deal-team member's neighbourhood shows it
    nb = await retrieval.neighborhood(eng, "e_040", limit=60)
    assert all("falcon" not in str(n.get("name", "")).lower() for n in nb["neighbors"])
    out = await retrieval.run_cypher_readonly(eng, "MATCH (c:Channel) RETURN c.id, c.name", limit=100)
    assert all("falcon" not in json.dumps(r, ensure_ascii=False).lower() for r in out["rows"])
    assert any("⟨restricted⟩" in r for r in out["rows"])
    banker = await retrieval.run_cypher_readonly(ctx_for("halcyon", "ma_banker"),
                                                 "MATCH (c:Channel) RETURN c.name", limit=100)
    assert any("falcon" in json.dumps(r).lower() for r in banker["rows"])


async def test_decision_trail_respects_acl(live_db):
    eng = await reasoning.trace_decision(ctx_for("halcyon", "engineer"), "crypto onboarding")
    cco = await reasoning.trace_decision(ctx_for("halcyon", "compliance_officer"), "crypto onboarding")
    assert eng["decision"]["id"] == cco["decision"]["id"] == "dec_crypto_pause"
    assert "hidden_sources" in eng
    eng_ids, cco_ids = {t["id"] for t in eng["timeline"]}, {t["id"] for t in cco["timeline"]}
    assert eng_ids < cco_ids
    assert "email-00084" in cco_ids


async def test_tenant_isolation(live_db):
    halcyon = ctx_for("halcyon", "compliance_officer")
    v = await doc_vector(halcyon, "slack-00988")  # a ReconLegacy message in Halcyon
    bw = ctx_for("brightwater", "compliance_officer")
    assert bw.graph_name == "brain__brightwater"
    assert await resolve(bw, "sys_reconlegacy") is None
    assert await resolve(bw, "ReconLegacy", {"System"}) is None or \
        (await resolve(bw, "ReconLegacy", {"System"}))["id"] != "sys_reconlegacy"
    res = await retrieval.search_knowledge(bw, "ReconLegacy Perl batch", k=10, vector=v)
    assert all("reconlegacy" not in r.get("text", "").lower() for r in res["results"])


async def test_read_only_enforced_by_database(live_db):
    ctx = ctx_for("halcyon", "engineer")
    with pytest.raises(ToolError):
        await ctx.q("test:write", "CREATE (:ShouldNotExist {x: 1})")
    with pytest.raises(ToolError):
        await retrieval.run_cypher_readonly(ctx, "MATCH (p:Person) SET p.hacked = true")
    out = await retrieval.run_cypher_readonly(ctx, "MATCH (d:Document) WHERE d.sensitivity = 'MNPI' RETURN d LIMIT 3")
    assert out["rows"] and all(r[0]["id"] == "⟨restricted⟩" for r in out["rows"])


async def test_who_owns_is_bitemporal(live_db):
    out = await reasoning.who_owns(ctx_for("halcyon", "engineer"), "KYC pipeline", as_of="2026-08-15")
    assert out["system"]["id"] == "sys_kycpipeline"
    assert {"e_063", "team_ops"} <= {h["owner_id"] for h in out["owners_as_of"]}


async def test_mcp_in_memory_auth_via_meta(live_db):
    from mcp.client.client import Client

    from backend.mcp_server.server import server

    async with Client(server) as c:
        tools = await c.list_tools()
        assert BRAIN_TOOLS <= {t.name for t in tools.tools}
        denied = await c.call_tool("get_entity", {"entity": "sys_reconlegacy"})
        assert denied.is_error and "unauthorized" in denied.content[0].text
        tok = sign_auth(make_auth("halcyon", "engineer"))
        missing = await c.call_tool("get_entity", {"entity": "deal_falcon"}, meta={"fb_auth": tok})
        assert missing.is_error and "is visible to you" in missing.content[0].text
        tok = sign_auth(make_auth("halcyon", "engineer"))
        ok = await c.call_tool("get_entity", {"entity": "sys_reconlegacy"}, meta={"fb_auth": tok})
        assert not ok.is_error
        data = json.loads(ok.content[0].text)
        assert data["result"]["id"] == "sys_reconlegacy" and data["_trace"]


async def test_http_api_and_mcp_bearer(live_db):
    from backend.api.app import app

    rpc_headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

    def rpc(method: str, params: dict, i: int = 1) -> dict:
        return {"jsonrpc": "2.0", "id": i, "method": method, "params": params}

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            tenants = (await c.get("/api/tenants")).json()
            assert {t["id"] for t in tenants} == set(TENANTS)
            assert (await c.get("/api/healthz")).json()["ok"] is True
            bad = await c.post("/api/chat", json={"tenant": "halcyon", "persona": "ceo", "message": "hi"})
            assert bad.status_code == 400

            listed = await c.post("/mcp", headers=rpc_headers, json=rpc("tools/list", {}))
            assert listed.status_code == 200, listed.text
            assert BRAIN_TOOLS <= {t["name"] for t in listed.json()["result"]["tools"]}

            call = rpc("tools/call", {"name": "who_owns", "arguments": {"system": "sys_kycpipeline"}}, 2)
            anon = (await c.post("/mcp", headers=rpc_headers, json=call)).json()
            assert anon["result"]["isError"] is True
            tok = sign_auth(make_auth("halcyon", "engineer"))
            authed = (await c.post("/mcp", headers={**rpc_headers, "Authorization": f"Bearer {tok}"}, json=call)).json()
            assert authed["result"]["isError"] is False
            assert json.loads(authed["result"]["content"][0]["text"])["result"]["system"]["id"] == "sys_kycpipeline"
