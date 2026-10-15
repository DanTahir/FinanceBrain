"""HTTP API: visitor-id cookie (per-visitor memory graphs), the /api/chat SSE stream (session runner stubbed, no LLM),
the Memory Inspector endpoints and input validation. The ASGI app is driven in-process through httpx."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets

import httpx
import pytest
import redis

from backend.api import app as app_module
from backend.config import get_settings
from backend.graph import db
from backend.llm import embeddings
from backend.memory.store import MemoryStore
from backend.tenancy import make_auth

DIMS = 1024


def fake_vec(text: str) -> list[float]:
    v = [0.0] * DIMS
    v[0] = 0.05
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[1 + int(hashlib.md5(w.encode()).hexdigest(), 16) % (DIMS - 1)] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


@pytest.fixture
def live_db(monkeypatch):
    s = get_settings()
    try:
        redis.Redis(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password, socket_timeout=2).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("FalkorDB not reachable")

    async def embed(texts, kind="document", **_kw):
        return [fake_vec(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed", embed)
    monkeypatch.setattr(s, "rate_limit_enabled", False)  # the rate-limit test turns it back on with its own IP
    db._db = None
    yield
    db._db = None


def client(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://test", **kw)


def sse_events(text: str) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event and event != "ping":
            out.append((event, json.loads(line[5:].strip())))
    return out


async def test_visitor_cookie_is_minted_once_and_names_the_memory_graph(live_db):
    async with client() as c:
        r = await c.get("/api/whoami", params={"tenant": "halcyon", "persona": "aml_analyst"})
        assert r.status_code == 200
        set_cookie = r.headers["set-cookie"]
        assert set_cookie.startswith("fb_vid=") and "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie
        assert "Secure" not in set_cookie
        vid = r.json()["visitor"]
        assert re.fullmatch(r"[0-9a-f]{12}", vid)
        assert r.json()["memory_graph"] == f"mem__halcyon__aml_analyst__{vid}"
        assert r.json()["scopes"] == ["read", "act"]

        again = await c.get("/api/whoami", params={"tenant": "halcyon", "persona": "aml_analyst"})
        assert again.json()["visitor"] == vid and "set-cookie" not in again.headers  # cookie reused, not re-minted

    async with client() as other:
        o = await other.get("/api/whoami", params={"tenant": "halcyon", "persona": "aml_analyst"})
        assert o.json()["visitor"] != vid  # a second browser gets its own visitor id (and memory graph)


async def test_visitor_cookie_is_secure_behind_cloudfront_and_rejects_forged_values(live_db):
    async with client() as c:
        r = await c.get("/api/tenants", headers={"cloudfront-forwarded-proto": "https"})
        assert "Secure" in r.headers["set-cookie"]
        forged = await c.get("/api/whoami", params={"tenant": "halcyon", "persona": "engineer"},
                             headers={"cookie": "fb_vid=../../evil"})
        vid = forged.json()["visitor"]
        assert vid != "../../evil" and re.fullmatch(r"[0-9a-f]{12}", vid)
    # non-API paths are left alone
    async with client() as c2:
        r = await c2.get("/not-api")
        assert "set-cookie" not in r.headers


async def test_chat_streams_session_events_bound_to_the_visitor(live_db, monkeypatch):
    calls = []

    async def fake_run_session(auth, message, **kw):
        calls.append((auth, message, kw))
        yield {"type": "session", "session_id": "sess_0123456789", "new": True}
        yield {"type": "route", "agent": "investigator", "by": "heuristic"}
        yield {"type": "done", "answer": "ok"}

    monkeypatch.setattr(app_module, "run_session", fake_run_session)
    async with client() as c:
        vid = (await c.get("/api/whoami", params={"tenant": "halcyon", "persona": "aml_analyst"})).json()["visitor"]
        r = await c.post("/api/chat", json={"tenant": "halcyon", "persona": "aml_analyst",
                                            "message": "Investigate the flagged 48k wire",
                                            "history": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert [e for e, _ in sse_events(r.text)] == ["session", "route", "done"]
        auth, msg, kw = calls[0]
        assert (auth.tenant, auth.persona, auth.user) == ("halcyon", "aml_analyst", vid)
        assert msg == "Investigate the flagged 48k wire"
        assert kw["agent"] == "auto" and kw["memory"] is True and kw["history"] == [{"role": "user", "content": "hi"}]

        bad = [({"agent": "hacker"}, 400), ({"session_id": "sess_../../x"}, 400), ({"persona": "ceo"}, 400),
               ({"message": ""}, 422)]
        for patch, code in bad:
            body = {"tenant": "halcyon", "persona": "aml_analyst", "message": "hello", **patch}
            assert (await c.post("/api/chat", json=body)).status_code == code, patch
    assert len(calls) == 1


async def test_memory_inspector_endpoints_show_and_forget_this_visitors_memories(live_db):
    params = {"tenant": "halcyon", "persona": "aml_analyst"}
    async with client() as c, client() as stranger:
        vid = (await c.get("/api/whoami", params=params)).json()["visitor"]
        empty = (await c.get("/api/memory", params=params)).json()
        assert empty["exists"] is False and empty["facts"] == []
        assert (await c.get("/api/memory/sessions/sess_0123456789", params=params)).status_code == 404

        st = MemoryStore(make_auth("halcyon", "aml_analyst", user=vid))
        try:
            await st.add_facts([{"text": "Prefers SAR drafts as bullet points", "kind": "preference"},
                                {"text": "Working on the Bitrail exit counterparty", "kind": "open_thread"}],
                               by="test")
            ov = (await c.get("/api/memory", params=params)).json()
            assert ov["exists"] and ov["graph"] == st.name
            texts = {f["text"]: f["id"] for f in ov["facts"]}
            assert "Prefers SAR drafts as bullet points" in texts and ov["trace"]

            # another visitor sees none of it
            assert (await stranger.get("/api/memory", params=params)).json()["facts"] == []

            fid = texts["Prefers SAR drafts as bullet points"]
            assert (await c.delete(f"/api/memory/facts/{fid}", params=params)).json() == {"forgotten": fid}
            assert (await c.delete(f"/api/memory/facts/{fid}", params=params)).status_code == 404
            assert (await c.delete("/api/memory/facts/not-an-id", params=params)).status_code == 400
            left = {f["text"] for f in (await c.get("/api/memory", params=params)).json()["facts"]}
            assert left == {"Working on the Bitrail exit counterparty"}

            gone = (await c.delete("/api/memory", params=params)).json()
            assert gone == {"deleted_graph": st.name} and not await db.graph_exists(st.name)
        finally:
            await st.forget_all()


async def test_live_status_and_tenant_validation(live_db):
    async with client() as c:
        st = (await c.get("/api/live", params={"tenant": "halcyon"})).json()
        assert st["cutoff"] == "2026-10-01" and st["graph"] == "brain__halcyon" and st["days"]
        assert (await c.get("/api/live", params={"tenant": "nope"})).status_code == 400
        tenants = (await c.get("/api/tenants")).json()
        assert {t["id"] for t in tenants} == {"halcyon", "brightwater"}
        guided = (await c.get("/api/guided", params={"tenant": "halcyon"})).json()["questions"]
        assert {g["track"] for g in guided} == {"T1", "T2", "T3"}
        assert (await c.get("/api/guided", params={"tenant": "nope"})).status_code == 400


def viewer() -> str:
    return f"[2001:db8::{secrets.token_hex(2)}:{secrets.token_hex(2)}]:443"  # fresh rate-limit identity per run


async def test_rate_limit_is_per_viewer_address(live_db, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "rate_chat", 2)

    async def fake_run_session(auth, message, **kw):
        yield {"type": "final", "text": "ok"}
        yield {"type": "done"}

    monkeypatch.setattr(app_module, "run_session", fake_run_session)
    body = {"tenant": "halcyon", "persona": "aml_analyst", "message": "hello there"}
    me = {"cloudfront-viewer-address": viewer()}
    async with client() as c:
        codes = [(await c.post("/api/chat", json=body, headers=me)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        limited = await c.post("/api/chat", json=body, headers=me)
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
        assert "Rate limit" in limited.json()["detail"]
        other = await c.post("/api/chat", json=body, headers={"cloudfront-viewer-address": viewer()})
        assert other.status_code == 200


async def test_guided_answers_are_cached_per_persona_and_served_over_budget(live_db, monkeypatch):
    from backend.api import guardrails
    from backend.ingest import live

    calls = []

    async def fake_run_session(auth, message, **kw):
        calls.append((auth.persona, message))
        yield {"type": "session", "session_id": "sess_0123456789", "resumed": False}
        yield {"type": "start", "agent": "brain"}
        yield {"type": "tool_call", "id": "c1", "name": "blast_radius", "args": {"system": "PricingSvc"}}
        yield {"type": "final", "agent": "brain", "text": "Trade pricing and risk reports break.", "citations": []}
        yield {"type": "memory", "facts_added": 0}
        yield {"type": "done", "session_id": "sess_0123456789"}

    monkeypatch.setattr(app_module, "run_session", fake_run_session)
    q = "What breaks if PricingSvc goes down?"
    cursor = await live.cursor("halcyon", None)
    keys = [guardrails.guided_key("halcyon", p, q, cursor) for p in ("engineer", "aml_analyst")]
    await db.execute("DEL", *keys)
    body = {"tenant": "halcyon", "persona": "engineer", "message": q}
    try:
        async with client() as c:
            first = sse_events((await c.post("/api/chat", json=body)).text)
            assert [e for e, _ in first] == ["session", "start", "tool_call", "final", "memory", "done"]
            again = await c.post("/api/chat", json={**body, "message": "  what breaks if pricingsvc goes down "})
            second = sse_events(again.text)
            assert [e for e, _ in second] == ["session", "start", "tool_call", "final", "done"]
            assert second[0][1]["cached"] is True and len(calls) == 1

            await c.post("/api/chat", json={**body, "persona": "aml_analyst"})  # other ACLs: its own live run
            assert len(calls) == 2
            await c.post("/api/chat", json={**body, "history": [{"role": "user", "content": "hi"}]})  # follow-up
            assert len(calls) == 3

            monkeypatch.setattr(get_settings(), "daily_budget_usd", 0.0)
            refused = await c.post("/api/chat", json={**body, "message": "Tell me a story"})
            assert refused.status_code == 429 and "budget" in refused.json()["detail"]
            still = sse_events((await c.post("/api/chat", json=body)).text)
            assert still[0][1]["cached"] is True and len(calls) == 3
            assert (await c.get("/api/budget")).json()["over"] is True
    finally:
        await db.execute("DEL", *keys)


async def test_graph_explorer_documents_and_redaction_view_respect_acl(live_db):
    eng = {"tenant": "halcyon", "persona": "engineer"}
    cco = {"tenant": "halcyon", "persona": "compliance_officer"}
    async with client() as c:
        found = (await c.get("/api/graph/search", params={**eng, "q": "ReconLegacy"})).json()["results"]
        system = next(r for r in found if r["label"] == "System")
        assert system["name"] == "ReconLegacy"
        eng_falcon = (await c.get("/api/graph/search", params={**eng, "q": "Falcon"})).json()["results"]
        cco_falcon = (await c.get("/api/graph/search", params={**cco, "q": "Falcon"})).json()["results"]
        assert not any(r["label"] == "Deal" for r in eng_falcon)  # behind the information barrier
        assert any(r["label"] == "Deal" for r in cco_falcon)

        nb = (await c.get("/api/graph/neighborhood", params={**eng, "entity": system["id"]})).json()
        ids = {n["id"] for n in nb["graph"]["nodes"]}
        assert system["id"] in ids and len(ids) > 3 and nb["graph"]["edges"]
        hidden = await c.get("/api/graph/neighborhood", params={**eng, "entity": "Project Falcon"})
        assert hidden.status_code == 404

        red = (await c.get("/api/redaction", params=eng)).json()
        assert red["documents_redacted"] > 100 and red["by_type"] and red["tokens"]
        assert red["eval"]["summary"]["recall_any"] == 1.0 and "blocked_total" in red["outbound_guard"]
        doc_id = red["recent"][0]["id"]
        doc = (await c.get(f"/api/docs/{doc_id}", params=eng)).json()
        assert doc["label"] == "Document" and doc["redaction"]["total"] >= 1 and "\u27e8" in doc["text"]
        assert (await c.get("/api/docs/email-99999", params=eng)).status_code == 404
        assert (await c.get("/api/docs/not_a_doc", params=eng)).status_code == 400


async def test_redaction_preview_masks_without_storing_and_canary_is_clean(live_db):
    async with client() as c:
        text = "Please charge card 4111 1111 1111 1111 for the onboarding fee."
        r = (await c.post("/api/redaction/preview", json={"tenant": "halcyon", "text": text})).json()
        assert "4111 1111 1111 1111" not in r["redacted"] and r["counts"].get("CARD_NUMBER") == 1
        assert r["spans"][0]["type"] == "CARD_NUMBER" and text[r["spans"][0]["start"]:].startswith("4111")
        can = (await c.post("/api/redaction/canary", json={"tenant": "brightwater"})).json()
        assert can["clean"] is True and can["hits"] == 0 and can["strings_scanned"] > 10000
        last = (await c.get("/api/redaction", params={"tenant": "brightwater", "persona": "engineer"})).json()
        assert last["canary"]["ts"] == can["ts"]
