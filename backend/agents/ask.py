"""CLI: ask an agent a question as a persona and stream the answer, tool calls and citations.

    python -m backend.agents.ask --tenant halcyon --persona engineer "Who should I ask about ReconLegacy?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from backend.agents.brain import brain_spec
from backend.agents.runtime import run_agent
from backend.tenancy import make_auth

AGENTS = {"brain": brain_spec}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--tenant", default="halcyon")
    ap.add_argument("--persona", default="engineer")
    ap.add_argument("--agent", default="brain", choices=sorted(AGENTS))
    ap.add_argument("--out", help="write the final event as JSON to this path")
    ap.add_argument("--trace", action="store_true", help="print every Cypher query run by the tools")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    logging.basicConfig(level=logging.WARNING)

    auth = make_auth(args.tenant, args.persona, user="cli")
    final = None
    async for ev in run_agent(AGENTS[args.agent](), auth, args.question):
        t = ev["type"]
        if t == "start":
            print(f"[{ev['agent']} | {ev['model']} | {ev['tenant']}/{ev['persona']} {auth.persona_info.name}]")
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
        elif t == "error":
            print(f"\nERROR: {ev['message']}")
            return 1
        elif t == "final":
            final = ev
    if final is None:
        return 1
    print("\n\n--- citations")
    for c in final["citations"]:
        print(f"[{c['n']}] {c['id']}  {c.get('display', '')}")
    if final["unverified_citations"]:
        print("unverified (not returned by any tool):", final["unverified_citations"])
    ev = final["evidence"]
    print(f"--- evidence: {len(ev['nodes'])} nodes, {len(ev['edges'])} edges | steps {final['steps']} | "
          f"tools {len(final['tool_calls'])} | ${final['cost_usd']:.4f} | {final['ms'] / 1000:.1f}s")
    if args.out:
        Path(args.out).write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
