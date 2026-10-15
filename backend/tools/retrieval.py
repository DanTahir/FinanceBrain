"""Retrieval tools: hybrid search (vector + full-text + fact vectors, RRF-fused, graph-expanded), entity lookup,
neighbourhood, schema and guarded read-only Cypher. Every document is ACL-filtered for the calling persona in Cypher."""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict, defaultdict
from typing import Any

from falkordb import Edge, Node, Path

from backend.graph import queries as Q
from backend.llm.embeddings import embed
from backend.tools.base import (CONTENT_PROPS, OPS_LABELS, OPS_RELS, ToolContext, ToolError, clean_props, fetch_docs,
                                name_entities, resolve)

KINDS = ("email", "slack", "ticket", "doc", "meeting", "crm")
_STOP = frozenset("""the a an and or of to in on for with is are was were be been what who whom which why how when where
 did does do our we us you your it its this that these those about from by at as any all has have had there their they
 them can could should would will not no yes me my i tell show give list find get much many some more most""".split())

_QCACHE: OrderedDict[str, list[float]] = OrderedDict()


async def query_vector(text: str) -> list[float]:
    """voyage-4 query embedding with a small in-process LRU (guided questions repeat)."""
    key = text.strip().lower()
    if key in _QCACHE:
        _QCACHE.move_to_end(key)
        return _QCACHE[key]
    v = (await embed([text], "query"))[0]
    _QCACHE[key] = v
    while len(_QCACHE) > 512:
        _QCACHE.popitem(last=False)
    return v


def query_words(text: str) -> list[str]:
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text or "")]
    return list(dict.fromkeys(w for w in words if w not in _STOP))[:12]


def iso_or_none(s: str | None, name: str) -> str | None:
    if s is None or str(s).strip() == "":
        return None
    s = str(s).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?", s):
        raise ToolError(f"{name} must be an ISO date like 2026-08-01")
    return s


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


# ------------------------------------------------------------------------------------------------ search_knowledge
async def search_knowledge(ctx: ToolContext, query: str, k: int = 8, kinds: list[str] | None = None,
                           since: str | None = None, until: str | None = None, *,
                           vector: list[float] | None = None) -> dict:
    query = (query or "").strip()
    if not query:
        raise ToolError("query is required")
    k = max(1, min(int(k or 8), 15))
    kinds = [x for x in (kinds or []) if x in KINDS] or None
    since, until = iso_or_none(since, "since"), iso_or_none(until, "until")
    base = {"pid": ctx.pid, "kinds": kinds, "since": since, "until": until}
    v = vector if vector is not None else await query_vector(query)
    words = query_words(query)
    want = k * 3

    async def vec() -> list:
        over = max(80, want * 8)
        while True:
            rows = await ctx.q("SEARCH_DOCS_VECTOR", Q.SEARCH_DOCS_VECTOR, {**base, "v": v, "k": over, "limit": want})
            if len(rows) >= want or over >= 400:
                return rows
            over = 400  # low-privilege persona: most nearest neighbours were filtered out by the ACL

    async def fulltext() -> list:
        if not words:
            return []
        try:
            return await ctx.q("SEARCH_DOCS_FULLTEXT", Q.SEARCH_DOCS_FULLTEXT,
                               {**base, "q": " | ".join(words), "limit": want})
        except ToolError:
            return []

    async def facts() -> list:
        return await ctx.q("SEARCH_FACTS_VECTOR", Q.SEARCH_FACTS_VECTOR, {"pid": ctx.pid, "v": v, "k": 60, "limit": 8})

    vrows, frows, factrows = await asyncio.gather(vec(), fulltext(), facts())

    # reciprocal-rank fusion
    score: dict[str, float] = defaultdict(float)
    why: dict[str, list[str]] = defaultdict(list)
    for rank, (did, dist) in enumerate(vrows):
        score[did] += 1.0 / (60 + rank)
        why[did].append(f"semantic #{rank + 1}")
    for rank, (did, _s) in enumerate(frows):
        score[did] += 0.8 / (60 + rank)
        why[did].append(f"keyword #{rank + 1}")
    fact_out: list[dict] = []
    fact_docs: list[str] = []
    for rank, (fid, stmt, subj, pred, vf, vt, docs, dist) in enumerate(factrows):
        if dist is not None and dist > 0.55:
            continue
        fact_out.append(_drop_none({"statement": stmt, "subject": subj, "predicate": pred, "valid_from": vf,
                                    "valid_to": vt, "status": "superseded" if vt else "current", "cites": docs}))
        fact_docs.extend(docs)
        if kinds is None and since is None and until is None:
            for did in docs:
                score[did] += 0.5 / (60 + rank)
                why[did].append(f"supports fact #{rank + 1}")
    top = sorted(score, key=lambda d: -score[d])[:k]

    details = await fetch_docs(ctx, [*top, *fact_docs], words=words, size=900)

    # conversational context: visible neighbours in the same thread as the strongest hits
    context_ids: list[tuple[str, str]] = []
    tids = list(dict.fromkeys(details[d]["thread"] for d in top[:5] if d in details and details[d]["thread"]))
    if tids:
        trows = await ctx.q("THREAD_DOCS", Q.THREAD_DOCS, {"tids": tids, "pid": ctx.pid})
        seqs: dict[str, list[str]] = defaultdict(list)
        for tid, did, _ts in trows:
            seqs[tid].append(did)
        taken = set(top)
        for hit in top[:5]:
            tid = details.get(hit, {}).get("thread")
            seq = seqs.get(tid or "", [])
            if hit not in seq:
                continue
            i = seq.index(hit)
            for did in [*seq[i + 1:i + 3], *seq[max(0, i - 2):i]]:
                if did not in taken and len(context_ids) < 6:
                    context_ids.append((did, hit))
                    taken.add(did)
    cdetails = await fetch_docs(ctx, [c for c, _ in context_ids], words=words, size=450) if context_ids else {}

    all_docs = {**details, **cdetails}
    names = await name_entities(ctx, [m for d in all_docs.values() for m in d["mentions"][:10]])
    mention_names: dict[str, list[str]] = {}
    for d in all_docs.values():
        shown = []
        for lab, eid in d["mentions"][:10]:
            if lab == "Deal" and not await ctx.deal_visible(eid):
                continue
            nm = names.get(eid, eid)
            ctx.evidence.node(eid, lab, nm)
            ctx.evidence.edge(d["id"], "MENTIONS", eid)
            shown.append(nm if lab != "Client" else f"{nm} ({eid})")
        mention_names[d["id"]] = shown[:8]

    def item(d: dict, **extra: Any) -> dict:
        return _drop_none({"id": d["id"], "kind": d["kind"], "ts": d["ts"], "title": d["title"], "author": d["author"],
                           "channel": d["channel"], "ticket": d["ticket"], "status": d["ticket_status"],
                           "sensitivity": d["sensitivity"] if d["sensitivity"] not in (None, "normal") else None,
                           "mentions": mention_names.get(d["id"]), **extra, "text": d["text"]})

    results = [item(details[d], match=", ".join(why[d])) for d in top if d in details]
    results += [item(cdetails[c], context_for=hit) for c, hit in context_ids if c in cdetails]
    out: dict[str, Any] = {"query": query, "results": results, "facts": fact_out}
    if not results:
        out["note"] = ("No visible documents matched. Documents outside the caller's access are never returned; try "
                       "other wording or a specialised tool.")
    return out


# ------------------------------------------------------------------------------------------------ entities
async def _resolve_or_fail(ctx: ToolContext, entity: str, labels: set[str] | None = None) -> dict:
    ent = await resolve(ctx, entity, labels)
    if not ent:
        what = "/".join(sorted(labels)) if labels else "entity"
        raise ToolError(f"No {what} matching {entity!r} is visible to you. Use search_knowledge to find names/ids.")
    return ent


async def get_entity(ctx: ToolContext, entity: str) -> dict:
    ent = await _resolve_or_fail(ctx, entity)
    label, eid = ent["label"], ent["id"]
    if label == "Fact":
        raise ToolError("Facts are served by search_knowledge (which checks their sources against your access).")
    if label == "Document":
        docs = await fetch_docs(ctx, [eid], size=5000)
        d = docs[eid]
        names = await name_entities(ctx, d["mentions"][:25])
        return {"label": "Document", **_drop_none({k: d[k] for k in ("id", "kind", "ts", "title", "author", "channel",
                                                                       "ticket", "ticket_status", "sensitivity",
                                                                       "doc_type", "text")}),
                "mentions": [{"label": lab, "id": m, "name": names.get(m, m)} for lab, m in d["mentions"][:25]]}
    rows = await ctx.q(f"GET_ENTITY[{label}]", Q.GET_ENTITY[label], {"id": eid})
    props = rows[0][0] if rows else {}
    degree = {r[1]: r[2] for r in rows if r[1]}
    visible = label != "Deal" or await ctx.deal_visible(eid)
    ctx.evidence.node(eid, label, ent["name"])
    return {"label": label, "id": eid, "name": ent["name"],
            "properties": clean_props(label, props, deal_visible=visible), "relationships": degree}


async def neighborhood(ctx: ToolContext, entity: str, rel_types: list[str] | None = None, limit: int = 30) -> dict:
    ent = await _resolve_or_fail(ctx, entity)
    label, eid = ent["label"], ent["id"]
    if label == "Fact":
        raise ToolError("Facts are served by search_knowledge.")
    limit = max(1, min(int(limit or 30), 60))
    types = [t.strip().upper() for t in (rel_types or []) if t and t.strip()] or None
    rows = await ctx.q(f"NEIGHBORHOOD[{label}]", Q.NEIGHBORHOOD[label],
                       {"id": eid, "pid": ctx.pid, "types": types, "limit": limit * 2})
    ctx.evidence.node(eid, label, ent["name"])
    out: list[dict] = []
    counts: dict[str, int] = defaultdict(int)
    for rel, outgoing, mlabel, mid, mname, weight, ts, kind, amount in rows:
        if await ctx.node_hidden(mlabel, mid, mname):
            continue
        if len(out) >= limit:
            break
        name = mname
        if mlabel == "Transaction":
            name = f"{kind} USD {amount} {str(ts or '')[:10]}"
        elif mlabel == "Document":
            ctx.docs.setdefault(mid, {"id": mid, "kind": kind, "ts": ts, "title": mname, "display": mname})
        counts[rel] += 1
        out.append(_drop_none({"rel": rel, "direction": "out" if outgoing else "in", "label": mlabel, "id": mid,
                               "name": name, "weight": round(weight, 2) if isinstance(weight, float) else weight,
                               "ts": ts}))
        ctx.evidence.node(mid, mlabel, name)
        if outgoing:
            ctx.evidence.edge(eid, rel, mid, weight=weight)
        else:
            ctx.evidence.edge(mid, rel, eid, weight=weight)
    return {"entity": ent, "neighbors": out, "counts": dict(counts)}


# ------------------------------------------------------------------------------------------------ schema / cypher
SCHEMA_PATTERNS = """\
(:Person)-[:MEMBER_OF]->(:Team); (:Person)-[:REPORTS_TO]->(:Person)
(:Person|Team)-[:OWNS {valid_from, valid_to}]->(:System)            formal ownership, bitemporal
(:Person)-[:KNOWS_ABOUT {weight, answered, asked, documented, evidence, last_seen}]->(:System|Client|Deal|Topic|Counterparty)
(:System)-[:DEPENDS_ON]->(:System)
(:Person)-[:AUTHORED]->(:Document {kind, ts, sensitivity, doc_type})-[:POSTED_IN]->(:Channel)
(:Document)-[:IN_THREAD]->(:Thread); (:Document)-[:REPLY_TO]->(:Document); (:Document)-[:SENT_TO]->(:Person)
(:Person)-[:ATTENDED]->(:Document {kind:'meeting'}); (:Document {kind:'ticket'})-[:ASSIGNED_TO]->(:Person)
(:Document)-[:MENTIONS]->(:Person|Team|System|Client|Account|Deal|Counterparty|Transaction|PiiToken|Document)
(:Document)-[:ABOUT]->(:Topic|Client)
(:Decision {status, effective})-[:DECIDED_IN|JUSTIFIED_BY|CITES]->(:Document); (:Decision)-[:SUPERSEDES]->(:Decision)
(:Fact {subject_id, predicate, valid_from, valid_to})-[:CITES]->(:Document); (:Fact)-[:SUPERSEDES]->(:Fact)
(:Client {segment, risk_rating, kyc_status})-[:HOLDS]->(:Account {kind, status}); (:Person)-[:MANAGES]->(:Client)
(:Transaction {ts, amount, currency, kind, memo, fraud_score})-[:FROM_ACCOUNT|TO_ACCOUNT]->(:Account)
(:Transaction)-[:COUNTERPARTY]->(:Counterparty {country})
(:Client|Account|Counterparty)-[:HAS_IDENTIFIER]->(:PiiToken {type})   a token shared by several owners = shared phone/address/device
(:Person)-[:ON_DEAL_TEAM]->(:Deal {sensitivity})
(:Account|Counterparty)-[:FLOWS_TO {amount, n, first_ts, last_ts, max_fraud_score, alerts}]->(:Account|Counterparty)   derived money flow
(:Person)-[:COMMUNICATES {weight, direct, replies, last}]->(:Person)   derived from non-MNPI messages and replies
(:Person|Team)-[:CAN_ACCESS]->(:Document|Channel)                      access control
"""
_SCHEMA_CACHE: dict[str, tuple[float, dict]] = {}


async def graph_schema(ctx: ToolContext) -> dict:
    hit = _SCHEMA_CACHE.get(ctx.graph_name)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    labels, rels = await asyncio.gather(ctx.q("LABEL_COUNTS", Q.LABEL_COUNTS), ctx.q("REL_COUNTS", Q.REL_COUNTS))
    out = {"graph": ctx.graph_name, "labels": {lab: n for lab, n in labels if lab != "Meta"},
           "relationships": {t: n for t, n in rels}, "patterns": SCHEMA_PATTERNS,
           "notes": ("Ids: e_ people, team_ teams, sys_ systems, c_ clients, a_ accounts, t_ transactions, deal_ deals, "
                     "dec_ decisions, documents like slack-00012. Timestamps are ISO strings (compare as strings). "
                     "PII appears only as ⟨TYPE_NNNN⟩ tokens. run_cypher_readonly cannot read document/fact content "
                     "(text, title, statement, codename...): use the other tools for content.")}
    _SCHEMA_CACHE[ctx.graph_name] = (time.monotonic(), out)
    return out


_BLOCK = re.compile(
    r"\.\s*`?(?:text|title|statement|object|object_key|summary|codename|target|redactions|title_redactions|embedding)`?\b"
    r"|\bproperties\s*\(|\b(?:create|merge|set|delete|detach|remove|drop|foreach)\b|\bload\s+csv\b"
    r"|db\.idx\.(?:fulltext|vector)\.query", re.I)
_OPS_RE = re.compile(r":\s*`?(?:Case|Action|Task)`?\b|\b(?:EVIDENCE|PROPOSED|FOR_CASE|TARGETS|HANDED_OFF_TO|CLAIMED_BY|"
                     r"APPROVED_BY|REJECTED_BY|OPENED_BY|REQUESTED_BY)\b")


def _ser(v: Any, hidden_docs: set[str], terms: list[str], hide_ops: bool = False) -> Any:
    """Serialize driver values; strip content properties, mask invisible documents, restricted deal terms and (for
    personas without case access) case-management nodes and edges."""
    def rec(x: Any) -> Any:
        return _ser(x, hidden_docs, terms, hide_ops)

    if isinstance(v, Node):
        label = v.labels[0] if v.labels else "Node"
        if hide_ops and label in OPS_LABELS:
            return {"_label": "⟨restricted⟩"}
        props = {k: x for k, x in (v.properties or {}).items() if k not in CONTENT_PROPS}
        if label == "Document":
            if props.get("id") in hidden_docs:
                return {"_label": "Document", "id": "⟨restricted⟩"}
            props = {k: props[k] for k in ("id", "kind", "ts", "sensitivity") if k in props}
        return {"_label": label, **{k: rec(x) for k, x in props.items()}}
    if isinstance(v, Edge):
        if hide_ops and v.relation in OPS_RELS:
            return {"_type": "⟨restricted⟩"}
        return {"_type": v.relation, **{k: rec(x) for k, x in (v.properties or {}).items()}}
    if isinstance(v, Path):
        nodes = v.nodes() if callable(getattr(v, "nodes", None)) else getattr(v, "_nodes", [])
        edges = v.edges() if callable(getattr(v, "edges", None)) else getattr(v, "_edges", [])
        return {"_path": [rec(n) for n in nodes],
                "_rels": ["⟨restricted⟩" if hide_ops and e.relation in OPS_RELS else e.relation for e in edges]}
    if isinstance(v, (list, tuple)):
        return [rec(x) for x in v[:200]]
    if isinstance(v, dict):
        return {k: rec(x) for k, x in v.items()}
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, str):
        low = v.lower()
        if any(t in low for t in terms):
            return "⟨restricted⟩"
        return v[:300] + "…" if len(v) > 300 else v
    return v


def _doc_node_ids(v: Any, acc: set[str]) -> None:
    if isinstance(v, Node):
        if v.labels and v.labels[0] == "Document" and (v.properties or {}).get("id"):
            acc.add(v.properties["id"])
    elif isinstance(v, (list, tuple)):
        for x in v:
            _doc_node_ids(x, acc)
    elif isinstance(v, dict):
        for x in v.values():
            _doc_node_ids(x, acc)
    elif isinstance(v, Path):
        nodes = v.nodes() if callable(getattr(v, "nodes", None)) else []
        for n in nodes:
            _doc_node_ids(n, acc)


async def run_cypher_readonly(ctx: ToolContext, cypher: str, params: dict | None = None, limit: int = 50) -> dict:
    c = (cypher or "").strip().rstrip(";").strip()
    if not c:
        raise ToolError("cypher is required")
    if len(c) > 2000:
        raise ToolError("query too long (max 2000 chars)")
    m = _BLOCK.search(c)
    if m:
        raise ToolError(f"Rejected ({m.group(0)!r}): run_cypher_readonly is for structural and aggregate questions. "
                        "Writes are not allowed, and document/fact content (text, title, statement, codename...) is only "
                        "served by the ACL-checked tools such as search_knowledge and get_entity.")
    hide_ops = not ctx.auth.can("act")
    if hide_ops and _OPS_RE.search(c):
        raise ToolError("Rejected: case-management data (cases, actions, tasks, handoffs) is restricted to Compliance "
                        "and AML personas.")
    limit = max(1, min(int(limit or 50), 100))
    rows = await ctx.q("run_cypher_readonly", c, params or {}, timeout_ms=5000)
    docs: set[str] = set()
    _doc_node_ids(rows[:limit], docs)
    hidden = docs - await ctx.visible_docs(docs) if docs else set()
    terms = await ctx.restricted_terms()
    out = [_ser(r, hidden, terms, hide_ops) for r in rows[:limit]]
    return {"rows": out, "row_count": len(rows), "truncated": len(rows) > limit}
