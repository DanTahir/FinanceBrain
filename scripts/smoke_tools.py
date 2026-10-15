"""Dev smoke test: run every graph tool directly (no LLM) for a few personas and print compact results.

    PYTHONPATH=. python -m uv run python scripts/smoke_tools.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from backend.tenancy import make_auth
from backend.tools import reasoning, retrieval
from backend.tools.base import ToolContext, ToolError


def show(title: str, ctx: ToolContext, result) -> None:
    text = json.dumps(result, ensure_ascii=False, default=str)
    qs = ", ".join(f"{t['query']}:{t.get('rows', 'ERR')}r/{t.get('ms')}ms" for t in ctx.trace)
    print(f"\n=== {title}\n  trace: {qs}\n  {text[:1400]}")


async def run(tenant: str, persona: str, title: str, fn, **kw) -> dict | None:
    ctx = ToolContext(make_auth(tenant, persona))
    try:
        res = await fn(ctx, **kw)
    except ToolError as e:
        print(f"\n=== {title}\n  ToolError: {e}")
        for t in ctx.trace:
            if t.get("error"):
                print("  query error:", t["query"], t["error"])
        return None
    show(title, ctx, res)
    return res


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    H = "halcyon"
    await run(H, "engineer", "search: ReconLegacy (engineer)", retrieval.search_knowledge,
              query="Who understands the ReconLegacy reconciliation system?", k=5)
    await run(H, "engineer", "search: Project Falcon (engineer, walled off)", retrieval.search_knowledge,
              query="Project Falcon diligence valuation", k=5)
    await run(H, "ma_banker", "search: Project Falcon (ma_banker, deal team)", retrieval.search_knowledge,
              query="Project Falcon diligence valuation", k=5)
    await run(H, "compliance_officer", "search: crypto onboarding pause", retrieval.search_knowledge,
              query="why did we stop onboarding crypto clients", k=6, kinds=["email", "ticket"])
    await run(H, "engineer", "who_knows ReconLegacy", reasoning.who_knows, about="ReconLegacy")
    await run(H, "engineer", "who_knows free text", reasoning.who_knows, about="on-chain AML tracing")
    await run(H, "engineer", "who_owns KYC pipeline as of 2026-08-15", reasoning.who_owns,
              system="KYC pipeline", as_of="2026-08-15")
    await run(H, "compliance_officer", "trace_decision crypto", reasoning.trace_decision, decision="crypto onboarding")
    await run(H, "engineer", "trace_decision (engineer)", reasoning.trace_decision, decision="dec_crypto_pause")
    await run(H, "engineer", "blast_radius PricingSvc", reasoning.blast_radius, system="PricingSvc")
    await run(H, "aml_analyst", "find_path c_0060 -> c_0100", reasoning.find_path, source="c_0060", target="c_0100")
    await run(H, "engineer", "get_entity sys_reconlegacy", retrieval.get_entity, entity="sys_reconlegacy")
    await run(H, "engineer", "get_entity deal_falcon (engineer)", retrieval.get_entity, entity="deal_falcon")
    await run(H, "engineer", "neighborhood c_0060", retrieval.neighborhood, entity="c_0060", limit=10)
    await run(H, "engineer", "graph_schema", retrieval.graph_schema)
    await run(H, "engineer", "cypher ok", retrieval.run_cypher_readonly,
              cypher="MATCH (s:System)<-[:DEPENDS_ON]-(d:System) RETURN s.name, count(d) AS n ORDER BY n DESC LIMIT 3")
    await run(H, "engineer", "cypher nodes", retrieval.run_cypher_readonly,
              cypher="MATCH (d:Document) RETURN d LIMIT 2")
    await run(H, "engineer", "cypher blocked", retrieval.run_cypher_readonly,
              cypher="MATCH (d:Document) RETURN d.text LIMIT 2")
    await run(H, "engineer", "cypher write blocked", retrieval.run_cypher_readonly,
              cypher="MATCH (p:Person) SET p.x = 1")
    await run("brightwater", "engineer", "tenant isolation: brightwater -> sys_reconlegacy", retrieval.get_entity,
              entity="sys_reconlegacy")
    await run("brightwater", "engineer", "brightwater who_knows share recon", reasoning.who_knows,
              about="sys_sharerecon")


if __name__ == "__main__":
    asyncio.run(main())
