"""Unit tests for the ingestion pipeline's pure parts (no FalkorDB or network needed)."""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from backend.graph import queries as Q
from backend.ingest import pipeline
from backend.ingest.pipeline import (Batch, clean_props, decay, normalize_topic, participation, supersession_plan,
                                     team_id)
from backend.ingest.resolver import Resolver
from backend.ingest.sources import load_entity_tokens, load_world


def test_clean_props_is_falkordb_safe():
    p = clean_props({"a": None, "b": [], "c": {"x": 1}, "d": [1, 2], "e": [{"k": 1}], "text": "", "f": 0})
    assert p == {"c": '{"x": 1}', "d": [1, 2], "e": '[{"k": 1}]', "text": "", "f": 0}


def test_decay_half_life():
    assert decay("2026-10-15T00:00:00Z") == pytest.approx(1.0)
    assert decay("2026-08-31T00:00:00Z") == pytest.approx(0.5)  # 45 days before REF_DATE
    assert decay("2026-11-01") == pytest.approx(1.0)  # never above 1


def test_helpers():
    assert team_id("ops") == "team_ops" and team_id("team_ops") == "team_ops"
    assert normalize_topic("  Wire   Fraud Alert. ") == "wire fraud alert"
    assert normalize_topic("ab") == ""


def test_batch_rejects_unregistered_edges_and_skips_missing_endpoints():
    b = Batch()
    b.edge("Person", "AUTHORED", "Document", None, "doc-1")
    assert not b.edges
    with pytest.raises(KeyError):
        b.edge("Person", "LIKES", "Document", "e_1", "doc-1")
    assert all(spec in Q.MERGE_EDGES for spec in Q.EDGE_SPECS)


def test_participation_separates_asking_from_answering():
    meta = {"q1": ("e_analyst", True), "s1": ("e_analyst", False)}
    question = {"doc_id": "q1", "kind": "slack", "author_id": "e_analyst",
                "text": "@Priya did the recon job start late?"}
    assert participation(question, {"e_expert", "e_analyst"}, meta) == {"e_analyst": "asked", "e_expert": "asked_of"}
    answer = {"doc_id": "a1", "kind": "slack", "author_id": "e_expert", "reply_to": "q1", "text": "Yes, GL was late."}
    assert participation(answer, set(), meta) == {"e_expert": "answered"}
    # replying to a statement, or to your own question, is ordinary authorship
    assert participation({**answer, "reply_to": "s1"}, set(), meta) == {"e_expert": "author"}
    assert participation({**answer, "author_id": "e_analyst"}, set(), meta) == {"e_analyst": "author"}
    runbook = {"doc_id": "d1", "kind": "doc", "doc_type": "runbook", "author_id": "e_expert", "text": "Steps?"}
    ticket = {"doc_id": "t1", "kind": "ticket", "author_id": "e_analyst", "ticket": {"assignee_id": "e_expert"}}
    assert participation(runbook, set(), meta) == {"e_expert": "documented"}
    assert participation(ticket, set(), meta) == {"e_analyst": "author", "e_expert": "assignee"}


def test_supersession_closes_on_changed_value_only():
    groups = {("sys_kyc", "owned_by"): [
        ("2026-07-01", "f1", "team_ops", None),
        ("2026-08-01", "f2", "team_ops", None),         # restatement: does not close f1
        ("2026-10-06", "f3", "team_compliance", None),  # change: closes f1 and f2
        ("2026-10-10", "f4", "team_compliance", None),
    ]}
    updates, sup = supersession_plan(groups)
    assert {u["id"]: u["valid_to"] for u in updates} == {"f1": "2026-10-06", "f2": "2026-10-06"}
    assert sorted(sup) == [("f3", "f1"), ("f3", "f2")]
    # idempotent once stored
    stored = {k: [(vf, fid, ok, {"f1": "2026-10-06", "f2": "2026-10-06"}.get(fid)) for vf, fid, ok, _ in v]
              for k, v in groups.items()}
    assert supersession_plan(stored)[0] == []


@pytest.fixture(scope="module")
def resolver() -> Resolver:
    return Resolver(load_world("halcyon"), load_entity_tokens("halcyon"))


def test_resolver_systems_people_deals_tokens(resolver: Resolver):
    w = load_world("halcyon")
    assert resolver.resolve("ReconLegacy", "system") == ("System", "sys_reconlegacy")
    assert resolver.resolve("the Pricing Service", "system") == ("System", "sys_pricingsvc")
    assert resolver.resolve("Falcon", "deal") == ("Deal", "deal_falcon")
    p = w["people"][5]
    assert resolver.resolve(p["name"], "person") == ("Person", p["id"])
    assert resolver.resolve("Operations team", "team") == ("Team", "team_ops")
    toks = load_entity_tokens("halcyon")[("client", "c_0060")]
    assert resolver.resolve(f"SSN on file {toks['SSN']}") == ("Client", "c_0060")
    # the ring's shared phone belongs to several clients, so it must not resolve to any single one
    assert len(resolver.token_owners[toks["PHONE"]]) > 1
    assert resolver.by_token(f"the phone {toks['PHONE']}") is None
    assert resolver.resolve("completely unrelated phrase", "system") is None


def test_topic_canonicalisation_by_embedding(monkeypatch):
    vecs = {"wire fraud alert": [1.0, 0.0, 0.0], "wire fraud alerts": [0.99, 0.05, 0.0],
            "kyc pipeline ownership": [0.0, 1.0, 0.0]}

    async def fake_embed(texts, kind="document", **_):
        return [vecs[t] for t in texts]

    monkeypatch.setattr(pipeline, "embed_cached", fake_embed)
    idx = pipeline.TopicIndex()
    idx.dims = 3
    idx._mat = idx._mat[:, :3].copy()
    out = asyncio.run(idx.canonicalize(Counter({"wire fraud alert": 3, "wire fraud alerts": 1,
                                                "kyc pipeline ownership": 2})))
    assert out["wire fraud alerts"][0] == out["wire fraud alert"][0] == "topic_wire_fraud_alert"
    assert out["wire fraud alert"][2] is True and out["wire fraud alerts"][2] is False
    assert out["kyc pipeline ownership"][0] == "topic_kyc_pipeline_ownership"
