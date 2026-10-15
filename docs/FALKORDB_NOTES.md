# FalkorDB and OpenRouter verification notes

Verified in Phase 0 (Thu Oct 15 2026) against `falkordb/falkordb:latest` (Redis 8.10.2, graph module 42201),
using `scripts/falkordb_spike.py` (42/44 checks passed; the two failures are documented limits) and
`scripts/check_openrouter.py`.

## FalkorDB

### Container

| Item | Verified behaviour |
|---|---|
| Data dir | `/var/lib/falkordb/data` (`--dir` set by `/var/lib/falkordb/bin/run.sh`) |
| Args | `run.sh` runs `redis-server ${REDIS_ARGS} ... --loadmodule falkordb.so ${FALKORDB_ARGS}`. Redis flags (AOF, `requirepass`) go in `REDIS_ARGS`; graph-module config goes in `FALKORDB_ARGS`. |
| Image defaults | `FALKORDB_ARGS=MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000`. The deprecated `TIMEOUT 1000` (1 s) is too low for algorithms, so we override it with `TIMEOUT_DEFAULT 15000 TIMEOUT_MAX 60000`. |
| Persistence | Default `save 3600 1 300 100 60 10000`, `appendonly no`. A graph survived `docker restart` via `dump.rdb` written on shutdown. Production adds `--appendonly yes --appendfsync everysec`. |
| Browser UI | Port 3000 inside the container (`BROWSER=1`); mapped to 3001 locally, never exposed in prod |

### Properties

- `SET n.p = null` removes the property (`exists(n.p)` returns false). `UNWIND` rows with `None` fields are safe: the property is simply absent.
- **Map-valued properties are rejected:** `Property values can only be of primitive types or arrays of primitive types`. Store nested data as JSON strings.
- `SET n += $map` works (merges keys as separate properties). Lists of primitives are allowed.

### Indexes and constraints

```cypher
CREATE INDEX FOR (p:Person) ON (p.id)                          -- range index
CREATE INDEX FOR ()-[k:KNOWS]-() ON (k.weight)                 -- edge range index
CREATE FULLTEXT INDEX FOR (p:Person) ON (p.name)               -- full-text (DDL form)
CALL db.idx.fulltext.createNodeIndex('Document', 'title', 'text')   -- full-text, multi-property
CREATE VECTOR INDEX FOR (d:Document) ON (d.embedding) OPTIONS {dimension: 1024, similarityFunction: 'cosine'}
```

- Unique constraint: `GRAPH.CONSTRAINT CREATE <graph> UNIQUE NODE Person PROPERTIES 1 id`. It returns `PENDING`, becomes `OPERATIONAL` (`CALL db.constraints()`), then rejects duplicates with `unique constraint violation on node of type Person`. It requires a range index on the same property first.
- `CALL db.indexes()` lists all indexes.

### Search

- Full-text: `CALL db.idx.fulltext.queryNodes('Document', 'crypto') YIELD node, score`. RediSearch syntax works: prefix `Pric*`, fuzzy `%falcn%`, OR `|`. **Higher score is better.**
- Vector: `CALL db.idx.vector.queryNodes('Document', 'embedding', k, vecf32($q)) YIELD node, score`.
  **`score` is cosine DISTANCE (0 = identical, 1 = orthogonal). Lower is better.** It matches `vec.cosineDistance(a, b)`.
  Similarity = `1 - score`.
- `WHERE` filters can follow `YIELD` (post-filter on the top-k), so ACL filtering must over-fetch k (for example k*4) and then filter.
- Vectors are written with `SET d.embedding = vecf32(r.v)` inside `UNWIND`.

### Algorithms (from `CALL dbms.procedures()`)

`algo.AStar`, `algo.betweenness`, `algo.BFS`, `algo.HarmonicCentrality`, `algo.labelPropagation`, `algo.maxFlow`,
`algo.MSF`, `algo.pageRank`, `algo.SPpaths`, `algo.SSpaths`, `algo.WCC`. Procedure names are case-insensitive (`algo.bfs` worked).

```cypher
CALL algo.pageRank('Person', 'KNOWS') YIELD node, score
CALL algo.WCC({nodeLabels:['Person'], relationshipTypes:['KNOWS']}) YIELD node, componentId
CALL algo.labelPropagation({nodeLabels:['Person'], relationshipTypes:['KNOWS']}) YIELD node, communityId
CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['KNOWS']}) YIELD node, score
MATCH (a {id:'p0'}) CALL algo.bfs(a, 3, 'KNOWS') YIELD nodes, edges
CALL algo.SPpaths({sourceNode:a, targetNode:b, relTypes:['KNOWS'], weightProp:'weight', maxLen:10, pathCount:2})
  YIELD path, pathWeight, pathCost
CALL algo.SSpaths({sourceNode:a, relTypes:['KNOWS'], maxLen:2, pathCount:1}) YIELD path
```

- **`shortestPath()` only works in `WITH`/`RETURN`**, not in `MATCH p = shortestPath(...)`:
  `MATCH (a {id:$a}), (b {id:$b}) RETURN shortestPath((a)-[:KNOWS*..10]->(b))`.
- `MATCH p = allShortestPaths((a)-[:KNOWS*..10]->(b))` works in `MATCH`, **but only when `a` and `b` are bound in
  earlier clauses and carried through `WITH`**:
  `MATCH (a {id:$a}) MATCH (b {id:$b}) WITH a, b MATCH p = allShortestPaths((a)-[*..5]-(b)) RETURN ...`
  (`find_path` uses this form; it also works undirected).
- `shortestPath()` does **not** support undirected traversals. It needs a direction, so `find_path` uses
  `allShortestPaths`.
- Undirected variable-length patterns (`-[:KNOWS*1..2]-`) work.

### Query APIs

- `graph.ro_query()` (`GRAPH.RO_QUERY`) rejects writes: `graph.RO_QUERY is to be executed only on read-only queries`. This backs the `run_cypher_readonly` tool.
- A per-query `timeout=` (ms) is accepted by the Python client.
- `GRAPH.COPY src dst` copies data and indexes (3 indexes present in the copy), which backs "Reset demo".
- `db.list_graphs()` lists graphs (used for visitor-graph TTL cleanup).
- `timestamp()` returns epoch ms. `localdatetime('...')` works. ISO date strings compare lexicographically, which we use for `valid_from` and `valid_to`.

### Pattern predicates and subqueries (verified during ingestion)

- **Pattern predicates in `WHERE` work**, including multi-hop ones, so ACLs are pure traversal:
  `WHERE d.public OR (p)-[:CAN_ACCESS]->(d) OR (p)-[:MEMBER_OF]->(:Team)-[:CAN_ACCESS]->(d)
  OR (p)-[:CAN_ACCESS]->(:Channel)<-[:POSTED_IN]-(d)`.
- **`exists((a)-[...]->(b))` is not supported**: `Unable to resolve filtered alias '(p)-[:CAN_ACCESS]->(d)'`.
  Use the bare pattern predicate instead.
- `CALL { WITH p MATCH ... RETURN count(c) AS n } RETURN n` subqueries work.
- `MERGE ... ON CREATE SET k.weight = 0.0 SET k.weight = k.weight + r.w` inside `UNWIND` works for incremental
  edge weights.
- `GRAPH.CONSTRAINT CREATE` via `execute_command` from the async client works; constraints start `PENDING`
  and become `OPERATIONAL` within a second on an empty graph (`CALL db.constraints() YIELD status`).
- Client tip (Windows dev): connect to `127.0.0.1`, not `localhost`. `localhost` tries IPv6 `::1` first, which
  adds ~2 s to every new connection and distorts latency measurements.

### Verified while building the tool layer

- **Pattern predicates inside a list comprehension `WHERE` fail**:
  `[(dec)-[:JUSTIFIED_BY]->(d) WHERE (p)-[:CAN_ACCESS]->(d) | d.id]` raises
  `Type mismatch: expected Null but was Pointer`. Property predicates inside comprehensions work. For ACL-filtered
  collections, use `OPTIONAL MATCH ... WITH ... WHERE d IS NULL OR <acl> ... collect(...)`.
- **Vector search and ACL:** `db.idx.vector.queryNodes` returns the global top-k and filters only after `YIELD`.
  With a selective ACL (for example, a persona that sees ~15% of documents), a small k returns few rows.
  `search_knowledge` over-fetches adaptively (k = 40, then 200 if fewer than the wanted rows survive the ACL). The
  score is cosine **distance** (lower is closer), so sort ascending.
- **Map properties are rejected.** Nested values are stored as JSON strings (for example `Document.redactions`).
- Bitemporal `as_of` filtering on relationship properties works:
  `WHERE e.valid_from <= $d AND (e.valid_to IS NULL OR e.valid_to > $d)`. ISO date strings compare lexicographically.
- Measured locally on halcyon (~13k nodes): ACL-filtered hybrid search makes 7–13 queries in 300–500 ms total,
  including graph expansion. Single structural tools take 2–60 ms.

### MCP server (Python SDK v2)

- The SDK in use is `mcp` 2.x: `from mcp.server.mcpserver import MCPServer, Context`. The client is
  `mcp.client.client.Client(server)` (in-memory) and `call_tool(name, args, meta={...})`.
- Per-call identity: in-process agents send a signed token in `_meta` (`meta={"fb_auth": token}`). The tool reads it
  from `ctx.request_context.meta`. HTTP clients send `Authorization: Bearer <token>`. Contextvars set by the caller
  do **not** cross the in-memory transport, so `_meta` is required.
- Any exception raised in a tool, except `mcp.server.mcpserver.exceptions.ToolError`, is replaced by the generic
  `Error executing tool <name>`. Our `_run` wrapper re-raises domain errors as the SDK `ToolError`, so the model sees
  actionable messages such as "No entity matching 'Project Falcon' is visible to you".
- `streamable_http_app(stateless_http=True, json_response=True)` is mounted inside FastAPI. The MCP app's lifespan
  (its session manager) must be entered from the FastAPI lifespan. DNS-rebinding protection is disabled because
  CloudFront and Caddy rewrite `Host`; the origin is protected by the CloudFront secret header instead.

### Async client

`falkordb.asyncio.FalkorDB(connection_pool=redis.asyncio.BlockingConnectionPool(max_connections=16, ...))`:
40 concurrent `UNWIND` writers (50 nodes each) plus 40 concurrent readers on one graph finished in 0.087 s locally with
all 2,000 nodes present.

## OpenRouter

| Check | Result |
|---|---|
| Key | `GET /api/v1/key` OK; no per-key limit; account auto-tops-up credits (per the user) |
| `anthropic/claude-opus-5.5` | Streaming OK, routed to "Claude Platform on AWS", TTFT ~3.7 s |
| `anthropic/claude-sonnet-5.5` | Streaming OK, TTFT ~1.5 s |
| `anthropic/claude-haiku-5.5` | Streaming OK, TTFT ~1.1 s |
| Tool loop (`tool_choice: auto`) | 2-step round trip correct on all three models; `tool_calls` deltas accumulate by `index`; tool IDs are `toolu_*` |
| Reasoning | `reasoning: {effort: low/high}`, `{enabled: true}`, and `{max_tokens: N}` are all accepted (HTTP 200), but **0 reasoning tokens and no `reasoning_details`** were returned for Sonnet 5.5, on both the "Claude Platform on AWS" and the direct "Anthropic" providers, including on a multi-step arithmetic prompt. **Decision:** no feature depends on visible reasoning. The agent loop still forwards `reasoning_details` unchanged whenever a response includes them. |
| JSON-schema `response_format` (strict) + `require_parameters: true` | Haiku 5.5 routed to **Azure**, valid JSON matching the schema |
| Same, plus `temperature: 0` | **404 "No endpoints found that can handle the requested parameters"** (fails at the *Filter by Parameters* routing step). The structured-output endpoint does not accept `temperature`, so `chat_json` never sends it. |
| Prompt caching (`cache_control: ephemeral` on the system block) | Call 1: `cache_write_tokens` 15,217, cost $0.0019. Call 2: `cached_tokens` 15,217, cost $0.00016 (about 12x cheaper) |
| Usage accounting | `usage.cost` (USD) is present on every response, including streaming (final chunk) |
| `voyageai/voyage-4` | 1024 dims by default and with `dimensions: 1024`, unit-normalized, provider "VoyageAI by MongoDB". `input_type` is accepted and changes the vector, so we send `document`/`query`. |
| `openai/text-embedding-3-large` + `dimensions: 1024` | 1024 dims, unit-normalized (fallback ready) |
