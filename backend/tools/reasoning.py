"""Reasoning tools: expertise (who_knows), bitemporal ownership (who_owns), decision trails, blast radius and paths."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from backend.graph import queries as Q
from backend.tools.base import ToolContext, ToolError, fetch_docs, resolve
from backend.tools.retrieval import _drop_none, _resolve_or_fail, query_vector, query_words

AUTHORITATIVE = "runbooks, architecture docs, model cards, methodologies, guides, postmortems, procedures"


def _day(s: str | None, default: str) -> str:
    if s is None or not str(s).strip():
        return default
    m = re.match(r"^\d{4}-\d{2}-\d{2}", str(s).strip())
    if not m:
        raise ToolError("dates must be ISO formatted, e.g. 2026-08-15")
    return m.group(0)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _why(answered: int, asked: int, documented: int, evidence: int, last_seen: str | None) -> str:
    parts = []
    if answered:
        parts.append(f"answered {_plural(answered, 'question')} from colleagues")
    if documented:
        parts.append(f"wrote {_plural(documented, 'authoritative doc')}")
    if asked:
        parts.append(f"asked {_plural(asked, 'question')}")
    parts.append(f"involved in {_plural(evidence, 'document')}")
    if last_seen:
        parts.append(f"last active {str(last_seen)[:10]}")
    return ", ".join(parts)


def _expert(pid, name, title, team, end, score, evidence, answered, asked, documented, last_seen, today) -> dict:
    e = _drop_none({"person_id": pid, "name": name, "title": title, "team": team, "score": round(float(score or 0), 2),
                    "answered": int(answered or 0), "asked": int(asked or 0), "documented": int(documented or 0),
                    "documents": int(evidence or 0), "last_seen": last_seen, "end_date": end})
    e["why"] = _why(e["answered"], e["asked"], e["documented"], e["documents"], last_seen)
    if end:
        e["status"] = f"left the company on {end}" if end <= today else f"leaving on {end}"
    return e


def bus_factor(experts: list[dict], today: str) -> dict:
    """Concentration of *demonstrated* knowledge (answers + authoritative docs; falls back to weight)."""
    if not experts:
        return {"level": "unknown", "summary": "Nobody has demonstrated knowledge of this in the graph."}
    signal = [e["answered"] + e["documented"] for e in experts]
    if sum(signal) < 3:
        signal = [e["score"] for e in experts]
    total = sum(signal) or 1
    i_top = max(range(len(experts)), key=lambda i: signal[i])
    top = experts[i_top]
    share = signal[i_top] / total
    others = sorted((s for i, s in enumerate(signal) if i != i_top), reverse=True)
    backup = others[0] / signal[i_top] if others and signal[i_top] else 0.0
    end = top.get("end_date")
    if end:
        level = "critical"
    elif share >= 0.5 or backup < 0.34:
        level = "high"
    elif share >= 0.33:
        level = "medium"
    else:
        level = "low"
    summary = (f"{top['name']} holds {share:.0%} of the demonstrated knowledge (answers and authoritative docs); "
               f"the next person has {backup:.0%} of that.")
    if end:
        summary += f" {top['name']} {'left' if end <= today else 'is leaving'} on {end}."
    return {"level": level, "key_person": top["name"], "top_share": round(share, 2), "backup_ratio": round(backup, 2),
            "summary": summary}


async def _ownership(ctx: ToolContext, sid: str) -> list[dict]:
    rows = await ctx.q("OWNERSHIP_HISTORY", Q.OWNERSHIP_HISTORY, {"id": sid})
    return [_drop_none({"owner_type": lab, "owner_id": oid, "owner": name, "title": title, "valid_from": vf,
                        "valid_to": vt, "source": src}) for lab, oid, name, title, vf, vt, src in rows]


def _active(h: dict, day: str) -> bool:
    return (h.get("valid_from") or "") <= day and (h.get("valid_to") is None or h["valid_to"] > day)


# ------------------------------------------------------------------------------------------------ who_knows
async def who_knows(ctx: ToolContext, about: str, limit: int = 6) -> dict:
    about = (about or "").strip()
    if not about:
        raise ToolError("about is required (a system, client, deal, counterparty or a free-text subject)")
    limit = max(1, min(int(limit or 6), 15))
    ent = await resolve(ctx, about, set(Q.KNOWS_LABELS))
    experts: list[dict] = []
    owners: list[dict] = []
    if ent:
        rows = await ctx.q(f"WHO_KNOWS_ENTITY[{ent['label']}]", Q.WHO_KNOWS_ENTITY[ent["label"]],
                           {"id": ent["id"], "limit": limit})
        for pid, name, title, team, end, w, ev, ans, ask, doc, last in rows:
            experts.append(_expert(pid, name, title, team, end, w, ev, ans, ask, doc, last, ctx.today))
        basis: dict[str, Any] = {"entity": ent, "method": "KNOWS_ABOUT edges into the entity"}
        if ent["label"] == "System":
            owners = [h for h in await _ownership(ctx, ent["id"]) if _active(h, ctx.today)]
        ctx.evidence.node(ent["id"], ent["label"], ent["name"])
        for e in experts:
            ctx.evidence.node(e["person_id"], "Person", e["name"], title=e.get("title"))
            ctx.evidence.edge(e["person_id"], "KNOWS_ABOUT", ent["id"], weight=e["score"])
        for h in owners:
            ctx.evidence.node(h["owner_id"], h["owner_type"], h["owner"])
            ctx.evidence.edge(h["owner_id"], "OWNS", ent["id"], valid_from=h.get("valid_from"))
    else:
        v = await query_vector(about)
        rows = await ctx.q("WHO_KNOWS_TOPICS", Q.WHO_KNOWS_TOPICS, {"v": v, "k": 8, "max_dist": 0.5})
        agg: dict[str, dict] = {}
        topics: dict[str, str] = {}
        for pid, name, title, team, end, tid, tname, dist, w, ev, ans, ask, doc, last in rows:
            if await ctx.is_restricted(tname):
                continue  # information barrier: topics derived from deals the caller is walled off from
            topics[tid] = tname
            sim = max(0.0, 1.0 - float(dist))
            a = agg.setdefault(pid, {"row": [pid, name, title, team, end], "w": 0.0, "ev": 0, "ans": 0, "ask": 0,
                                     "doc": 0, "last": None, "topics": set()})
            a["w"] += float(w or 0) * sim
            a["ev"] += int(ev or 0)
            a["ans"] += int(ans or 0)
            a["ask"] += int(ask or 0)
            a["doc"] += int(doc or 0)
            a["last"] = max(filter(None, [a["last"], last]), default=None)
            a["topics"].add(tname)
            ctx.evidence.node(tid, "Topic", tname)
            ctx.evidence.node(pid, "Person", name, title=title)
            ctx.evidence.edge(pid, "KNOWS_ABOUT", tid, weight=round(float(w or 0), 2))
        for a in sorted(agg.values(), key=lambda a: -a["w"])[:limit]:
            e = _expert(*a["row"], a["w"], a["ev"], a["ans"], a["ask"], a["doc"], a["last"], ctx.today)
            e["topics"] = sorted(a["topics"])[:5]
            experts.append(e)
        basis = {"topics": sorted(topics.values())[:8], "method": "KNOWS_ABOUT edges into the nearest topics"}
    return {"about": about, "basis": basis, "experts": experts, "formal_owners": owners,
            "bus_factor": bus_factor(experts, ctx.today),
            "how_scored": ("KNOWS_ABOUT is derived from activity with 45-day recency decay. Answering colleagues' questions "
                           f"and writing authoritative docs ({AUTHORITATIVE}) weigh most; asking questions or being "
                           "mentioned weigh little. Formal ownership (OWNS) is listed separately.")}


# ------------------------------------------------------------------------------------------------ who_owns
async def who_owns(ctx: ToolContext, system: str, as_of: str | None = None) -> dict:
    ent = await _resolve_or_fail(ctx, system, {"System"})
    day = _day(as_of, ctx.today)
    hist = await _ownership(ctx, ent["id"])
    frows = await ctx.q("OWNERSHIP_FACTS", Q.OWNERSHIP_FACTS, {"id": ent["id"], "pid": ctx.pid})
    facts = [_drop_none({"statement": stmt, "predicate": pred, "object": obj, "valid_from": vf, "valid_to": vt,
                         "status": "superseded" if vt else "current", "cites": docs})
             for _fid, stmt, pred, obj, vf, vt, docs in frows]
    await fetch_docs(ctx, [d for f in facts for d in f.get("cites", [])], size=300)
    ctx.evidence.node(ent["id"], "System", ent["name"])
    for h in hist:
        ctx.evidence.node(h["owner_id"], h["owner_type"], h["owner"])
        ctx.evidence.edge(h["owner_id"], "OWNS", ent["id"], valid_from=h.get("valid_from"), valid_to=h.get("valid_to"))
    return {"system": ent, "as_of": day, "owners_as_of": [h for h in hist if _active(h, day)],
            "current_owners": [h for h in hist if _active(h, ctx.today)], "history": hist, "facts": facts,
            "note": "OWNS edges are formal, bitemporal ownership (valid_from inclusive, valid_to exclusive). "
                    "Use who_knows for demonstrated expertise."}


# ------------------------------------------------------------------------------------------------ trace_decision
_ROLE = {"DECIDED_IN": "decided in", "JUSTIFIED_BY": "justified by", "CITES": "cited"}


async def trace_decision(ctx: ToolContext, decision: str | None = None) -> dict:
    rows = await ctx.q("LIST_DECISIONS", Q.LIST_DECISIONS)
    decs = []
    for did, title, status, eff, summary in rows:
        if await ctx.is_restricted(title, summary):
            continue
        decs.append(_drop_none({"id": did, "title": title, "status": status, "effective": eff, "summary": summary}))
    if not decs:
        return {"decisions": [], "note": "No decisions are recorded in the graph yet."}
    pick = None
    if decision and decision.strip():
        key = decision.strip().lower()
        pick = next((d for d in decs if d["id"].lower() == key), None)
        if pick is None:
            words = query_words(decision)

            def overlap(d: dict) -> int:
                text = f"{d.get('title', '')} {d.get('summary', '')}".lower()
                return sum(1 for w in words if w in text)

            best = max(decs, key=overlap)
            pick = best if overlap(best) > 0 else None
    elif len(decs) == 1:
        pick = decs[0]
    if pick is None:
        return {"decisions": decs, "note": "Pass one of these decision ids (or words from its title) as `decision`."}

    trail = await ctx.q("DECISION_TRAIL", Q.DECISION_TRAIL, {"pid": ctx.pid, "id": pick["id"]})
    total = (await ctx.q("DECISION_SOURCE_COUNT", Q.DECISION_SOURCE_COUNT, {"id": pick["id"]}))[0][0]
    links = await ctx.q("DECISION_LINKS", Q.DECISION_LINKS, {"id": pick["id"]})
    roles: dict[str, list[str]] = defaultdict(list)
    for rel, did, _ts in trail:
        if did:
            roles[did].append(_ROLE.get(rel, rel.lower()))
    words = query_words(f"{pick.get('title', '')} {pick.get('summary', '')}")
    docs = await fetch_docs(ctx, list(roles), words=words, size=450)
    timeline = sorted(
        (_drop_none({"ts": d["ts"], "id": did, "kind": d["kind"], "role": ", ".join(roles[did]),
                     "author": d["author"], "channel": d["channel"], "title": d["title"], "excerpt": d["text"]})
         for did, d in docs.items()), key=lambda x: x.get("ts") or "")
    prev, nxt, topics = links[0] if links else ([], [], [])
    supersedes = [_drop_none({"id": p[0], "title": p[1], "effective": p[2]}) for p in prev if p and p[0]]
    superseded_by = [_drop_none({"id": p[0], "title": p[1], "effective": p[2]}) for p in nxt if p and p[0]]
    ctx.evidence.node(pick["id"], "Decision", pick.get("title"), status=pick.get("status"))
    for did in docs:
        for rel in roles[did]:
            ctx.evidence.edge(pick["id"], {v: k for k, v in _ROLE.items()}.get(rel, "CITES"), did)
    for s in supersedes:
        ctx.evidence.node(s["id"], "Decision", s.get("title"))
        ctx.evidence.edge(pick["id"], "SUPERSEDES", s["id"])
    for s in superseded_by:
        ctx.evidence.node(s["id"], "Decision", s.get("title"))
        ctx.evidence.edge(s["id"], "SUPERSEDES", pick["id"])
    out: dict[str, Any] = {"decision": pick, "timeline": timeline, "supersedes": supersedes,
                           "superseded_by": superseded_by, "topics": [t for t in topics if t][:8],
                           "other_decisions": [d for d in decs if d["id"] != pick["id"]]}
    hidden = int(total or 0) - len(docs)
    if hidden > 0:
        out["hidden_sources"] = f"{hidden} further source document(s) exist but are outside your access."
    return out


# ------------------------------------------------------------------------------------------------ blast_radius
async def blast_radius(ctx: ToolContext, system: str, depth: int = 4) -> dict:
    ent = await _resolve_or_fail(ctx, system, {"System"})
    depth = max(1, min(int(depth or 4), 6))
    down = [r for r in await ctx.q("BLAST_RADIUS", Q.BLAST_RADIUS, {"id": ent["id"]}) if r[3] <= depth]
    up = await ctx.q("UPSTREAM", Q.UPSTREAM, {"id": ent["id"]})
    ids = list(dict.fromkeys([ent["id"], *(r[0] for r in down), *(r[0] for r in up)]))
    edges = await ctx.q("DEPENDENCY_EDGES", Q.DEPENDENCY_EDGES, {"ids": ids})
    oe = await ctx.q("SYSTEM_OWNERS_EXPERTS", Q.SYSTEM_OWNERS_EXPERTS, {"ids": [ent["id"], *(r[0] for r in down)]})
    info = {sid: {"owners": [o for o in owners if o], "experts": [n for n in experts if n],
                  "departing_experts": [n for n, e in zip(experts, ends) if n and e]}
            for sid, owners, experts, ends in oe}
    drows = await ctx.q("SYSTEM_DOCS", Q.SYSTEM_DOCS, {"pid": ctx.pid, "id": ent["id"], "limit": 6})
    docs = await fetch_docs(ctx, [r[0] for r in drows], words=query_words(ent["name"]), size=300)
    impacted = [_drop_none({"id": sid, "name": name, "tier": tier, "hops": hops, **info.get(sid, {})})
                for sid, name, tier, hops in down]
    by_hops: dict[int, list[str]] = defaultdict(list)
    for x in impacted:
        by_hops[x["hops"]].append(x["name"])
    ctx.evidence.node(ent["id"], "System", ent["name"], focus=True)
    for sid, name, tier, _h in [*down, *up]:
        ctx.evidence.node(sid, "System", name, tier=tier)
    for a, b in edges:
        ctx.evidence.edge(a, "DEPENDS_ON", b)
    return {"system": {**ent, **info.get(ent["id"], {})}, "impacted_count": len(impacted),
            "impacted": impacted, "by_hops": {str(h): names for h, names in sorted(by_hops.items())},
            "depends_on": [_drop_none({"id": sid, "name": name, "tier": tier, "hops": hops}) for sid, name, tier, hops in up],
            "related_docs": [_drop_none({"id": d["id"], "kind": d["kind"], "ts": d["ts"], "title": d["title"],
                                         "excerpt": d["text"]}) for d in docs.values()],
            "note": "Impacted = systems that transitively DEPENDS_ON this one (hops = shortest dependency distance)."}


# ------------------------------------------------------------------------------------------------ find_path
async def find_path(ctx: ToolContext, source: str, target: str, max_hops: int = 4) -> dict:
    a = await _resolve_or_fail(ctx, source)
    b = await _resolve_or_fail(ctx, target)
    if a["id"] == b["id"]:
        raise ToolError("source and target are the same entity")
    hops = max(2, min(int(max_hops or 4), 6))
    rows = await ctx.q(f"FIND_PATH[{hops}]", Q.FIND_PATH[hops], {"a": a["id"], "b": b["id"]}, timeout_ms=8000)
    doc_ids = {n[1] for nodes, _ in rows for n in nodes if n[0] == "Document"}
    visible = await ctx.visible_docs(doc_ids) if doc_ids else set()
    paths: list[dict] = []
    hidden = 0
    for nodes, rels in rows:
        blocked = False
        for lab, nid, name in nodes:
            if (lab == "Document" and nid not in visible) or await ctx.node_hidden(lab, nid, name):
                blocked = True
                break
        if blocked:
            hidden += 1
            continue
        steps = [f"{nodes[0][0]} {nodes[0][2]}"]
        for rel, (lab, _nid, name) in zip(rels, nodes[1:]):
            steps.append(f"-[{rel}]- {lab} {name}")
        paths.append({"hops": len(rels), "nodes": [{"label": lab, "id": nid, "name": name} for lab, nid, name in nodes],
                      "rels": rels, "text": " ".join(steps)})
        for lab, nid, name in nodes:
            ctx.evidence.node(nid, lab, name)
            if lab == "Document":
                ctx.docs.setdefault(nid, {"id": nid, "title": name, "display": name})
        for rel, x, y in zip(rels, nodes, nodes[1:]):
            ctx.evidence.edge(x[1], rel, y[1])
    out: dict[str, Any] = {"source": a, "target": b, "max_hops": hops, "path_count": len(paths), "paths": paths[:6]}
    if hidden:
        out["hidden_paths"] = f"{hidden} path(s) pass through documents or deals outside your access and were omitted."
    if not paths:
        out["note"] = f"No visible connection within {hops} hops."
    return out
