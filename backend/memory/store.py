"""Per-user memory graphs: episodic memory (Session -> Turn) and semantic memory (MemoryFact) in a private FalkorDB
graph per tenant, persona and visitor: ``mem__<tenant>__<persona>__<visitor>``.

Memory is separate per user *and* per tenant because it lives in a different graph, not behind a filter. Memories
reference company entities only by stable id (``EntityRef`` stubs), never by copying company content, and every
text is PII-scrubbed before it is written. Turns and memories carry voyage-4 vectors for recall.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.graph import queries as Q
from backend.graph.db import delete_graph, get_db, graph_exists, memory_graph
from backend.graph.schema import apply_schema, ensure_meta, wait_constraints
from backend.llm import embeddings
from backend.llm.guard import scrub_pii
from backend.tenancy import AuthContext
from backend.tools.base import _ID_RE, label_for_id

log = logging.getLogger(__name__)

FACT_KINDS = ("preference", "fact", "goal", "open_thread")
MAX_ACTIVE_FACTS = 200       # older current memories beyond this are retired (closed, not deleted)
TURN_CHARS = 4000
EMBED_CHARS = 2000
# Calibrated on voyage-4 (query vs memory sentence): relevant 0.44-0.63, unrelated mostly >= 0.72;
# a re-worded copy of the same sentence is ~0.003 (document vs document).
RECALL_MAX_DISTANCE = 0.68   # cosine distance cut-off for "relevant" memories
DUP_DISTANCE = 0.06          # a new memory this close to an existing one is treated as the same memory
SESSION_RE = re.compile(r"^sess_[0-9a-f]{10}$")
_READY: set[str] = set()     # memory graphs whose schema and User node exist (per process)
_PRESENT: set[str] = set()   # memory graphs seen to exist (per process), so reads skip the existence check


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def new_session_id() -> str:
    return new_id("sess")


def memory_owner(auth: AuthContext) -> str:
    user = re.sub(r"[^a-z0-9]+", "_", (auth.user or "anon").lower()).strip("_")[:40] or "anon"
    return f"{auth.persona}__{user}"


def memory_graph_for(auth: AuthContext) -> str:
    return memory_graph(auth.tenant, memory_owner(auth))


def _hide_vectors(v: Any) -> Any:
    """Trace-friendly params: vectors become a placeholder, long lists are truncated."""
    if isinstance(v, list):
        if len(v) >= 64 and isinstance(v[0], float):
            return f"<{len(v)}-d vector>"
        out = [_hide_vectors(x) for x in v[:25]]
        if len(v) > 25:
            out.append(f"... (+{len(v) - 25})")
        return out
    if isinstance(v, dict):
        return {k: _hide_vectors(x) for k, x in v.items()}
    return v


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def entity_refs(ids, names: dict | None = None, cited=()) -> list[dict]:
    """EntityRef rows for company entity ids (anything else is dropped)."""
    names = names or {}
    cited = set(cited)
    out, seen = [], set()
    for i in ids or []:
        if not isinstance(i, str) or i in seen or not _ID_RE.match(i):
            continue
        seen.add(i)
        out.append({"id": i, "label": label_for_id(i) or "Entity", "name": str(names.get(i) or i)[:140],
                    "cited": i in cited})
    return out


class MemoryStore:
    """All reads and writes of one user's memory graph; every query is recorded in ``trace`` like a tool query."""

    def __init__(self, auth: AuthContext, trace: list | None = None) -> None:
        self.auth = auth
        self.uid = memory_owner(auth)
        self.name = memory_graph_for(auth)
        self.g = get_db().select_graph(self.name)
        self.trace: list[dict] = trace if trace is not None else []

    async def q(self, name: str, cypher: str, params: dict | None = None, *, write: bool = False,
                timeout_ms: int = 10_000) -> list[list[Any]]:
        # Memory graphs are created lazily: the first write creates the schema and User node; reads against a
        # graph that does not exist yet simply find nothing (FalkorDB errors on queries against a missing key).
        if write and name != "MEM_USER":
            await self.ensure()
        elif not write and not await self._present():
            self.trace.append({"query": name, "cypher": cypher, "graph": self.name, "rows": 0,
                               "note": "memory graph not created yet"})
            return []
        t0 = time.perf_counter()
        entry: dict[str, Any] = {"query": name, "cypher": cypher, "params": _hide_vectors(params or {}),
                                 "graph": self.name}
        try:
            fn = self.g.query if write else self.g.ro_query
            res = await fn(cypher, params or {}, timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            entry.update(error=str(e)[:300], ms=round((time.perf_counter() - t0) * 1000, 1))
            self.trace.append(entry)
            raise
        rows = res.result_set or []
        entry.update(rows=len(rows), ms=round((time.perf_counter() - t0) * 1000, 1))
        self.trace.append(entry)
        return rows

    # ------------------------------------------------------------------------------------------------ lifecycle
    async def exists(self) -> bool:
        return self.name in _READY or await graph_exists(self.name)

    async def _present(self) -> bool:
        if self.name in _READY or self.name in _PRESENT:
            return True
        if await graph_exists(self.name):
            _PRESENT.add(self.name)
            return True
        return False

    async def ensure(self) -> None:
        """Create the memory graph lazily on first use: indexes (incl. vector), :Meta, the User node."""
        if self.name in _READY:
            return
        await apply_schema(self.name, "memory")
        await ensure_meta(self.name, self.auth.tenant, "memory")
        await wait_constraints(self.name)
        await self.q("MEM_USER", Q.MEM_USER, {"uid": self.uid, "now": now_iso(), "tenant": self.auth.tenant,
                                              "persona": self.auth.persona, "pid": self.auth.person_id}, write=True)
        _READY.add(self.name)

    async def forget_all(self) -> None:
        await delete_graph(self.name)
        _READY.discard(self.name)
        _PRESENT.discard(self.name)

    async def _embed(self, texts: list[str], kind: str = "document") -> list[list[float]] | None:
        try:
            return await embeddings.embed([t[:EMBED_CHARS] for t in texts], kind)
        except Exception as e:  # noqa: BLE001  (memory must keep working without vectors)
            log.warning("memory embedding failed: %s", str(e)[:200])
            return None

    # ------------------------------------------------------------------------------------------------ episodic
    async def start_session(self, sid: str, agent: str | None = None) -> dict:
        rows = await self.q("MEM_SESSION", Q.MEM_SESSION, {"uid": self.uid, "sid": sid, "now": now_iso(),
                                                           "agent": agent}, write=True)
        _, turns, cn = rows[0] if rows else (sid, 0, 0)
        return {"id": sid, "turns": int(turns or 0), "consolidated_n": int(cn or 0)}

    async def session_info(self, sid: str) -> dict | None:
        rows = await self.q("MEM_SESSION_INFO", Q.MEM_SESSION_INFO, {"sid": sid})
        if not rows:
            return None
        keys = ("id", "title", "summary", "started", "updated", "turns", "agent", "consolidated_n")
        return dict(zip(keys, rows[0]))

    async def add_turn(self, sid: str, n: int, role: str, text: str, *, agent: str | None = None,
                       tools: list[str] | None = None, cost: float | None = None, refs: list[dict] | None = None,
                       embed: bool = True) -> str:
        tid = new_id("turn")
        text = (text or "")[:TURN_CHARS]
        props = _clean({"agent": agent, "tools": list(tools) if tools else None, "cost_usd": cost})
        await self.q("MEM_ADD_TURN", Q.MEM_ADD_TURN, {"sid": sid, "id": tid, "n": n, "ts": now_iso(), "role": role,
                                                     "text": text, "props": props}, write=True)
        if n > 1:
            await self.q("MEM_LINK_TURN", Q.MEM_LINK_TURN, {"sid": sid, "prev": n - 1, "id": tid}, write=True)
        if refs:
            await self.q("MEM_TURN_REFS", Q.MEM_TURN_REFS, {"tid": tid, "refs": refs[:60]}, write=True)
        if embed and text.strip():
            vecs = await self._embed([text])
            if vecs:
                await self.q("MEM_SET_EMBEDDINGS[Turn]", Q.MEM_SET_EMBEDDINGS["Turn"],
                             {"rows": [{"id": tid, "v": vecs[0]}]}, write=True)
        return tid

    async def turns(self, sid: str, after: int = 0) -> list[dict]:
        rows = await self.q("MEM_TURNS", Q.MEM_TURNS, {"sid": sid, "after": after})
        return [{"id": tid, "n": int(n), "role": role, "agent": agent, "text": text or "", "ts": ts,
                 "tools": list(tools or []), "cost_usd": cost, "refs": [r for r in refs if r]}
                for tid, n, role, agent, text, ts, tools, cost, refs in rows]

    async def history(self, sid: str, limit: int = 12) -> list[dict]:
        """Prior turns of a session as chat messages (the graph, not the browser, holds conversation history)."""
        return [{"role": t["role"], "content": t["text"]} for t in await self.turns(sid)
                if t["role"] in ("user", "assistant") and t["text"]][-limit:]

    async def sessions(self, limit: int = 20) -> list[dict]:
        rows = await self.q("MEM_SESSIONS", Q.MEM_SESSIONS, {"uid": self.uid, "limit": limit})
        keys = ("id", "title", "summary", "started", "updated", "turns", "agent", "consolidated_n")
        return [dict(zip(keys, r)) for r in rows]

    async def set_session(self, sid: str, **props: Any) -> None:
        await self.q("MEM_SET_SESSION", Q.MEM_SET_SESSION, {"sid": sid, "props": _clean(props)}, write=True)

    async def forget_session(self, sid: str) -> bool:
        await self.q("MEM_DELETE_TURNS", Q.MEM_DELETE_TURNS, {"sid": sid}, write=True)
        rows = await self.q("MEM_DELETE_SESSION", Q.MEM_DELETE_SESSION, {"sid": sid}, write=True)
        return bool(rows)

    # ------------------------------------------------------------------------------------------------ semantic
    async def facts(self, limit: int = 60, include_retired: bool = False) -> list[dict]:
        rows = await self.q("MEM_FACTS", Q.MEM_FACTS, {"all": include_retired, "limit": limit})
        keys = ("id", "kind", "text", "created", "valid_to", "session", "by", "superseded_by", "about")
        return [{k: v for k, v in zip(keys, r) if v not in (None, [])} for r in rows]

    async def _nearest(self, vec: list[float]) -> tuple[str, float, str, str] | None:
        rows = await self.q("MEM_FACT_SEARCH", Q.MEM_FACT_SEARCH, {"k": 1, "v": vec})
        if not rows:
            return None
        fid, kind, text, _created, _sess, score = rows[0]
        return fid, float(score), text, kind

    async def add_facts(self, items: list[dict], *, sid: str | None = None, by: str = "librarian") -> list[dict]:
        """Store memories ``{text, kind, about?, replaces?}``. Near-duplicates of a current memory are not stored
        twice; ``replaces`` closes the old memory with SUPERSEDES. Returns what happened to each item."""
        prepared = []
        for it in items:
            text, _ = scrub_pii(str(it.get("text") or "").strip())
            if len(text) < 3:
                continue
            kind = str(it.get("kind") or "fact").lower()
            prepared.append({"text": text[:500], "kind": kind if kind in FACT_KINDS else "fact",
                             "about": [a for a in (it.get("about") or []) if isinstance(a, str)][:8],
                             "replaces": it.get("replaces") or None})
        if not prepared:
            return []
        vecs = await self._embed([p["text"] for p in prepared])
        now, out = now_iso(), []
        for i, p in enumerate(prepared):
            vec = vecs[i] if vecs else None
            if vec is not None:
                near = await self._nearest(vec)
                # A near-identical current memory means nothing new was learned; this also covers "replacing" a
                # memory with a restatement of itself, which would only churn SUPERSEDES edges.
                if near and near[1] <= DUP_DISTANCE and (not p["replaces"] or near[0] == p["replaces"]):
                    out.append({"id": near[0], "kind": near[3], "text": near[2], "duplicate_of": near[0]})
                    continue
            fid = new_id("mf")
            await self.q("MEM_ADD_FACT", Q.MEM_ADD_FACT, {
                "uid": self.uid, "id": fid, "kind": p["kind"], "text": p["text"], "now": now, "by": by,
                "props": _clean({"source_session": sid})}, write=True)
            if sid:
                await self.q("MEM_FACT_SOURCE", Q.MEM_FACT_SOURCE, {"id": fid, "sid": sid}, write=True)
            refs = entity_refs(p["about"])
            if refs:
                await self.q("MEM_FACT_ABOUT", Q.MEM_FACT_ABOUT, {"id": fid, "refs": refs}, write=True)
            if vec is not None:
                await self.q("MEM_SET_EMBEDDINGS[MemoryFact]", Q.MEM_SET_EMBEDDINGS["MemoryFact"],
                             {"rows": [{"id": fid, "v": vec}]}, write=True)
            replaced = None
            if p["replaces"]:
                rows = await self.q("MEM_SUPERSEDE", Q.MEM_SUPERSEDE, {"new": fid, "old": p["replaces"], "now": now},
                                    write=True)
                replaced = rows[0][0] if rows else None
            out.append(_clean({"id": fid, "kind": p["kind"], "text": p["text"], "about": p["about"] or None,
                               "replaced": replaced}))
        await self.q("MEM_CAP", Q.MEM_CAP, {"keep": MAX_ACTIVE_FACTS, "now": now}, write=True)
        return out

    async def retire(self, ids: list[str]) -> list[str]:
        if not ids:
            return []
        rows = await self.q("MEM_RETIRE", Q.MEM_RETIRE, {"ids": list(ids), "now": now_iso()}, write=True)
        return [r[0] for r in rows]

    async def forget_fact(self, fid: str) -> bool:
        return bool(await self.q("MEM_FORGET_FACT", Q.MEM_FORGET_FACT, {"id": fid}, write=True))

    # ------------------------------------------------------------------------------------------------ recall
    async def recall(self, query: str, k: int = 6, exclude_session: str = "") -> dict:
        vecs = await self._embed([query], "query")
        if not vecs:
            return {"facts": (await self.facts(limit=k)), "turns": [], "method": "most recent (embedding unavailable)"}
        v = vecs[0]
        f_rows = await self.q("MEM_FACT_SEARCH", Q.MEM_FACT_SEARCH, {"k": k * 2, "v": v})
        t_rows = await self.q("MEM_TURN_SEARCH", Q.MEM_TURN_SEARCH, {"k": k * 2, "v": v, "exclude": exclude_session})
        facts = [{"id": fid, "kind": kind, "text": text, "created": created, "session": sess,
                  "distance": round(float(score), 4)}
                 for fid, kind, text, created, sess, score in f_rows if float(score) <= RECALL_MAX_DISTANCE][:k]
        turns = [{"turn_id": tid, "role": role, "agent": agent, "text": (text or "")[:700], "ts": ts,
                  "session_id": sid, "session_title": title, "distance": round(float(score), 4)}
                 for tid, role, agent, text, ts, sid, title, score in t_rows if float(score) <= RECALL_MAX_DISTANCE][:k]
        return {"facts": facts, "turns": turns, "method": "vector recall (voyage-4, cosine distance)"}

    async def context_block(self, message: str, *, exclude_session: str = "") -> tuple[str | None, dict]:
        """What to tell the agent about this user before it answers: preferences, memories relevant to the
        message, and summaries of recent sessions. Returns (system-message text or None, UI info)."""
        current = await self.facts(limit=80)
        sessions = [s for s in await self.sessions(limit=6) if s["id"] != exclude_session and s.get("summary")][:3]
        if not current and not sessions:
            return None, {}
        # Preferences and open threads are always in context; other memories only when relevant to the message.
        prefs = [f for f in current if f["kind"] == "preference"][:8]
        threads = [f for f in current if f["kind"] == "open_thread"][:4]
        relevant: list[dict] = []
        if current:
            rec = await self.recall(message, k=5, exclude_session=exclude_session)
            pinned = {p["id"] for p in prefs + threads}
            relevant = [f for f in rec["facts"] if f["id"] not in pinned][:5]
        if not (prefs or threads or relevant or sessions):
            return None, {}
        lines = ["Long-term memory about this user, from their private memory graph. Use it to personalise the answer "
                 "and pick up earlier threads; follow stated preferences without commenting on them. Memory is not a "
                 "source: verify company facts with tools and cite documents, never the memory."]
        if prefs:
            lines.append("Preferences:")
            lines += [f"- {p['text']} ({p['id']})" for p in prefs]
        if threads:
            lines.append("Open threads (unfinished work to pick up when relevant):")
            lines += [f"- {t['text']} ({t['id']}, {str(t.get('created') or '')[:10]})" for t in threads]
        if relevant:
            lines.append("Relevant memories:")
            lines += [f"- [{f['kind']}] {f['text']} ({f['id']}, {str(f.get('created') or '')[:10]})" for f in relevant]
        if sessions:
            lines.append("Recent sessions:")
            lines += [f"- {str(s.get('updated') or '')[:10]} \"{s.get('title') or 'untitled'}\" ({s['id']}): "
                      f"{s.get('summary')}" for s in sessions]
        info = {"graph": self.name, "preferences": prefs, "open_threads": threads, "facts": relevant,
                "sessions": [{k: s.get(k) for k in ("id", "title", "summary", "updated")} for s in sessions]}
        return "\n".join(lines), info

    async def overview(self) -> dict:
        """Memory Inspector payload: what the agents remember about this user."""
        if not await self.exists():
            return {"graph": self.name, "exists": False, "counts": {}, "sessions": [], "facts": []}
        counts = {label: int(n) for label, n in await self.q("MEM_COUNTS", Q.MEM_COUNTS)}
        return {"graph": self.name, "exists": True, "counts": counts, "sessions": await self.sessions(limit=30),
                "facts": await self.facts(limit=120, include_retired=True)}
