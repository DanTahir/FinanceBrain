"""Isolation eval: an automated pass/fail matrix proving tenant isolation, the information barrier and private
per-user memory, run directly against the tool layer (no agent LLM calls).

What it measures (every check is a row ``{check, category, passed, detail}``):

1. tenant_isolation   - every Brightwater persona: Halcyon-only concepts (ReconLegacy, PricingSvc, Project Falcon) never
                        appear in search_knowledge results and Halcyon-only ids (sys_reconlegacy, deal_falcon,
                        sys_pricingsvc) never resolve; the converse for every Halcyon persona with Brightwater-only
                        ShareRecon / sys_sharerecon; every ToolContext targets brain__<own tenant>. Positive controls
                        show the probed ids/concepts do exist in their own tenant.
2. information_barrier - Halcyon personas outside the Falcon deal team (engineer, aml_analyst) never receive an MNPI
                        document (ground_truth mnpi_docs) from search, cannot resolve deal_falcon, and cannot fetch any
                        MNPI document individually; positive controls: the M&A banker (deal team) sees MNPI documents
                        and the compliance officer resolves the deal unrestricted (mirrors tests/test_tools.py).
3. memory_privacy     - a distinctive memory written by user A (halcyon/aml_analyst) is recalled by A only: not by
                        another user of the same persona, not by A under another persona, not by A in Brightwater.
                        Memory graphs differ per (tenant, persona, user). All memory graphs are deleted afterwards.
4. auth               - unknown tenants/personas raise AuthError; HMAC tokens cannot be re-targeted to another tenant,
                        tampered/forged/expired tokens are rejected; read-only personas lack act/approve scopes and the
                        compliance officer has approve.

Searches use real query embeddings (cheap); no chat LLM is called.

Run:
    PYTHONPATH=. .venv/Scripts/python -m evals.isolation_eval [--out isolation]

Writes evals/results/<out>.json and evals/results/<out>.md. Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from backend import tenancy
from backend.config import REPO_ROOT, get_settings
from backend.memory.store import MemoryStore, memory_graph_for
from backend.tenancy import AuthError, make_auth, personas, sign_auth, verify_auth
from backend.tools import retrieval
from backend.tools.base import ToolContext, ToolError, resolve

RESULTS_DIR = Path("evals/results")
CATEGORIES = ("tenant_isolation", "information_barrier", "memory_privacy", "auth")

# (query, keyword that must never appear) — concepts that only exist in the Halcyon world
HALCYON_ONLY_QUERIES = [
    ("ReconLegacy Perl batch reconciliation", "reconlegacy"),
    ("PricingSvc outage blast radius", "pricingsvc"),
    ("Project Falcon acquisition diligence", "falcon"),
]
HALCYON_ONLY_IDS = ["sys_reconlegacy", "deal_falcon", "sys_pricingsvc"]
BRIGHTWATER_ONLY_QUERIES = [("ShareRecon share reconciliation", "sharerecon")]
BRIGHTWATER_ONLY_IDS = ["sys_sharerecon"]

# Personas outside the Falcon deal team: neither may read any MNPI document (document ACL).
BARRIER_OUTSIDERS = ("engineer", "aml_analyst")
# Of those, personas outside Compliance as well: the deal itself (entity, codename) is hidden from them too.
# The AML analyst sits in the Compliance team (the control room keeps the watch list), so by policy the deal
# entity is resolvable for them while every deal document stays behind the wall - asserted as a control below.
WALL_OUTSIDERS = ("engineer",)
BARRIER_QUERIES = ["Project Falcon valuation", "Falcon deal terms", "Project Falcon diligence meeting",
                   "acquisition target purchase price per share"]
FALCON_MEETING = "meeting-00012"  # the Project Falcon diligence meeting (MNPI), as in tests/test_tools.py


# ------------------------------------------------------------------------------------------------ matrix
class Matrix:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, category: str, check: str, passed: bool, detail: str) -> None:
        self.checks.append({"check": check, "category": category, "passed": bool(passed), "detail": detail})

    async def run(self, category: str, check: str, fn: Callable[[], Awaitable[tuple[bool, str]]]) -> None:
        try:
            passed, detail = await fn()
        except Exception as e:  # noqa: BLE001  (a crashing check is a failed check)
            passed, detail = False, f"error: {type(e).__name__}: {str(e)[:240]}"
        self.add(category, check, passed, detail)


async def raises_tool_error(coro: Awaitable[Any]) -> tuple[bool, str]:
    try:
        out = await coro
    except ToolError as e:
        return True, f"ToolError: {str(e)[:120]}"
    rid = out.get("id") if isinstance(out, dict) else None
    return False, f"returned a result (id={rid!r})"


def raises_auth_error(fn: Callable[[], Any]) -> tuple[bool, str]:
    try:
        out = fn()
    except AuthError as e:
        return True, f"AuthError: {str(e)[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, f"raised {type(e).__name__} instead of AuthError: {str(e)[:120]}"
    return False, f"accepted: {out!r}"[:200]


def ctx_for(tenant: str, persona: str) -> ToolContext:
    return ToolContext(make_auth(tenant, persona))


async def doc_vector(ctx: ToolContext, doc_id: str) -> list[float]:
    rows = await ctx.q("eval:embedding", "MATCH (d:Document {id: $id}) RETURN d.embedding", {"id": doc_id})
    return [float(x) for x in rows[0][0]]


def result_blob(r: dict) -> str:
    parts = [str(r.get(k) or "") for k in ("title", "text", "channel")]
    parts += [str(m) for m in (r.get("mentions") or [])]
    return " ".join(parts).lower()


def load_mnpi_docs() -> set[str]:
    gt = json.loads((REPO_ROOT / "data" / "raw" / "halcyon" / "ground_truth.json").read_text(encoding="utf-8"))
    out: set[str] = set()
    for d in gt.get("mnpi_docs") or []:
        if isinstance(d, str):
            out.add(d)
        elif isinstance(d, dict):
            did = d.get("id") or d.get("doc_id")
            if did:
                out.add(str(did))
    return out


# ------------------------------------------------------------------------------------------------ 1. tenancy
async def _no_keyword_in_search(ctx: ToolContext, query: str, keyword: str) -> tuple[bool, str]:
    res = await retrieval.search_knowledge(ctx, query, k=10)
    hits = [r["id"] for r in res["results"] if keyword in result_blob(r)]
    fact_hits = [f.get("statement") for f in res.get("facts") or [] if keyword in str(f.get("statement") or "").lower()]
    ok = not hits and not fact_hits
    return ok, (f"{len(res['results'])} results, {len(res.get('facts') or [])} facts; "
                f"'{keyword}' in results={hits[:5]} facts={len(fact_hits)}")


async def _id_unresolvable(ctx: ToolContext, eid: str) -> tuple[bool, str]:
    r = await resolve(ctx, eid)
    ok_get, det_get = await raises_tool_error(retrieval.get_entity(ctx, eid))
    ok = r is None and ok_get
    return ok, f"resolve={'None' if r is None else r.get('id')}; get_entity: {det_get}"


async def _name_not_found(ctx: ToolContext, text: str, eid: str) -> tuple[bool, str]:
    hits = await retrieval.find_entities(ctx, text, limit=20)
    bad = [h["id"] for h in hits if h["id"] == eid or text.lower() in str(h.get("name") or "").lower()]
    return not bad, f"find_entities({text!r}) -> {len(hits)} hits, matching={bad[:5]}"


async def tenant_isolation(m: Matrix) -> None:
    cat = "tenant_isolation"
    for tenant in ("halcyon", "brightwater"):
        for persona in personas(tenant):
            ctx = ctx_for(tenant, persona)

            async def graph_ok(ctx=ctx, tenant=tenant) -> tuple[bool, str]:
                return ctx.graph_name == f"brain__{tenant}", f"graph_name={ctx.graph_name}"

            await m.run(cat, f"{tenant}/{persona}: ToolContext graph is brain__{tenant}", graph_ok)

    for persona in personas("brightwater"):
        ctx = ctx_for("brightwater", persona)
        for query, kw in HALCYON_ONLY_QUERIES:
            await m.run(cat, f"brightwater/{persona}: search {query!r} has no '{kw}'",
                        lambda ctx=ctx, query=query, kw=kw: _no_keyword_in_search(ctx, query, kw))
        for eid in HALCYON_ONLY_IDS:
            await m.run(cat, f"brightwater/{persona}: {eid} does not resolve",
                        lambda ctx=ctx, eid=eid: _id_unresolvable(ctx, eid))
        await m.run(cat, f"brightwater/{persona}: find_entities 'ReconLegacy' finds nothing",
                    lambda ctx=ctx: _name_not_found(ctx, "ReconLegacy", "sys_reconlegacy"))

    for persona in personas("halcyon"):
        ctx = ctx_for("halcyon", persona)
        for query, kw in BRIGHTWATER_ONLY_QUERIES:
            await m.run(cat, f"halcyon/{persona}: search {query!r} has no '{kw}'",
                        lambda ctx=ctx, query=query, kw=kw: _no_keyword_in_search(ctx, query, kw))
        for eid in BRIGHTWATER_ONLY_IDS:
            await m.run(cat, f"halcyon/{persona}: {eid} does not resolve",
                        lambda ctx=ctx, eid=eid: _id_unresolvable(ctx, eid))
        await m.run(cat, f"halcyon/{persona}: find_entities 'ShareRecon' finds nothing",
                    lambda ctx=ctx: _name_not_found(ctx, "ShareRecon", "sys_sharerecon"))

    # positive controls: the probed entities exist in their own tenant
    async def resolves(tenant: str, persona: str, eid: str) -> tuple[bool, str]:
        r = await resolve(ctx_for(tenant, persona), eid)
        return bool(r and r["id"] == eid), f"resolve={r!r}"[:200]

    for eid in ("sys_reconlegacy", "sys_pricingsvc"):
        await m.run(cat, f"control: halcyon/engineer resolves {eid}",
                    lambda eid=eid: resolves("halcyon", "engineer", eid))
    await m.run(cat, "control: brightwater/engineer resolves sys_sharerecon",
                lambda: resolves("brightwater", "engineer", "sys_sharerecon"))

    async def recon_visible_in_halcyon() -> tuple[bool, str]:
        res = await retrieval.search_knowledge(ctx_for("halcyon", "engineer"), HALCYON_ONLY_QUERIES[0][0], k=10)
        hits = [r["id"] for r in res["results"] if "reconlegacy" in result_blob(r)]
        return bool(hits), f"halcyon results mentioning reconlegacy: {hits[:5]}"

    await m.run(cat, "control: halcyon/engineer search finds ReconLegacy content", recon_visible_in_halcyon)


# ------------------------------------------------------------------------------------------------ 2. barrier
async def _barrier_search(ctx: ToolContext, query: str, mnpi: set[str],
                          vector: list[float] | None = None) -> tuple[bool, str]:
    res = await retrieval.search_knowledge(ctx, query, k=12, vector=vector)
    rs = res["results"]
    leaked = [r["id"] for r in rs if r["id"] in mnpi]
    flagged = [r["id"] for r in rs if r.get("sensitivity") == "MNPI"]
    falcon = [r["id"] for r in rs if "falcon" in (str(r.get("title") or "") + str(r.get("text") or "")).lower()]
    cites = sorted({d for f in res.get("facts") or [] for d in (f.get("cites") or []) if d in mnpi})
    ok = not (leaked or flagged or falcon or cites)
    return ok, (f"{len(rs)} results; mnpi ids={leaked[:5]} sensitivity=MNPI={flagged[:5]} "
                f"'falcon' in title/text={falcon[:5]} fact cites mnpi={cites[:5]}")


async def information_barrier(m: Matrix) -> None:
    cat = "information_barrier"
    mnpi = load_mnpi_docs()
    if not mnpi:
        m.add(cat, "ground truth lists MNPI documents", False, "data/raw/halcyon/ground_truth.json has no mnpi_docs")
        return
    m.add(cat, "ground truth lists MNPI documents", True, f"{len(mnpi)} mnpi docs")

    vec: list[float] | None = None
    try:
        vec = await doc_vector(ctx_for("halcyon", "compliance_officer"), FALCON_MEETING)
    except Exception as e:  # noqa: BLE001
        m.add(cat, f"load stored embedding of {FALCON_MEETING}", False, f"error: {type(e).__name__}: {str(e)[:200]}")

    for persona in BARRIER_OUTSIDERS:
        ctx = ctx_for("halcyon", persona)
        for query in BARRIER_QUERIES:
            await m.run(cat, f"halcyon/{persona}: search {query!r} returns no MNPI",
                        lambda ctx=ctx, query=query: _barrier_search(ctx, query, mnpi))
        if vec is not None:
            await m.run(cat, f"halcyon/{persona}: search with {FALCON_MEETING} vector returns no MNPI",
                        lambda ctx=ctx: _barrier_search(ctx, "Project Falcon diligence", mnpi, vec))
        if persona in WALL_OUTSIDERS:
            await m.run(cat, f"halcyon/{persona}: deal_falcon does not resolve",
                        lambda ctx=ctx: _id_unresolvable(ctx, "deal_falcon"))

            async def name_hidden(ctx=ctx) -> tuple[bool, str]:
                r = await resolve(ctx, "Project Falcon")
                return r is None, f"resolve('Project Falcon')={r!r}"[:200]

            await m.run(cat, f"halcyon/{persona}: 'Project Falcon' does not resolve", name_hidden)
        else:
            async def control_room(ctx=ctx) -> tuple[bool, str]:
                r = await resolve(ctx, "deal_falcon")
                vis = await ctx.visible_docs(sorted(mnpi))
                ok = bool(r and r["id"] == "deal_falcon") and not vis
                return ok, f"deal resolves={bool(r)}; visible mnpi docs={len(vis)} of {len(mnpi)}"

            await m.run(cat, f"control room: halcyon/{persona} (Compliance team) sees the deal entity but no "
                             "MNPI documents", control_room)

        async def none_visible(ctx=ctx) -> tuple[bool, str]:
            vis = await ctx.visible_docs(sorted(mnpi))
            return not vis, f"visible mnpi docs: {sorted(vis)[:8]} of {len(mnpi)}"

        await m.run(cat, f"halcyon/{persona}: visible_docs(mnpi_docs) is empty", none_visible)

        async def restricted(ctx=ctx) -> tuple[bool, str]:
            flag = await ctx.is_restricted("Project Falcon")
            return flag, f"is_restricted('Project Falcon')={flag}"

        if persona in WALL_OUTSIDERS:
            await m.run(cat, f"halcyon/{persona}: 'Project Falcon' is a restricted term", restricted)

    eng = ctx_for("halcyon", "engineer")
    for did in sorted(mnpi):
        await m.run(cat, f"halcyon/engineer: get_entity({did}) is denied",
                    lambda did=did: raises_tool_error(retrieval.get_entity(eng, did)))

    # positive controls: deal team and compliance
    banker = ctx_for("halcyon", "ma_banker")

    async def banker_sees_query() -> tuple[bool, str]:
        res = await retrieval.search_knowledge(banker, "Project Falcon diligence", k=12, vector=vec)
        hits = [r["id"] for r in res["results"] if r["id"] in mnpi]
        return bool(hits), f"mnpi docs returned to deal team: {hits[:8]}"

    await m.run(cat, "control: halcyon/ma_banker (deal team) search returns MNPI docs", banker_sees_query)
    if vec is not None:
        async def banker_sees_meeting() -> tuple[bool, str]:
            res = await retrieval.search_knowledge(banker, "Project Falcon diligence", k=12, vector=vec)
            ids = {r["id"] for r in res["results"]}
            return FALCON_MEETING in ids, f"{FALCON_MEETING} in results: {FALCON_MEETING in ids}"

        await m.run(cat, f"control: halcyon/ma_banker sees {FALCON_MEETING}", banker_sees_meeting)

    async def banker_visible() -> tuple[bool, str]:
        vis = await banker.visible_docs(sorted(mnpi))
        return bool(vis), f"{len(vis)} of {len(mnpi)} mnpi docs visible to deal team"

    await m.run(cat, "control: halcyon/ma_banker visible_docs(mnpi_docs) non-empty", banker_visible)

    async def banker_resolves() -> tuple[bool, str]:
        r = await resolve(banker, "deal_falcon")
        return bool(r and r["id"] == "deal_falcon"), f"resolve={r!r}"[:200]

    await m.run(cat, "control: halcyon/ma_banker resolves deal_falcon", banker_resolves)

    cco = ctx_for("halcyon", "compliance_officer")

    async def cco_deal() -> tuple[bool, str]:
        out = await retrieval.get_entity(cco, "deal_falcon")
        code = (out.get("properties") or {}).get("codename")
        return code is not None and code != "⟨restricted⟩", f"codename restricted: {code == '⟨restricted⟩'}"

    await m.run(cat, "control: halcyon/compliance_officer get_entity(deal_falcon) unrestricted", cco_deal)

    async def cco_channel() -> tuple[bool, str]:
        r = await resolve(cco, "#project-falcon")
        return r is not None, f"resolve('#project-falcon') -> {r.get('id') if r else None}"

    await m.run(cat, "control: halcyon/compliance_officer resolves #project-falcon", cco_channel)


# ------------------------------------------------------------------------------------------------ 3. memory
def _has_marker(rec: dict, marker: str) -> list[str]:
    low = marker.lower()
    texts = [str(f.get("text") or "") for f in rec.get("facts") or []]
    texts += [str(t.get("text") or "") for t in rec.get("turns") or []]
    return [t for t in texts if low in t.lower()]


async def memory_privacy(m: Matrix, runid: str) -> None:
    cat = "memory_privacy"
    marker = f"ZEBRA-{runid}"
    fact_text = f"My favourite reconciliation code word is {marker}"
    query = "What is my favourite reconciliation code word?"
    a_user, b_user = f"eval_iso_a_{runid}", f"eval_iso_b_{runid}"
    auth_a = make_auth("halcyon", "aml_analyst", user=a_user)
    auth_b = make_auth("halcyon", "aml_analyst", user=b_user)
    auth_p = make_auth("halcyon", "engineer", user=a_user)
    auth_t = make_auth("brightwater", "aml_analyst", user=a_user)
    sa, sb, sp, st = MemoryStore(auth_a), MemoryStore(auth_b), MemoryStore(auth_p), MemoryStore(auth_t)
    stores = [sa, sb, sp, st]
    try:
        async def names_distinct() -> tuple[bool, str]:
            names = [s.name for s in stores]
            prefixes = ["mem__halcyon__aml_analyst__", "mem__halcyon__aml_analyst__", "mem__halcyon__engineer__",
                        "mem__brightwater__aml_analyst__"]
            ok = len(set(names)) == 4 and all(n.startswith(p) for n, p in zip(names, prefixes))
            ok = ok and all(s.name == memory_graph_for(s.auth) for s in stores)
            return ok, "; ".join(names)

        await m.run(cat, "memory graph names differ per (tenant, persona, user)", names_distinct)

        async def write_a() -> tuple[bool, str]:
            await sa.ensure()
            out = await sa.add_facts([{"text": fact_text, "kind": "preference"}])
            ok = bool(out) and marker.lower() in str(out[0].get("text") or "").lower()
            return ok, f"stored {len(out)} memory in {sa.name}"

        await m.run(cat, "user A stores a distinctive memory", write_a)

        async def a_recalls() -> tuple[bool, str]:
            rec = await sa.recall(query)
            hits = _has_marker(rec, marker)
            return bool(hits), f"user A recall: {len(rec.get('facts') or [])} facts, marker hits={len(hits)}"

        await m.run(cat, "control: user A recalls own memory", a_recalls)

        async def a_lists() -> tuple[bool, str]:
            facts = await sa.facts()
            hits = [f for f in facts if marker.lower() in str(f.get("text") or "").lower()]
            return bool(hits), f"user A facts(): {len(facts)}, marker hits={len(hits)}"

        await m.run(cat, "control: user A facts() lists own memory", a_lists)

        for label, store in (("other user, same tenant+persona", sb), ("same user, other persona", sp),
                             ("same user, other tenant", st)):
            async def isolated(store=store) -> tuple[bool, str]:
                rec = await store.recall(query)
                hits = _has_marker(rec, marker)
                facts = await store.facts()
                fhits = [f for f in facts if marker.lower() in str(f.get("text") or "").lower()]
                return not hits and not fhits, (f"{store.name}: recall marker hits={len(hits)}, "
                                                f"facts() marker hits={len(fhits)}")

            await m.run(cat, f"{label}: memory not recalled", isolated)

            async def not_created(store=store) -> tuple[bool, str]:
                ex = await store.exists()
                return not ex, f"{store.name} exists={ex}"

            await m.run(cat, f"{label}: no memory graph created by reads", not_created)
    finally:
        for s in stores:
            try:
                await s.forget_all()
            except Exception:  # noqa: BLE001  (graph may never have been created)
                pass


# ------------------------------------------------------------------------------------------------ 4. auth
async def auth_checks(m: Matrix) -> None:
    cat = "auth"
    for tenant, persona in (("acme", "engineer"), ("halcyon", "ceo"), ("brightwater", "ma_banker"),
                            ("halcyon", "lender")):
        ok, det = raises_auth_error(lambda t=tenant, p=persona: make_auth(t, p))
        m.add(cat, f"make_auth({tenant!r}, {persona!r}) raises AuthError", ok, det)

    a = make_auth("halcyon", "aml_analyst", user="eval_iso")
    tok = sign_auth(a)
    try:
        back = verify_auth(tok)
        m.add(cat, "halcyon token round-trips", back == a, f"tenant={back.tenant} persona={back.persona}")
        ctx = ToolContext(back)
        m.add(cat, "verified halcyon token targets brain__halcyon", ctx.graph_name == "brain__halcyon",
              f"graph_name={ctx.graph_name}")
    except Exception as e:  # noqa: BLE001
        m.add(cat, "halcyon token round-trips", False, f"error: {type(e).__name__}: {str(e)[:200]}")

    _, _, sig = tok.split(".")
    exp = int(time.time()) + 900

    def forged(payload: dict) -> str:
        return f"fb1.{tenancy._b64(json.dumps(payload, separators=(',', ':')).encode())}.{sig}"

    retarget = forged({"t": "brightwater", "p": "aml_analyst", "u": "eval_iso", "s": ["read", "act"], "exp": exp})
    ok, det = raises_auth_error(lambda: verify_auth(retarget))
    m.add(cat, "halcyon signature cannot be re-targeted to brightwater", ok, det)

    escalated = forged({"t": "halcyon", "p": "compliance_officer", "u": "eval_iso", "s": ["read", "approve"],
                        "exp": exp})
    ok, det = raises_auth_error(lambda: verify_auth(escalated))
    m.add(cat, "forged persona/scope escalation rejected", ok, det)

    tampered = tok[:-3] + ("xyz" if not tok.endswith("xyz") else "abc")
    ok, det = raises_auth_error(lambda: verify_auth(tampered))
    m.add(cat, "tampered signature rejected", ok, det)

    ok, det = raises_auth_error(lambda: verify_auth("not-a-token"))
    m.add(cat, "malformed token rejected", ok, det)

    expired = sign_auth(make_auth("halcyon", "engineer"), ttl=-5)
    ok, det = raises_auth_error(lambda: verify_auth(expired))
    m.add(cat, "expired token rejected", ok, det)

    for tenant in ("halcyon", "brightwater"):
        for persona in personas(tenant):
            au = make_auth(tenant, persona)
            sc = au.scopes
            if persona == "compliance_officer":
                ok = au.can("read") and au.can("act") and au.can("approve")
                want = "read+act+approve"
            elif persona == "aml_analyst":
                ok = au.can("act") and not au.can("approve")
                want = "act, no approve"
            else:
                ok = au.can("read") and not au.can("act") and not au.can("approve")
                want = "read-only (no act/approve)"
            m.add(cat, f"{tenant}/{persona}: default scopes {want}", ok, f"scopes={list(sc)}")

    eng_ctx = ToolContext(make_auth("halcyon", "engineer"))
    try:
        eng_ctx.require("act", "opening a case")
        m.add(cat, "halcyon/engineer ToolContext.require('act') denied", False, "require('act') did not raise")
    except ToolError as e:
        m.add(cat, "halcyon/engineer ToolContext.require('act') denied", True, f"ToolError: {str(e)[:120]}")


# ------------------------------------------------------------------------------------------------ output
def _md(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_reports(out: str, checks: list[dict]) -> tuple[Path, Path, dict]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    n = len(checks)
    passed = sum(1 for c in checks if c["passed"])
    summary = {"n": n, "passed": passed, "failed": n - passed}
    by_cat = {}
    for cat in CATEGORIES:
        cs = [c for c in checks if c["category"] == cat]
        p = sum(1 for c in cs if c["passed"])
        by_cat[cat] = {"n": len(cs), "passed": p, "failed": len(cs) - p}
    data = {"generated_at": datetime.now(timezone.utc).isoformat(), "env": get_settings().env, "summary": summary,
            "by_category": by_cat, "checks": checks}
    jp = RESULTS_DIR / f"{out}.json"
    jp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Isolation eval", "", f"Generated {data['generated_at']} (env `{data['env']}`)", "",
             f"**{passed}/{n} checks passed**", "", "| Category | Checks | Passed | Failed |", "|---|---|---|---|"]
    for cat, s in by_cat.items():
        lines.append(f"| {cat} | {s['n']} | {s['passed']} | {s['failed']} |")
    lines.append(f"| **total** | {n} | {passed} | {n - passed} |")
    for cat in CATEGORIES:
        cs = [c for c in checks if c["category"] == cat]
        if not cs:
            continue
        lines += ["", f"## {cat}", "", "| | Check | Detail |", "|---|---|---|"]
        for c in cs:
            lines.append(f"| {'PASS' if c['passed'] else 'FAIL'} | {_md(c['check'])} | {_md(c['detail'])} |")
    mp = RESULTS_DIR / f"{out}.md"
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp, data


async def main() -> int:
    ap = argparse.ArgumentParser(description="Tenant isolation / information barrier / memory privacy matrix")
    ap.add_argument("--out", default="isolation", help="result file stem under evals/results/")
    args = ap.parse_args()

    runid = uuid.uuid4().hex[:8]
    m = Matrix()
    t0 = time.perf_counter()
    await tenant_isolation(m)
    await information_barrier(m)
    await memory_privacy(m, runid)
    await auth_checks(m)

    jp, mp, data = write_reports(args.out, m.checks)
    s = data["summary"]
    print(f"Isolation eval: {s['passed']}/{s['n']} checks passed ({time.perf_counter() - t0:.1f}s)")
    for cat, cs in data["by_category"].items():
        print(f"  {cat:<20} {cs['passed']}/{cs['n']}")
    for c in m.checks:
        if not c["passed"]:
            print(f"  FAIL [{c['category']}] {c['check']}: {c['detail'][:200]}")
    print(f"Wrote {jp} and {mp}")
    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))