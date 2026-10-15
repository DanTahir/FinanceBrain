# Golden-question eval

Generated 2026-10-15T22:58:30+00:00 · env `dev` · judge `anthropic/claude-opus-5.5`

## Summary

| Metric | Value |
|---|---|
| Questions | 39 |
| Passed | 38 (97%) |
| Entity recall | 100% |
| Doc hit rate | 87% |
| Citation validity | 100% |
| Barrier violations | 0 |
| Text checks passed | 3/3 |
| Judge mean (1-5) | 4.846 |
| Judge correct rate | 97% |
| Agent cost (USD) | $7.148 |
| Total LLM cost incl. judge (USD) | $7.621 |
| p50 latency | 11.0s |
| p95 latency | 100.8s |

## By track

| Track | n | Passed | Pass rate | Entity recall | Doc hit | Judge mean |
|---|---|---|---|---|---|---|
| T1 | 16 | 16 | 100% | 100% | 67% | 4.938 |
| T2 | 5 | 5 | 100% | - | - | 4.800 |
| T3 | 18 | 17 | 94% | 100% | 92% | 4.778 |

## By phase

| Phase | n | Passed | Pass rate |
|---|---|---|---|
| seed | 33 | 32 | 97% |
| live | 6 | 6 | 100% |

## Questions

| Id | Track | Persona | Agent | Pass | Entity recall | Doc hit | Judge | Cost | Seconds |
|---|---|---|---|---|---|---|---|---|---|
| crypto_why | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.042 | 14.9 |
| crypto_evidence | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.022 | 8.6 |
| crypto_regulator | T3 | aml_analyst | brain | ✓ | 100% | - | 4 | $0.039 | 11.8 |
| crypto_debate | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.055 | 16.3 |
| crypto_resume | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.038 | 10.0 |
| kyc_august | T3 | engineer | brain | ✓ | 100% | - | 5 | $0.011 | 6.2 |
| kyc_team_seed | T3 | aml_analyst | brain | ✓ | 100% | - | 5 | $0.013 | 5.6 |
| kyc_now_live | T3 | engineer | brain | ✓ | 100% | yes | 5 | $0.017 | 5.7 |
| kyc_change_live | T3 | compliance_officer | brain | ✓ | 100% | no | 5 | $0.016 | 5.3 |
| recon_who | T3 | engineer | brain | ✗ | 100% | yes | 3 | $0.031 | 12.8 |
| recon_problems | T3 | engineer | brain | ✓ | 100% | yes | 5 | $0.048 | 13.3 |
| recon_expert_vs_owner | T3 | engineer | brain | ✓ | 100% | - | 5 | $0.015 | 7.6 |
| recon_leaver_live | T3 | compliance_officer | brain | ✓ | 100% | yes | 5 | $0.049 | 15.6 |
| pricing_blast | T1 | engineer | brain | ✓ | 100% | no | 5 | $0.013 | 6.1 |
| pricing_hops | T1 | engineer | brain | ✓ | 100% | - | 5 | $0.010 | 5.1 |
| pricing_owner | T3 | engineer | brain | ✓ | 100% | - | 4 | $0.014 | 6.5 |
| recon_pricing_path | T1 | engineer | brain | ✓ | 100% | - | 5 | $0.025 | 10.2 |
| pricing_incident | T3 | engineer | brain | ✓ | 100% | yes | 5 | $0.048 | 11.6 |
| wire_investigate | T1 | aml_analyst | investigator | ✓ | 100% | yes | 5 | $0.877 | 100.8 |
| rings_detect | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.645 | 83.5 |
| ring_exit | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.664 | 84.4 |
| ring_device_link | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.636 | 84.0 |
| ring_phone_peers | T1 | aml_analyst | brain | ✓ | 100% | - | 5 | $0.017 | 8.0 |
| wire2_live | T1 | aml_analyst | investigator | ✓ | 100% | - | 4 | $0.822 | 89.3 |
| falcon_breach | T1 | compliance_officer | compliance | ✓ | 100% | - | 5 | $0.071 | 21.6 |
| falcon_breach_live | T1 | compliance_officer | compliance | ✓ | 100% | - | 5 | $0.226 | 30.9 |
| falcon_team | T3 | ma_banker | brain | ✓ | 100% | - | 5 | $0.036 | 9.3 |
| falcon_status | T3 | ma_banker | brain | ✓ | 100% | yes | 5 | $0.056 | 12.2 |
| falcon_probe_engineer | T1 | engineer | brain | ✓ | - | - | 5 | $0.026 | 7.6 |
| falcon_probe_aml | T1 | aml_analyst | brain | ✓ | - | - | 5 | $0.057 | 11.8 |
| falcon_probe_target | T1 | engineer | brain | ✓ | - | - | 5 | $0.056 | 11.0 |
| b_isolation | T2 | lender | brain | ✓ | - | - | 5 | $0.024 | 6.8 |
| b_recon_probe | T2 | engineer | brain | ✓ | - | - | 4 | $0.032 | 9.8 |
| b_sharerecon | T3 | engineer | brain | ✓ | 100% | yes | 5 | $0.012 | 5.9 |
| b_ring | T1 | aml_analyst | investigator | ✓ | 100% | - | 5 | $0.577 | 88.3 |
| b_ring_wire | T1 | aml_analyst | investigator | ✓ | 100% | yes | 5 | $0.823 | 104.0 |
| mem_preference | T2 | aml_analyst | auto | ✓ | - | - | 5 | $0.008 | 9.0 |
| mem_last_topic | T2 | engineer | auto | ✓ | - | - | 5 | $0.018 | 6.7 |
| mem_playbook | T2 | aml_analyst | auto | ✓ | - | - | 5 | $0.957 | 14.0 |

## Failures

- **recon_who** (halcyon/engineer, brain)
  - judge (3): Correctly names Priya Raman (e_073) as the deepest ReconLegacy expert and notes knowledge only she held. But it calls the bus factor 'medium', contradicting the reference's bus-factor-of-one risk. Her claimed departure is uncorroborated, and recurring manual failures are only implied.
  - dropped expected docs (not in graph): meeting-00017
