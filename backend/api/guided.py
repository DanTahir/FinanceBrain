""""Try these" guided questions, one set per tenant, tagged with the hackathon track each one showcases.

``cacheable`` questions are read-only Q&A: their answers are cached per (tenant, persona, live-feed position) and
replayed for later visitors. Questions that write to the graph (open cases, propose actions, remember things) always
run live, because replaying them would claim work that did not happen.
"""

from __future__ import annotations

from backend.api.guardrails import norm_question

GUIDED: list[dict] = [
    # ---- Halcyon Ridge Capital: Track 3 (company brain)
    {"id": "h_crypto", "tenant": "halcyon", "track": "T3", "title": "Trace a decision",
     "question": "Why did we stop onboarding crypto clients?", "persona": "compliance_officer", "agent": "brain",
     "cacheable": True, "hint": "Decision trail: regulator email, compliance ticket, Slack debate, board memo."},
    {"id": "h_kyc", "tenant": "halcyon", "track": "T3", "title": "Ownership over time",
     "question": "Who owns the KYC pipeline now, and who owned it in August?", "persona": "engineer",
     "agent": "brain", "cacheable": True,
     "hint": "Bitemporal OWNS edges. Replay the live feed past Oct 6 and ask again."},
    {"id": "h_recon", "tenant": "halcyon", "track": "T3", "title": "Who knows what",
     "question": "Who should I ask about ReconLegacy, and what is our risk if that person leaves?",
     "persona": "engineer", "agent": "brain", "cacheable": True,
     "hint": "Derived KNOWS_ABOUT expertise vs formal ownership (bus factor)."},
    {"id": "h_pricing", "tenant": "halcyon", "track": "T3", "title": "Blast radius",
     "question": "What breaks if PricingSvc goes down?", "persona": "engineer", "agent": "brain", "cacheable": True,
     "hint": "Multi-hop DEPENDS_ON traversal with owners and on-call experts."},
    # ---- Halcyon: Track 1 (agentic reasoning and actions)
    {"id": "h_wire", "tenant": "halcyon", "track": "T1", "title": "Investigate a flagged wire",
     "question": "Investigate the flagged $48k wire and tell me what we should do about it.",
     "persona": "aml_analyst", "agent": "investigator", "cacheable": False,
     "hint": "Shared PII tokens expose a fraud ring; the Investigator opens a case, proposes freezes and a SAR, "
             "and hands off to Compliance."},
    {"id": "h_rings", "tenant": "halcyon", "track": "T1", "title": "Find fraud rings",
     "question": "Are there any fraud rings hiding behind shared phone numbers, addresses or devices?",
     "persona": "aml_analyst", "agent": "investigator", "cacheable": False,
     "hint": "WCC over clients and pseudonymous PII tokens, then money-flow betweenness."},
    {"id": "h_falcon", "tenant": "halcyon", "track": "T1", "title": "Information-barrier breach",
     "question": "Has Project Falcon leaked outside the deal team?", "persona": "compliance_officer",
     "agent": "compliance", "cacheable": False,
     "hint": "Deal-wall check across channels and recipients; audit-logged surveillance read."},
    {"id": "h_falcon_probe", "tenant": "halcyon", "track": "T1", "title": "Try to get past the wall",
     "question": "What is Project Falcon and who is working on it?", "persona": "engineer", "agent": "brain",
     "cacheable": True, "hint": "An engineer outside the deal team gets nothing: the ACL is enforced in Cypher."},
    # ---- Halcyon: Track 2 (memory and coordination)
    {"id": "h_pref", "tenant": "halcyon", "track": "T2", "title": "Teach it a preference",
     "question": "Remember that I prefer answers as short bullet points.", "persona": "aml_analyst",
     "agent": "auto", "cacheable": False,
     "hint": "Stored in your private memory graph; check the Memory view, then start a new chat."},
    {"id": "h_recall", "tenant": "halcyon", "track": "T2", "title": "Pick up where we left off",
     "question": "What were we working on last time, and what is still open?", "persona": "aml_analyst",
     "agent": "auto", "cacheable": False, "hint": "Episodic memory: sessions, turns and open threads."},
    {"id": "h_playbook", "tenant": "halcyon", "track": "T2", "title": "Procedural memory",
     "question": "Which playbook do we follow for a suspicious wire, and has it been used before?",
     "persona": "aml_analyst", "agent": "auto", "cacheable": False,
     "hint": "Playbooks the Librarian learned from earlier investigations."},
    # ---- Brightwater Credit Union: tenant isolation and a second company
    {"id": "b_isolation", "tenant": "brightwater", "track": "T2", "title": "Tenant isolation",
     "question": "What is Project Falcon?", "persona": "lender", "agent": "brain", "cacheable": True,
     "hint": "Halcyon's deal does not exist here: each tenant is a separate graph."},
    {"id": "b_loandesk", "tenant": "brightwater", "track": "T3", "title": "Who knows what",
     "question": "Who knows the most about LoanDesk, and who formally owns it?", "persona": "engineer",
     "agent": "brain", "cacheable": True, "hint": "Derived expertise vs formal ownership."},
    {"id": "b_core", "tenant": "brightwater", "track": "T3", "title": "Blast radius",
     "question": "What breaks if CoreBanking goes down?", "persona": "engineer", "agent": "brain", "cacheable": True,
     "hint": "Dependency traversal in the second tenant's graph."},
]

_BY_TEXT = {(g["tenant"], norm_question(g["question"])): g for g in GUIDED}


def guided_for(tenant: str) -> list[dict]:
    return [g for g in GUIDED if g["tenant"] == tenant]


def find_guided(tenant: str, question: str) -> dict | None:
    return _BY_TEXT.get((tenant, norm_question(question)))
