"""Phase 0 FalkorDB spike: verify syntax/behaviour we depend on. Prints PASS/FAIL per item.

Run: uv run python scripts/falkordb_spike.py [port]
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback

from falkordb import FalkorDB
from falkordb.asyncio import FalkorDB as AsyncFalkorDB
from redis.asyncio import BlockingConnectionPool

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 6380
RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: object = None) -> None:
    RESULTS.append({"item": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {json.dumps(detail, default=str)[:600]}", flush=True)


def attempt(name: str, fn):
    try:
        res = fn()
        record(name, True, res)
        return res
    except Exception as e:  # noqa: BLE001
        record(name, False, f"{type(e).__name__}: {e}")
        return None


def rows(res) -> list:
    return res.result_set


def main_sync() -> None:
    db = FalkorDB(host="localhost", port=PORT)
    for g in ("spike", "spike_copy"):
        try:
            db.select_graph(g).delete()
        except Exception:  # noqa: BLE001
            pass
    g = db.select_graph("spike")

    record("server version", True, db.connection.execute_command("GRAPH.CONFIG", "GET", "*"))

    # --- basic writes, UNWIND MERGE ---
    attempt("UNWIND MERGE nodes", lambda: g.query(
        "UNWIND $rows AS r MERGE (p:Person {id: r.id}) SET p.name = r.name, p.team = r.team RETURN count(p)",
        {"rows": [{"id": f"p{i}", "name": f"Person {i}", "team": f"T{i % 3}"} for i in range(30)]}).result_set)
    attempt("UNWIND MERGE rels", lambda: g.query(
        "UNWIND $rows AS r MATCH (a:Person {id: r.a}), (b:Person {id: r.b}) MERGE (a)-[k:KNOWS]->(b) "
        "SET k.weight = r.w RETURN count(k)",
        {"rows": [{"a": f"p{i}", "b": f"p{(i + 1) % 30}", "w": 1.0 + i} for i in range(30)]
         + [{"a": f"p{i}", "b": f"p{(i * 7) % 30}", "w": 0.5} for i in range(0, 30, 3)]}).result_set)

    # --- null / map properties ---
    attempt("SET prop = null (removes)", lambda: rows(g.query(
        "MATCH (p:Person {id:'p0'}) SET p.tmp = null RETURN p.tmp, exists(p.tmp)")))
    attempt("UNWIND with null field in SET", lambda: rows(g.query(
        "UNWIND $rows AS r MERGE (x:Thing {id:r.id}) SET x.opt = r.opt RETURN x.id, x.opt",
        {"rows": [{"id": "t1", "opt": None}, {"id": "t2", "opt": "v"}]})))
    attempt("map property (expect failure)", lambda: rows(g.query(
        "MERGE (x:Thing {id:'m1'}) SET x.meta = {a: 1} RETURN x.meta")))
    attempt("SET x += map", lambda: rows(g.query(
        "MERGE (x:Thing {id:'m2'}) SET x += $props RETURN properties(x)", {"props": {"a": 1, "b": "two"}})))
    attempt("list property", lambda: rows(g.query(
        "MERGE (x:Thing {id:'l1'}) SET x.tags = ['a','b'] RETURN x.tags")))

    # --- indexes & constraints ---
    attempt("range index", lambda: g.query("CREATE INDEX FOR (p:Person) ON (p.id)").result_set)
    attempt("range index on edge prop", lambda: g.query("CREATE INDEX FOR ()-[k:KNOWS]-() ON (k.weight)").result_set)
    attempt("unique constraint (GRAPH.CONSTRAINT CREATE)", lambda: db.connection.execute_command(
        "GRAPH.CONSTRAINT", "CREATE", "spike", "UNIQUE", "NODE", "Person", "PROPERTIES", "1", "id"))

    def dup():
        try:
            g.query("CREATE (:Person {id:'p1'})")
            return "duplicate ALLOWED (constraint not enforced yet?)"
        except Exception as e:  # noqa: BLE001
            return f"rejected: {e}"
    attempt("unique constraint enforced", dup)
    attempt("db.constraints()", lambda: rows(g.query("CALL db.constraints()")))

    # --- full-text ---
    attempt("create docs", lambda: g.query(
        "UNWIND $rows AS r CREATE (:Document {id:r.id, title:r.title, text:r.text, kind:'slack'})",
        {"rows": [
            {"id": "d1", "title": "Crypto onboarding pause", "text": "We are pausing crypto client onboarding."},
            {"id": "d2", "title": "PricingSvc outage", "text": "PricingSvc is down; RiskEngine depends on it."},
            {"id": "d3", "title": "Project Falcon", "text": "Falcon term sheet attached, keep confidential."},
        ]}).result_set)
    attempt("fulltext index (multi-prop)", lambda: rows(g.query(
        "CALL db.idx.fulltext.createNodeIndex('Document', 'title', 'text')")))
    attempt("fulltext query + score", lambda: rows(g.query(
        "CALL db.idx.fulltext.queryNodes('Document', 'crypto') YIELD node, score RETURN node.id, score")))
    attempt("fulltext prefix / fuzzy", lambda: rows(g.query(
        "CALL db.idx.fulltext.queryNodes('Document', 'Pric* | %falcn%') YIELD node, score RETURN node.id, score")))
    attempt("fulltext DDL via CREATE FULLTEXT INDEX", lambda: g.query(
        "CREATE FULLTEXT INDEX FOR (p:Person) ON (p.name)").result_set)

    # --- vector index ---
    attempt("vector index DDL", lambda: g.query(
        "CREATE VECTOR INDEX FOR (d:Document) ON (d.embedding) "
        "OPTIONS {dimension: 4, similarityFunction: 'cosine'}").result_set)
    attempt("set vecf32", lambda: g.query(
        "UNWIND $rows AS r MATCH (d:Document {id:r.id}) SET d.embedding = vecf32(r.v) RETURN count(d)",
        {"rows": [{"id": "d1", "v": [1, 0, 0, 0]}, {"id": "d2", "v": [0, 1, 0, 0]},
                  {"id": "d3", "v": [0.7, 0.7, 0, 0]}]}).result_set)
    attempt("vector queryNodes (score semantics)", lambda: rows(g.query(
        "CALL db.idx.vector.queryNodes('Document', 'embedding', 3, vecf32($q)) YIELD node, score "
        "RETURN node.id, score ORDER BY score", {"q": [1, 0, 0, 0]})))
    attempt("vec.cosineDistance", lambda: rows(g.query(
        "MATCH (d:Document) RETURN d.id, vec.cosineDistance(d.embedding, vecf32($q)) AS dist ORDER BY dist",
        {"q": [1, 0, 0, 0]})))
    attempt("vector + graph filter after YIELD", lambda: rows(g.query(
        "CALL db.idx.vector.queryNodes('Document', 'embedding', 3, vecf32($q)) YIELD node, score "
        "WHERE node.kind = 'slack' RETURN node.id, score", {"q": [0, 1, 0, 0]})))
    attempt("db.indexes()", lambda: rows(g.query("CALL db.indexes()")))

    # --- algorithms ---
    attempt("dbms.procedures() algo list", lambda: [r[0] for r in rows(g.query(
        "CALL dbms.procedures() YIELD name RETURN name")) if "algo" in r[0].lower()])
    attempt("algo.pageRank", lambda: rows(g.query(
        "CALL algo.pageRank('Person', 'KNOWS') YIELD node, score RETURN node.id, score ORDER BY score DESC LIMIT 3")))
    attempt("algo.WCC", lambda: rows(g.query(
        "CALL algo.WCC({nodeLabels:['Person'], relationshipTypes:['KNOWS']}) YIELD node, componentId "
        "RETURN componentId, count(node) AS n")))
    attempt("algo.labelPropagation", lambda: rows(g.query(
        "CALL algo.labelPropagation({nodeLabels:['Person'], relationshipTypes:['KNOWS']}) YIELD node, communityId "
        "RETURN communityId, count(node) AS n")))
    attempt("algo.betweenness", lambda: rows(g.query(
        "CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['KNOWS']}) YIELD node, score "
        "RETURN node.id, score ORDER BY score DESC LIMIT 3")))
    attempt("algo.bfs", lambda: rows(g.query(
        "MATCH (a:Person {id:'p0'}) CALL algo.bfs(a, 3, 'KNOWS') YIELD nodes, edges "
        "RETURN size(nodes), size(edges)")))
    attempt("algo.SPpaths", lambda: rows(g.query(
        "MATCH (a:Person {id:'p0'}), (b:Person {id:'p5'}) "
        "CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['KNOWS'], weightProp: 'weight', "
        "maxLen: 10, pathCount: 2}) YIELD path, pathWeight, pathCost "
        "RETURN [n IN nodes(path) | n.id], pathWeight")))
    attempt("algo.SSpaths", lambda: rows(g.query(
        "MATCH (a:Person {id:'p0'}) CALL algo.SSpaths({sourceNode: a, relTypes: ['KNOWS'], maxLen: 2, "
        "pathCount: 1}) YIELD path RETURN count(path)")))
    attempt("shortestPath()", lambda: rows(g.query(
        "MATCH (a:Person {id:'p0'}), (b:Person {id:'p9'}) WITH a, b "
        "MATCH p = shortestPath((a)-[:KNOWS*..10]->(b)) RETURN length(p), [n IN nodes(p) | n.id]")))
    attempt("allShortestPaths()", lambda: rows(g.query(
        "MATCH (a:Person {id:'p0'}), (b:Person {id:'p9'}) WITH a, b "
        "MATCH p = allShortestPaths((a)-[:KNOWS*..10]->(b)) RETURN count(p)")))
    attempt("undirected var-length", lambda: rows(g.query(
        "MATCH (a:Person {id:'p0'})-[:KNOWS*1..2]-(b) RETURN count(DISTINCT b)")))

    # --- read-only query + timeout ---
    attempt("ro_query", lambda: rows(g.ro_query("MATCH (p:Person) RETURN count(p)")))

    def ro_write():
        try:
            g.ro_query("CREATE (:X)")
            return "write ALLOWED in RO_QUERY (bad)"
        except Exception as e:  # noqa: BLE001
            return f"rejected: {e}"
    attempt("ro_query rejects writes", ro_write)
    attempt("query timeout param", lambda: rows(g.query("MATCH (p:Person) RETURN count(p)", timeout=500)))

    # --- GRAPH.COPY ---
    attempt("GRAPH.COPY", lambda: db.connection.execute_command("GRAPH.COPY", "spike", "spike_copy"))
    attempt("copy has data + indexes", lambda: (
        rows(db.select_graph("spike_copy").query("MATCH (n) RETURN count(n)")),
        len(rows(db.select_graph("spike_copy").query("CALL db.indexes()")))))
    attempt("list_graphs", lambda: db.list_graphs())

    # --- datetime / temporal helpers ---
    attempt("timestamp() & localdatetime", lambda: rows(g.query(
        "RETURN timestamp(), localdatetime('2026-08-14T10:00:00')")))
    attempt("string ISO compare", lambda: rows(g.query("RETURN '2026-08-14' < '2026-09-01'")))


async def main_async() -> None:
    pool = BlockingConnectionPool(host="localhost", port=PORT, max_connections=16, timeout=None,
                                  decode_responses=True)
    db = AsyncFalkorDB(connection_pool=pool)
    g = db.select_graph("spike_async")
    await g.query("MERGE (:Counter {id:'c'})")

    async def writer(i: int):
        await g.query("UNWIND range(1, 50) AS j CREATE (:W {i:$i, j:j})", {"i": i})

    async def reader():
        return (await g.ro_query("MATCH (w:W) RETURN count(w)")).result_set[0][0]

    import time
    t0 = time.perf_counter()
    await asyncio.gather(*[writer(i) for i in range(40)], *[reader() for _ in range(40)])
    dt = time.perf_counter() - t0
    n = (await g.ro_query("MATCH (w:W) RETURN count(w)")).result_set[0][0]
    record("async client + pool: 40 writers + 40 readers", n == 2000, {"nodes": n, "seconds": round(dt, 3)})
    await g.delete()
    await pool.aclose()


if __name__ == "__main__":
    try:
        main_sync()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    try:
        asyncio.run(main_async())
    except Exception as e:  # noqa: BLE001
        record("async client + pool", False, f"{type(e).__name__}: {e}")
    print(f"\nSUMMARY: {sum(r['ok'] for r in RESULTS)}/{len(RESULTS)} passed")
