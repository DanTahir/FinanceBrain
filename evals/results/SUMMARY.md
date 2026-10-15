# Evaluation & benchmarks

## Headline results

One row per eval with its key number(s); see the sections below for breakdowns.

| Eval | Key result | Details |
|---|---|---|
| Golden questions | pass 97.4% (38/39), judge mean 4.85, cost $7.148 | [golden.md](golden.md) |
| Retrieval | Recall@5 30.0%, Recall@10 30.8%, MRR 0.510 (n=11) | [retrieval.md](retrieval.md) |
| Redaction (halcyon) | recall 100.0% (typed 100.0%), precision 99.6%, canary hits n/a | [redaction.md](redaction.md) |
| Redaction (brightwater) | recall 100.0% (typed 100.0%), precision 100.0%, canary hits n/a | [redaction.md](redaction.md) |
| Graph checks (halcyon) | 12/12 passed | [graph_checks_halcyon.md](graph_checks_halcyon.md) |
| Graph checks (brightwater) | 7/7 passed | [graph_checks_brightwater.md](graph_checks_brightwater.md) |
| Tenant isolation | 155/155 passed | [isolation.md](isolation.md) |
| Load test (shared_graph) | 1,034.1 ops/s total, read p95 30.8 ms (worst: neighborhood), errors 0 | [load_test.md](load_test.md) |
| Load test (graph_per_user) | 1,074.5 ops/s total, read p95 30.4 ms (worst: neighborhood), errors 0 | [load_test.md](load_test.md) |
| Load test (read_only) | 1,168.2 ops/s total, read p95 18.1 ms (worst: neighborhood), errors 0 | [load_test.md](load_test.md) |

## Golden questions

End-to-end agent answers on curated questions, scored on entity recall, cited-document hits, citation validity, barrier violations and an LLM judge.

Details: [golden.md](golden.md)

Judge model: `anthropic/claude-opus-5.5`

| n | Passed | Pass rate | Entity recall | Doc hit rate | Citation validity | Barrier violations | Judge mean | Judge correct | Cost | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 39 | 38 | 97.4% | 100.0% | 86.7% | 100.0% | 0 | 4.85 | 97.4% | $7.148 | 11020.0 | 100758.0 |

### By track

| Track | n | Passed | Pass rate | Entity recall | Doc hit rate | Judge mean |
|---|---|---|---|---|---|---|
| T1 | 16 | 16 | 100.0% | 100.0% | 66.7% | 4.94 |
| T2 | 5 | 5 | 100.0% | — | — | 4.80 |
| T3 | 18 | 17 | 94.4% | 100.0% | 91.7% | 4.78 |

### By phase

| Phase | n | Passed | Pass rate |
|---|---|---|---|
| seed | 33 | 32 | 97.0% |
| live | 6 | 6 | 100.0% |

## Retrieval

Ranking quality of document retrieval against expected source documents (Recall@k, hit@k, MRR).

Details: [retrieval.md](retrieval.md)

Embedding model: `voyageai/voyage-4`

| n | Recall@5 | Recall@10 | Hit@5 | Hit@10 | MRR |
|---|---|---|---|---|---|
| 11 | 30.0% | 30.8% | 63.6% | 72.7% | 0.510 |

### Modes ablation

| Mode | n | Recall@5 | Recall@10 | Hit@5 | Hit@10 | MRR |
|---|---|---|---|---|---|---|
| hybrid | 11 | 30.0% | 30.8% | 63.6% | 72.7% | 0.510 |
| vector | 11 | 24.1% | 31.9% | 63.6% | 72.7% | 0.424 |
| fulltext | 11 | 8.6% | 13.9% | 18.2% | 27.3% | 0.192 |

## Redaction

Detection of injected PII spans in the synthetic corpus, per tenant and per PII type (recall over ground-truth spans, precision over detections).

Details: [redaction.md](redaction.md)

### halcyon

1,345 ground-truth spans across 2,219 documents, 1,351 detections; recall (any type) **100.0%**, typed recall 100.0%, precision **99.6%**, 68.3 s.

| Type | GT spans | Recall (any) | Recall (typed) | Detections | Precision |
|---|---|---|---|---|---|
| ACCOUNT_NUMBER | 506 | 100.0% | 100.0% | 506 | 100.0% |
| ADDRESS | 53 | 100.0% | 100.0% | 53 | 100.0% |
| CARD_NUMBER | 3 | 100.0% | 100.0% | 3 | 100.0% |
| CLIENT_NAME | 479 | 100.0% | 100.0% | 485 | 98.8% |
| DEVICE_ID | 5 | 100.0% | 100.0% | 5 | 100.0% |
| DOB | 5 | 100.0% | 100.0% | 5 | 100.0% |
| EIN | 4 | 100.0% | 100.0% | 4 | 100.0% |
| EMAIL | 155 | 100.0% | 100.0% | 155 | 100.0% |
| IBAN | 13 | 100.0% | 100.0% | 13 | 100.0% |
| MEDICAL | 2 | 100.0% | 100.0% | 2 | 100.0% |
| PHONE | 103 | 100.0% | 100.0% | 103 | 100.0% |
| SALARY | 1 | 100.0% | 100.0% | 1 | 100.0% |
| SSN | 2 | 100.0% | 100.0% | 2 | 100.0% |
| SWIFT | 14 | 100.0% | 100.0% | 14 | 100.0% |

_No canary or outbound-guard results recorded for this tenant._

### brightwater

546 ground-truth spans across 666 documents, 546 detections; recall (any type) **100.0%**, typed recall 100.0%, precision **100.0%**, 19.2 s.

| Type | GT spans | Recall (any) | Recall (typed) | Detections | Precision |
|---|---|---|---|---|---|
| ACCOUNT_NUMBER | 187 | 100.0% | 100.0% | 187 | 100.0% |
| ADDRESS | 20 | 100.0% | 100.0% | 20 | 100.0% |
| CARD_NUMBER | 11 | 100.0% | 100.0% | 11 | 100.0% |
| CLIENT_NAME | 210 | 100.0% | 100.0% | 210 | 100.0% |
| DEVICE_ID | 3 | 100.0% | 100.0% | 3 | 100.0% |
| DOB | 2 | 100.0% | 100.0% | 2 | 100.0% |
| EMAIL | 46 | 100.0% | 100.0% | 46 | 100.0% |
| IBAN | 2 | 100.0% | 100.0% | 2 | 100.0% |
| MEDICAL | 2 | 100.0% | 100.0% | 2 | 100.0% |
| PHONE | 59 | 100.0% | 100.0% | 59 | 100.0% |
| SALARY | 1 | 100.0% | 100.0% | 1 | 100.0% |
| SSN | 2 | 100.0% | 100.0% | 2 | 100.0% |
| SWIFT | 1 | 100.0% | 100.0% | 1 | 100.0% |

_No canary or outbound-guard results recorded for this tenant._

## Graph checks

Structural assertions on each tenant's knowledge graph (fraud rings, blast radius, ownership, experts, decision trails, access restrictions, vector and full-text indexes).

### halcyon

Details: [graph_checks_halcyon.md](graph_checks_halcyon.md)

**12/12** checks passed.

|  | Check |
|---|---|
| ✓ | fraud ring component via shared PII tokens |
| ✓ | flagged wire lands in a ring account |
| ✓ | PricingSvc blast radius (systems and hop counts) |
| ✓ | KYC pipeline current owner (before transfer) |
| ✓ | legacy recon system: top derived expert is the recon expert |
| ✓ | Project Falcon deal team |
| ✓ | decision trail dec_crypto_pause |
| ✓ | decision dec_crypto_resume not yet known before live feed |
| ✓ | no live-feed documents before replay |
| ✓ | MNPI documents are access-restricted |
| ✓ | vector index returns documents (cosine distance ascending) |
| ✓ | full-text index returns documents ('ReconLegacy') |

### brightwater

Details: [graph_checks_brightwater.md](graph_checks_brightwater.md)

**7/7** checks passed.

|  | Check |
|---|---|
| ✓ | fraud ring component via shared PII tokens |
| ✓ | flagged wire lands in a ring account |
| ✓ | legacy recon system: top derived expert is the recon expert |
| ✓ | no live-feed documents before replay |
| ✓ | MNPI documents are access-restricted |
| ✓ | vector index returns documents (cosine distance ascending) |
| ✓ | full-text index returns documents ('ShareRecon') |

## Tenant isolation

Verifies that tenants, personas and per-user memory graphs cannot read each other's data.

Details: [isolation.md](isolation.md)

**155/155** checks passed.

| Category | Passed/total |
|---|---|
| auth | 20/20 |
| information_barrier | 73/73 |
| memory_privacy | 10/10 |
| tenant_isolation | 52/52 |

## Load test

Concurrent read/write throughput and latency percentiles against FalkorDB.

Details: [load_test.md](load_test.md)

### shared_graph

W writers + R readers all on the single scratch brain copy (writes serialize on one graph)

8 writers, 16 readers, 15 s — 15,551 ops, **1,034.1 ops/s**, 0 errors.

| Op | Count | ops/s | p50 ms | p95 ms | p99 ms | max ms | Errors |
|---|---|---|---|---|---|---|---|
| blast_radius | 2,347 | 156.1 | 19.5 | 26.7 | 32.2 | 69.5 | 0 |
| fulltext | 2,350 | 156.3 | 17.7 | 25.1 | 29.1 | 82.3 | 0 |
| neighborhood | 2,349 | 156.2 | 22.1 | 30.8 | 37.0 | 64.7 | 0 |
| ring_hops | 2,351 | 156.3 | 19.0 | 26.2 | 32.5 | 66.1 | 0 |
| vector_knn | 2,352 | 156.4 | 21.3 | 29.5 | 36.4 | 67.7 | 0 |
| write_merge | 3,802 | 252.8 | 30.1 | 48.1 | 58.9 | 80.0 | 0 |

### graph_per_user

W writers each on their own graph test__eval_load_mem_<i> + R readers on the brain copy

8 writers, 16 readers, 15 s — 16,131 ops, **1,074.5 ops/s**, 0 errors.

| Op | Count | ops/s | p50 ms | p95 ms | p99 ms | max ms | Errors |
|---|---|---|---|---|---|---|---|
| blast_radius | 2,371 | 157.9 | 18.4 | 26.5 | 32.8 | 44.7 | 0 |
| fulltext | 2,373 | 158.1 | 18.3 | 26.8 | 34.4 | 55.3 | 0 |
| neighborhood | 2,370 | 157.9 | 21.0 | 30.4 | 36.8 | 53.6 | 0 |
| ring_hops | 2,372 | 158.0 | 19.9 | 29.0 | 35.5 | 49.4 | 0 |
| vector_knn | 2,373 | 158.1 | 20.9 | 30.0 | 36.8 | 52.0 | 0 |
| write_merge | 4,272 | 284.6 | 27.5 | 40.6 | 48.1 | 67.1 | 0 |

### read_only

R readers only on the scratch brain copy (baseline read throughput/latency)

0 writers, 16 readers, 15 s — 17,530 ops, **1,168.2 ops/s**, 0 errors.

| Op | Count | ops/s | p50 ms | p95 ms | p99 ms | max ms | Errors |
|---|---|---|---|---|---|---|---|
| blast_radius | 3,507 | 233.7 | 12.9 | 16.7 | 19.4 | 25.0 | 0 |
| fulltext | 3,507 | 233.7 | 12.7 | 16.4 | 18.5 | 25.2 | 0 |
| neighborhood | 3,506 | 233.6 | 14.3 | 18.1 | 21.3 | 27.2 | 0 |
| ring_hops | 3,505 | 233.6 | 12.9 | 16.5 | 19.0 | 26.0 | 0 |
| vector_knn | 3,505 | 233.6 | 14.0 | 17.9 | 20.5 | 27.9 | 0 |

## Sources

Provenance of each result file used to build this summary.

| File | generated_at | env |
|---|---|---|
| golden.json | 2026-10-15T22:58:30+00:00 | dev |
| retrieval.json | 2026-10-15T22:13:40+00:00 | dev |
| redaction.json | 2026-10-15T18:29:05+00:00 (file mtime) | n/a |
| graph_checks_halcyon.json | 2026-10-15T20:53:31+00:00 (file mtime) | n/a |
| graph_checks_brightwater.json | 2026-10-15T20:53:32+00:00 (file mtime) | n/a |
| isolation.json | 2026-10-15T22:15:11.659393+00:00 | dev |
| load_test.json | 2026-10-15T22:16:00.073806+00:00 | dev (host: 127.0.0.1) |

_Summary generated 2026-10-15T23:01:25+00:00._
