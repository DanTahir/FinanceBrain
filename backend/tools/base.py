"""Shared plumbing for the graph tools: per-call context, traced Cypher, evidence subgraph, document hydration,
entity resolution and the information-barrier filter."""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from typing import Any

from backend.config import get_settings
from backend.graph import queries as Q
from backend.graph.db import brain_graph, get_db
from backend.tenancy import AuthContext


class ToolError(Exception):
    """Expected, user-facing failure (bad argument, unknown entity, not permitted). Shown to the model verbatim."""


DOC_ID_RE = re.compile(r"\b(?:slack|email|ticket|doc|meeting|crm)-\d{5}\b")
_ID_RE = re.compile(r"^(?:e_\d+|team_\w+|sys_\w+|c_\d+|a_\d+|t_\w+|deal_\w+|topic_\w+|f_[0-9a-f]+|dec_\w+|cp_\w+|"
                    r"case_\w+|task_\w+|(?:slack|email|ticket|doc|meeting|crm)-\d{5}|⟨[A-Z_]+_\d+⟩)$")
_PREFIXES = (("team_", "Team"), ("topic_", "Topic"), ("sys_", "System"), ("deal_", "Deal"), ("dec_", "Decision"),
             ("case_", "Case"), ("task_", "Task"), ("act_", "Action"), ("e_", "Person"), ("c_", "Client"),
             ("a_", "Account"), ("f_", "Fact"), ("t_", "Transaction"))
# Labels whose *names* can betray a walled-off deal (e.g. channel #project-falcon): filtered by restricted terms.
RESTRICTABLE = frozenset({"Channel", "Thread", "Topic", "Decision", "Fact", "Deal", "Document"})
# Properties that carry document / fact / MNPI content: never returned by raw Cypher, only by ACL-checked tools.
CONTENT_PROPS = frozenset({"text", "title", "statement", "object", "object_key", "summary", "codename", "target",
                           "redactions", "title_redactions", "embedding"})


def label_for_id(eid: str) -> str | None:
    if DOC_ID_RE.fullmatch(eid):
        return "Document"
    if eid.startswith("⟨") and eid.endswith("⟩"):
        return "PiiToken"
    for prefix, label in _PREFIXES:
        if eid.startswith(prefix):
            return label
    return None


def _sanitize(params: dict | None) -> dict:
    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        if isinstance(v, list) and len(v) >= 64 and isinstance(v[0], float):
            out[k] = f"<{len(v)}-d query vector>"
        elif isinstance(v, list) and len(v) > 25:
            out[k] = [*v[:25], f"... (+{len(v) - 25})"]
        else:
            out[k] = v
    return out


def _ids_in(value: Any, acc: list[str], limit: int = 80) -> None:
    if len(acc) >= limit:
        return
    if isinstance(value, str):
        if _ID_RE.match(value) and value not in acc:
            acc.append(value)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _ids_in(v, acc, limit)
    elif isinstance(value, dict):
        for v in value.values():
            _ids_in(v, acc, limit)


class Evidence:
    """Accumulates the nodes and edges a tool touched; becomes the answer's evidence subgraph."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    def node(self, eid: str | None, label: str | None = None, name: Any = None, **extra: Any) -> None:
        if not eid:
            return
        n = self.nodes.setdefault(eid, {"id": eid})
        if label and not n.get("label"):
            n["label"] = label
        if name and not n.get("name"):
            n["name"] = str(name)[:140]
        for k, v in extra.items():
            if v is not None:
                n[k] = v

    def edge(self, src: str | None, rel: str, dst: str | None, **extra: Any) -> None:
        if not src or not dst:
            return
        e = self.edges.setdefault((src, rel, dst), {"source": src, "target": dst, "type": rel})
        e.update({k: v for k, v in extra.items() if v is not None})

    def merge(self, data: dict) -> None:
        for n in data.get("nodes") or []:
            n = dict(n)
            self.node(n.pop("id", None), **n)
        for e in data.get("edges") or []:
            e = dict(e)
            self.edge(e.pop("source", None), e.pop("type", "RELATED"), e.pop("target", None), **e)

    def to_dict(self) -> dict:
        for eid, n in self.nodes.items():
            n.setdefault("label", label_for_id(eid) or "Entity")
            n.setdefault("name", eid)
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}


def doc_display(d: dict) -> str:
    if d.get("title"):
        return str(d["title"])
    who = d.get("author") or "?"
    where = f" in {d['channel']}" if d.get("channel") else ""
    return f"{d.get('kind', 'doc')} by {who}{where}, {str(d.get('ts') or '')[:10]}"


class ToolContext:
    """Everything one tool invocation needs: the caller's tenant graph and persona, plus trace and evidence sinks."""

    def __init__(self, auth: AuthContext) -> None:
        self.auth = auth
        self.graph_name = brain_graph(auth.tenant)
        self.g = get_db().select_graph(self.graph_name)
        self.trace: list[dict] = []
        self.evidence = Evidence()
        self.docs: dict[str, dict] = {}
        self._deals: dict[str, dict] | None = None

    @property
    def pid(self) -> str:
        return self.auth.person_id

    @property
    def today(self) -> str:
        return get_settings().demo_today

    async def q(self, name: str, cypher: str, params: dict | None = None, *, timeout_ms: int = 10_000,
                write: bool = False) -> list[list[Any]]:
        """Run one Cypher query (GRAPH.RO_QUERY unless write=True) and record it in the trace."""
        t0 = time.perf_counter()
        entry: dict[str, Any] = {"query": name, "cypher": cypher, "params": _sanitize(params)}
        try:
            fn = self.g.query if write else self.g.ro_query
            res = await fn(cypher, params or {}, timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            entry.update(error=str(e)[:300], ms=round((time.perf_counter() - t0) * 1000, 1))
            self.trace.append(entry)
            raise ToolError(f"graph query {name} failed: {str(e)[:200]}") from e
        rows = res.result_set or []
        ids: list[str] = []
        _ids_in(rows, ids)
        entry.update(rows=len(rows), ms=round((time.perf_counter() - t0) * 1000, 1), ids=ids)
        self.trace.append(entry)
        return rows

    # ---------------------------------------------------------------- information barrier
    async def deals(self) -> dict[str, dict]:
        if self._deals is None:
            rows = await self.q("VISIBLE_DEALS", Q.VISIBLE_DEALS, {"pid": self.pid})
            self._deals = {}
            for did, codename, target, sens, on_team, compliance in rows:
                visible = sens != "MNPI" or bool(on_team) or bool(compliance)
                self._deals[did] = {"codename": codename, "target": target, "visible": visible}
        return self._deals

    async def deal_visible(self, did: str) -> bool:
        return (await self.deals()).get(did, {}).get("visible", True)

    async def restricted_terms(self) -> list[str]:
        terms: list[str] = []
        for d in (await self.deals()).values():
            if d["visible"]:
                continue
            for t in (d["codename"], d["target"]):
                if not t:
                    continue
                low = str(t).lower()
                terms.append(low)
                stripped = re.sub(r"^project\s+", "", low)
                if len(stripped) >= 4:
                    terms.append(stripped)
        return terms

    async def is_restricted(self, *texts: Any) -> bool:
        terms = await self.restricted_terms()
        if not terms:
            return False
        blob = " ".join(str(t) for t in texts if t).lower()
        return any(term in blob for term in terms)

    async def node_hidden(self, label: str, nid: str, name: Any) -> bool:
        """True if a node returned by a structural query must be withheld by the information barrier."""
        if label == "Deal" and not await self.deal_visible(nid):
            return True
        return label in RESTRICTABLE and label != "Document" and await self.is_restricted(name)

    async def visible_docs(self, ids) -> set[str]:
        ids = list(dict.fromkeys(i for i in ids if i))
        if not ids:
            return set()
        rows = await self.q("VISIBLE_DOCS", Q.VISIBLE_DOCS, {"ids": ids, "pid": self.pid})
        return {r[0] for r in rows}

    # ---------------------------------------------------------------- documents
    def register_doc(self, d: dict) -> None:
        """Remember a document the caller is allowed to see (citation registry + evidence node)."""
        did = d["id"]
        entry = {k: d.get(k) for k in ("id", "kind", "ts", "title", "author", "channel", "ticket", "sensitivity")
                 if d.get(k) is not None}
        if d.get("text"):
            entry["snippet"] = str(d["text"])[:320]
        entry["display"] = doc_display(d)
        self.docs[did] = {**self.docs.get(did, {}), **entry}
        self.evidence.node(did, "Document", entry["display"], kind=d.get("kind"), ts=d.get("ts"))
        if d.get("author_id"):
            self.evidence.node(d["author_id"], "Person", d.get("author"))
            self.evidence.edge(d["author_id"], "AUTHORED", did)
        if d.get("channel_id"):
            self.evidence.node(d["channel_id"], "Channel", d.get("channel"))
            self.evidence.edge(did, "POSTED_IN", d["channel_id"])

    def envelope(self, result: Any) -> dict:
        return {"result": result, "_trace": self.trace, "_evidence": self.evidence.to_dict(),
                "_docs": list(self.docs.values())}


def _window(text: str, words: list[str] | tuple[str, ...], size: int) -> str:
    """A `size`-char excerpt of `text`, centred near the first query-word hit."""
    if len(text) <= size:
        return text
    low = text.lower()
    hits = [p for p in (low.find(w) for w in words) if p >= 0]
    pos = min(hits) if hits else 0
    start = max(0, min(pos - size // 3, len(text) - size))
    return ("…" if start else "") + text[start:start + size] + ("…" if start + size < len(text) else "")


async def fetch_docs(ctx: ToolContext, ids, *, words=(), size: int = 700, register: bool = True) -> dict[str, dict]:
    """Hydrate visible documents (ACL re-checked in Cypher). Invisible or unknown ids are silently dropped."""
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return {}
    rows = await ctx.q("DOC_DETAILS", Q.DOC_DETAILS, {"ids": ids, "pid": ctx.pid})
    out: dict[str, dict] = {}
    for (did, kind, ts, title, text, sens, doc_type, tkey, tstatus, aid, aname, cid, cname, tid, mentions) in rows:
        d = {"id": did, "kind": kind, "ts": ts, "title": title, "author_id": aid, "author": aname, "channel_id": cid,
             "channel": cname, "thread": tid, "sensitivity": sens, "doc_type": doc_type, "ticket": tkey,
             "ticket_status": tstatus, "text": _window(text or "", list(words), size),
             "mentions": [(m[0], m[1]) for m in (mentions or []) if m and m[1]]}
        out[did] = d
        if register:
            ctx.register_doc(d)
    return out


async def name_entities(ctx: ToolContext, pairs) -> dict[str, str]:
    """Display names for (label, id) pairs, one batched query per label."""
    by_label: dict[str, set[str]] = {}
    for label, eid in pairs:
        if label in Q.ENTITY_NAMES:
            by_label.setdefault(label, set()).add(eid)

    async def one(label: str, ids: set[str]) -> list:
        return await ctx.q(f"ENTITY_NAMES[{label}]", Q.ENTITY_NAMES[label], {"ids": sorted(ids)})

    results = await asyncio.gather(*(one(lab, ids) for lab, ids in by_label.items()))
    return {r[0]: r[1] for rows in results for r in rows if r[1] is not None}


# -------------------------------------------------------------------- entity resolution
_CATALOG: dict[str, tuple[float, list[dict]]] = {}


async def catalog(ctx: ToolContext) -> list[dict]:
    hit = _CATALOG.get(ctx.graph_name)
    if hit and time.monotonic() - hit[0] < 120:
        return hit[1]
    rows = await ctx.q("ENTITY_CATALOG", Q.ENTITY_CATALOG)
    items = [{"label": r[0], "id": r[1], "name": r[2] or r[1], "alt": [r[3]] if r[3] else []} for r in rows]
    _CATALOG[ctx.graph_name] = (time.monotonic(), items)
    return items


def invalidate_catalog(graph_name: str | None = None) -> None:
    if graph_name is None:
        _CATALOG.clear()
    else:
        _CATALOG.pop(graph_name, None)


def _norm(s: str) -> str:
    return re.sub(r"^(project|the)\s+", "", re.sub(r"\s+", " ", s.strip().lower()))


async def resolve(ctx: ToolContext, text: str | None, labels: set[str] | None = None) -> dict | None:
    """Resolve an id or a free-text name to {label, id, name}, respecting ACL and the information barrier."""
    t = (text or "").strip().strip("\"'`")
    if not t:
        return None
    lab = label_for_id(t)
    if lab and (labels is None or lab in labels) and lab in Q.ENTITY_NAMES:
        rows = await ctx.q(f"ENTITY_NAMES[{lab}]", Q.ENTITY_NAMES[lab], {"ids": [t]})
        if rows:
            return await _vet(ctx, {"label": lab, "id": rows[0][0], "name": rows[0][1]})
    cands = []
    for c in await catalog(ctx):
        if labels is not None and c["label"] not in labels:
            continue
        if c["label"] == "Deal" and not await ctx.deal_visible(c["id"]):
            continue
        cands.append(c)
    low = _norm(t)

    def names(c: dict) -> list[str]:
        return [_norm(n) for n in (c["name"], *c["alt"]) if n]

    for c in cands:
        if c["id"].lower() == t.lower() or low in names(c):
            return await _vet(ctx, c)
    best, best_len = None, 0
    for c in cands:
        for n in names(c):
            if len(n) >= 4 and (n in low or (len(low) >= 4 and low in n)) and len(n) > best_len:
                best, best_len = c, len(n)
    if best:
        return await _vet(ctx, best)
    pool = {n: c for c in cands for n in names(c)}
    close = difflib.get_close_matches(low, list(pool), n=1, cutoff=0.8)
    return await _vet(ctx, pool[close[0]]) if close else None


async def _vet(ctx: ToolContext, ent: dict) -> dict | None:
    out = {"label": ent["label"], "id": ent["id"], "name": ent.get("name") or ent["id"]}
    if out["label"] == "Document" and out["id"] not in await ctx.visible_docs([out["id"]]):
        return None
    if out["label"] == "Deal" and not await ctx.deal_visible(out["id"]):
        return None
    if out["label"] in RESTRICTABLE and await ctx.is_restricted(out["name"]):
        return None
    return out


def clean_props(label: str, props: dict, *, deal_visible: bool = True, max_len: int = 1500) -> dict:
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if k == "embedding":
            continue
        if label == "Deal" and not deal_visible and k in ("codename", "target"):
            out[k] = "⟨restricted⟩"
            continue
        if isinstance(v, str) and len(v) > max_len:
            v = v[:max_len] + "…"
        out[k] = v
    return out
