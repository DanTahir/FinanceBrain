# Isolation eval

Generated 2026-10-15T22:15:11.659393+00:00 (env `dev`)

**155/155 checks passed**

| Category | Checks | Passed | Failed |
|---|---|---|---|
| tenant_isolation | 52 | 52 | 0 |
| information_barrier | 73 | 73 | 0 |
| memory_privacy | 10 | 10 | 0 |
| auth | 20 | 20 | 0 |
| **total** | 155 | 155 | 0 |

## tenant_isolation

| | Check | Detail |
|---|---|---|
| PASS | halcyon/compliance_officer: ToolContext graph is brain__halcyon | graph_name=brain__halcyon |
| PASS | halcyon/aml_analyst: ToolContext graph is brain__halcyon | graph_name=brain__halcyon |
| PASS | halcyon/ma_banker: ToolContext graph is brain__halcyon | graph_name=brain__halcyon |
| PASS | halcyon/engineer: ToolContext graph is brain__halcyon | graph_name=brain__halcyon |
| PASS | brightwater/compliance_officer: ToolContext graph is brain__brightwater | graph_name=brain__brightwater |
| PASS | brightwater/aml_analyst: ToolContext graph is brain__brightwater | graph_name=brain__brightwater |
| PASS | brightwater/lender: ToolContext graph is brain__brightwater | graph_name=brain__brightwater |
| PASS | brightwater/engineer: ToolContext graph is brain__brightwater | graph_name=brain__brightwater |
| PASS | brightwater/compliance_officer: search 'ReconLegacy Perl batch reconciliation' has no 'reconlegacy' | 10 results, 8 facts; 'reconlegacy' in results=[] facts=0 |
| PASS | brightwater/compliance_officer: search 'PricingSvc outage blast radius' has no 'pricingsvc' | 10 results, 0 facts; 'pricingsvc' in results=[] facts=0 |
| PASS | brightwater/compliance_officer: search 'Project Falcon acquisition diligence' has no 'falcon' | 15 results, 0 facts; 'falcon' in results=[] facts=0 |
| PASS | brightwater/compliance_officer: sys_reconlegacy does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_reconlegacy' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/compliance_officer: deal_falcon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'deal_falcon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/compliance_officer: sys_pricingsvc does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_pricingsvc' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/compliance_officer: find_entities 'ReconLegacy' finds nothing | find_entities('ReconLegacy') -> 0 hits, matching=[] |
| PASS | brightwater/aml_analyst: search 'ReconLegacy Perl batch reconciliation' has no 'reconlegacy' | 12 results, 3 facts; 'reconlegacy' in results=[] facts=0 |
| PASS | brightwater/aml_analyst: search 'PricingSvc outage blast radius' has no 'pricingsvc' | 10 results, 0 facts; 'pricingsvc' in results=[] facts=0 |
| PASS | brightwater/aml_analyst: search 'Project Falcon acquisition diligence' has no 'falcon' | 16 results, 0 facts; 'falcon' in results=[] facts=0 |
| PASS | brightwater/aml_analyst: sys_reconlegacy does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_reconlegacy' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/aml_analyst: deal_falcon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'deal_falcon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/aml_analyst: sys_pricingsvc does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_pricingsvc' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/aml_analyst: find_entities 'ReconLegacy' finds nothing | find_entities('ReconLegacy') -> 0 hits, matching=[] |
| PASS | brightwater/lender: search 'ReconLegacy Perl batch reconciliation' has no 'reconlegacy' | 10 results, 7 facts; 'reconlegacy' in results=[] facts=0 |
| PASS | brightwater/lender: search 'PricingSvc outage blast radius' has no 'pricingsvc' | 14 results, 0 facts; 'pricingsvc' in results=[] facts=0 |
| PASS | brightwater/lender: search 'Project Falcon acquisition diligence' has no 'falcon' | 15 results, 0 facts; 'falcon' in results=[] facts=0 |
| PASS | brightwater/lender: sys_reconlegacy does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_reconlegacy' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/lender: deal_falcon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'deal_falcon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/lender: sys_pricingsvc does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_pricingsvc' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/lender: find_entities 'ReconLegacy' finds nothing | find_entities('ReconLegacy') -> 0 hits, matching=[] |
| PASS | brightwater/engineer: search 'ReconLegacy Perl batch reconciliation' has no 'reconlegacy' | 10 results, 8 facts; 'reconlegacy' in results=[] facts=0 |
| PASS | brightwater/engineer: search 'PricingSvc outage blast radius' has no 'pricingsvc' | 11 results, 0 facts; 'pricingsvc' in results=[] facts=0 |
| PASS | brightwater/engineer: search 'Project Falcon acquisition diligence' has no 'falcon' | 16 results, 0 facts; 'falcon' in results=[] facts=0 |
| PASS | brightwater/engineer: sys_reconlegacy does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_reconlegacy' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/engineer: deal_falcon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'deal_falcon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/engineer: sys_pricingsvc does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_pricingsvc' is visible to you. Use search_knowledge to find names/ids. |
| PASS | brightwater/engineer: find_entities 'ReconLegacy' finds nothing | find_entities('ReconLegacy') -> 0 hits, matching=[] |
| PASS | halcyon/compliance_officer: search 'ShareRecon share reconciliation' has no 'sharerecon' | 14 results, 0 facts; 'sharerecon' in results=[] facts=0 |
| PASS | halcyon/compliance_officer: sys_sharerecon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_sharerecon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/compliance_officer: find_entities 'ShareRecon' finds nothing | find_entities('ShareRecon') -> 0 hits, matching=[] |
| PASS | halcyon/aml_analyst: search 'ShareRecon share reconciliation' has no 'sharerecon' | 16 results, 0 facts; 'sharerecon' in results=[] facts=0 |
| PASS | halcyon/aml_analyst: sys_sharerecon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_sharerecon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/aml_analyst: find_entities 'ShareRecon' finds nothing | find_entities('ShareRecon') -> 0 hits, matching=[] |
| PASS | halcyon/ma_banker: search 'ShareRecon share reconciliation' has no 'sharerecon' | 15 results, 0 facts; 'sharerecon' in results=[] facts=0 |
| PASS | halcyon/ma_banker: sys_sharerecon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_sharerecon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/ma_banker: find_entities 'ShareRecon' finds nothing | find_entities('ShareRecon') -> 0 hits, matching=[] |
| PASS | halcyon/engineer: search 'ShareRecon share reconciliation' has no 'sharerecon' | 14 results, 0 facts; 'sharerecon' in results=[] facts=0 |
| PASS | halcyon/engineer: sys_sharerecon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'sys_sharerecon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: find_entities 'ShareRecon' finds nothing | find_entities('ShareRecon') -> 0 hits, matching=[] |
| PASS | control: halcyon/engineer resolves sys_reconlegacy | resolve={'label': 'System', 'id': 'sys_reconlegacy', 'name': 'ReconLegacy'} |
| PASS | control: halcyon/engineer resolves sys_pricingsvc | resolve={'label': 'System', 'id': 'sys_pricingsvc', 'name': 'PricingSvc'} |
| PASS | control: brightwater/engineer resolves sys_sharerecon | resolve={'label': 'System', 'id': 'sys_sharerecon', 'name': 'ShareRecon'} |
| PASS | control: halcyon/engineer search finds ReconLegacy content | halcyon results mentioning reconlegacy: ['slack-01259', 'slack-00918', 'slack-00140', 'ticket-00115', 'slack-00987'] |

## information_barrier

| | Check | Detail |
|---|---|---|
| PASS | ground truth lists MNPI documents | 50 mnpi docs |
| PASS | halcyon/engineer: search 'Project Falcon valuation' returns no MNPI | 14 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/engineer: search 'Falcon deal terms' returns no MNPI | 14 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/engineer: search 'Project Falcon diligence meeting' returns no MNPI | 18 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/engineer: search 'acquisition target purchase price per share' returns no MNPI | 18 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/engineer: search with meeting-00012 vector returns no MNPI | 14 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/engineer: deal_falcon does not resolve | resolve=None; get_entity: ToolError: No entity matching 'deal_falcon' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: 'Project Falcon' does not resolve | resolve('Project Falcon')=None |
| PASS | halcyon/engineer: visible_docs(mnpi_docs) is empty | visible mnpi docs: [] of 50 |
| PASS | halcyon/engineer: 'Project Falcon' is a restricted term | is_restricted('Project Falcon')=True |
| PASS | halcyon/aml_analyst: search 'Project Falcon valuation' returns no MNPI | 15 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/aml_analyst: search 'Falcon deal terms' returns no MNPI | 14 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/aml_analyst: search 'Project Falcon diligence meeting' returns no MNPI | 17 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/aml_analyst: search 'acquisition target purchase price per share' returns no MNPI | 14 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | halcyon/aml_analyst: search with meeting-00012 vector returns no MNPI | 13 results; mnpi ids=[] sensitivity=MNPI=[] 'falcon' in title/text=[] fact cites mnpi=[] |
| PASS | control room: halcyon/aml_analyst (Compliance team) sees the deal entity but no MNPI documents | deal resolves=True; visible mnpi docs=0 of 50 |
| PASS | halcyon/aml_analyst: visible_docs(mnpi_docs) is empty | visible mnpi docs: [] of 50 |
| PASS | halcyon/engineer: get_entity(doc-00014) is denied | ToolError: No entity matching 'doc-00014' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(email-00301) is denied | ToolError: No entity matching 'email-00301' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(meeting-00012) is denied | ToolError: No entity matching 'meeting-00012' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00543) is denied | ToolError: No entity matching 'slack-00543' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00545) is denied | ToolError: No entity matching 'slack-00545' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00546) is denied | ToolError: No entity matching 'slack-00546' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00548) is denied | ToolError: No entity matching 'slack-00548' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00551) is denied | ToolError: No entity matching 'slack-00551' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00552) is denied | ToolError: No entity matching 'slack-00552' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00554) is denied | ToolError: No entity matching 'slack-00554' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00555) is denied | ToolError: No entity matching 'slack-00555' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00557) is denied | ToolError: No entity matching 'slack-00557' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00558) is denied | ToolError: No entity matching 'slack-00558' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00559) is denied | ToolError: No entity matching 'slack-00559' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00560) is denied | ToolError: No entity matching 'slack-00560' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00561) is denied | ToolError: No entity matching 'slack-00561' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00562) is denied | ToolError: No entity matching 'slack-00562' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00563) is denied | ToolError: No entity matching 'slack-00563' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00704) is denied | ToolError: No entity matching 'slack-00704' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00705) is denied | ToolError: No entity matching 'slack-00705' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00821) is denied | ToolError: No entity matching 'slack-00821' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00822) is denied | ToolError: No entity matching 'slack-00822' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00823) is denied | ToolError: No entity matching 'slack-00823' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00824) is denied | ToolError: No entity matching 'slack-00824' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00825) is denied | ToolError: No entity matching 'slack-00825' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00826) is denied | ToolError: No entity matching 'slack-00826' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00827) is denied | ToolError: No entity matching 'slack-00827' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00828) is denied | ToolError: No entity matching 'slack-00828' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00829) is denied | ToolError: No entity matching 'slack-00829' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00830) is denied | ToolError: No entity matching 'slack-00830' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00831) is denied | ToolError: No entity matching 'slack-00831' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00832) is denied | ToolError: No entity matching 'slack-00832' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00833) is denied | ToolError: No entity matching 'slack-00833' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00834) is denied | ToolError: No entity matching 'slack-00834' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00942) is denied | ToolError: No entity matching 'slack-00942' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00943) is denied | ToolError: No entity matching 'slack-00943' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00944) is denied | ToolError: No entity matching 'slack-00944' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00945) is denied | ToolError: No entity matching 'slack-00945' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00946) is denied | ToolError: No entity matching 'slack-00946' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00947) is denied | ToolError: No entity matching 'slack-00947' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00948) is denied | ToolError: No entity matching 'slack-00948' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00949) is denied | ToolError: No entity matching 'slack-00949' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00950) is denied | ToolError: No entity matching 'slack-00950' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00951) is denied | ToolError: No entity matching 'slack-00951' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00952) is denied | ToolError: No entity matching 'slack-00952' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00953) is denied | ToolError: No entity matching 'slack-00953' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00954) is denied | ToolError: No entity matching 'slack-00954' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-00955) is denied | ToolError: No entity matching 'slack-00955' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-01172) is denied | ToolError: No entity matching 'slack-01172' is visible to you. Use search_knowledge to find names/ids. |
| PASS | halcyon/engineer: get_entity(slack-01399) is denied | ToolError: No entity matching 'slack-01399' is visible to you. Use search_knowledge to find names/ids. |
| PASS | control: halcyon/ma_banker (deal team) search returns MNPI docs | mnpi docs returned to deal team: ['meeting-00012', 'slack-01172', 'slack-00543', 'doc-00014', 'slack-00821', 'slack-00955', 'slack-00545', 'slack-00546'] |
| PASS | control: halcyon/ma_banker sees meeting-00012 | meeting-00012 in results: True |
| PASS | control: halcyon/ma_banker visible_docs(mnpi_docs) non-empty | 48 of 50 mnpi docs visible to deal team |
| PASS | control: halcyon/ma_banker resolves deal_falcon | resolve={'label': 'Deal', 'id': 'deal_falcon', 'name': 'Project Falcon'} |
| PASS | control: halcyon/compliance_officer get_entity(deal_falcon) unrestricted | codename restricted: False |
| PASS | control: halcyon/compliance_officer resolves #project-falcon | resolve('#project-falcon') -> ch_project-falcon |

## memory_privacy

| | Check | Detail |
|---|---|---|
| PASS | memory graph names differ per (tenant, persona, user) | mem__halcyon__aml_analyst__eval_iso_a_3a7c76b8; mem__halcyon__aml_analyst__eval_iso_b_3a7c76b8; mem__halcyon__engineer__eval_iso_a_3a7c76b8; mem__brightwater__aml_analyst__eval_iso_a_3a7c76b8 |
| PASS | user A stores a distinctive memory | stored 1 memory in mem__halcyon__aml_analyst__eval_iso_a_3a7c76b8 |
| PASS | control: user A recalls own memory | user A recall: 1 facts, marker hits=1 |
| PASS | control: user A facts() lists own memory | user A facts(): 1, marker hits=1 |
| PASS | other user, same tenant+persona: memory not recalled | mem__halcyon__aml_analyst__eval_iso_b_3a7c76b8: recall marker hits=0, facts() marker hits=0 |
| PASS | other user, same tenant+persona: no memory graph created by reads | mem__halcyon__aml_analyst__eval_iso_b_3a7c76b8 exists=False |
| PASS | same user, other persona: memory not recalled | mem__halcyon__engineer__eval_iso_a_3a7c76b8: recall marker hits=0, facts() marker hits=0 |
| PASS | same user, other persona: no memory graph created by reads | mem__halcyon__engineer__eval_iso_a_3a7c76b8 exists=False |
| PASS | same user, other tenant: memory not recalled | mem__brightwater__aml_analyst__eval_iso_a_3a7c76b8: recall marker hits=0, facts() marker hits=0 |
| PASS | same user, other tenant: no memory graph created by reads | mem__brightwater__aml_analyst__eval_iso_a_3a7c76b8 exists=False |

## auth

| | Check | Detail |
|---|---|---|
| PASS | make_auth('acme', 'engineer') raises AuthError | AuthError: unknown tenant 'acme' |
| PASS | make_auth('halcyon', 'ceo') raises AuthError | AuthError: unknown persona 'ceo' for tenant 'halcyon' |
| PASS | make_auth('brightwater', 'ma_banker') raises AuthError | AuthError: unknown persona 'ma_banker' for tenant 'brightwater' |
| PASS | make_auth('halcyon', 'lender') raises AuthError | AuthError: unknown persona 'lender' for tenant 'halcyon' |
| PASS | halcyon token round-trips | tenant=halcyon persona=aml_analyst |
| PASS | verified halcyon token targets brain__halcyon | graph_name=brain__halcyon |
| PASS | halcyon signature cannot be re-targeted to brightwater | AuthError: bad token signature |
| PASS | forged persona/scope escalation rejected | AuthError: bad token signature |
| PASS | tampered signature rejected | AuthError: bad token signature |
| PASS | malformed token rejected | AuthError: malformed token |
| PASS | expired token rejected | AuthError: token expired |
| PASS | halcyon/compliance_officer: default scopes read+act+approve | scopes=['read', 'act', 'approve', 'reveal'] |
| PASS | halcyon/aml_analyst: default scopes act, no approve | scopes=['read', 'act'] |
| PASS | halcyon/ma_banker: default scopes read-only (no act/approve) | scopes=['read'] |
| PASS | halcyon/engineer: default scopes read-only (no act/approve) | scopes=['read'] |
| PASS | brightwater/compliance_officer: default scopes read+act+approve | scopes=['read', 'act', 'approve', 'reveal'] |
| PASS | brightwater/aml_analyst: default scopes act, no approve | scopes=['read', 'act'] |
| PASS | brightwater/lender: default scopes read-only (no act/approve) | scopes=['read'] |
| PASS | brightwater/engineer: default scopes read-only (no act/approve) | scopes=['read'] |
| PASS | halcyon/engineer ToolContext.require('act') denied | ToolError: Permission denied: opening a case requires the 'act' permission, which Kevin Zhao (engineer) does not have. Investigatio |
