"""Sentinel: watches every live-feed batch and acts on what is new.

Detection is deterministic graph work over the batch; only the case write-up (title, summary, handoff note) is
written by Haiku 5.5, with a template fallback. Detectors:

1. Transaction monitoring: alerted / high fraud-score transactions -> linked_accounts over shared identifiers ->
   ``fraud_ring`` (2+ linked clients) or ``suspicious_activity`` case; high/critical cases are handed off to the
   Investigator on the blackboard. A transaction into an already-frozen account is escalated.
2. Information barrier: barrier_check on every MNPI deal, restricted to documents in the batch. MNPI-classified
   content outside the wall -> ``mnpi_breach`` case handed off to Compliance; a non-MNPI document that only names
   the deal (logistics, sanctioned wall-crossings) -> a lower-confidence ``barrier_watch`` alert, no case.
3. Bus factor: a departing person who is the main expert on a system with little backup -> key_person_risk alert.
4. Ownership changes in the batch are reported as events.

Transaction monitoring runs as an AML-analyst persona (read + act, no reveal). The information-barrier detector runs
as compliance surveillance (the compliance-officer persona, which may read walled documents; every such read is
audit-logged by barrier_check), so the leaked documents can be attached to the case as evidence. Neither path ever
reveals vault values.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.graph import queries as Q
from backend.llm.client import COSTS, chat_json, system_block
from backend.tenancy import make_auth
from backend.tools import investigation, ops
from backend.tools.base import ToolContext, ToolError

log = logging.getLogger(__name__)

SENTINEL_PERSONA = "aml_analyst"
SURVEILLANCE_PERSONA = "compliance_officer"
MIN_SCORE = 0.6
HANDOFF_SEVERITIES = ("high", "critical")
KEY_PERSON_MIN_WEIGHT = 2.0
# Bus factor: alert when the best remaining expert's KNOWS_ABOUT weight is below BACKUP_COVERED x the leaver's;
# "high" below BACKUP_THIN x. KNOWS_ABOUT is derived from activity, so colleagues who only touch a system
# (e.g. ReconLegacy: leaver 18.5, best backup 12.1 = 0.65) still carry sizeable weight.
BACKUP_COVERED = 0.75
BACKUP_THIN = 0.70


class Triage(BaseModel):
    title: str = Field(description="Case title under 90 characters, with the key ids.")
    summary: str = Field(description="2-4 sentences: what was detected, why it matters, what the graph shows.")
    severity: Literal["low", "medium", "high", "critical"]
    handoff_note: str = Field(description="One paragraph for the specialist taking over: what to verify, what to propose.")


TRIAGE_PROMPT = """You are the Sentinel of FinanceBrain, a monitoring agent that watches newly ingested data at a
financial firm (synthetic data). You receive one deterministic finding from graph analysis of the latest live-feed
batch. Write the case header and the handoff note for the specialist agent. Use only facts in the finding and keep
ids exactly as given (t_..., a_..., c_..., cp_..., email-..., privacy tokens). No speculation about motives, no
personal data. Severity: start from suggested_severity; raise it only for a reason stated in the finding (for
example funds moving into an account that is already frozen)."""


def sentinel_context(tenant: str, graph: str | None = None, persona: str = SENTINEL_PERSONA) -> ToolContext:
    return ToolContext(make_auth(tenant, persona, user="sentinel"), agent="sentinel", graph=graph)


async def _triage(finding: dict, fallback: dict, use_llm: bool) -> dict:
    if use_llm:
        try:
            out: Triage = await chat_json(
                [system_block(TRIAGE_PROMPT),
                 {"role": "user", "content": json.dumps(finding, ensure_ascii=False, default=str)}],
                Triage, model=get_settings().model_fast, name="triage", max_tokens=700, purpose="sentinel")
            return {"title": out.title.strip()[:200], "summary": out.summary.strip()[:2000], "severity": out.severity,
                    "handoff_note": out.handoff_note.strip()[:2500], "by": "llm"}
        except Exception as e:  # noqa: BLE001
            log.warning("sentinel triage fell back to template: %s", str(e)[:200])
    return {**fallback, "by": "template"}


# ------------------------------------------------------------------------------------------------ detectors
async def _txn_candidates(ctx: ToolContext, txn_ids: list[str], day: str) -> list[dict]:
    if not txn_ids:
        return []
    rows = await ctx.q("SENTINEL_TXNS", Q.SENTINEL_TXNS, {"ids": txn_ids, "min_score": MIN_SCORE})
    out = []
    for tid, kind, amount, ts, alert, score, fa, fa_st, ta, ta_st, cp, cp_name, cp_country in rows:
        linked = None
        try:
            linked = await investigation.linked_accounts(ctx, tid, hops=2)
        except ToolError as e:
            log.info("sentinel: no link analysis for %s: %s", tid, e)
        seeds = list((linked or {}).get("seed_clients") or [])
        others = [x for x in (linked or {}).get("linked") or [] if x.get("hops", 0) >= 1]
        tokens = sorted({v["token"] for x in others for v in x.get("via") or []})
        shares = sorted({v["shares"] for x in others for v in x.get("via") or []})
        frozen = [a for a, s in ((fa, fa_st), (ta, ta_st)) if a and s == "frozen"]
        ring = len(others) >= 2
        sc = float(score) if score is not None else 0.0
        sev = ("critical" if frozen and ring else "high" if ring or frozen or sc >= 0.85 else "medium")
        case_kind = "fraud_ring" if ring else "suspicious_activity"
        linked_ids = [x["id"] for x in others][:10]
        finding = {
            "detector": "transaction_monitoring", "batch_day": day, "case_kind": case_kind,
            "suggested_severity": sev,
            "transaction": {"id": tid, "kind": kind, "amount": amount, "ts": ts, "alert": alert, "fraud_score": score},
            "accounts": {"from": fa, "to": ta, "already_frozen": frozen},
            "counterparty": {"id": cp, "name": cp_name, "country": cp_country} if cp else None,
            "holder_clients": seeds, "linked_clients": linked_ids, "shared_identifier_types": shares,
            "shared_tokens": tokens[:8],
            "internal_flows_between_linked_accounts": len((linked or {}).get("internal_flows") or []),
        }
        what = f"{kind} {tid} of {amount:,.0f}" + (f" (alert {alert})" if alert else f" (fraud score {sc:.2f})")
        ring_txt = (f"; holder {', '.join(seeds) or '?'} shares {', '.join(shares)} identifiers with "
                    f"{len(linked_ids)} other client(s): {', '.join(linked_ids[:6])}") if ring else ""
        frozen_txt = f"; funds moved into already-frozen account(s) {', '.join(frozen)}" if frozen else ""
        fallback = {
            "title": f"Sentinel: {'possible ring activity' if ring else 'suspicious transaction'} - {tid}"[:200],
            "summary": f"Live-feed batch {day}: {what} {'into' if ta else 'from'} {ta or fa}{ring_txt}{frozen_txt}.",
            "severity": sev,
            "handoff_note": (f"Sentinel flagged {tid} in the {day} live-feed batch. Confirm whether the holder belongs to "
                             f"a ring (detect_rings / linked_accounts), trace the funds, and propose actions for "
                             f"approval if warranted."),
        }
        evidence = [tid, *(a for a in (fa, ta) if a), *seeds, *linked_ids, *([cp] if cp else []), *tokens[:6]]
        out.append({"kind": case_kind, "finding": finding, "fallback": fallback, "to": "investigator",
                    "evidence": list(dict.fromkeys(evidence))})
    return out


async def _mnpi_candidates(ctx: ToolContext, doc_ids: list[str], day: str) -> tuple[list[dict], list[dict]]:
    """(cases to open, barrier_watch alerts) for the batch's documents."""
    if not doc_ids:
        return [], []
    new = set(doc_ids)
    out, watch = [], []
    for deal_id, codename in await ctx.q("SENTINEL_MNPI_DEALS", Q.SENTINEL_MNPI_DEALS):
        try:
            res = await investigation.barrier_check(ctx, deal_id)
        except ToolError as e:
            log.info("sentinel: barrier_check %s failed: %s", deal_id, e)
            continue
        batch = [b for b in res.get("breaches") or [] if b["id"] in new]
        hits = [b for b in batch if b.get("mnpi_content")]
        for b in batch:
            if not b.get("mnpi_content"):
                watch.append({"kind": "barrier_watch", "batch_day": day, "severity": "medium", "deal": deal_id,
                              "codename": codename, "document": b["id"], "outsider_count": b.get("outsider_count"),
                              "note": "non-MNPI document naming a walled deal reached people outside the wall"})
        if not hits:
            continue
        outsiders = sorted({o["id"] for b in hits for o in b.get("outsiders") or []})
        authors = sorted({b["author"]["id"] for b in hits if b.get("author")})
        finding = {
            "detector": "information_barrier", "batch_day": day, "case_kind": "mnpi_breach",
            "suggested_severity": "high", "deal": deal_id, "codename": codename,
            "documents": [{k: b.get(k) for k in ("id", "kind", "ts", "channel", "author", "author_inside_wall",
                                                 "outsider_count", "pattern")} for b in hits],
            "outsiders": outsiders[:15],
        }
        fallback = {
            "title": f"Sentinel: possible information-barrier breach - {codename or deal_id}"[:200],
            "summary": (f"Live-feed batch {day}: {len(hits)} new document(s) mentioning MNPI deal {deal_id} reached "
                        f"{len(outsiders)} person(s) outside the deal wall: {', '.join(b['id'] for b in hits)}."),
            "severity": "high",
            "handoff_note": (f"Sentinel found new documents about {deal_id} outside the deal wall in the {day} batch. "
                             f"Review them with barrier_check, decide whether this is a breach, and propose "
                             f"restrict_channel / notify_owner actions for approval."),
        }
        evidence = [deal_id, *(b["id"] for b in hits), *authors, *outsiders[:8]]
        out.append({"kind": "mnpi_breach", "finding": finding, "fallback": fallback, "to": "compliance",
                    "evidence": list(dict.fromkeys(evidence))})
    return out, watch


async def _departure_alerts(ctx: ToolContext, leavers: list[str], day: str) -> list[dict]:
    if not leavers:
        return []
    rows = await ctx.q("SENTINEL_DEPARTURE_RISK", Q.SENTINEL_DEPARTURE_RISK,
                       {"ids": list(leavers), "min_weight": KEY_PERSON_MIN_WEIGHT})
    by_person: dict[str, dict] = {}
    for pid, name, sid, system, w, backup in rows:
        w, b = float(w), float(backup or 0.0)
        if b >= BACKUP_COVERED * w:
            continue  # someone else knows this system nearly as well
        p = by_person.setdefault(pid, {"person": pid, "name": name, "systems": []})
        p["systems"].append({"id": sid, "name": system, "weight": round(w, 2), "best_backup_weight": round(b, 2)})
    return [{"kind": "key_person_risk", "batch_day": day, **p,
             "severity": "high" if any(s["best_backup_weight"] < BACKUP_THIN * s["weight"] for s in p["systems"])
             else "medium"}
            for p in by_person.values()]


async def _open_and_route(ctx: ToolContext, kind: str, tri: dict, evidence: list[str], to_agent: str) -> dict:
    res = await ops.open_case(ctx, tri["title"], kind, tri["severity"], tri["summary"], evidence)
    case = res["case"]
    out = {"case_id": case["id"], "kind": kind, "severity": tri["severity"], "title": tri["title"],
           "summary": tri["summary"], "reused_existing_case": res.get("reused_existing_case", False),
           "evidence": len(evidence), "written_by": tri["by"]}
    if tri["severity"] in HANDOFF_SEVERITIES:
        tasks = (await ops.list_tasks(ctx, status="open", mine=False, limit=50)).get("tasks") or []
        if any(t.get("case_id") == case["id"] for t in tasks):
            out["handoff"] = {"to": to_agent, "status": "already queued"}
        else:
            h = await ops.handoff(ctx, case["id"], to_agent, tri["handoff_note"])
            out["handoff"] = {"to": to_agent, "task_id": h.get("task_id"), "status": "open"}
    return out


# ------------------------------------------------------------------------------------------------ entry point
async def scan(tenant: str, *, day: str, txn_ids: list[str], doc_ids: list[str], leavers: list[str] | None = None,
               ownership: list[dict] | None = None, graph: str | None = None, use_llm: bool = True) -> dict:
    ctx = sentinel_context(tenant, graph)
    surv = sentinel_context(tenant, graph, SURVEILLANCE_PERSONA)
    c0 = COSTS.total_usd()
    mnpi_cases, watch = await _mnpi_candidates(surv, doc_ids, day)
    candidates = [(ctx, c) for c in await _txn_candidates(ctx, txn_ids, day)] + [(surv, c) for c in mnpi_cases]
    cases = []
    for cx, c in candidates:
        tri = await _triage(c["finding"], c["fallback"], use_llm)
        try:
            cases.append({"detector": c["finding"]["detector"], "subject": c["evidence"][0],
                          **await _open_and_route(cx, c["kind"], tri, c["evidence"], c["to"])})
        except ToolError as e:
            cases.append({"detector": c["finding"]["detector"], "subject": c["evidence"][0], "error": str(e)})
    alerts = await _departure_alerts(ctx, leavers or [], day) + watch
    for a in alerts:
        ctx.events.append({"agent": "sentinel", **a})
    for o in ownership or []:
        ctx.events.append({"kind": "ownership_change", "agent": "sentinel", "batch_day": day, **o})
    return {"day": day, "cases": cases, "alerts": alerts, "events": ctx.events + surv.events,
            "queries": len(ctx.trace) + len(surv.trace), "cost_usd": round(COSTS.total_usd() - c0, 5)}
