"""Storyline beats: the ground-truth events that LLM writers narrate.

Each Beat becomes exactly one document. Briefs are authoritative; the writer only adds natural
language around them. Placeholders (``{{TYPE:id}}``) stand in for every PII value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from datagen.world import World, at


def ph(ptype: str, eid: str) -> str:
    return "{{" + f"{ptype}:{eid}" + "}}"


@dataclass
class Beat:
    id: str
    storyline: str
    kind: str  # slack | email | ticket | doc | meeting | crm
    ts: str
    author: str  # person id, or "ext:<Organization>" for external senders
    brief: str
    title: str | None = None
    channel: str | None = None  # channel id (slack)
    thread: str | None = None  # thread key: beats sharing a key form one slack thread / email chain
    recipients: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)
    ticket_key: str | None = None
    ticket_status: str | None = None
    assignee: str | None = None
    client_id: str | None = None
    doc_type: str | None = None
    sensitivity: str | None = None  # MNPI | confidential | internal
    placeholders: list[str] = field(default_factory=list)
    mentions: dict[str, list[str]] = field(default_factory=dict)
    decision: dict | None = None


def build_beats(w: World) -> list[Beat]:
    beats = _halcyon(w) if w.tenant == "halcyon" else _brightwater(w)
    ids = [b.id for b in beats]
    assert len(ids) == len(set(ids)), "duplicate beat ids"
    return sorted(beats, key=lambda b: (b.ts, b.id))


def _d(iso_date: str, hh: int, mm: int) -> str:
    d = date.fromisoformat(iso_date)
    return at(d.year, d.month, d.day, hh, mm)


def _fraud_onboarding(w: World, segment_word: str) -> list[Beat]:
    out = []
    clients = w.index("client")
    for i, (cid, aid) in enumerate(zip(w.refs["ring_clients"], w.refs["ring_accounts"])):
        cl = clients[cid]
        out.append(Beat(
            f"fr_onboard_{i + 1}", "fraud_ring", "crm", _d(cl.onboarded, 11, 10 + i * 5), cl.rm_id,  # type: ignore[attr-defined]
            brief=(f"Routine onboarding note for a new {segment_word} client ({cid}). State the client's name "
                   f"{ph('CLIENT_NAME', cid)}, contact phone {ph('CLIENT_PHONE', cid)}, and home address "
                   f"{ph('CLIENT_ADDRESS', cid)}; note that online banking was enrolled from device "
                   f"{ph('CLIENT_DEVICE', cid)} and that checking account {ph('ACCOUNT_NUMBER', aid)} was opened. "
                   "Source of funds: self-employed consulting income; expects incoming wires from overseas "
                   "customers. Tone is routine; nothing alarming. 60-110 words."),
            client_id=cid, placeholders=[ph("CLIENT_NAME", cid), ph("CLIENT_PHONE", cid), ph("CLIENT_ADDRESS", cid),
                                         ph("CLIENT_DEVICE", cid), ph("ACCOUNT_NUMBER", aid)],
            mentions={"clients": [cid], "accounts": [aid]},
        ))
    return out


def _halcyon(w: World) -> list[Beat]:
    c = w.cast
    N = lambda role: w.by_role(role).name  # noqa: E731
    ch = {x.name: x.id for x in w.channels}
    refs = w.refs
    ring, racc, rcp = refs["ring_clients"], refs["ring_accounts"], refs["ring_counterparties"]
    cps = w.index("counterparty")
    o1, o2, ex = cps[rcp["origin1"]], cps[rcp["origin2"]], cps[rcp["exit"]]  # type: ignore[index]
    t1, t2 = refs["ring_wire_1"], refs["ring_wire_2"]
    falcon = next(d for d in w.deals if d.id == "deal_falcon")
    team = {p.team_id: [] for p in w.people}
    for p in w.people:
        team[p.team_id].append(p.id)
    analyst = next(pid for pid in falcon.team_ids if pid not in (c["falcon_lead"], c["falcon_vp"], c["falcon_assoc"]))
    B: list[Beat] = []

    # ---------------------------------------------------------------- fraud ring (Track 1)
    B += _fraud_onboarding(w, "wealth")
    B.append(Beat(
        "fr_alert_2291", "fraud_ring", "ticket", at(2026, 9, 25, 10, 16), c["aml_analyst"],
        title="FraudScore alert: inbound wire $48,000.00 (score 0.91)", ticket_key="FRAUD-2291",
        ticket_status="in_progress", assignee=c["aml_analyst"],
        brief=(f"Description is an auto-generated FraudScore alert for transaction {t1}: inbound wire of $48,000.00 on "
               f"2026-09-25 10:14 ET from {o1.name} (Cyprus), originator IBAN {ph('CP_IBAN', o1.id)}, BIC "  # type: ignore[attr-defined]
               f"{ph('CP_SWIFT', o1.id)}, beneficiary checking account {ph('ACCOUNT_NUMBER', racc[0])} held by "  # type: ignore[attr-defined]
               f"{ph('CLIENT_NAME', ring[0])}. Score 0.91. Drivers: account under 90 days old, first international "
               "wire, amount far above expected profile, memo 'consulting fees'. Then two comments by "
               f"{N('aml_analyst')}: (1) triage - account opened this summer and received several ACH credits just "
               "under $10,000 in August; (2) later the same afternoon - funds are already moving out: internal "
               "transfers of $9,500 and $9,800 to other Halcyon accounts; she will review the receiving accounts. "
               "Do not name the receiving clients."),
        placeholders=[ph("CP_IBAN", o1.id), ph("ACCOUNT_NUMBER", racc[0]), ph("CLIENT_NAME", ring[0])],  # type: ignore[attr-defined]
        mentions={"clients": [ring[0]], "accounts": [racc[0]], "counterparties": [o1.id],  # type: ignore[attr-defined]
                  "transactions": [t1], "systems": ["sys_fraudscore"]},
    ))
    B.append(Beat(
        "fr_slack_1", "fraud_ring", "slack", at(2026, 9, 25, 14, 41), c["aml_analyst"], channel=ch["#compliance"],
        thread="fr_thread",
        brief=("Nadia flags FRAUD-2291 to the channel: a $48k inbound wire from a Cyprus shell-looking company into a "
               "brand-new wealth account, and money is already being split into sub-$10k transfers. Asks Marcus "
               "whether to place a hold."),
        mentions={"transactions": [t1]},
    ))
    B.append(Beat(
        "fr_slack_2", "fraud_ring", "slack", at(2026, 9, 25, 14, 58), c["cco"], channel=ch["#compliance"],
        thread="fr_thread",
        brief=("Marcus replies in thread: no hold yet without more evidence; asks Nadia to map where the money went and "
               "whether the receiving accounts are connected to each other, then write it up."),
    ))
    B.append(Beat(
        "fr_email_review", "fraud_ring", "email", at(2026, 9, 30, 17, 25), c["aml_analyst"],
        title="FRAUD-2291 preliminary review", recipients=[c["cco"]], cc=[c["compliance_eng"]],
        brief=(f"Preliminary write-up of FRAUD-2291 ({t1}). Pattern: structuring in August (sub-$10k ACH credits), the "
               f"$48,000 inbound wire from {o1.name} on Sep 25, rapid internal transfers under $10k to several other "  # type: ignore[attr-defined]
               f"Halcyon accounts, then outgoing wires on Sep 29-30 to {ex.name}, a US crypto exchange "  # type: ignore[attr-defined]
               f"(beneficiary account {ph('CP_ACCOUNT', ex.id)}). She has not yet checked whether the receiving "  # type: ignore[attr-defined]
               "accounts share phone numbers, addresses, or devices - that is the next step - and she suspects a "
               "possible SAR. Asks for approval to escalate."),
        placeholders=[ph("CP_ACCOUNT", ex.id)],  # type: ignore[attr-defined]
        mentions={"counterparties": [o1.id, ex.id], "transactions": [t1]},  # type: ignore[attr-defined]
    ))
    B.append(Beat(
        "fr_alert_2307", "fraud_ring", "ticket", at(2026, 10, 3, 11, 24), c["aml_analyst"],
        title="FraudScore alert: inbound wire $31,500.00 (score 0.88)", ticket_key="FRAUD-2307", ticket_status="open",
        assignee=c["aml_analyst"],
        brief=(f"Auto-generated FraudScore alert for transaction {t2}: inbound wire of $31,500.00 on 2026-10-03 11:22 "
               f"ET from {o2.name} (UAE), originator IBAN {ph('CP_IBAN', o2.id)}, into checking account "  # type: ignore[attr-defined]
               f"{ph('ACCOUNT_NUMBER', racc[2])} held by {ph('CLIENT_NAME', ring[2])}. Score 0.88. Drivers: new "
               "account, high-risk jurisdiction, amount above profile. No comments yet."),
        placeholders=[ph("CP_IBAN", o2.id), ph("ACCOUNT_NUMBER", racc[2]), ph("CLIENT_NAME", ring[2])],  # type: ignore[attr-defined]
        mentions={"clients": [ring[2]], "accounts": [racc[2]], "counterparties": [o2.id],  # type: ignore[attr-defined]
                  "transactions": [t2], "systems": ["sys_fraudscore"]},
    ))

    # ---------------------------------------------------------------- Project Falcon / information barrier
    mnpi = "MNPI"
    B.append(Beat(
        "fa_policy", "falcon", "doc", at(2026, 7, 15, 9, 0), c["cco"], title="Information Barrier and MNPI Policy",
        doc_type="policy",
        brief=("Firm policy: what counts as material non-public information (MNPI), deal codenames, wall-crossing "
               "procedure via the Control Room (Compliance), restricted and watch lists, that deal discussion is only "
               "allowed in deal-team channels, that traders must never receive MNPI, escalation and disciplinary "
               "consequences. 450-650 words."),
    ))
    B.append(Beat(
        "fa_wall_memo", "falcon", "doc", at(2026, 8, 3, 9, 30), c["falcon_lead"],
        title="Project Falcon - Wall-Crossing Memo and Deal Team", doc_type="memo", sensitivity=mnpi,
        brief=(f"Confidential memo. Project Falcon = buy-side advisory to {falcon.principal} on the acquisition of "
               f"{falcon.target} (ticker NWLG), indicative value {falcon.value}. Deal team (wall-crossed): "
               f"{N('falcon_lead')} (lead), {N('falcon_vp')}, {N('falcon_assoc')}, {w.person(analyst).name}. Control "
               f"Room contact: {N('cco')}. NWLG is added to the restricted list. Discussion only in #project-falcon. "
               "Target announcement date: 2026-11-02. 250-400 words."),
        mentions={"deals": ["deal_falcon"], "people": falcon.team_ids + [c["cco"]]},
    ))
    B.append(Beat(
        "fa_slack_1", "falcon", "slack", at(2026, 8, 18, 10, 5), c["falcon_assoc"], channel=ch["#project-falcon"],
        thread="fa_dd", sensitivity=mnpi,
        brief="Aisha posts a data-room update on Northwind: FY25 EBITDA $142M, top-5 customers 38% of revenue, a pending "
              "labor dispute in their Ohio depots.",
        mentions={"deals": ["deal_falcon"]},
    ))
    B.append(Beat(
        "fa_slack_2", "falcon", "slack", at(2026, 8, 18, 10, 31), c["falcon_vp"], channel=ch["#project-falcon"],
        thread="fa_dd", sensitivity=mnpi,
        brief="Tom replies: customer concentration will come up with Meridian's board; asks for a churn analysis by Friday.",
        mentions={"deals": ["deal_falcon"]},
    ))
    B.append(Beat(
        "fa_slack_3", "falcon", "slack", at(2026, 9, 9, 16, 12), c["falcon_lead"], channel=ch["#project-falcon"],
        sensitivity=mnpi,
        brief="Elena: Meridian is comfortable with a $1.15-1.25B range; still targeting announcement on Nov 2. Reminds the "
              "team that nothing about Falcon leaves this channel.",
        mentions={"deals": ["deal_falcon"]},
    ))
    B.append(Beat(
        "fa_meeting", "falcon", "meeting", at(2026, 9, 10, 15, 0), c["falcon_lead"],
        title="Project Falcon diligence sync", attendees=falcon.team_ids, sensitivity=mnpi,
        brief=("Deal team call: diligence workstreams (commercial, legal, labor dispute), financing from Meridian's "
               "lenders, fairness opinion timing, announcement prep for Nov 2. 400-700 words."),
        mentions={"deals": ["deal_falcon"]},
    ))
    B.append(Beat(
        "fa_leak_email", "falcon", "email", at(2026, 9, 22, 20, 47), c["falcon_vp"], title="Thursday",
        recipients=[c["leaker"]], sensitivity=mnpi,
        brief=("Casual, friendly note from Tom to his friend Derek (a trader on the desk) cancelling Thursday drinks. "
               "He carelessly says he is buried in Falcon, that Northwind is going to be a big one in early November, "
               "and 'keep that under your hat'. 40-80 words."),
        mentions={"deals": ["deal_falcon"], "people": [c["falcon_vp"], c["leaker"]]},
    ))
    B.append(Beat(
        "fa_leak_slack", "falcon", "slack", at(2026, 10, 7, 9, 48), c["leaker"], channel=ch["#trading-desk"],
        thread="fa_leak", sensitivity=mnpi,
        brief="Derek posts on the desk channel that he is hearing Northwind Logistics is getting taken out ('Falcon' is the "
              "word upstairs) and that NWLG November calls look cheap.",
        mentions={"deals": ["deal_falcon"]},
    ))
    B.append(Beat(
        "fa_leak_reply", "falcon", "slack", at(2026, 10, 7, 9, 53), c["head_trading"], channel=ch["#trading-desk"],
        thread="fa_leak",
        brief="Daniel replies sharply in thread: not here, do not trade on that, and he is looping in Compliance.",
    ))

    # ---------------------------------------------------------------- crypto decision trail (Track 3)
    B.append(Beat(
        "cr_regulator", "crypto", "email", at(2026, 7, 28, 13, 5), "ext:Office of Financial Supervision",
        title="Supervisory guidance: digital-asset client onboarding and custody", recipients=[c["cco"]],
        cc=[c["ceo"]],
        brief=("Formal letter from the state regulator's examination team to Halcyon's Chief Compliance Officer. "
               "Draws attention to new interim guidance on onboarding clients whose wealth derives from digital assets: "
               "expects AML controls able to trace on-chain flows, custody due diligence, and source-of-funds "
               "verification. Requests a written risk assessment by 2026-08-29. 180-280 words."),
    ))
    B.append(Beat(
        "cr_ticket", "crypto", "ticket", at(2026, 8, 4, 9, 40), c["cco"],
        title="Assess digital-asset client onboarding risk (OFS guidance)", ticket_key="COMP-112",
        ticket_status="resolved", assignee=c["aml_analyst"],
        brief=("Marcus opens the ticket referencing the OFS letter of Jul 28. Comments by Nadia: AMLMonitor cannot trace "
               "on-chain flows, no blockchain analytics vendor in place; 9 current wealth prospects have "
               "crypto-derived wealth; recommendation: pause new digital-asset client onboarding until a blockchain "
               "analytics tool is integrated into AMLMonitor and final guidance is published."),
        mentions={"systems": ["sys_amlmonitor", "sys_kycpipeline"]},
    ))
    debate = [
        ("cr_slack_1", (8, 6, 10, 2), "head_wealth", "Grace argues for continuing: 14 prospects, roughly $40M of AUM "
         "pipeline, competitors are onboarding these clients, enhanced due diligence could work."),
        ("cr_slack_2", (8, 6, 10, 27), "cco", "Marcus pushes back citing COMP-112 findings and the OFS letter: "
         "AMLMonitor cannot see on-chain flows, so the firm cannot evidence source of funds."),
        ("cr_slack_3", (8, 7, 11, 15), "head_risk", "Owen adds custody and counterparty risk with crypto custodians "
         "and asks what the exit plan is if guidance tightens."),
        ("cr_slack_4", (8, 12, 16, 40), "cfo", "Martin weighs the revenue against regulatory risk and suggests the "
         "board risk committee decide on Aug 21."),
    ]
    for bid, (m, d, hh, mm), role, brief in debate:
        B.append(Beat(bid, "crypto", "slack", at(2026, m, d, hh, mm), c[role], channel=ch["#crypto-policy"],
                      thread="cr_debate", brief=brief, mentions={"systems": ["sys_amlmonitor"]}))
    B.append(Beat(
        "cr_board_memo", "crypto", "doc", at(2026, 8, 20, 18, 0), c["ceo"],
        title="Board memo: Pause on onboarding digital-asset clients", doc_type="memo",
        brief=("Memo to the board risk committee recommending a pause on onboarding new clients whose wealth derives "
               "from digital assets, effective 2026-08-22. Justification: OFS supervisory letter (Jul 28), Compliance "
               "assessment COMP-112 (AMLMonitor cannot trace on-chain flows), Risk's custody concerns, and the "
               "#crypto-policy discussion. Lifting conditions: (a) blockchain analytics integrated into AMLMonitor, "
               "(b) OFS final guidance published. Existing clients unaffected. 350-550 words."),
        mentions={"systems": ["sys_amlmonitor"]},
        decision={"id": "dec_crypto_pause", "summary": "Pause onboarding of new digital-asset clients",
                  "effective": "2026-08-22", "justified_by": ["cr_regulator", "cr_ticket", "cr_slack_2", "cr_slack_3"]},
    ))
    B.append(Beat(
        "cr_meeting", "crypto", "meeting", at(2026, 8, 21, 14, 0), c["ceo"],
        title="Board risk committee - digital-asset clients",
        attendees=[c["ceo"], c["cfo"], c["coo"], c["cco"], c["head_risk"], c["head_wealth"]],
        brief=("Committee discussion of the board memo; Grace registers her disagreement but accepts; vote approves the "
               "pause effective Aug 22 with the two lifting conditions; Marcus to report back when conditions are met. "
               "400-700 words."),
        decision={"id": "dec_crypto_pause", "role": "decided_in"},
    ))
    B.append(Beat(
        "cr_wealth_email", "crypto", "email", at(2026, 8, 24, 8, 15), c["head_wealth"],
        title="Pause on new digital-asset clients", recipients=[p for p in team["wealth"] if p != c["head_wealth"]],
        brief=("Grace informs advisors that, per the board decision of Aug 21, onboarding of new clients with "
               "crypto-derived wealth is paused effective Aug 22; existing clients unaffected; route questions to "
               "Compliance. 90-160 words."),
    ))
    B.append(Beat(
        "cr_resume", "crypto", "email", at(2026, 10, 8, 9, 10), c["cco"],
        title="Recommendation: resume digital-asset client onboarding", recipients=[c["ceo"], c["head_wealth"]],
        cc=[c["cfo"], c["head_risk"]],
        brief=("Both lifting conditions are met: OFS published final guidance on Oct 5, and ChainSight blockchain "
               "analytics went live inside AMLMonitor on Oct 6. Recommends resuming onboarding effective 2026-10-12 "
               "with enhanced due diligence (source-of-funds tracing, quarterly reviews). This supersedes the Aug 22 "
               "pause. 160-260 words."),
        mentions={"systems": ["sys_amlmonitor"]},
        decision={"id": "dec_crypto_resume", "summary": "Resume onboarding of digital-asset clients with EDD",
                  "effective": "2026-10-12", "supersedes": "dec_crypto_pause", "justified_by": ["cr_resume"]},
    ))
    B.append(Beat(
        "cr_resume_ack", "crypto", "slack", at(2026, 10, 8, 11, 2), c["ceo"], channel=ch["#crypto-policy"],
        brief="Catherine: approved, onboarding resumes Oct 12 with enhanced due diligence; Grace to brief advisors.",
        decision={"id": "dec_crypto_resume", "role": "decided_in"},
    ))

    # ---------------------------------------------------------------- bus factor: ReconLegacy (Track 3)
    B.append(Beat(
        "re_runbook", "recon", "doc", at(2026, 7, 10, 10, 0), c["recon_expert"], title="ReconLegacy Runbook (v3.2)",
        doc_type="runbook",
        brief=("Runbook for ReconLegacy: Perl batch jobs on an Oracle 11g host, cron at 02:15 ET, reads "
               "SettlementEngine extracts and GLCore balances, posts the break report to #recon by 06:30. Common "
               "failures: late settlement file, Oracle tablespace full (ORA-01653), FX rate table not loaded from the "
               "PricingSvc snapshot. Restart steps per job. Notes that the FX adjustment module and the month-end "
               "manual override are not documented here. Contact: Priya Raman. 450-700 words."),
        mentions={"systems": ["sys_reconlegacy", "sys_settlementengine", "sys_glcore", "sys_pricingsvc"]},
    ))
    recon_slack = [
        ("re_slack_1", (7, 22, 7, 5), "recon_analyst", "re_t1", "Tasha: 14 breaks this morning, all FX trades, "
         "is ReconLegacy broken again?"),
        ("re_slack_2", (7, 22, 7, 31), "recon_expert", "re_t1", "Priya explains the FX rate table loaded late from "
         "the PricingSvc snapshot, she re-ran step 4 of the batch, breaks should clear."),
        ("re_slack_3", (8, 15, 6, 50), "recon_analyst", "re_t2", "Tasha: the recon report never arrived after "
         "yesterday's PricingSvc incident."),
        ("re_slack_4", (8, 15, 7, 12), "recon_expert", "re_t2", "Priya re-ran the batch manually with the --asof "
         "flag for 08-14 and explains why the job aborts when prices are stale."),
    ]
    for bid, (m, d, hh, mm), role, th, brief in recon_slack:
        B.append(Beat(bid, "recon", "slack", at(2026, m, d, hh, mm), c[role], channel=ch["#recon"], thread=th,
                      brief=brief, mentions={"systems": ["sys_reconlegacy"]}))
    B.append(Beat(
        "re_ticket_1", "recon", "ticket", at(2026, 8, 27, 3, 5), c["recon_analyst"],
        title="ReconLegacy nightly job failed: ORA-01653 unable to extend table", ticket_key="ITOPS-231",
        ticket_status="resolved", assignee=c["recon_expert"],
        brief="Nightly job failure; Priya's comments explain the tablespace fix and a manual re-run; resolved by 08:10.",
        mentions={"systems": ["sys_reconlegacy"]},
    ))
    B.append(Beat(
        "re_ticket_2", "recon", "ticket", at(2026, 9, 16, 11, 0), c["head_it"],
        title="ReconLegacy: migrate off Oracle 11g (extended support ending)", ticket_key="ITOPS-262",
        ticket_status="open", assignee=c["recon_expert"],
        brief=("Sam opens a migration ticket. Priya comments: the Perl modules (especially FX adjustment) are known only "
               "to her, estimate about 3 months, recommends pairing someone with her before any migration."),
        mentions={"systems": ["sys_reconlegacy"]},
    ))
    B.append(Beat(
        "re_resign", "recon", "email", at(2026, 9, 24, 16, 30), c["recon_expert"], title="Resignation",
        recipients=[c["head_it"]],
        brief=("Priya resigns; last day 2026-10-09. Offers knowledge-transfer sessions on ReconLegacy and lists the "
               "undocumented parts: the FX adjustment module, the month-end manual override, and the Oracle "
               "housekeeping scripts. Warm and professional. 120-200 words."),
        mentions={"systems": ["sys_reconlegacy"]},
    ))
    B.append(Beat(
        "re_slack_5", "recon", "slack", at(2026, 9, 25, 9, 20), c["head_it"], channel=ch["#it-ops"], thread="re_t3",
        brief="Sam asks the team: besides Priya, who has ever worked on ReconLegacy internals? Need names today.",
        mentions={"systems": ["sys_reconlegacy"]},
    ))
    B.append(Beat(
        "re_slack_6", "recon", "slack", at(2026, 9, 25, 9, 44), c["junior_eng"], channel=ch["#it-ops"], thread="re_t3",
        brief="Kevin replies he only looked at it once for a ticket and does not understand the Perl jobs.",
        mentions={"systems": ["sys_reconlegacy"]},
    ))
    B.append(Beat(
        "re_kt_meeting", "recon", "meeting", at(2026, 10, 2, 14, 0), c["head_it"],
        title="ReconLegacy knowledge transfer #1",
        attendees=[c["recon_expert"], c["junior_eng"], c["head_it"], c["recon_analyst"]],
        brief=("Priya walks Kevin through the batch sequence, the FX adjustment module, and the month-end override; "
               "Kevin asks basic questions; Sam notes they need at least four more sessions before Oct 9. 400-700 words."),
        mentions={"systems": ["sys_reconlegacy", "sys_settlementengine", "sys_glcore"]},
    ))
    B.append(Beat(
        "re_farewell", "recon", "slack", at(2026, 10, 9, 16, 45), c["recon_expert"], channel=ch["#general"],
        brief="Priya's warm farewell after 13 years; mentions Kevin is the new go-to for ReconLegacy questions.",
        mentions={"systems": ["sys_reconlegacy"]},
    ))

    # ---------------------------------------------------------------- blast radius: PricingSvc (Tracks 1, 3)
    B.append(Beat(
        "pr_incident", "pricing", "ticket", at(2026, 8, 14, 9, 52), c["pricing_owner"],
        title="SEV1: PricingSvc serving stale prices (MarketDataFeed outage)", ticket_key="INC-0814",
        ticket_status="resolved", assignee=c["pricing_owner"],
        brief=("Incident record. Impact: RiskEngine VaR stale, OMS rejecting orders on price checks, TradeBlotter P&L "
               "frozen, PortfolioMgr rebalancing halted; next morning SettlementEngine delays and ReconLegacy breaks; "
               "ReportingBI dashboards showed wrong exposures. Root cause: expired TLS certificate on the "
               "MarketDataFeed vendor connection. Duration 3h40m. Comments from Leo with the timeline."),
        mentions={"systems": ["sys_pricingsvc", "sys_marketdatafeed", "sys_riskengine", "sys_oms", "sys_tradeblotter",
                              "sys_portfoliomgr", "sys_settlementengine", "sys_reconlegacy", "sys_reportingbi"]},
    ))
    inc = [
        ("pr_slack_1", (8, 14, 9, 47), "pricing_owner", "Leo declares a SEV1: PricingSvc prices are stale since 09:31."),
        ("pr_slack_2", (8, 14, 9, 55), "head_trading", "Daniel: OMS is rejecting orders on price checks, the desk is "
         "blind, TradeBlotter P&L frozen."),
        ("pr_slack_3", (8, 14, 10, 3), "head_risk", "Owen: RiskEngine VaR is stale, cannot monitor limits; asks for ETA."),
        ("pr_slack_4", (8, 14, 10, 20), "head_ops", "Ravi warns SettlementEngine and tomorrow's ReconLegacy run will "
         "be affected if prices are not backfilled."),
        ("pr_slack_5", (8, 14, 13, 15), "pricing_owner", "Leo: resolved, the vendor certificate was renewed; backfill "
         "running; postmortem to follow."),
    ]
    for bid, (m, d, hh, mm), role, brief in inc:
        B.append(Beat(bid, "pricing", "slack", at(2026, m, d, hh, mm), c[role], channel=ch["#incidents"],
                      thread="pr_inc", brief=brief, mentions={"systems": ["sys_pricingsvc"]}))
    B.append(Beat(
        "pr_postmortem", "pricing", "doc", at(2026, 8, 19, 15, 0), c["pricing_owner"],
        title="Postmortem: PricingSvc stale prices (2026-08-14)", doc_type="postmortem",
        brief=("Blameless postmortem: timeline, root cause (expired vendor TLS certificate on MarketDataFeed), "
               "downstream impact chain (RiskEngine, OMS, TradeBlotter, PortfolioMgr, then SettlementEngine, "
               "ReportingBI, ReconLegacy), action items (staleness alarms, certificate monitoring, secondary vendor, "
               "graceful degradation in OMS). 450-700 words."),
        mentions={"systems": ["sys_pricingsvc", "sys_marketdatafeed"]},
    ))
    B.append(Beat(
        "pr_arch", "pricing", "doc", at(2026, 9, 2, 11, 0), c["pricing_owner"],
        title="PricingSvc architecture and consumers", doc_type="architecture",
        brief=("Architecture overview: PricingSvc consumes MarketDataFeed; direct consumers RiskEngine, OMS, "
               "TradeBlotter, PortfolioMgr; indirect consumers via those systems. SLAs, caching, failover. "
               "400-600 words."),
        mentions={"systems": ["sys_pricingsvc", "sys_marketdatafeed", "sys_riskengine", "sys_oms", "sys_tradeblotter",
                              "sys_portfoliomgr"]},
    ))

    # ---------------------------------------------------------------- ownership change: KYCPipeline (Track 3)
    kyc_client = next(cl for cl in w.clients if cl.kind == "individual" and cl.id not in ring and cl.segment == "lending")
    B.append(Beat(
        "ky_slack_1", "kyc", "slack", at(2026, 7, 20, 10, 12), c["head_ops"], channel=ch["#kyc-pipeline"],
        brief="Ravi, as KYCPipeline owner, posts the weekly backlog: 140 open cases, SanctionsScreen latency is the "
              "bottleneck, asks IT for a fix.",
        mentions={"systems": ["sys_kycpipeline", "sys_sanctionsscreen"]},
    ))
    B.append(Beat(
        "ky_ticket", "kyc", "ticket", at(2026, 8, 12, 14, 20), c["head_ops"],
        title="KYCPipeline: DocVault upload timeouts blocking onboarding", ticket_key="KYC-045",
        ticket_status="resolved", assignee=c["head_ops"],
        brief=(f"Ravi (pipeline owner) reports DocVault upload timeouts. Example case: client {ph('CLIENT_NAME', kyc_client.id)} "
               f"stuck because their ID scan failed to upload; SSN {ph('CLIENT_SSN', kyc_client.id)} and date of "
               f"birth {ph('CLIENT_DOB', kyc_client.id)} verified manually. Comments: IT increases the timeout; resolved."),
        placeholders=[ph("CLIENT_NAME", kyc_client.id), ph("CLIENT_SSN", kyc_client.id), ph("CLIENT_DOB", kyc_client.id)],
        mentions={"systems": ["sys_kycpipeline", "sys_docvault"], "clients": [kyc_client.id]},
    ))
    B.append(Beat(
        "ky_plan_email", "kyc", "email", at(2026, 9, 15, 10, 0), c["coo"],
        title="KYCPipeline ownership moving to Compliance Engineering", recipients=[c["head_ops"], c["compliance_eng"]],
        cc=[c["cco"]],
        brief=("Linda announces the plan: ownership of KYCPipeline moves from Operations (Ravi Patel) to Compliance "
               "Engineering (Jenna Cho) effective 2026-10-06; Ravi remains escalation contact through October; asks "
               "for a handover checklist. 120-200 words."),
        mentions={"systems": ["sys_kycpipeline"]},
    ))
    B.append(Beat(
        "ky_announce", "kyc", "slack", at(2026, 10, 6, 9, 0), c["coo"], channel=ch["#general"],
        brief="Linda: as of today the KYCPipeline is owned by Jenna Cho's Compliance Engineering team; thanks Ravi.",
        mentions={"systems": ["sys_kycpipeline"]},
    ))
    B.append(Beat(
        "ky_jenna", "kyc", "slack", at(2026, 10, 7, 10, 30), c["compliance_eng"], channel=ch["#kyc-pipeline"],
        brief="Jenna's first update as owner: priorities (SanctionsScreen latency, DocVault retries), office hours.",
        mentions={"systems": ["sys_kycpipeline", "sys_sanctionsscreen", "sys_docvault"]},
    ))
    return B


def _brightwater(w: World) -> list[Beat]:
    c = w.cast
    ch = {x.name: x.id for x in w.channels}
    refs = w.refs
    ring, racc, rcp = refs["ring_clients"], refs["ring_accounts"], refs["ring_counterparties"]
    cps = w.index("counterparty")
    o1, o2 = cps[rcp["origin1"]], cps[rcp["origin2"]]  # type: ignore[index]
    B = _fraud_onboarding(w, "member")
    B.append(Beat(
        "fr_alert_0412", "fraud_ring", "ticket", at(2026, 9, 25, 10, 30), c["aml_analyst"],
        title="BSAWatch alert: inbound wire $22,000.00", ticket_key="BSA-0412", ticket_status="in_progress",
        assignee=c["aml_analyst"],
        brief=(f"Alert for transaction {refs['ring_wire_1']}: inbound wire $22,000.00 from {o1.name}, originator "  # type: ignore[attr-defined]
               f"account {ph('CP_ACCOUNT', o1.id) if o1.account_number else ph('CP_IBAN', o1.id)}, to member account "  # type: ignore[attr-defined]
               f"{ph('ACCOUNT_NUMBER', racc[0])} ({ph('CLIENT_NAME', ring[0])}). Monica comments that funds moved to "
               "two other member accounts within hours."),
        mentions={"clients": [ring[0]], "accounts": [racc[0]], "transactions": [refs["ring_wire_1"]]},
    ))
    B.append(Beat(
        "fr_alert_0419", "fraud_ring", "ticket", at(2026, 10, 3, 11, 40), c["aml_analyst"],
        title="BSAWatch alert: inbound wire $14,750.00", ticket_key="BSA-0419", ticket_status="open",
        assignee=c["aml_analyst"],
        brief=(f"Alert for transaction {refs['ring_wire_2']}: inbound wire $14,750.00 from {o2.name} into member "  # type: ignore[attr-defined]
               f"account {ph('ACCOUNT_NUMBER', racc[2])} ({ph('CLIENT_NAME', ring[2])}). No comments yet."),
        mentions={"clients": [ring[2]], "accounts": [racc[2]], "transactions": [refs["ring_wire_2"]]},
    ))
    B.append(Beat(
        "re_slack_1", "recon", "slack", at(2026, 8, 5, 7, 40), c["head_ops"], channel=ch["#it"], thread="bw_r1",
        brief="Rita: ShareRecon flagged 6 GL differences overnight, is it the scripts again?",
        mentions={"systems": ["sys_sharerecon"]},
    ))
    B.append(Beat(
        "re_slack_2", "recon", "slack", at(2026, 8, 5, 8, 2), c["recon_expert"], channel=ch["#it"], thread="bw_r1",
        brief="Hector explains a CoreBanking extract arrived late; he patched the cron and re-ran ShareRecon.",
        mentions={"systems": ["sys_sharerecon", "sys_corebanking"]},
    ))
    B.append(Beat(
        "re_doc", "recon", "doc", at(2026, 7, 14, 10, 0), c["recon_expert"], title="ShareRecon scripts - operator notes",
        doc_type="runbook",
        brief="Operator notes for the nightly ShareRecon scripts against CoreBanking: schedule, inputs, known failure "
              "modes, re-run steps. 300-450 words.",
        mentions={"systems": ["sys_sharerecon", "sys_corebanking"]},
    ))
    return B
