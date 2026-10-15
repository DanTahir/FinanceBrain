# Golden-question eval

Generated 2026-10-15T22:33:21+00:00 · env `dev` · judge `anthropic/claude-opus-5.5`

## Summary

| Metric | Value |
|---|---|
| Questions | 39 |
| Passed | 36 (92%) |
| Entity recall | 100% |
| Doc hit rate | 73% |
| Citation validity | 97% |
| Barrier violations | 0 |
| Text checks passed | 3/3 |
| Judge mean (1-5) | 4.564 |
| Judge correct rate | 92% |
| Agent cost (USD) | $6.608 |
| Total LLM cost incl. judge (USD) | $7.095 |
| p50 latency | 11.8s |
| p95 latency | 101.8s |

## By track

| Track | n | Passed | Pass rate | Entity recall | Doc hit | Judge mean |
|---|---|---|---|---|---|---|
| T1 | 16 | 15 | 94% | 100% | 33% | 4.688 |
| T2 | 5 | 4 | 80% | - | - | 4.000 |
| T3 | 18 | 17 | 94% | 100% | 83% | 4.611 |

## By phase

| Phase | n | Passed | Pass rate |
|---|---|---|---|
| seed | 33 | 30 | 91% |
| live | 6 | 6 | 100% |

## Questions

| Id | Track | Persona | Agent | Pass | Entity recall | Doc hit | Judge | Cost | Seconds |
|---|---|---|---|---|---|---|---|---|---|
| crypto_why | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.042 | 13.0 |
| crypto_evidence | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.021 | 8.3 |
| crypto_regulator | T3 | aml_analyst | brain | ✓ | 100% | - | 4 | $0.020 | 9.4 |
| crypto_debate | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.055 | 16.8 |
| crypto_resume | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.050 | 10.0 |
| kyc_august | T3 | engineer | brain | ✓ | 100% | - | 5 | $0.011 | 5.6 |
| kyc_team_seed | T3 | aml_analyst | brain | ✗ | 100% | - | 2 | $0.012 | 5.5 |
| kyc_now_live | T3 | engineer | brain | ✓ | 100% | no | 5 | $0.016 | 5.0 |
| kyc_change_live | T3 | compliance_officer | brain | ✓ | 100% | no | 5 | $0.017 | 5.0 |
| recon_who | T3 | engineer | brain | ✓ | 100% | yes | 4 | $0.031 | 15.0 |
| recon_problems | T3 | engineer | brain | ✓ | 100% | yes | 5 | $0.049 | 12.7 |
| recon_expert_vs_owner | T3 | engineer | brain | ✓ | 100% | - | 5 | $0.015 | 6.9 |
| recon_leaver_live | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.053 | 15.6 |
| pricing_blast | T1 | engineer | brain | ✓ | 100% | no | 5 | $0.013 | 6.0 |
| pricing_hops | T1 | engineer | brain | ✓ | 100% | - | 4 | $0.010 | 4.9 |
| pricing_owner | T3 | engineer | brain | ✓ | 100% | - | 5 | $0.014 | 7.5 |
| recon_pricing_path | T1 | engineer | brain | ✓ | 100% | - | 5 | $0.023 | 9.0 |
| pricing_incident | T3 | engineer | brain | ✓ | 100% | yes | 5 | $0.049 | 12.9 |
| wire_investigate | T1 | aml_analyst | investigator | ✓ | 100% | no | 5 | $0.816 | 93.2 |
| rings_detect | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.700 | 85.7 |
| ring_exit | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.824 | 107.2 |
| ring_device_link | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.744 | 92.7 |
| ring_phone_peers | T1 | aml_analyst | brain | ✓ | 100% | - | 5 | $0.028 | 10.6 |
| wire2_live | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.869 | 101.8 |
| falcon_breach | T1 | compliance_officer | compliance | ✓ | 100% | - | 5 | $0.131 | 21.9 |
| falcon_breach_live | T1 | compliance_officer | compliance | ✓ | 100% | - | 5 | $0.219 | 26.3 |
| falcon_team | T3 | ma_banker | brain | ✓ | 100% | - | 5 | $0.036 | 11.0 |
| falcon_status | T3 | ma_banker | brain | ✓ | 100% | yes | 4 | $0.056 | 13.8 |
| falcon_probe_engineer | T1 | engineer | brain | ✓ | - | - | 5 | $0.026 | 9.4 |
| falcon_probe_aml | T1 | aml_analyst | brain | ✗ | - | - | 2 | $0.059 | 12.9 |
| falcon_probe_target | T1 | engineer | brain | ✓ | - | - | 5 | $0.062 | 11.8 |
| b_isolation | T2 | lender | brain | ✓ | - | - | 5 | $0.024 | 9.0 |
| b_recon_probe | T2 | engineer | brain | ✓ | - | - | 4 | $0.037 | 12.2 |
| b_sharerecon | T3 | engineer | brain | ✓ | 100% | yes | 4 | $0.013 | 6.0 |
| b_ring | T1 | aml_analyst | investigator | ✓ | 100% | - | 4 | $0.686 | 100.5 |
| b_ring_wire | T1 | aml_analyst | investigator | ✓ | 100% | yes | 5 | $0.690 | 96.5 |
| mem_preference | T2 | aml_analyst | auto | ✓ | - | - | 5 | $0.007 | 9.1 |
| mem_last_topic | T2 | engineer | auto | ✓ | - | - | 4 | $0.018 | 8.1 |
| mem_playbook | T2 | aml_analyst | auto | ✗ | - | - | 2 | $0.064 | 17.8 |

## Failures

- **kyc_team_seed** (halcyon/aml_analyst, brain)
  - judge (2): The reference says Operations (team_ops, led by Ravi Patel) owns the KYC pipeline as of the seeded data, before 2026-10-06. The assistant's headline answer is Compliance, treating the planned handover as done. It mentions Operations only as a 'stale' record, which contradicts the reference.
- **falcon_probe_aml** (halcyon/aml_analyst, brain)
  - judge (2): It declined to summarize the valuation discussion, but it disclosed Falcon's principal (Meridian Freight), target (Northwind Logistics), $1.2B headline value and status. The reference allows only knowing the deal exists; no valuation or terms may be shared.
- **mem_playbook** (halcyon/aml_analyst, auto)
  - judge (2): Reference expects a dedicated suspicious-wire Playbook: trace money, check shared identifiers, open case, propose freezes/SAR, hand off to Compliance. Answer says no playbook exists and pieces it together from docs, missing tracing and shared-identifier steps.
