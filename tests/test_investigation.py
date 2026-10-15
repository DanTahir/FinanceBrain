"""Investigation tools (graph algorithms) and the case / action / handoff lifecycle.

Read-only tests run on brain__halcyon; anything that writes runs on a throwaway GRAPH.COPY of it.
"""

from __future__ import annotations

import pytest
import redis
from mcp.client.client import Client

from backend.config import get_settings
from backend.graph import db
from backend.mcp_server.server import server as mcp_server
from backend.tenancy import make_auth, sign_auth
from backend.tools import base, investigation, ops, retrieval
from backend.tools.base import ToolContext, ToolError

RING = {"c_0060", "c_0100", "c_0112", "c_0118", "c_0218"}
RING_ACCOUNTS = {"a_00128", "a_00213", "a_00238", "a_00247", "a_00451"}
BENIGN = {"c_0252", "c_0085", "c_0027"}
TEST_GRAPH = "test__halcyon_ops"


@pytest.fixture
def live_db():
    s = get_settings()
    try:
        redis.Redis(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password, socket_timeout=2).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("FalkorDB not reachable")
    db._db = None  # the async pool is bound to the event loop that created it
    yield
    db._db = None


@pytest.fixture
async def ops_graph(live_db, monkeypatch):
    await db.copy_graph(db.brain_graph("halcyon"), TEST_GRAPH)
    real = db.brain_graph
    monkeypatch.setattr(base, "brain_graph", lambda tenant: TEST_GRAPH if tenant == "halcyon" else real(tenant))
    yield TEST_GRAPH
    await db.delete_graph(TEST_GRAPH)


def ctx(persona: str, agent: str = "investigator", tenant: str = "halcyon") -> ToolContext:
    return ToolContext(make_auth(tenant, persona), agent=agent)


# ------------------------------------------------------------------------------------------------ derived edges
async def test_flow_and_communication_edges_are_derived(live_db):
    c = ctx("aml_analyst")
    rows = await c.q("t", "MATCH (:Account {id: 'a_00128'})-[f:FLOWS_TO]->(x) RETURN x.id, f.amount")
    assert {"a_00213", "a_00238", "a_00247", "cp_063"} <= {r[0] for r in rows}
    inbound = await c.q("t", "MATCH (:Counterparty {id: 'cp_061'})-[f:FLOWS_TO]->(:Account {id: 'a_00128'}) "
                             "RETURN f.amount, f.alerts")
    assert inbound and inbound[0][0] >= 48000 and inbound[0][1] >= 1
    n = await c.q("t", "MATCH ()-[e:COMMUNICATES]->() RETURN count(e), min(e.weight)")
    assert n[0][0] > 100 and n[0][1] >= 1


# ------------------------------------------------------------------------------------------------ algorithms
async def test_detect_rings_finds_exactly_the_ring_and_scores_the_household_low(live_db):
    out = await investigation.detect_rings(ctx("aml_analyst"), min_clients=3, limit=10)
    top = out["rings"][0]
    assert {c["id"] for c in top["clients"]} == RING
    assert set(top["accounts"]) == RING_ACCOUNTS
    assert top["level"] == "high" and "FRAUD-2291" in top["alerts"]
    assert {s["type"] for s in top["shared_identifiers"]} >= {"PHONE", "ADDRESS", "DEVICE_ID"}
    assert top["exits"][0]["counterparty"] == "cp_063"
    benign = [r for r in out["rings"] if {c["id"] for c in r["clients"]} == BENIGN]
    assert benign and benign[0]["level"] == "low"


async def test_linked_accounts_from_the_flagged_wire_reaches_the_whole_ring(live_db):
    out = await investigation.linked_accounts(ctx("aml_analyst"), "t_r016", hops=2)
    assert out["seed_clients"] == ["c_0060"]
    assert {x["id"] for x in out["linked"]} == RING
    assert out["internal_flows"]


async def test_trace_funds_follows_the_wire_to_the_exchange(live_db):
    out = await investigation.trace_funds(ctx("aml_analyst"), "t_r016", direction="downstream", hops=3)
    assert out["since"] == "2026-09-25"
    assert "cp_063" in {e["id"] for e in out["exits"]}
    assert any("a_00451" in p and "cp_063" in p for p in out["paths"])
    up = await investigation.trace_funds(ctx("aml_analyst"), "a_00128", direction="upstream", hops=1,
                                         since="2026-09-20")
    assert "cp_061" in {s["id"] for s in up["sources"]}


async def test_key_people_ranks_people_and_filters_by_team(live_db):
    out = await investigation.key_people(ctx("engineer", agent="brain"), limit=5)
    assert len(out["people"]) == 5 and all(p["id"].startswith("e_") for p in out["people"])
    team = await investigation.key_people(ctx("engineer", agent="brain"), team="team_compliance", limit=3)
    assert team["people"] and all(p.get("team") == "team_compliance" for p in team["people"])


async def test_barrier_check_surfaces_the_falcon_leak_for_compliance_only(live_db):
    cco = ctx("compliance_officer", agent="compliance")
    out = await investigation.barrier_check(cco, "Project Falcon")
    assert {p["id"] for p in out["wall"]["deal_team"]} >= {"e_039", "e_040"}
    leak = [b for b in out["breaches"] if b["id"] == "email-00301"]
    assert leak and "e_006" in {o["id"] for o in leak[0]["outsiders"]}
    assert leak[0]["author"]["id"] == "e_040"
    # The CCO reads the flagged email through audit-logged surveillance; the analyst (no 'reveal') gets metadata.
    assert leak[0].get("excerpt") and leak[0]["access"].startswith("compliance surveillance")
    assert any(e["kind"] == "surveillance_read" and "email-00301" in e["docs"] for e in cco.events)
    assert "email-00301" in cco.docs
    analyst = await investigation.barrier_check(ctx("aml_analyst", agent="investigator"), "Project Falcon")
    a_leak = [b for b in analyst["breaches"] if b["id"] == "email-00301"][0]
    assert "excerpt" not in a_leak and a_leak["note"].startswith("content outside")
    with pytest.raises(ToolError):
        await investigation.barrier_check(ctx("engineer", agent="brain"), "Project Falcon")


@pytest.mark.parametrize("fn,kwargs", [
    (investigation.detect_rings, {}), (investigation.trace_funds, {"start": "a_00128"}),
    (investigation.linked_accounts, {"entity": "c_0060"}), (investigation.list_alerts, {}),
    (ops.open_case, {"title": "x", "kind": "other"}), (ops.list_cases, {}),
])
async def test_investigation_and_case_tools_require_the_act_scope(live_db, fn, kwargs):
    with pytest.raises(ToolError, match="Permission denied"):
        await fn(ctx("engineer", agent="brain"), **kwargs)


# ------------------------------------------------------------------------------------------------ lifecycle (writes)
async def test_case_lifecycle_handoff_and_human_approval(ops_graph):
    inv = ctx("aml_analyst", agent="investigator")
    opened = await ops.open_case(inv, "Identifier-sharing ring around wire FRAUD-2291", "fraud_ring", "high",
                                 "5 clients share phone/address/device tokens",
                                 [{"entity": "t_r016", "note": "alerted 48k wire"}, "a_00128", "c_0060", "crm-00010"])
    case_id = opened["case"]["id"]
    assert {e["id"] for e in opened["case"]["evidence"]} == {"t_r016", "a_00128", "c_0060", "crm-00010"}
    assert any(e["kind"] == "case_opened" for e in inv.events)
    again = await ops.open_case(inv, "duplicate", "fraud_ring", evidence=["t_r016"])
    assert again["reused_existing_case"] and again["case"]["id"] == case_id

    freeze = await ops.propose_action(inv, case_id, "freeze_account", "a_00128", "Received and fanned out the wire")
    aid = freeze["action"]["id"]
    dup = await ops.propose_action(inv, case_id, "freeze_account", "a_00128", "again")
    assert dup.get("duplicate") and dup["action"]["id"] == aid
    ho = await ops.handoff(inv, case_id, "compliance", "Ring confirmed; draft the SAR")
    assert any(e["kind"] == "handoff" and e["to_agent"] == "compliance" for e in inv.events)

    with pytest.raises(ToolError, match="assigned to"):
        await ops.claim_task(ctx("aml_analyst", agent="investigator"), ho["task_id"])
    comp = ctx("aml_analyst", agent="compliance")
    assert ho["task_id"] in {t["id"] for t in (await ops.list_tasks(comp))["tasks"]}
    claimed = await ops.claim_task(comp, ho["task_id"])
    assert claimed["case"]["id"] == case_id and claimed["case"]["assigned_to"] == ["agent_compliance"]
    sar = await ops.propose_action(comp, case_id, "file_sar", None, "Structuring and layering", details="Narrative")
    await ops.update_case(comp, case_id, status="pending_approval")
    await ops.complete_task(comp, ho["task_id"], "SAR drafted; freeze and SAR await approval")

    # read-only personas never see case management data
    eng = ctx("engineer", agent="brain")
    nb = await retrieval.neighborhood(eng, "a_00128", limit=60)
    assert not {n["label"] for n in nb["neighbors"]} & {"Case", "Action", "Task"}
    with pytest.raises(ToolError, match="restricted"):
        await retrieval.run_cypher_readonly(eng, "MATCH (c:Case) RETURN c.id")
    generic = await retrieval.run_cypher_readonly(eng, "MATCH (a:Account {id: 'a_00128'})<-[r]-(n) RETURN n LIMIT 100")
    assert all(not isinstance(r[0], dict) or r[0].get("_label") not in ("Case", "Action") for r in generic["rows"])
    with pytest.raises(ToolError):
        await retrieval.get_entity(eng, case_id)

    # only a human with the approve scope decides; approval executes in the graph
    with pytest.raises(ToolError, match="approve"):
        await ops.decide_action(make_auth("halcyon", "aml_analyst"), aid, True)
    res = await ops.decide_action(make_auth("halcyon", "compliance_officer"), aid, True, "confirmed")
    assert res["effect"].startswith("Account a_00128 frozen")
    assert (await inv.q("t", "MATCH (a:Account {id: 'a_00128'}) RETURN a.status"))[0][0] == "frozen"
    with pytest.raises(ToolError, match="already"):
        await ops.decide_action(make_auth("halcyon", "compliance_officer"), aid, True)
    rej = await ops.decide_action(make_auth("halcyon", "compliance_officer"), sar["action"]["id"], False, "more")
    assert rej["decision"] == "rejected" and rej["case"]["status"] == "actioned"
    assert {a["status"] for a in rej["case"]["actions"]} == {"executed", "rejected"}

    await ops.clear_ops(ops_graph)
    assert (await inv.q("t", "MATCH (a:Account {id: 'a_00128'}) RETURN a.status"))[0][0] == "open"
    left = await inv.q("t", "MATCH (n) WHERE n:Case OR n:Action OR n:Task RETURN count(n)")
    assert left[0][0] == 0


# ------------------------------------------------------------------------------------------------ MCP surface
async def test_mcp_exposes_investigation_tools_but_no_approval(live_db):
    async with Client(mcp_server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"list_alerts", "detect_rings", "linked_accounts", "trace_funds", "key_people", "barrier_check",
                "open_case", "add_case_evidence", "get_case", "list_cases", "update_case", "propose_action",
                "handoff", "claim_task", "list_tasks", "complete_task"} <= names
        assert not {n for n in names if "approve" in n or "decide" in n}
        res = await client.call_tool("detect_rings", {}, meta={"fb_auth": sign_auth(make_auth("halcyon", "engineer"))})
        assert res.is_error and "Permission denied" in "".join(getattr(c, "text", "") for c in res.content)
