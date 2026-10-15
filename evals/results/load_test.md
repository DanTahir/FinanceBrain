# FalkorDB load test (halcyon)

Generated 2026-10-15T22:16:00.073806+00:00 · env `dev` · host `127.0.0.1` · source graph `pristine__halcyon` · pool size 32 · clients redis 8.1.0, falkordb 1.8.0

## Summary

| Scenario | Writers | Readers | Seconds | Total ops | Ops/s | Errors | Error rate | Gate |
|---|---|---|---|---|---|---|---|---|
| shared_graph | 8 | 16 | 15.0 | 15551 | 1034.12 | 0 | 0.00% | PASS |
| graph_per_user | 8 | 16 | 15.0 | 16131 | 1074.45 | 0 | 0.00% | PASS |
| read_only | 0 | 16 | 15.0 | 17530 | 1168.21 | 0 | 0.00% | PASS |

Notes:

- GRAPH.COPY pristine__halcyon -> test__eval_load_brain: 16117 nodes in 2581 ms
- vector_knn query vector: stored embedding of slack-00122 (1024 dims)

## shared_graph

W writers + R readers all on the single scratch brain copy (writes serialize on one graph)

| Op | Count | Ops/s | p50 ms | p95 ms | p99 ms | Max ms | Errors |
|---|---|---|---|---|---|---|---|
| blast_radius | 2347 | 156.07 | 19.52 | 26.68 | 32.18 | 69.53 | 0 |
| fulltext | 2350 | 156.27 | 17.72 | 25.06 | 29.09 | 82.3 | 0 |
| neighborhood | 2349 | 156.2 | 22.11 | 30.84 | 36.97 | 64.74 | 0 |
| ring_hops | 2351 | 156.34 | 19.03 | 26.21 | 32.48 | 66.1 | 0 |
| vector_knn | 2352 | 156.4 | 21.32 | 29.48 | 36.41 | 67.65 | 0 |
| write_merge | 3802 | 252.83 | 30.09 | 48.06 | 58.94 | 80.0 | 0 |

**Interpretation:** 8 writers on one shared graph (writes serialize on its lock): 252.83 writes/s (12641.3 rows/s), write p95 48.06 ms. Concurrent reads: 781.29 reads/s, p95 28.17 ms (1.63x read-only baseline p95).

## graph_per_user

W writers each on their own graph test__eval_load_mem_<i> + R readers on the brain copy

| Op | Count | Ops/s | p50 ms | p95 ms | p99 ms | Max ms | Errors |
|---|---|---|---|---|---|---|---|
| blast_radius | 2371 | 157.93 | 18.45 | 26.52 | 32.79 | 44.69 | 0 |
| fulltext | 2373 | 158.06 | 18.31 | 26.82 | 34.41 | 55.33 | 0 |
| neighborhood | 2370 | 157.86 | 21.02 | 30.38 | 36.83 | 53.62 | 0 |
| ring_hops | 2372 | 157.99 | 19.85 | 28.99 | 35.55 | 49.44 | 0 |
| vector_knn | 2373 | 158.06 | 20.93 | 29.98 | 36.78 | 52.0 | 0 |
| write_merge | 4272 | 284.55 | 27.48 | 40.58 | 48.08 | 67.14 | 0 |

**Interpretation:** 8 writers on 8 separate graphs: 284.55 writes/s (14227.4 rows/s), write p95 40.58 ms (1.13x shared_graph write throughput). Concurrent reads: 789.9 reads/s, p95 28.85 ms (1.67x read-only baseline p95).

## read_only

R readers only on the scratch brain copy (baseline read throughput/latency)

| Op | Count | Ops/s | p50 ms | p95 ms | p99 ms | Max ms | Errors |
|---|---|---|---|---|---|---|---|
| blast_radius | 3507 | 233.71 | 12.95 | 16.67 | 19.44 | 24.97 | 0 |
| fulltext | 3507 | 233.71 | 12.71 | 16.43 | 18.52 | 25.2 | 0 |
| neighborhood | 3506 | 233.64 | 14.26 | 18.15 | 21.28 | 27.21 | 0 |
| ring_hops | 3505 | 233.58 | 12.86 | 16.54 | 19.03 | 26.02 | 0 |
| vector_knn | 3505 | 233.58 | 14.04 | 17.92 | 20.54 | 27.95 | 0 |

**Interpretation:** Baseline: 16 readers sustain 1168.21 reads/s, p50 13.36 ms, p95 17.29 ms.
