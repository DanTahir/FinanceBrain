"""Agent memory: per-user memory graphs (episodic + semantic), memory tools over MCP, shared playbooks
(procedural memory), the Librarian and a full chat session with memory.

Embeddings are replaced by a deterministic hashed bag-of-words vector so the tests are free and repeatable; the
LLM calls (Librarian consolidation, agent flow) are stubbed. Every test uses its own memory user and deletes the
memory graph afterwards; playbook tests write to a throwaway GRAPH.COPY of brain__halcyon.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid

import pytest
import redis
from mcp.client.client import Client

from backend.agents import concierge, librarian, session
from backend.config import get_settings
from backend.graph import db
from backend.llm import embeddings
from backend.mcp_server.server import server as mcp_server
from backend.memory.store import MemoryStore, entity_refs, new_session_id
from backend.tenancy import make_auth, sign_auth
from backend.tools import memory as memtools
from backend.tools.base import ToolContext, ToolError

DIMS = 1024
TEST_GRAPH = "test__halcyon_mem"


def fake_vec(text: str) -> list[float]:
    v = [0.0] * DIMS
    v[0] = 0.05  # never a zero vector
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[1 + h % (DIMS - 1)] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


@pytest.fixture
def live_db():
    s = get_settings()
    try:
        redis.Redis(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password, socket_timeout=2).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("FalkorDB not reachable")
    db._db = None
    yield
    db._db = None


@pytest.fixture
def fake_embed(monkeypatch):
    async def embed(texts, kind="document", **_kw):
        return [fake_vec(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed", embed)


@pytest.fixture
async def users(live_db, fake_embed):
    """Factory for throwaway memory users; their memory graphs are deleted afterwards."""
    made: list[MemoryStore] = []

    def make(persona: str = "aml_analyst", tenant: str = "halcyon"):
        auth = make_auth(tenant, persona, user=f"t{uuid.uuid4().hex[:8]}")
        made.append(MemoryStore(auth))
        return auth

    yield make
    for st in made:
        await st.forget_all()


@pytest.fixture
async def pb_graph(live_db, fake_embed):
    await db.copy_graph(db.brain_graph("halcyon"), TEST_GRAPH)
    yield TEST_GRAPH
    await db.delete_graph(TEST_GRAPH)


# ------------------------------------------------------------------------------------------------ episodic
async def test_turns_history_and_evidence_refs(users):
    st = MemoryStore(users())
    await st.ensure()
    assert st.name.startswith("mem__halcyon__aml_analyst__t")
    sid = new_session_id()
    await st.start_session(sid, "brain")
    await st.add_turn(sid, 1, "user", "Who owns the KYC pipeline now?")
    await st.add_turn(sid, 2, "assistant", "Team Compliance owns it since 2026-10-06 [email-00012].", agent="brain",
                      tools=["who_owns"], refs=entity_refs(["sys_kycpipeline", "team_compliance", "not an id"]))
    turns = await st.turns(sid)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert set(turns[1]["refs"]) == {"sys_kycpipeline", "team_compliance"}
    assert turns[1]["tools"] == ["who_owns"]
    assert await st.history(sid) == [{"role": "user", "content": "Who owns the KYC pipeline now?"},
                                     {"role": "assistant", "content": turns[1]["text"]}]
    chain = await st.q("t", "MATCH (:Turn {n: 1})-[:NEXT]->(b:Turn) RETURN b.n")
    assert chain == [[2]]
    info = await st.session_info(sid)
    assert info["turns"] == 2 and info["agent"] == "brain"
    assert [s["id"] for s in await st.sessions()] == [sid]
    assert await st.forget_session(sid)
    assert await st.turns(sid) == [] and await st.session_info(sid) is None


# ------------------------------------------------------------------------------------------------ semantic
async def test_facts_recall_dedupe_supersede_and_forget(users):
    st = MemoryStore(users())
    await st.ensure()
    [a] = await st.add_facts([{"text": "Prefers SAR drafts as bullet points", "kind": "preference"}])
    [dup] = await st.add_facts([{"text": "Prefers SAR drafts as bullet points.", "kind": "preference"}])
    assert dup["duplicate_of"] == a["id"]
    rec = await st.recall("SAR drafts bullet points")
    assert rec["facts"] and rec["facts"][0]["id"] == a["id"]
    assert (await st.recall("zebra quantum volcano"))["facts"] == []

    [b] = await st.add_facts([{"text": "Prefers SAR drafts as numbered paragraphs", "kind": "preference",
                               "replaces": a["id"]}])
    assert b["replaced"] == a["id"]
    assert [f["id"] for f in await st.facts()] == [b["id"]]
    old = {f["id"]: f for f in await st.facts(include_retired=True)}[a["id"]]
    assert old["valid_to"] and old["superseded_by"] == b["id"]

    block, info = await st.context_block("draft the SAR")
    assert block and "numbered paragraphs" in block and "bullet points" not in block
    assert [p["id"] for p in info["preferences"]] == [b["id"]]

    assert await st.forget_fact(b["id"])
    assert await st.facts() == []


async def test_memories_are_pii_scrubbed(users):
    st = MemoryStore(users())
    await st.ensure()
    [f] = await st.add_facts([{"text": "Client SSN is 123-45-6789 and card 4111 1111 1111 1111", "kind": "fact"}])
    assert "6789" not in f["text"] and "4111" not in f["text"]
    assert "⟨REDACTED_" in f["text"]


async def test_memory_graphs_are_isolated_per_user_and_tenant(users):
    a, b = users(), users()
    other_tenant = make_auth("brightwater", "aml_analyst", user=a.user)
    sa, sb, sc = MemoryStore(a), MemoryStore(b), MemoryStore(other_tenant)
    assert len({sa.name, sb.name, sc.name}) == 3
    assert sc.name.startswith("mem__brightwater__")
    await sa.ensure()
    await sa.add_facts([{"text": "Working on the Bitrail exit counterparty", "kind": "goal"}])
    c_b = ToolContext(b, agent="brain")
    assert (await memtools.recall(c_b, "Bitrail exit counterparty"))["facts"] == []
    c_a = ToolContext(a, agent="brain")
    assert (await memtools.recall(c_a, "Bitrail exit counterparty"))["facts"]
    assert not await sc.exists()


# ------------------------------------------------------------------------------------------------ tools over MCP
def _result(res) -> dict:
    data = res.structured_content if isinstance(res.structured_content, dict) else json.loads(
        "".join(getattr(c, "text", "") or "" for c in res.content))
    return data["result"]


async def test_memory_tools_over_mcp(users):
    auth = users()
    tok = sign_auth(auth)
    sid = new_session_id()
    meta = {"fb_auth": tok, "fb_agent": "brain", "fb_session": sid}
    async with Client(mcp_server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert {"remember", "recall", "get_session_history", "save_playbook", "find_playbook"} <= names
        r = await c.call_tool("remember", {"text": "Wants weekly summaries of open AML cases", "kind": "goal",
                                           "about": ["case_x1", "bogus id"]}, meta=meta)
        assert not r.is_error
        saved = _result(r)["saved"]
        assert saved[0]["kind"] == "goal" and saved[0]["about"] == ["case_x1", "bogus id"]
        bad = await c.call_tool("remember", {"text": "x", "kind": "secret"}, meta=meta)
        assert bad.is_error
        rec = await c.call_tool("recall", {"query": "weekly summaries open AML cases"}, meta=meta)
        assert _result(rec)["facts"][0]["text"].startswith("Wants weekly")
        hist = await c.call_tool("get_session_history", {}, meta={"fb_auth": tok})
        assert [s["id"] for s in _result(hist)["sessions"]] == [sid]
        # the session the caller is in is excluded from "earlier sessions"
        hist2 = await c.call_tool("get_session_history", {}, meta=meta)
        assert _result(hist2)["sessions"] == []
    st = MemoryStore(auth)
    about = await st.q("t", "MATCH (:MemoryFact)-[:ABOUT]->(r:EntityRef) RETURN r.id")
    assert about == [["case_x1"]]


# ------------------------------------------------------------------------------------------------ playbooks
STEPS = [{"tool": "list_alerts", "instruction": "Find the flagged inbound wire and its receiving account."},
         {"tool": "linked_accounts", "instruction": "Find clients sharing identifier tokens with the account holder."},
         {"tool": "detect_rings", "instruction": "Confirm the shared-identifier component and its risk score."},
         {"tool": "trace_funds", "instruction": "Follow the money from the receiving account to the exit."},
         {"tool": "open_case", "instruction": "Open a fraud_ring case with the ring accounts as evidence."}]


async def test_playbook_save_reinforce_find_and_permissions(pb_graph, monkeypatch):
    # bag-of-words test vectors put a short query far from a long playbook text; voyage-4 is calibrated separately
    monkeypatch.setattr(memtools, "PLAYBOOK_MATCH", 0.8)
    analyst = ToolContext(make_auth("halcyon", "aml_analyst"), agent="investigator", graph=pb_graph)
    out = await memtools.save_playbook(analyst, "Investigate a flagged inbound wire for ring activity",
                                       "A large inbound wire is flagged for fraud", STEPS,
                                       "Expand shared identifiers, confirm the ring, trace funds, open a case.")
    assert out["action"] == "created" and len(out["playbook"]["steps"]) == 5
    pid = out["playbook"]["id"]
    again = await memtools.save_playbook(analyst, "Investigate a flagged inbound wire for ring activity",
                                         "A large inbound wire is flagged for fraud", STEPS,
                                         "Expand shared identifiers, confirm the ring, trace funds, open a case.")
    assert again["action"] == "reinforced" and again["playbook"]["id"] == pid and again["playbook"]["uses"] == 1
    chain = await analyst.q("t", "MATCH (:Playbook {id: $id})-[:HAS_STEP]->(a:Step)-[:NEXT_STEP]->(b:Step) "
                                 "RETURN a.n, b.n ORDER BY a.n", {"id": pid})
    assert chain == [[1, 2], [2, 3], [3, 4], [4, 5]]

    engineer = ToolContext(make_auth("halcyon", "engineer"), agent="brain", graph=pb_graph)
    found = await memtools.find_playbook(engineer, "investigate flagged inbound wire ring activity fraud")
    assert found["best_match"] == pid
    assert [s["tool"] for s in found["playbooks"][0]["steps"]][:2] == ["list_alerts", "linked_accounts"]
    assert any(n["id"] == pid for n in engineer.evidence.to_dict()["nodes"])
    assert (await memtools.find_playbook(engineer, "zebra quantum volcano"))["best_match"] is None
    with pytest.raises(ToolError):
        await memtools.save_playbook(engineer, "x", "y", STEPS)  # read-only persona: no act scope
    with pytest.raises(ToolError):
        await memtools.save_playbook(analyst, "too short", "y", STEPS[:1])
    assert pid in {p["id"] for p in (await memtools.list_playbooks(engineer))["playbooks"]}


async def test_playbook_naming_a_restricted_deal_stays_behind_the_wall(pb_graph):
    cco = ToolContext(make_auth("halcyon", "compliance_officer"), agent="compliance", graph=pb_graph)
    out = await memtools.save_playbook(cco, "Review a Project Falcon leak", "MNPI about Falcon may have leaked",
                                       [{"tool": "barrier_check", "instruction": "Check Falcon documents for outsiders"},
                                        {"tool": "open_case", "instruction": "Open an mnpi_breach case"}])
    pid = out["playbook"]["id"]
    seen_by_cco = await memtools.find_playbook(cco, "review project falcon leak mnpi")
    assert pid in {p["id"] for p in seen_by_cco["playbooks"]}
    engineer = ToolContext(make_auth("halcyon", "engineer"), agent="brain", graph=pb_graph)
    assert pid not in {p["id"] for p in (await memtools.find_playbook(engineer, "review project falcon leak mnpi"))
                       ["playbooks"]}
    assert pid not in {p["id"] for p in (await memtools.list_playbooks(engineer))["playbooks"]}


# ------------------------------------------------------------------------------------------------ librarian
async def test_librarian_consolidates_session_into_memories_and_playbook(users, pb_graph, monkeypatch):
    auth = users()
    st = MemoryStore(auth)
    await st.ensure()
    sid = new_session_id()
    await st.start_session(sid, "investigator")
    await st.add_turn(sid, 1, "user", "Investigate the flagged 48k wire, and keep SAR drafts short please")
    await st.add_turn(sid, 2, "assistant", "Ring of 5 accounts; case opened, freezes proposed.", agent="investigator",
                      tools=["list_alerts", "linked_accounts", "detect_rings", "trace_funds", "open_case"],
                      refs=entity_refs(["a_00128", "cp_063"]))
    calls = []

    async def fake_chat_json(messages, schema, **kw):
        calls.append(messages[-1]["content"])
        return librarian.Consolidation(
            title="Flagged wire ring investigation", summary="Investigated t_r016; ring a_00128 et al.; case opened.",
            facts=[librarian.LFact(kind="preference", text="Prefers short SAR drafts", about=[], replaces=""),
                   librarian.LFact(kind="open_thread", text="Ring case awaiting approval", about=["a_00128"],
                                   replaces="mf_doesnotexist")],
            retire=["mf_alsonotreal"],
            playbooks=[librarian.LPlaybook(name="Investigate a flagged wire for ring activity",
                                           trigger="A flagged inbound wire", description="Expand, confirm, trace.",
                                           steps=[librarian.LStep(**s) for s in STEPS])])

    monkeypatch.setattr(librarian, "chat_json", fake_chat_json)
    out = await librarian.consolidate(auth, sid, store=st, graph=pb_graph)
    assert out["playbook_eligible"] is True
    assert sorted(f["kind"] for f in out["facts_added"]) == ["open_thread", "preference"]
    assert out["facts_superseded"] == [] and out["facts_retired"] == []  # unknown ids are ignored
    assert out["playbook"]["action"] == "created"
    info = await st.session_info(sid)
    assert info["title"] == "Flagged wire ring investigation" and info["consolidated_n"] == 2
    assert '"playbook_eligible": true' in calls[0]
    again = await librarian.consolidate(auth, sid, store=st, graph=pb_graph)
    assert again["skipped"] == "nothing new to consolidate" and len(calls) == 1


async def test_librarian_never_learns_playbooks_for_read_only_personas(users, monkeypatch):
    auth = users("engineer")
    st = MemoryStore(auth)
    await st.ensure()
    sid = new_session_id()
    await st.start_session(sid, "brain")
    await st.add_turn(sid, 1, "user", "q")
    await st.add_turn(sid, 2, "assistant", "a", tools=["list_alerts", "linked_accounts", "detect_rings", "trace_funds"])

    async def fake_chat_json(messages, schema, **kw):
        return librarian.Consolidation(title="t", summary="s", facts=[], retire=[], playbooks=[
            librarian.LPlaybook(name="n", trigger="t", description="d", steps=[librarian.LStep(**s) for s in STEPS])])

    monkeypatch.setattr(librarian, "chat_json", fake_chat_json)
    out = await librarian.consolidate(auth, sid, store=st)
    assert out["playbook_eligible"] is False and out["playbook"] is None


# ------------------------------------------------------------------------------------------------ routing + session
async def test_concierge_routing_policy():
    assert concierge.heuristic("Investigate the flagged 48k wire") == "investigator"
    assert concierge.heuristic("Who owns the KYC pipeline?") == "brain"
    r = await concierge.route_request(make_auth("halcyon", "engineer"), "Investigate the flagged wire")
    assert r["agent"] == "brain" and r["by"] == "policy"


async def test_session_records_turns_masks_pii_and_resumes(users, monkeypatch):
    auth = users()
    seen: list[dict] = []

    async def fake_flow(agent, auth_, message, history=None, max_handoffs=2, *, memory=None, session_id=None):
        seen.append({"agent": agent, "message": message, "history": history, "memory": memory, "sid": session_id})
        yield {"type": "start", "agent": agent}
        yield {"type": "final", "agent": agent, "text": f"answer {len(seen)} [email-00301]",
               "citations": [{"n": 1, "id": "email-00301", "display": "Falcon email"}],
               "evidence": {"nodes": [{"id": "deal_falcon", "label": "Deal", "name": "Falcon"},
                                      {"id": "email-00301", "label": "Document"}], "edges": []},
               "tool_calls": [{"name": "barrier_check"}], "ops": [{"kind": "case_opened", "case_id": "case_abc"}],
               "cost_usd": 0.01}

    async def fake_consolidate(auth_, sid, store=None, graph=None):
        await store.set_session(sid, title="Falcon check", summary="Checked Falcon; case_abc opened.")
        await store.add_facts([{"text": "Follows the Falcon leak case", "kind": "open_thread"}], sid=sid)
        return {"session_id": sid, "title": "Falcon check", "facts_added": []}

    monkeypatch.setattr(session, "run_flow", fake_flow)
    monkeypatch.setattr(session, "consolidate", fake_consolidate)

    evs = [e async for e in session.run_session(auth, "Has Falcon leaked? my SSN is 123-45-6789", agent="compliance")]
    types = [e["type"] for e in evs]
    assert types[:2] == ["session", "pii_masked"] and types[-2:] == ["memory", "done"]
    sid = evs[0]["session_id"]
    assert "6789" not in seen[0]["message"] and seen[0]["sid"] == sid and seen[0]["memory"] is None
    st = MemoryStore(auth)
    turns = await st.turns(sid)
    assert [t["role"] for t in turns] == ["user", "assistant"] and "6789" not in turns[0]["text"]
    assert {"email-00301", "deal_falcon", "case_abc"} <= set(turns[1]["refs"])

    # resuming: history comes from the graph, and memory context is injected into the next run
    evs2 = [e async for e in session.run_session(auth, "And who received it?", agent="compliance", session_id=sid)]
    assert evs2[0]["resumed"] is True
    assert [h["role"] for h in seen[1]["history"]] == ["user", "assistant"]
    assert seen[1]["memory"] and "Follows the Falcon leak case" in seen[1]["memory"]
    assert len(await st.turns(sid)) == 4

    # a new session sees the earlier one's summary
    evs3 = [e async for e in session.run_session(auth, "What was I working on?", agent="brain")]
    ctx_ev = next(e for e in evs3 if e["type"] == "memory_context")
    assert ctx_ev["sessions"][0]["id"] == sid and "Falcon check" in seen[2]["memory"]

    # stateless mode never touches the memory graph
    evs4 = [e async for e in session.run_session(make_auth("halcyon", "aml_analyst", user="nobody_stateless"), "hi",
                                                 agent="brain", memory=False)]
    assert evs4[0]["memory_graph"] is None
    assert not await MemoryStore(make_auth("halcyon", "aml_analyst", user="nobody_stateless")).exists()
