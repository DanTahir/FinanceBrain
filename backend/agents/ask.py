"""CLI: ask an agent a question as a persona and stream the answer, tool calls, handoffs and citations.

    python -m backend.agents.ask --tenant halcyon --persona engineer "Who should I ask about ReconLegacy?"
    python -m backend.agents.ask --persona aml_analyst --agent investigator "Investigate the flagged 48k wire"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from backend.agents.orchestrator import SPECS, run_flow
from backend.tenancy import make_auth


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--tenant", default="halcyon")
    ap.add_argument("--persona", default="engineer")
    ap.add_argument("--agent", default="brain", choices=sorted(SPECS))
    ap.add_argument("--out", help="write the final event(s) as JSON to this path")
    ap.add_argument("--trace", action="store_true", help="print every Cypher query run by the tools")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    logging.basicConfig(level=logging.WARNING)

    auth = make_auth(args.tenant, args.persona, user="cli")
    finals: list[dict] = []
    ops_events: list[dict] = []
    async for ev in run_flow(args.agent, auth, args.question):
        t = ev["type"]
        if t == "start":
            print(f"\n[{ev['agent']} | {ev['model']} | {ev['tenant']}/{ev['persona']} {auth.persona_info.name}]")
        elif t == "text":
            print(ev["delta"], end="", flush=True)
        elif t == "tool_call":
            print(f"\n  -> {ev['name']}({json.dumps(ev['args'], ensure_ascii=False)[:200]})", flush=True)
        elif t == "tool_result":
            status = "ok" if ev["ok"] else "ERROR"
            print(f"  <- {ev['name']} {status} {ev['summary']} ({ev['ms']} ms, {len(ev['trace'])} queries)")
            if args.trace:
                for q in ev["trace"]:
                    print(f"     [{q.get('ms')} ms, {q.get('rows', '-')} rows] {q['query']}")
        elif t == "ops":
            ops_events.append(ev)
            detail = {k: v for k, v in ev.items() if k not in ("type", "note")}
            print(f"  ** {json.dumps(detail, ensure_ascii=False)[:240]}")
        elif t == "handoff":
            print(f"\n==> handoff {ev['from']} -> {ev['to']} (task {ev['task_id']}, case {ev['case_id']})")
        elif t == "error":
            print(f"\nERROR: {ev['message']}")
            return 1
        elif t == "final":
            finals.append(ev)
            print("\n\n--- citations")
            for c in ev["citations"]:
                print(f"[{c['n']}] {c['id']}  {c.get('display', '')}")
            if ev["unverified_citations"]:
                print("unverified (not returned by any tool):", ev["unverified_citations"])
            e = ev["evidence"]
            print(f"--- {ev['agent']}: evidence {len(e['nodes'])} nodes, {len(e['edges'])} edges | steps {ev['steps']} | "
                  f"tools {len(ev['tool_calls'])} | ${ev['cost_usd']:.4f} | {ev['ms'] / 1000:.1f}s")
    if not finals:
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps({"finals": finals, "ops": ops_events}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
