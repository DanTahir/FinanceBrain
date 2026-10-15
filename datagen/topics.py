"""Background-traffic configuration: channel topics, email/ticket/doc/meeting seeds per tenant.

Hook pools tell the planner which entities to offer a writer:
  systems:<team|all|recon|kyc>, clients:<segment|any>, clients_kyc:any, counterparties, flagged
"""

from __future__ import annotations

# channel name -> (weight, topic seeds, hook pools, note)
CHANNELS: dict[str, dict[str, tuple[int, list[str], list[str], str]]] = {
    "halcyon": {
        "#general": (6, ["office move to the 14th floor", "Q3 town hall recap", "new expense tool rollout",
                         "benefits enrollment reminder", "phishing test results", "welcome new joiners",
                         "charity volunteer day", "building access badge changes"], [], ""),
        "#random": (5, ["lunch spots near the office", "fantasy football league", "marathon training",
                        "the broken espresso machine", "weekend plans", "book club pick", "commute horror stories"], [], ""),
        "#trading-desk": (16, ["FOMC decision market color", "OMS latency complaints", "working a large block order",
                               "quarter-end rebalancing flows", "credit spreads widening", "TradeBlotter P&L mismatch",
                               "earnings season volatility", "Treasury auction tail", "FX hedging for a client",
                               "options expiry positioning"], ["systems:trading"],
                          "Traders are terse and use desk jargon. Never mention M&A deals, deal codenames, or "
                          "Northwind."),
        "#wealth-team": (12, ["client review meeting prep", "model portfolio rebalance", "fee schedule exception",
                              "tax-loss harvesting ideas", "advisor coverage during vacation", "estate planning referral",
                              "new alternatives fund on the platform", "client complaint about statements"],
                         ["clients:wealth", "systems:wealth"], ""),
        "#lending": (10, ["credit committee prep", "covenant breach on a commercial loan", "HELOC rate change",
                          "appraisal delays", "loan pipeline numbers", "underwriting exception",
                          "construction draw request"], ["clients:lending", "clients:commercial"], ""),
        "#compliance": (10, ["trade surveillance alerts", "KYC refresh backlog", "personal trading pre-clearance",
                             "marketing material review", "regulatory exam document request", "AML training completion",
                             "sanctions list update", "false-positive FraudScore alerts"],
                        ["clients:any", "counterparties", "flagged"],
                        "Do not discuss FRAUD-2291 or FRAUD-2307 (covered elsewhere)."),
        "#risk": (8, ["VaR backtesting exceptions", "counterparty limit review", "stress scenario update",
                      "model validation findings", "liquidity coverage numbers", "concentration limits"],
                  ["systems:risk"], ""),
        "#it-ops": (12, ["patching window this weekend", "AuthSSO migration", "laptop refresh", "cloud cost review",
                         "vulnerability scan results", "DR test planning", "access recertification",
                         "DataLake pipeline delays", "certificate expiry tracking"], ["systems:it"], ""),
        "#incidents": (8, ["AuthSSO login failures", "DataLake ingestion lag", "PaymentsGateway ACH file rejected",
                           "DocVault slow uploads", "OMS FIX session drop", "ClientHub outage",
                           "SanctionsScreen timeouts", "CreditScoring batch failure"], ["systems:all"],
                       "Each conversation is one small SEV2/SEV3 incident with detection, impact, and resolution. "
                       "Do not describe the PricingSvc outage of 2026-08-14."),
        "#recon": (7, ["morning breaks report", "unmatched settlement items", "GL suspense account balance",
                       "FX trade breaks", "month-end recon sign-off", "late settlement file"], ["systems:recon"],
                   "Ops analysts ask about reconciliation breaks. Priya Raman (Staff Engineer) is the only person who "
                   "answers technical questions about ReconLegacy internals (Perl jobs, Oracle 11g, cron schedule); "
                   "nobody else knows them."),
        "#kyc-pipeline": (6, ["onboarding backlog", "SanctionsScreen false positives", "DocVault upload retries",
                              "periodic KYC refresh campaign", "beneficial ownership forms"],
                          ["clients_kyc:any", "systems:kyc"],
                          "Before 2026-10-06 KYCPipeline is owned by Ravi Patel (Operations). From 2026-10-06 it is "
                          "owned by Jenna Cho (Compliance Engineering)."),
        "#crypto-policy": (3, ["blockchain analytics vendor evaluation", "existing crypto-holding clients",
                               "peer firms' approach"], [],
                           "Onboarding of new digital-asset clients is paused by board decision from 2026-08-22 until "
                           "2026-10-12."),
        "#ma-team": (4, ["Project Osprey buyer outreach", "Project Kestrel lender meetings", "league table submission",
                         "pitch book formatting", "analyst staffing"], [],
                     "Project Osprey and Project Kestrel may be discussed. Never mention Project Falcon or Northwind."),
        "#project-falcon": (3, ["diligence request list", "management presentation logistics", "model updates",
                                "legal diligence findings"], [],
                            "Deal team only: Project Falcon is buy-side advisory to Meridian Freight Partners on the "
                            "acquisition of Northwind Logistics Group (~$1.2B, announce Nov 2)."),
    },
    "brightwater": {
        "#general": (5, ["branch hours change", "member appreciation day", "benefits reminder", "new hires",
                         "fraud awareness week"], [], ""),
        "#member-services": (10, ["member called about a card decline", "online banking lockouts", "dispute process",
                                  "mobile deposit holds", "address change requests", "teller cash limits"],
                             ["clients:member"], ""),
        "#lending": (6, ["auto loan pipeline", "rate sheet update", "underwriting exception", "dealer complaints"],
                     ["clients:lending"], ""),
        "#bsa-aml": (4, ["CTR filings", "structuring alerts", "OFAC hits", "monthly BSA report"],
                     ["clients:any", "flagged"], "Do not discuss BSA-0412 or BSA-0419 (covered elsewhere)."),
        "#it": (6, ["core banking vendor maintenance", "MFA rollout for members", "patching", "backup failures",
                    "printer and branch hardware"], ["systems:all"],
                "Hector Salas is the only person who understands the ShareRecon scripts."),
    },
}

# team -> (weight, seeds)
EMAIL_TEAMS: dict[str, dict[str, tuple[int, list[str]]]] = {
    "halcyon": {
        "exec": (4, ["Q3 board deck preparation", "2027 headcount plan", "office lease renewal", "all-hands agenda",
                     "vendor contract approvals", "client experience survey results"]),
        "trading": (8, ["risk limit breach follow-up", "broker commission review", "best execution quarterly report",
                        "new FIX connection to a venue", "trade error write-up", "desk P&L attribution"]),
        "wealth": (9, ["client review meeting prep", "model portfolio changes", "fee schedule exceptions",
                       "advisor coverage for vacation", "estate planning referral", "custody platform migration"]),
        "lending": (7, ["credit committee agenda", "covenant breach on a commercial loan", "HELOC rate change",
                        "loan pipeline report", "appraisal delays", "underwriting exception approval"]),
        "ma": (3, ["Project Osprey buyer list", "Project Kestrel lender meetings", "pitch book for a new mandate",
                   "league table submission"]),
        "compliance": (8, ["quarterly AML training completion", "trade surveillance alert review",
                           "regulatory exam document request", "KYC refresh backlog",
                           "personal trading pre-clearance reminder", "marketing material review"]),
        "risk": (6, ["VaR backtesting exceptions", "counterparty limit review", "stress test scenario update",
                     "model validation findings", "liquidity coverage report"]),
        "ops": (7, ["failed settlement follow-up", "wire cutoff time change", "month-end close checklist",
                    "corporate action processing", "returned ACH items", "SWIFT payment tracking"]),
        "it": (8, ["weekend patching window", "AuthSSO migration", "laptop refresh", "cloud cost review",
                   "vulnerability scan results", "DR test planning", "access recertification"]),
    },
    "brightwater": {
        "exec": (2, ["board packet", "strategic plan update", "NCUA exam prep"]),
        "member": (5, ["member complaint follow-up", "branch staffing", "new account promotions", "dispute backlog"]),
        "lending": (4, ["auto loan rate change", "delinquency report", "dealer agreement renewal"]),
        "compliance": (3, ["BSA training", "Reg E dispute timelines", "exam document request"]),
        "it": (3, ["core banking upgrade", "MFA rollout", "vendor security questionnaire"]),
        "ops": (3, ["ACH returns", "card processor reconciliation", "month-end close"]),
    },
}

# project -> (teams, weight, seeds, hook pools)
TICKET_PROJECTS: dict[str, dict[str, tuple[list[str], int, list[str], list[str]]]] = {
    "halcyon": {
        "ITOPS": (["it"], 9, ["disk space alert", "access request", "certificate renewal", "job scheduler failure",
                             "API rate limiting", "database index maintenance", "VPN issues"], ["systems:it"]),
        "INC": (["it", "ops"], 4, ["login failures", "batch delay", "payment file rejected", "slow UI"], ["systems:all"]),
        "COMP": (["compliance"], 4, ["surveillance alert review", "KYC exception", "adverse media hit",
                                     "complaint log review"], ["clients:any", "counterparties"]),
        "KYC": (["ops", "compliance"], 4, ["identity document mismatch", "beneficial ownership missing",
                                           "periodic review overdue", "address verification failed"],
                ["clients_kyc:any", "systems:kyc"]),
        "OPS": (["ops"], 5, ["wire repair", "returned ACH", "failed settlement", "incorrect beneficiary details"],
                ["counterparties", "clients:any"]),
        "RISK": (["risk"], 2, ["limit breach", "model data issue", "scenario request"], ["systems:risk"]),
        "FRAUD": (["compliance"], 2, ["FraudScore alert triage (false positive)"], ["flagged"]),
    },
    "brightwater": {
        "IT": (["it"], 4, ["password resets", "branch PC failure", "core banking batch delay"], ["systems:all"]),
        "BSA": (["compliance"], 2, ["structuring alert triage", "CTR review"], ["flagged", "clients:any"]),
        "OPS": (["ops"], 3, ["ACH return", "card dispute", "wire repair"], ["counterparties", "clients:member"]),
        "LOAN": (["lending"], 2, ["missing title document", "payoff request"], ["clients_kyc:any"]),
    },
}

# (title, doc_type, author team, hook pool or "")
DOCS: dict[str, list[tuple[str, str, str, str]]] = {
    "halcyon": [
        ("Wire Transfer Operating Procedures", "procedure", "ops", "systems:ops"),
        ("AML Program Overview", "policy", "compliance", "systems:compliance"),
        ("KYC and CDD Standards", "policy", "compliance", "systems:kyc"),
        ("Personal Trading Policy", "policy", "compliance", ""),
        ("Business Continuity Plan", "plan", "it", "systems:all"),
        ("Incident Management Process", "procedure", "it", ""),
        ("Access Management Standard", "policy", "it", "systems:it"),
        ("OMS User Guide for Traders", "guide", "trading", "systems:trading"),
        ("Best Execution Policy", "policy", "trading", ""),
        ("Model Risk Management Policy", "policy", "risk", ""),
        ("Market Risk Limits Framework", "policy", "risk", "systems:risk"),
        ("Counterparty Credit Risk Methodology", "methodology", "risk", ""),
        ("Commercial Lending Credit Policy", "policy", "lending", ""),
        ("HELOC Product Guide", "guide", "lending", ""),
        ("Wealth Model Portfolio Guide", "guide", "wealth", "systems:wealth"),
        ("Client Onboarding Checklist", "procedure", "wealth", "systems:kyc"),
        ("Month-End Close Procedures", "procedure", "ops", "systems:recon"),
        ("Settlement Fails Playbook", "procedure", "ops", "systems:ops"),
        ("DataLake Architecture Overview", "architecture", "it", "systems:it"),
        ("AuthSSO Migration Plan", "plan", "it", "systems:it"),
        ("RiskEngine Methodology Notes", "methodology", "risk", "systems:risk"),
        ("SanctionsScreen Tuning Report", "report", "compliance", "systems:kyc"),
        ("Q3 Compliance Testing Report", "report", "compliance", ""),
        ("Vendor Management Policy", "policy", "exec", ""),
        ("Remote Work Policy", "policy", "exec", ""),
        ("Data Classification Standard", "policy", "it", ""),
        ("Disaster Recovery Test Results (July)", "report", "it", "systems:all"),
        ("FraudScore Model Card", "model_card", "compliance", "systems:compliance"),
        ("PaymentsGateway Runbook", "runbook", "ops", "systems:ops"),
        ("Expense and Travel Policy", "policy", "exec", ""),
    ],
    "brightwater": [
        ("BSA/AML Program", "policy", "compliance", ""),
        ("Member Identification Program", "policy", "compliance", ""),
        ("Card Dispute Procedures", "procedure", "ops", "systems:ops"),
        ("Auto Lending Guidelines", "guide", "lending", ""),
        ("Online Banking Support Guide", "guide", "member", "systems:it"),
        ("Incident Response Plan", "plan", "it", "systems:all"),
        ("Vendor Management Policy", "policy", "exec", ""),
    ],
}

# (title, team or "all")
MEETINGS: dict[str, list[tuple[str, str]]] = {
    "halcyon": [
        ("Weekly trading desk huddle", "trading"), ("Wealth advisors monthly", "wealth"), ("Credit committee", "lending"),
        ("Compliance team weekly", "compliance"), ("Risk committee monthly", "risk"), ("Ops daily standup", "ops"),
        ("IT change advisory board", "it"), ("Q3 town hall", "all"), ("Vendor review: market data", "it"),
        ("KYC backlog review", "compliance"), ("Project Osprey kickoff", "ma"), ("Executive staff meeting", "exec"),
        ("Incident review: AuthSSO outage", "it"), ("Lending pipeline review", "lending"),
        ("Quarterly business review - Wealth", "wealth"),
    ],
    "brightwater": [
        ("Management team weekly", "exec"), ("Member services huddle", "member"), ("Loan committee", "lending"),
        ("IT vendor check-in", "it"), ("BSA committee", "compliance"),
    ],
}

CRM_SEEDS = ["annual portfolio review", "risk tolerance update", "address change request", "phone number update",
             "beneficiary designation change", "loan renewal discussion", "fee complaint", "referral from an existing client",
             "liquidity needs for a home purchase", "estate planning check-in", "interest in alternative investments",
             "required minimum distribution planning", "card replacement and travel notice", "business expansion financing"]
