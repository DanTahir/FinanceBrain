"""Investigation tools for fraud / AML and information-barrier work, built on FalkorDB graph algorithms (WCC,
betweenness, PageRank) and the derived FLOWS_TO / COMMUNICATES edges.

Ring detection, identifier linking, fund tracing and barrier checks cross client and deal boundaries by design, so
they require the `act` scope (Compliance and AML personas). key_people runs on the internal communication network
(built only from non-MNPI messages) and is open to every persona.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any

from backend.graph import queries as Q
from backend.tools.base import ToolContext, ToolError, fetch_docs, name_entities
from backend.tools.retrieval import _drop_none, _resolve_or_fail, iso_or_none

# How strongly one shared identifier links two clients. Shared addresses and birth dates happen innocently (family,
# same building); a shared device, phone or SSN rarely does.
ID_WEIGHT = {"SSN": 4.0, "DEVICE_ID": 3.0, "EIN": 3.0, "PHONE": 2.5, "EMAIL": 2.5, "ADDRESS": 1.0, "CLIENT_NAME": 1.0,
             "DOB": 0.5}
LINK_TYPES = ("SSN", "DEVICE_ID", "EIN", "PHONE", "EMAIL", "ADDRESS")
MAX_SHARE = 10  # a token held by more clients than this is a hub (an office address), not a personal link
MAX_COMPONENT = 40  # larger WCC components are hub-merged noise, not rings


def _r(x: Any, n: int = 2) -> Any:
    return round(x, n) if isinstance(x, float) else x


def _flow(r: list) -> dict:
    sl, sid, sname, dl, did, dname, amount, n, first, last, risk, alerts = r
    return {"from": sid, "from_label": sl, "from_name": sname, "to": did, "to_label": dl, "to_name": dname,
            "amount": _r(amount), "n": n, "first": first, "last": last, "max_fraud_score": _r(risk), "alerts": alerts}


def _flow_out(f: dict) -> dict:
    return _drop_none({"from": f["from"] if f["from_label"] == "Account" else f"{f['from_name']} ({f['from']})",
                       "to": f["to"] if f["to_label"] == "Account" else f"{f['to_name']} ({f['to']})",
                       "amount": f["amount"], "transactions": f["n"], "first": str(f["first"] or "")[:10],
                       "last": str(f["last"] or "")[:10], "alerts": f["alerts"] or None})


def _flow_evidence(ctx: ToolContext, f: dict) -> None:
    for side in ("from", "to"):
        lab = f[f"{side}_label"]
        ctx.evidence.node(f[side], lab, f[f"{side}_name"] if lab == "Counterparty" else f[side])
    ctx.evidence.edge(f["from"], "FLOWS_TO", f["to"], amount=f["amount"], n=f["n"])


async def _holders(ctx: ToolContext, account_ids) -> dict[str, list[str]]:
    ids = sorted({a for a in account_ids if a})
    if not ids:
        return {}
    out: dict[str, list[str]] = defaultdict(list)
    for aid, cid in await ctx.q("ACCOUNT_HOLDERS", Q.ACCOUNT_HOLDERS, {"ids": ids}):
        out[aid].append(cid)
    return out


def _client_info(rows: list) -> tuple[dict[str, dict], dict[str, list[str]]]:
    info: dict[str, dict] = {}
    accts: dict[str, list[str]] = defaultdict(list)
    for cid, name, risk, kyc, aid, akind, astatus in rows:
        c = info.setdefault(cid, _drop_none({"id": cid, "name": name, "risk_rating": risk, "kyc_status": kyc,
                                             "accounts": []}) | {"accounts": []})
        if aid:
            c["accounts"].append(_drop_none({"id": aid, "kind": akind, "status": astatus}))
            accts[cid].append(aid)
    return info, accts


# ------------------------------------------------------------------------------------------------ alerts
async def list_alerts(ctx: ToolContext, min_score: float = 0.8, limit: int = 15) -> dict:
    ctx.require("act", "list_alerts")
    min_score = max(0.0, min(float(0.8 if min_score is None else min_score), 1.0))
    limit = max(1, min(int(limit or 15), 50))
    rows = await ctx.q("ALERTS", Q.ALERTS, {"min_score": min_score, "limit": limit})
    holders = await _holders(ctx, [r[7] for r in rows] + [r[8] for r in rows])
    out = []
    for tid, kind, amount, cur, ts, alert, score, fa, ta, cp, cpname, cpc in rows:
        out.append(_drop_none({"id": tid, "kind": kind, "amount": _r(amount), "currency": cur, "ts": ts, "alert": alert,
                               "fraud_score": _r(score), "from_account": fa, "from_holders": holders.get(fa),
                               "to_account": ta, "to_holders": holders.get(ta),
                               "counterparty": f"{cpname} ({cp}, {cpc})" if cp else None}))
        ctx.evidence.node(tid, "Transaction", f"{kind} USD {amount} {str(ts or '')[:10]}", alert=alert)
        if fa:
            ctx.evidence.edge(tid, "FROM_ACCOUNT", fa)
        if ta:
            ctx.evidence.edge(tid, "TO_ACCOUNT", ta)
        if cp:
            ctx.evidence.node(cp, "Counterparty", cpname)
            ctx.evidence.edge(tid, "COUNTERPARTY", cp)
    return {"alerts": out, "min_score": min_score}


# ------------------------------------------------------------------------------------------------ linked accounts
async def _seed_clients(ctx: ToolContext, ent: dict) -> list[str]:
    label, eid = ent["label"], ent["id"]
    if label == "Client":
        return [eid]
    if label == "Account":
        return (await _holders(ctx, [eid])).get(eid, [])
    if label == "PiiToken":
        return [r[1] for r in await ctx.q("TOKEN_HOLDERS", Q.TOKEN_HOLDERS, {"ids": [eid]})]
    rows = await ctx.q("TXN_PARTIES", Q.TXN_PARTIES, {"ids": [eid]})
    out: list[str] = []
    for tid, kind, amount, ts, alert, fs, fa, fc, ta, tc, cp, cpname in rows:
        ctx.evidence.node(tid, "Transaction", f"{kind} USD {amount} {str(ts or '')[:10]}", alert=alert)
        for acct, rel, holder in ((fa, "FROM_ACCOUNT", fc), (ta, "TO_ACCOUNT", tc)):
            if acct:
                ctx.evidence.edge(tid, rel, acct)
                if holder:
                    ctx.evidence.edge(holder, "HOLDS", acct)
                    out.append(holder)
    return list(dict.fromkeys(out))


async def linked_accounts(ctx: ToolContext, entity: str, hops: int = 2,
                          identifier_types: list[str] | None = None) -> dict:
    ctx.require("act", "linked_accounts")
    hops = max(1, min(int(hops or 2), 3))
    types = [t.strip().upper() for t in (identifier_types or []) if t and t.strip().upper() in ID_WEIGHT]
    types = types or list(LINK_TYPES)
    ent = await _resolve_or_fail(ctx, entity, {"Client", "Account", "Transaction", "PiiToken"})
    seeds = await _seed_clients(ctx, ent)
    if not seeds:
        raise ToolError(f"{ent['id']} has no client holder to link from.")
    depth = {c: 0 for c in seeds}
    via: dict[str, list[dict]] = defaultdict(list)
    frontier = list(seeds)
    for hop in range(1, hops + 1):
        if not frontier:
            break
        rows = await ctx.q("SHARED_IDENTIFIERS", Q.SHARED_IDENTIFIERS,
                           {"ids": frontier, "types": types, "max_share": MAX_SHARE})
        nxt: list[str] = []
        for cid, tok, typ, holders, others in rows:
            ctx.evidence.node(tok, "PiiToken", tok, type=typ)
            ctx.evidence.edge(cid, "HAS_IDENTIFIER", tok)
            for o in others:
                ctx.evidence.edge(o, "HAS_IDENTIFIER", tok)
                if o not in depth:
                    depth[o] = hop
                    nxt.append(o)
                if depth[o] == hop and len(via[o]) < 4:
                    via[o].append({"shares": typ, "token": tok, "with": cid})
        frontier = nxt
    clients = list(depth)
    info, accts_of = _client_info(await ctx.q("CLIENT_ACCOUNTS", Q.CLIENT_ACCOUNTS, {"ids": clients}))
    acct_set = {a for c in clients for a in accts_of.get(c, [])}
    flows = [_flow(r) for r in await ctx.q("FLOWS_AROUND", Q.FLOWS_AROUND, {"ids": sorted(acct_set)})] if acct_set else []
    internal = [f for f in flows if f["from"] in acct_set and f["to"] in acct_set]
    for c in clients:
        ctx.evidence.node(c, "Client", info.get(c, {}).get("name", c))
        for a in accts_of.get(c, []):
            ctx.evidence.node(a, "Account", a)
            ctx.evidence.edge(c, "HOLDS", a)
    for f in internal:
        _flow_evidence(ctx, f)
    linked = [{**info.get(c, {"id": c}), "hops": depth[c], "via": via.get(c) or None}
              for c in sorted(clients, key=lambda x: (depth[x], x))]
    return {"start": ent, "seed_clients": seeds, "identifier_types": types,
            "linked": [_drop_none(x) for x in linked],
            "internal_flows": [_flow_out(f) for f in internal[:20]],
            "summary": (f"{len(clients) - len(seeds)} other client(s) linked to {', '.join(seeds)} within {hops} hop(s) "
                        f"through shared identifiers ({', '.join(types)}); {len(internal)} money flow(s) between "
                        f"their accounts. Tokens held by more than {MAX_SHARE} clients are ignored as hubs.")}


# ------------------------------------------------------------------------------------------------ ring detection
async def detect_rings(ctx: ToolContext, min_clients: int = 3, limit: int = 5) -> dict:
    ctx.require("act", "detect_rings")
    min_clients = max(2, min(int(min_clients or 3), 10))
    limit = max(1, min(int(limit or 5), 10))
    comps = await ctx.q("RING_COMPONENTS", Q.RING_COMPONENTS, {"min_clients": min_clients}, timeout_ms=20_000)
    comps = [(cid, sorted(set(cl))) for cid, cl in comps if len(set(cl)) <= MAX_COMPONENT]
    method = ("algo.WCC over (:Client)-[:HAS_IDENTIFIER]->(:PiiToken) groups clients connected through any chain of "
              "shared identifiers; each component is risk-scored from identifier strength, FLOWS_TO transfers between "
              "member accounts, common exit counterparties, sub-10k structuring deposits and alerts, and member "
              "accounts are ranked by algo.betweenness over FLOWS_TO.")
    if not comps:
        return {"rings": [], "components_considered": 0, "method": method,
                "note": f"No group of {min_clients}+ clients shares identifiers."}
    clients = sorted({c for _, cl in comps for c in cl})
    tok_rows, acct_rows = await asyncio.gather(
        ctx.q("RING_SHARED_TOKENS", Q.RING_SHARED_TOKENS, {"ids": clients}),
        ctx.q("CLIENT_ACCOUNTS", Q.CLIENT_ACCOUNTS, {"ids": clients}))
    info, accts_of = _client_info(acct_rows)
    accts = sorted({a for c in clients for a in accts_of.get(c, [])})
    act_rows, flow_rows, cent_rows = await asyncio.gather(
        ctx.q("ACCOUNT_ACTIVITY", Q.ACCOUNT_ACTIVITY, {"ids": accts}),
        ctx.q("FLOWS_AROUND", Q.FLOWS_AROUND, {"ids": accts}),
        ctx.q("FLOW_CENTRALITY", Q.FLOW_CENTRALITY, {"ids": accts}, timeout_ms=20_000))
    activity = {r[0]: r for r in act_rows}
    flows = [_flow(r) for r in flow_rows]
    central = {r[0]: (r[1], r[2], r[3]) for r in cent_rows}
    owner_of = {a: c for c, al in accts_of.items() for a in al}
    acct_set = set(accts)
    exit_cps = sorted({f["to"] for f in flows if f["from"] in acct_set and f["to_label"] == "Counterparty"})
    senders = {r[0]: int(r[1]) for r in await ctx.q("CP_SENDERS", Q.CP_SENDERS, {"ids": exit_cps})} if exit_cps else {}

    rings = []
    for comp_id, members in comps:
        mset = set(members)
        maccts = {a for c in members for a in accts_of.get(c, [])}
        shared = [(tok, typ, sorted(set(h) & mset)) for tok, typ, h in tok_rows if len(set(h) & mset) >= 2]
        ident = sum(ID_WEIGHT.get(typ, 1.0) * (len(h) - 1) for _, typ, h in shared)
        strong = sorted({typ for _, typ, _h in shared if ID_WEIGHT.get(typ, 1.0) >= 2.5})
        # Transfers between two accounts of the *same* client are ordinary account management, not layering.
        internal = [f for f in flows if f["from"] in maccts and f["to"] in maccts
                    and owner_of.get(f["from"]) != owner_of.get(f["to"])]
        outflow = [f for f in flows if f["from"] in maccts and f["to_label"] == "Counterparty"]
        exits: dict[str, set[str]] = defaultdict(set)
        exit_amt: dict[str, float] = defaultdict(float)
        exit_name: dict[str, str] = {}
        for f in outflow:
            exits[f["to"]].add(f["from"])
            exit_amt[f["to"]] += float(f["amount"] or 0)
            exit_name[f["to"]] = f["to_name"]
        # A common exit must be (mostly) exclusive to the group: members are at least half of all its payers.
        common_exits = {cp: sorted(s) for cp, s in exits.items()
                        if len(s) >= 2 and len(s) / max(senders.get(cp, len(s)), 1) >= 0.5}
        structuring = sum(int(activity[a][3] or 0) for a in maccts if a in activity)
        alerts = sorted({x for a in maccts if a in activity for x in (activity[a][5] or []) if x})
        max_fs = max([float(activity[a][4] or 0) for a in maccts if a in activity] or [0.0])
        parts = {"shared_identifiers": 0.30 * min(ident / 8.0, 1.0),
                 "internal_transfers": 0.20 * min(len(internal) / 4.0, 1.0),
                 "common_exit": 0.15 if common_exits else 0.0,
                 "structuring": 0.15 * min(structuring / 10.0, 1.0),
                 "alerts": 0.20 * max(max_fs if max_fs >= 0.5 else 0.0, 1.0 if alerts else 0.0)}
        risk = round(sum(parts.values()), 2)
        level = "high" if risk >= 0.6 else "medium" if risk >= 0.35 else "low"

        signals = [f"{len(members)} clients connected by {len(shared)} shared identifier token(s): " + ", ".join(
            f"{typ} {tok} shared by {len(h)}" for tok, typ, h in shared[:8])]
        if internal:
            signals.append(f"{len(internal)} internal transfer route(s) between member accounts totalling USD "
                           f"{sum(float(f['amount'] or 0) for f in internal):,.0f}")
        for cp, srcs in common_exits.items():
            signals.append(f"common exit: {exit_name[cp]} ({cp}) receives from {len(srcs)} member accounts "
                           f"(USD {exit_amt[cp]:,.0f})")
        if structuring:
            signals.append(f"{structuring} inbound deposit(s) between USD 8,000 and 10,000, just under the cash "
                           "reporting threshold (possible structuring)")
        if alerts or max_fs >= 0.5:
            signals.append(f"alerts: {', '.join(alerts) or 'none'}; highest fraud score {max_fs:.2f}")
        if not strong and not internal and not common_exits:
            signals.append("only weak identifiers shared (address / date of birth) and no money moving between "
                           "members: consistent with a household or coincidence")
        hub = max((a for a in maccts if a in central and central[a][0] > 0), key=lambda a: central[a][0], default=None)
        if hub:
            score, rank, total = central[hub]
            signals.append(f"{hub} ranks #{rank} of {total} accounts by flow betweenness (score {score:.0f}): the "
                           "hub that moves money through the group")
        rings.append({
            "component": comp_id, "risk": risk, "level": level,
            "score_breakdown": {k: round(v, 2) for k, v in parts.items()}, "signals": signals,
            "clients": [info.get(c, {"id": c}) for c in members], "accounts": sorted(maccts),
            "shared_identifiers": [{"token": tok, "type": typ, "clients": h} for tok, typ, h in shared],
            "internal_transfers": [_flow_out(f) for f in internal[:12]],
            "exits": [{"counterparty": cp, "name": exit_name[cp], "from_accounts": sorted(exits[cp]),
                       "amount": round(exit_amt[cp], 2)} for cp in sorted(exits, key=lambda c: -exit_amt[c])][:6],
            "structuring_deposits": structuring, "alerts": alerts, "max_fraud_score": round(max_fs, 2),
            "_internal": internal, "_outflow": [f for f in outflow if f["to"] in common_exits],
        })
    rings.sort(key=lambda r: (-r["risk"], -len(r["clients"])))
    rings = rings[:limit]
    for r in rings:
        internal_f, outflow_f = r.pop("_internal"), r.pop("_outflow")
        if r["level"] == "low":
            continue
        for c in r["clients"]:
            ctx.evidence.node(c["id"], "Client", c.get("name", c["id"]))
            for a in accts_of.get(c["id"], []):
                ctx.evidence.node(a, "Account", a)
                ctx.evidence.edge(c["id"], "HOLDS", a)
        for s in r["shared_identifiers"]:
            ctx.evidence.node(s["token"], "PiiToken", s["token"], type=s["type"])
            for c in s["clients"]:
                ctx.evidence.edge(c, "HAS_IDENTIFIER", s["token"])
        for f in internal_f + outflow_f:
            _flow_evidence(ctx, f)
    return {"method": method, "components_considered": len(comps), "rings": rings}


# ------------------------------------------------------------------------------------------------ fund tracing
async def trace_funds(ctx: ToolContext, start: str, direction: str = "downstream", hops: int = 3,
                      since: str | None = None) -> dict:
    ctx.require("act", "trace_funds")
    down = not str(direction or "").strip().lower().startswith("up")
    hops = max(1, min(int(hops or 3), 4))
    since = iso_or_none(since, "since")
    ent = await _resolve_or_fail(ctx, start, {"Account", "Counterparty", "Transaction", "Client"})
    origin = None
    if ent["label"] == "Transaction":
        rows = await ctx.q("TXN_PARTIES", Q.TXN_PARTIES, {"ids": [ent["id"]]})
        tid, kind, amount, ts, alert, fs, fa, fc, ta, tc, cp, cpname = rows[0]
        origin = _drop_none({"transaction": tid, "kind": kind, "amount": _r(amount), "ts": ts, "alert": alert,
                             "from_account": fa, "to_account": ta, "counterparty": f"{cpname} ({cp})" if cp else None})
        ctx.evidence.node(tid, "Transaction", f"{kind} USD {amount} {str(ts or '')[:10]}", alert=alert)
        for acct, rel in ((fa, "FROM_ACCOUNT"), (ta, "TO_ACCOUNT")):
            if acct:
                ctx.evidence.node(acct, "Account", acct)
                ctx.evidence.edge(tid, rel, acct)
        if cp:
            ctx.evidence.node(cp, "Counterparty", cpname)
            ctx.evidence.edge(tid, "COUNTERPARTY", cp)
        ids = [x for x in ([ta or fa] if down else [fa or ta]) if x]
        if since is None and down and ts:
            since = str(ts)[:10]  # follow the money from the moment it arrived
    elif ent["label"] == "Client":
        rows = await ctx.q("CLIENT_ACCOUNTS", Q.CLIENT_ACCOUNTS, {"ids": [ent["id"]]})
        ids = [r[4] for r in rows if r[4]]
    else:
        ids = [ent["id"]]
    if not ids:
        raise ToolError(f"Nothing to trace from {ent['id']}.")
    key = f"{'downstream' if down else 'upstream'}{hops}"
    rows = await ctx.q(f"TRACE_FUNDS[{key}]", Q.TRACE_FUNDS[key], {"ids": ids, "since": since or "0000"},
                       timeout_ms=15_000)
    names: dict[str, tuple[str, str]] = {}
    edges: dict[tuple[str, str], dict] = {}
    paths: set[tuple[str, ...]] = set()
    for nodes, rels in rows:
        for lab, nid, nm in nodes:
            names[nid] = (lab, nm)
        for i, (amount, n, first, last, risk) in enumerate(rels):
            a, b = nodes[i][1], nodes[i + 1][1]
            edges[(a, b)] = {"from": a, "from_label": nodes[i][0], "from_name": nodes[i][2], "to": b,
                             "to_label": nodes[i + 1][0], "to_name": nodes[i + 1][2], "amount": _r(amount), "n": n,
                             "first": first, "last": last, "max_fraud_score": _r(risk), "alerts": None}
        paths.add(tuple(x[1] for x in nodes))
    if down:
        maximal = [p for p in paths if not any(len(q) > len(p) and q[:len(p)] == p for q in paths)]
        ends = defaultdict(float)
        for (a, b), e in edges.items():
            if e["to_label"] == "Counterparty":
                ends[b] += float(e["amount"] or 0)
    else:
        maximal = [p for p in paths if not any(len(q) > len(p) and q[-len(p):] == p for q in paths)]
        ends = defaultdict(float)
        for (a, b), e in edges.items():
            if e["from_label"] == "Counterparty":
                ends[a] += float(e["amount"] or 0)
    holders = await _holders(ctx, [n for n, (lab, _) in names.items() if lab == "Account"])

    def show(nid: str) -> str:
        lab, nm = names.get(nid, ("", nid))
        return f"{nm} ({nid})" if lab == "Counterparty" else nid

    maximal.sort(key=lambda p: (-len(p), p))
    for e in list(edges.values())[:80]:
        _flow_evidence(ctx, e)
    for a, cs in holders.items():
        for c in cs:
            ctx.evidence.node(c, "Client", c)
            ctx.evidence.edge(c, "HOLDS", a)
    endpoint_key = "exits" if down else "sources"
    return _drop_none({
        "start": ent, "origin": origin, "direction": "downstream" if down else "upstream", "hops": hops,
        "since": since, "paths": [" -> ".join(show(x) for x in p) for p in maximal[:12]],
        endpoint_key: [{"id": cp, "name": names[cp][1], "amount": round(v, 2)}
                       for cp, v in sorted(ends.items(), key=lambda kv: -kv[1])][:10],
        "account_holders": {a: cs for a, cs in holders.items()},
        "flows": [_flow_out(e) for e in sorted(edges.values(), key=lambda e: str(e["first"] or ""))][:40],
        "summary": (f"{len(edges)} flow edge(s) over {len(names)} accounts/counterparties within {hops} hop(s) "
                    f"{'downstream' if down else 'upstream'}" + (f" with activity since {since}" if since else "")
                    + f"; {len(ends)} external {'exit' if down else 'source'} counterparty(ies). Edges aggregate all "
                    "transactions between a pair, so check first/last dates for timing."),
    })


# ------------------------------------------------------------------------------------------------ key people
async def key_people(ctx: ToolContext, team: str | None = None, limit: int = 8) -> dict:
    limit = max(1, min(int(limit or 8), 20))
    tid = tname = None
    if team and str(team).strip():
        ent = await _resolve_or_fail(ctx, team, {"Team"})
        tid, tname = ent["id"], ent["name"]
    params = {"team": tid, "limit": 400}
    pr, bt = await asyncio.gather(ctx.q("KEY_PEOPLE_PAGERANK", Q.KEY_PEOPLE_PAGERANK, params, timeout_ms=20_000),
                                  ctx.q("KEY_PEOPLE_BETWEENNESS", Q.KEY_PEOPLE_BETWEENNESS, params, timeout_ms=20_000))
    people: dict[str, dict] = {}
    for metric, rows in (("pagerank", pr), ("betweenness", bt)):
        for rank, (pid, name, title, team_id, end, score) in enumerate(rows, start=1):
            p = people.setdefault(pid, _drop_none({"id": pid, "name": name, "title": title, "team": team_id,
                                                   "departing": end}))
            p[metric] = round(float(score or 0), 4 if metric == "pagerank" else 1)
            p[f"{metric}_rank"] = rank
    n = max(len(pr), len(bt), 1)
    ranked = sorted(people.values(), key=lambda p: ((p.get("pagerank_rank", n) + p.get("betweenness_rank", n)) / 2,
                                                    p["id"]))[:limit]
    for p in ranked:
        ctx.evidence.node(p["id"], "Person", p.get("name", p["id"]))
    return {"team": tname, "people": ranked, "network": "COMMUNICATES (derived from addressed messages and replies; "
                                                     "MNPI documents excluded)",
            "method": ("algo.pageRank('Person','COMMUNICATES') = who is most consulted; algo.betweenness over "
                       "COMMUNICATES = who bridges otherwise separate groups (key-person / single point of failure). "
                       "Ranked by the mean of both ranks.")}


# ------------------------------------------------------------------------------------------------ information barrier
def _ft_query(term: str) -> str:
    return " ".join(re.findall(r"[A-Za-z0-9]+", term))


async def barrier_check(ctx: ToolContext, deal: str) -> dict:
    ctx.require("act", "barrier_check")
    ent = await _resolve_or_fail(ctx, deal, {"Deal"})
    rows = await ctx.q("DEAL_WALL", Q.DEAL_WALL, {"id": ent["id"]})
    if not rows:
        raise ToolError(f"No deal {deal!r}.")
    props, team, compliance = rows[0]
    props = props or {}
    team = sorted(x for x in team if x)
    compliance = sorted(x for x in compliance if x)
    wall = set(team) | set(compliance)
    terms: list[str] = []
    for t in (props.get("codename"), props.get("target")):
        if t:
            low = str(t).strip().lower()
            stripped = re.sub(r"^project\s+", "", low)
            terms.append(stripped if len(stripped) >= 4 else low)
    queries = [ctx.q("DEAL_DOCS", Q.DEAL_DOCS, {"id": ent["id"]})]
    queries += [ctx.q("DEAL_DOCS_FULLTEXT", Q.DEAL_DOCS_FULLTEXT, {"q": _ft_query(t), "needle": t})
                for t in terms if _ft_query(t)]
    found = await asyncio.gather(*queries)
    doc_ids = sorted({r[0] for rs in found for r in rs})[:400]
    aud = await ctx.q("DOC_AUDIENCE", Q.DOC_AUDIENCE, {"ids": doc_ids}) if doc_ids else []
    people = set(wall)
    raw = []
    for did, kind, ts, sens, public, author, chid, chname, recips, members, granted, team_members in aud:
        recips, members = set(x for x in recips if x), set(x for x in members if x)
        audience = {x for x in [author, *recips, *members, *granted, *team_members] if x}
        outsiders = audience - wall
        if not outsiders and not public:
            continue
        how = {o: ("author" if o == author else "recipient" if o in recips else
                   "channel member" if o in members else "granted access") for o in outsiders}
        people |= outsiders | ({author} if author else set())
        raw.append((did, kind, ts, sens, bool(public), author, chid, chname, how))
    names = await name_entities(ctx, [("Person", p) for p in people])
    visible = await ctx.visible_docs([r[0] for r in raw])
    docs = await fetch_docs(ctx, sorted(visible), words=terms, size=400) if visible else {}
    # Surveillance: the CCO ('reveal' scope) may read flagged breach documents the normal ACL hides. Audit-logged.
    surveilled: list[str] = []
    hidden_ids = sorted({r[0] for r in raw} - set(docs))
    if hidden_ids and ctx.auth.can("reveal"):
        from backend.tools.base import _window
        for did, kind, ts, title, text, sens, aid, aname, cid, cname in await ctx.q(
                "DOC_SURVEILLANCE", Q.DOC_SURVEILLANCE, {"ids": hidden_ids}):
            d = {"id": did, "kind": kind, "ts": ts, "title": title, "author_id": aid, "author": aname,
                 "channel_id": cid, "channel": cname, "sensitivity": sens, "text": _window(text or "", terms, 400)}
            ctx.register_doc(d)
            docs[did] = d
            surveilled.append(did)
        if surveilled:
            ctx.events.append({"kind": "surveillance_read", "agent": ctx.agent, "by": ctx.pid, "docs": surveilled,
                               "reason": f"barrier_check {ent['id']}", "ts": ctx.now()})
    breaches = []
    for did, kind, ts, sens, public, author, chid, chname, how in sorted(raw, key=lambda r: str(r[2] or "")):
        outsiders = [{"id": o, "name": names.get(o, o), "how": h} for o, h in sorted(how.items())]
        item = {"id": did, "kind": kind, "ts": ts, "sensitivity": sens, "public": public or None,
                "author": {"id": author, "name": names.get(author, author)} if author else None,
                "author_inside_wall": author in wall if author else None, "channel": chname,
                "outsiders": outsiders[:15], "outsider_count": len(outsiders),
                "pattern": ("deal-team member disclosed deal information to people outside the wall"
                            if author in wall else "someone outside the wall is handling deal information")}
        if did in docs:
            item["excerpt"] = docs[did]["text"]
            if did in surveilled:
                item["access"] = "compliance surveillance read (outside your normal access; audit-logged)"
        else:
            item["note"] = "content outside your access; metadata only"
        breaches.append(_drop_none(item))
        ctx.evidence.node(did, "Document", docs.get(did, {}).get("title") or f"{kind} {str(ts or '')[:10]}", kind=kind)
        ctx.evidence.edge(did, "MENTIONS", ent["id"])
        if author:
            ctx.evidence.node(author, "Person", names.get(author, author))
            ctx.evidence.edge(author, "AUTHORED", did)
        for o in outsiders[:8]:
            ctx.evidence.node(o["id"], "Person", o["name"])
            if o["how"] == "recipient":
                ctx.evidence.edge(did, "SENT_TO", o["id"])
            elif o["how"] == "channel member" and chid:
                ctx.evidence.node(chid, "Channel", chname)
                ctx.evidence.edge(did, "POSTED_IN", chid)
                ctx.evidence.edge(o["id"], "CAN_ACCESS", chid)
            elif o["how"] == "granted access":
                ctx.evidence.edge(o["id"], "CAN_ACCESS", did)
    ctx.evidence.node(ent["id"], "Deal", ent["name"])
    for p in team:
        ctx.evidence.node(p, "Person", names.get(p, p))
        ctx.evidence.edge(p, "ON_DEAL_TEAM", ent["id"])
    outsiders_all = sorted({o["id"] for b in breaches for o in b.get("outsiders", [])})
    return {
        "deal": _drop_none({k: props.get(k) for k in ("id", "codename", "target", "kind", "status", "sensitivity")}),
        "wall": {"deal_team": [{"id": p, "name": names.get(p, p)} for p in team],
                 "compliance": [{"id": p, "name": names.get(p, p)} for p in compliance]},
        "documents_checked": len(doc_ids), "breach_count": len(breaches), "clean": not breaches,
        "breaches": breaches[:25], "surveillance_reads": surveilled or None,
        "summary": (f"{len(doc_ids)} document(s) reference the deal; {len(breaches)} reached people outside the wall "
                    f"({len(outsiders_all)} distinct outsider(s))." if breaches else
                    f"{len(doc_ids)} document(s) reference the deal; all stayed inside the wall."),
    }
