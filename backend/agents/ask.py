"""CLI: ask FinanceBrain a question as a persona and stream the answer, routing, tool calls, handoffs, citations and
what the memory graph learned.

    python -m backend.agents.ask --tenant halcyon --persona engineer "Who should I ask about ReconLegacy?"
    python -m backend.agents.ask --persona aml_analyst "Investigate the flagged 48k wire"
    python -m backend.agents.ask --persona aml_analyst --session sess_0123456789 "What did we conclude last time?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from backend.agents.orchestrator import SPECS
from backend.agents.session import run_session
from backend.tenancy import make_auth


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--tenant", default="halcyon")
    ap.add_argument("--persona", default="engineer")
    ap.add_argument("--agent", default="auto", choices=["auto", *sorted(SPECS)])
    ap.add_argument("--user", default="cli", help="memory owner (memory graph mem__<tenant>__<persona>__<user>)")
    ap.add_argument("--session", help="continue a session (sess_...)")
    ap.add_argument("--no-memory", action="store_true", help="stateless run: no memory graph reads or writes")
    ap.add_argument("--out", help="write the final event(s) as JSON to this path")
    ap.add_argument("--trace", action="store_true", help="print every Cypher query run by the tools")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    logging.basicConfig(level=logging.WARNING)

    auth = make_auth(args.tenant, args.persona, user=args.user)
    finals: list[dict] = []
    ops_events: list[dict] = []
    memory_events: list[dict] = []
    async for ev in run_session(auth, args.question, agent=args.agent, session_id=args.session,
                                memory=not args.no_memory):
        t = ev["type"]
        if t == "session":
            print(f"[session {ev['session_id']}{' (resumed)' if ev['resumed'] else ''} | memory {ev['memory_graph']}]")
        elif t == "pii_masked":
            print(f"[pii masked before any model or graph saw it: {ev['counts']}]")
        elif t == "memory_context":
            print(f"[memory: {len(ev.get('preferences') or [])} preferences, {len(ev.get('open_threads') or [])} open "
                  f"threads, {len(ev.get('facts') or [])} relevant memories, {len(ev.get('sessions') or [])} recent "
                  "sessions]")
        elif t == "route":
            print(f"[concierge -> {ev['agent']} ({ev['by']}): {ev['reason']}]")
        elif t == "start":
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
        elif t == "memory":
            memory_events.append(ev)
            if ev.get("error"):
                print(f"--- memory: ERROR {ev['error']}")
            elif ev.get("skipped"):
                print(f"--- memory: skipped ({ev['skipped']})")
            else:
                print(f"--- librarian: \"{ev.get('title')}\" | +{len(ev.get('facts_added') or [])} memories "
                      f"{[f['text'] for f in ev.get('facts_added') or []]} | superseded {ev.get('facts_superseded')} | "
                      f"playbook {json.dumps(ev.get('playbook'), ensure_ascii=False)[:300] if ev.get('playbook') else '-'}"
                      f" | ${ev.get('cost_usd', 0):.4f}")
        elif t == "done":
            print(f"--- session {ev['session_id']}")
    if not finals:
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps({"finals": finals, "ops": ops_events, "memory": memory_events},
                                             ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
