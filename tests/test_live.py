"""Live feed + Sentinel: replay the post-cutoff timeline into a throwaway GRAPH.COPY of pristine__halcyon and check
that the graph stays current (transactions, ownership change, departure) and that the Sentinel opens exactly the
cases the ground truth calls for.

Offline: extraction comes from the committed cache, the Sentinel uses template write-ups, and any LLM or embedding
network call fails the test (embeddings for live documents are served from the committed embedding cache).
"""

from __future__ import annotations

import uuid

import pytest
import redis

from backend.agents import sentinel
from backend.config import get_settings
from backend.graph import db
from backend.ingest import live
from backend.llm import client as llm_client
from backend.llm import embeddings

TENANT = "halcyon"


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
def no_network(monkeypatch):
    async def boom(*_a, **_kw):
        raise AssertionError("network LLM/embedding call in an offline live-feed test")

    monkeypatch.setattr(embeddings, "embed", boom)
    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "chat_json", boom)
    monkeypatch.setattr(sentinel, "chat_json", boom)


@pytest.fixture
async def live_graph(live_db, no_network):
    name = f"test__live_{uuid.uuid4().hex[:8]}"
    await db.copy_graph(db.pristine_graph(TENANT), name)
    yield name
    live.drop_ingestors(TENANT, name)
    await db.delete_graph(name)


async def rows(graph: str, cypher: str, params: dict | None = None) -> list[list]:
    return (await db.get_db().select_graph(graph).ro_query(cypher, params or {})).result_set


def test_timeline_carries_the_storyline_events():
    days = {d["day"]: d for d in live.timeline(TENANT)}
    assert min(days) >= "2026-10-01"
    assert "t_r025" in days["2026-10-03"]["txns"]            # wire into the fraud ring
    assert days["2026-10-09"]["leavers"] == ["e_073"]         # ReconLegacy expert leaves
    kyc = [(o["event"], o.get("team_id")) for o in days["2026-10-06"]["ownership"] if o["system_id"] == "sys_kycpipeline"]
    assert sorted(kyc) == [("end", "team_ops"), ("start", "team_compliance")]
    assert "slack-01399" in days["2026-10-07"]["docs"]        # Falcon leak


async def test_live_feed_replay_keeps_the_graph_current_and_the_sentinel_acts(live_graph):
    g = live_graph
    st = await live.status(TENANT, g)
    assert st["cursor"] is None and st["next_day"] == "2026-10-01"
    assert not await rows(g, "MATCH (t:Transaction {id: 't_r025'}) RETURN t.id")

    out = await live.advance(TENANT, days=8, graph=g, offline=True, use_llm=False)
    replayed = out["replayed"]
    assert [r["day"] for r in replayed][-1] == "2026-10-09"
    assert out["status"]["cursor"] == "2026-10-09"
    assert sum(r["docs"] for r in replayed) == 139

    # the graph is current: new transactions, ownership interval closed + opened, departure recorded
    assert await rows(g, "MATCH (:Transaction {id: 't_r025'})-[:TO_ACCOUNT]->(a:Account) RETURN a.id") == [["a_00238"]]
    owns = await rows(g, """MATCH (t:Team)-[o:OWNS]->(:System {id: 'sys_kycpipeline'})
                            RETURN t.id, o.valid_from, o.valid_to, o.current ORDER BY o.valid_from""")
    assert [(r[0], r[3]) for r in owns][-2:] == [("team_ops", False), ("team_compliance", True)]
    assert owns[-2][2] == "2026-10-06" and owns[-1][1] == "2026-10-06"
    assert await rows(g, "MATCH (p:Person {id: 'e_073'}) RETURN p.active, p.end_date") == [[False, "2026-10-09"]]
    cur = await rows(g, "MATCH (m:Meta) RETURN m.live_cursor, m.live_batches")
    assert cur == [["2026-10-09", 8]]

    # Sentinel: the ring wire becomes a fraud_ring case handed to the Investigator
    by_day = {r["day"]: r["sentinel"] for r in replayed}
    ring = [c for c in by_day["2026-10-03"]["cases"] if c["subject"] == "t_r025"]
    assert len(ring) == 1 and ring[0]["kind"] == "fraud_ring" and ring[0]["severity"] in ("high", "critical")
    assert ring[0]["handoff"]["to"] == "investigator" and ring[0]["handoff"]["status"] == "open"
    ring_rows = await rows(g, "MATCH (:Case {id: $c})-[:EVIDENCE]->(x) RETURN x.id, labels(x)[0]",
                           {"c": ring[0]["case_id"]})
    ring_ev = {r[0] for r in ring_rows}
    assert {"t_r025", "a_00238"} <= ring_ev
    labels = {r[1] for r in ring_rows}
    assert {"Client", "PiiToken"} <= labels  # linked ring clients and the shared identifiers came along
    assert len([r for r in ring_rows if r[1] == "Client"]) >= 3

    # the Falcon leak is the only MNPI breach case; it carries the leaked document and goes to Compliance
    mnpi = await rows(g, "MATCH (c:Case {kind: 'mnpi_breach'}) RETURN c.id")
    assert len(mnpi) == 1
    ev = {r[0] for r in await rows(g, "MATCH (:Case {id: $c})-[:EVIDENCE]->(x) RETURN x.id", {"c": mnpi[0][0]})}
    assert {"deal_falcon", "slack-01399"} <= ev and not ev & {"deal_osprey", "deal_kestrel"}
    falcon = [c for c in by_day["2026-10-07"]["cases"] if c["kind"] == "mnpi_breach"][0]
    assert falcon["handoff"]["to"] == "compliance"
    tasks = await rows(g, "MATCH (t:Task {case_id: $c}) RETURN t.to_agent, t.status", {"c": mnpi[0][0]})
    assert tasks == [["agent_compliance", "open"]]

    # deal-logistics documents that only name Osprey/Kestrel are lower-confidence watch alerts, not cases
    watch = [a for s in by_day.values() for a in s["alerts"] if a["kind"] == "barrier_watch"]
    assert {a["deal"] for a in watch} <= {"deal_osprey", "deal_kestrel"} and watch

    # bus factor: the ReconLegacy expert leaving raises a key-person alert
    kp = [a for a in by_day["2026-10-09"]["alerts"] if a["kind"] == "key_person_risk"]
    assert kp and kp[0]["person"] == "e_073" and kp[0]["severity"] == "high"
    assert "sys_reconlegacy" in {s["id"] for s in kp[0]["systems"]}

    # ownership change is reported as an event
    assert any(e.get("kind") == "ownership_change" and e.get("system") == "sys_kycpipeline"
               and e.get("to_team") == "team_compliance" for e in by_day["2026-10-06"]["events"])


async def test_replaying_a_day_again_reuses_cases_instead_of_duplicating(live_graph):
    g = live_graph
    first = await live.replay_day(TENANT, "2026-10-03", graph=g, offline=True, use_llm=False)
    again = await live.replay_day(TENANT, "2026-10-03", graph=g, offline=True, use_llm=False)
    c1 = {c["subject"]: c for c in first["sentinel"]["cases"]}
    c2 = {c["subject"]: c for c in again["sentinel"]["cases"]}
    assert c1["t_r025"]["case_id"] == c2["t_r025"]["case_id"] and c2["t_r025"]["reused_existing_case"]
    assert c2["t_r025"]["handoff"]["status"] == "already queued"
    assert await rows(g, "MATCH (t:Transaction {id: 't_r025'}) RETURN count(t)") == [[1]]
    assert await rows(g, "MATCH (c:Case {kind: 'fraud_ring'}) RETURN count(c)") == [[1]]
