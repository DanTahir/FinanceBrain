"""Storyline invariants on a seeded brain graph (structure the headline demos rely on).

    python -m evals.graph_checks --tenant halcyon [--live]   # --live: graph has had the live feed replayed

Each check runs real Cypher against FalkorDB and compares with data/raw/<tenant>/ground_truth.json refs.
Results are written to evals/results/graph_checks_<tenant>.{json,md}.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from backend.config import REPO_ROOT
from backend.graph.db import brain_graph, get_db
from backend.llm.embeddings import embed_cached

RING_TYPES = ["PHONE", "ADDRESS", "DEVICE_ID"]


async def run(tenant: str, live: bool) -> list[dict]:
    gt = json.loads((REPO_ROOT / "data" / "raw" / tenant / "ground_truth.json").read_text(encoding="utf-8"))
    refs = gt["refs"]
    g = get_db().select_graph(brain_graph(tenant))
    out: list[dict] = []

    async def q(cypher: str, params: dict | None = None) -> list[list]:
        return (await g.ro_query(cypher, params or {})).result_set

    def check(name: str, ok: bool, detail) -> None:
        out.append({"check": name, "pass": bool(ok), "detail": detail})

    # 1. fraud ring: clients reachable from the first ring client through shared phone/address/device tokens
    if refs.get("ring_clients"):
        rows = await q("""
            MATCH p = (a:Client {id: $id})-[:HAS_IDENTIFIER*2..10]-(b:Client)
            WHERE all(n IN nodes(p) WHERE NOT n:PiiToken OR n.type IN $types)
            RETURN collect(DISTINCT b.id)""", {"id": refs["ring_clients"][0], "types": RING_TYPES})
        found = sorted(set(rows[0][0]) | {refs["ring_clients"][0]})
        check("fraud ring component via shared PII tokens", found == sorted(refs["ring_clients"]), found)
        rows = await q("""
            MATCH (t:Transaction {id: $tx})-[:TO_ACCOUNT]->(a:Account)<-[:HOLDS]-(c:Client)
            RETURN t.amount, t.fraud_score, a.id, c.id""", {"tx": refs["ring_wire_1"]})
        check("flagged wire lands in a ring account",
              bool(rows) and rows[0][2] in refs["ring_accounts"] and rows[0][3] in refs["ring_clients"], rows)

    # 2. blast radius of the pricing service
    if refs.get("pricing_system"):
        rows = await q("""
            MATCH p = (s:System)-[:DEPENDS_ON*1..6]->(:System {id: $id})
            RETURN s.id, min(length(p)) ORDER BY s.id""", {"id": refs["pricing_system"]})
        got = {r[0]: r[1] for r in rows}
        want = {x["system_id"]: x["hops"] for x in refs["pricing_blast_radius"]}
        check("PricingSvc blast radius (systems and hop counts)", got == want, got)

    # 3. KYC ownership: open interval at seed time (Operations), transfer arrives with the live feed
    if refs.get("kyc_system"):
        rows = await q("""
            MATCH (t:Team)-[o:OWNS]->(:System {id: $id})
            RETURN t.id, o.valid_from, o.valid_to ORDER BY o.valid_from""", {"id": refs["kyc_system"]})
        current = [r[0] for r in rows if r[2] is None]
        want = refs["kyc_owner_after"]["team"] if live else refs["kyc_owner_before"]["team"]
        check(f"KYC pipeline current owner ({'after' if live else 'before'} transfer)", current == [want], rows)

    # 4. bus factor: the recon expert is the top derived KNOWS_ABOUT for ReconLegacy
    if refs.get("recon_system"):
        rows = await q("""
            MATCH (p:Person)-[k:KNOWS_ABOUT]->(:System {id: $id})
            RETURN p.id, k.weight, k.evidence, k.answered, k.asked, k.documented
            ORDER BY k.weight DESC LIMIT 5""", {"id": refs["recon_system"]})
        top_ok = bool(rows) and rows[0][0] == refs["recon_expert"]
        dominance = rows[0][1] / rows[1][1] if len(rows) > 1 and rows[1][1] else None
        check("legacy recon system: top derived expert is the recon expert", top_ok,
              {"top5 [id, weight, docs, answered, asked, documented]": rows, "weight_ratio_1st_2nd": dominance})

    # 5. deal wall
    if refs.get("falcon_deal"):
        rows = await q("MATCH (p:Person)-[:ON_DEAL_TEAM]->(:Deal {id: $id}) RETURN collect(p.id)",
                       {"id": refs["falcon_deal"]})
        check("Project Falcon deal team", sorted(rows[0][0]) == sorted(refs["falcon_team"]), sorted(rows[0][0]))

    # 6. decision trail
    for dec_id, meta in gt.get("decisions", {}).items():
        decided_live = meta.get("effective", "") >= refs.get("cutoff", "9999")
        rows = await q("""
            MATCH (d:Decision {id: $id})
            OPTIONAL MATCH (d)-[:DECIDED_IN]->(m:Document)
            OPTIONAL MATCH (d)-[:JUSTIFIED_BY]->(j:Document)
            OPTIONAL MATCH (d)-[:SUPERSEDES]->(s:Decision)
            RETURN d.status, collect(DISTINCT m.id), collect(DISTINCT j.id), collect(DISTINCT s.id)""", {"id": dec_id})
        if decided_live and not live:
            check(f"decision {dec_id} not yet known before live feed", not rows, rows)
            continue
        ok = bool(rows) and sorted(rows[0][1]) == sorted(meta["decided_in"]) and \
            set(meta.get("justified_by", [])) <= set(rows[0][2]) and \
            (not meta.get("supersedes") or rows[0][3] == [meta["supersedes"]])
        check(f"decision trail {dec_id}", ok, rows)

    # 7. live-feed separation
    rows = await q("MATCH (d:Document) RETURN sum(CASE WHEN d.live_feed THEN 1 ELSE 0 END), count(d), max(d.ts)")
    if not live:
        check("no live-feed documents before replay", rows[0][0] == 0 and rows[0][2] < refs.get("cutoff", "9"), rows)

    # 8. MNPI documents are never public
    rows = await q("MATCH (d:Document {sensitivity: 'MNPI'}) RETURN count(d), sum(CASE WHEN d.public THEN 1 ELSE 0 END)")
    check("MNPI documents are access-restricted", rows[0][1] == 0, rows)

    # 9. hybrid retrieval primitives
    qv = (await embed_cached(["flagged incoming wire fraud alert"], "query"))[0]
    rows = await q("""
        CALL db.idx.vector.queryNodes('Document', 'embedding', 5, vecf32($v)) YIELD node, score
        RETURN node.id, node.title, score""", {"v": qv})
    check("vector index returns documents (cosine distance ascending)",
          len(rows) == 5 and all(rows[i][2] <= rows[i + 1][2] for i in range(4)), rows)
    term = (await q("MATCH (s:System {id: $id}) RETURN s.name", {"id": refs.get("recon_system", "")}) or
            [["reconciliation"]])[0][0]
    rows = await q("""
        CALL db.idx.fulltext.queryNodes('Document', $term) YIELD node, score
        RETURN node.id, score ORDER BY score DESC LIMIT 5""", {"term": term})
    check(f"full-text index returns documents ('{term}')", len(rows) > 0, rows)
    return out


def write(tenant: str, results: list[dict]) -> None:
    d = REPO_ROOT / "evals" / "results"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"graph_checks_{tenant}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str)
                                                   + "\n", encoding="utf-8")
    lines = [f"# Graph storyline checks: {tenant}", "", "| Check | Result |", "|---|---|"]
    lines += [f"| {r['check']} | {'PASS' if r['pass'] else 'FAIL'} |" for r in results]
    (d / f"graph_checks_{tenant}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", action="append", required=True)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    failed = 0
    for t in args.tenant:
        res = await run(t, args.live)
        write(t, res)
        for r in res:
            print(f"[{'PASS' if r['pass'] else 'FAIL'}] {t}: {r['check']}")
            if not r["pass"]:
                print("      ", json.dumps(r["detail"], default=str, ensure_ascii=False)[:600])
        failed += sum(not r["pass"] for r in res)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(asyncio.run(_main()))
