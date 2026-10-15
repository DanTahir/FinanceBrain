# Retrieval eval (Recall@k of expected source documents)

- generated_at: 2026-10-15T22:13:40+00:00
- env: dev
- embed_model: voyageai/voyage-4
- phase: seed, search depth k=10

## Summary

| Mode | n | R@5 | R@10 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|---|---|
| hybrid (search_knowledge) | 11 | 0.300 | 0.308 | 0.636 | 0.727 | 0.510 |
| vector | 11 | 0.241 | 0.319 | 0.636 | 0.727 | 0.424 |
| fulltext | 11 | 0.086 | 0.139 | 0.182 | 0.273 | 0.192 |

Notes:
- recall@k = |expected ∩ top-k| / min(k, |expected|); thread-neighbour context results are excluded from the ranking.
- Expected documents are filtered to ids that exist in brain__<tenant> and are visible to the persona (ToolContext.visible_docs).
- vector / fulltext modes reuse the exact ACL-filtered Cypher constants of search_knowledge (Q.SEARCH_DOCS_VECTOR, Q.SEARCH_DOCS_FULLTEXT) with the same parameters; fact-vector boosts and RRF fusion apply only to hybrid.

## Per question

| id | persona | |expected| | R@5 | R@10 | hit@5 | RR | first expected rank | missed |
|---|---|---|---|---|---|---|---|---|
| crypto_why | compliance_officer | 6 | 0.600 | 0.667 | 1 | 1.000 | 1 | email-00084, slack-00531 |
| crypto_evidence | compliance_officer | 5 | 0.400 | 0.400 | 1 | 1.000 | 1 | email-00084, slack-00506, slack-00531 |
| crypto_debate | compliance_officer | 4 | 0.250 | 0.250 | 1 | 0.500 | 2 | slack-00496, slack-00506, slack-00531 |
| recon_who | engineer | 5 | 0.000 | 0.000 | 0 | 0.000 | - | doc-00003, slack-00293, slack-00294, ticket-00073, ticket-00098 |
| recon_problems | engineer | 4 | 0.000 | 0.000 | 0 | 0.000 | - | ticket-00073, ticket-00098, slack-00673, slack-00674 |
| pricing_blast | engineer | 8 | 1.000 | 0.750 | 1 | 1.000 | 1 | slack-00669, slack-00670 |
| pricing_incident | engineer | 6 | 0.400 | 0.500 | 1 | 1.000 | 1 | slack-00669, slack-00670, slack-00671 |
| wire_investigate | aml_analyst | 6 | 0.000 | 0.167 | 0 | 0.111 | 9 | crm-00010, crm-00011, crm-00022, crm-00056, crm-00061 |
| falcon_status | ma_banker | 5 | 0.400 | 0.400 | 1 | 0.500 | 2 | doc-00005, slack-00704, slack-00705 |
| b_sharerecon | engineer | 3 | 0.000 | 0.000 | 0 | 0.000 | - | doc-00001, slack-00180, slack-00181 |
| b_ring_wire | aml_analyst | 4 | 0.250 | 0.250 | 1 | 0.500 | 2 | crm-00010, crm-00014, crm-00015 |

## Per question by mode (R@10 / RR)

| id | hybrid | vector | fulltext |
|---|---|---|---|
| crypto_why | 0.667 / 1.000 | 0.667 / 1.000 | 0.000 / 0.000 |
| crypto_evidence | 0.400 / 1.000 | 0.600 / 0.500 | 0.000 / 0.000 |
| crypto_debate | 0.250 / 0.500 | 0.500 / 0.167 | 0.000 / 0.000 |
| recon_who | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| recon_problems | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| pricing_blast | 0.750 / 1.000 | 0.625 / 0.500 | 0.125 / 0.111 |
| pricing_incident | 0.500 / 1.000 | 0.500 / 1.000 | 0.000 / 0.000 |
| wire_investigate | 0.167 / 0.111 | 0.167 / 0.500 | 0.000 / 0.000 |
| falcon_status | 0.400 / 0.500 | 0.200 / 0.500 | 0.400 / 1.000 |
| b_sharerecon | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| b_ring_wire | 0.250 / 0.500 | 0.250 / 0.500 | 1.000 / 1.000 |

## Dropped expected documents

| id | missing from graph | not visible to persona |
|---|---|---|
| recon_who | meeting-00017 | - |
| crypto_regulator | - | email-00084 |
| kyc_august | - | slack-00292, ticket-00052, email-00273 |
| falcon_breach | - | email-00301 |

## Skipped

| id | phase | reason |
|---|---|---|
| crypto_regulator | seed | no expected document exists in the graph and is visible to the persona |
| kyc_august | seed | no expected document exists in the graph and is visible to the persona |
| falcon_breach | seed | no expected document exists in the graph and is visible to the persona |
