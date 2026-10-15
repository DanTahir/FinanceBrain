"""Hand-rolled streaming tool-use loop on OpenRouter; tools are discovered and called through our MCP server.

Per step: stream a chat completion (text deltas go straight to the client), accumulate tool calls, run them
concurrently through the MCP client with the caller's signed auth token in ``_meta``, feed only each tool's
``result`` back to the model, and stream the Cypher trace / evidence to the client. Stops when the model answers
without tool calls or the step budget is spent. The final answer's ``[doc-id]`` citations are validated against the
documents tools actually returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from typing import Any

from mcp.client.client import Client

from backend.config import mask_secrets
from backend.llm.client import Usage, chat_stream, system_block
from backend.mcp_server.server import server as mcp_server
from backend.tenancy import AuthContext, sign_auth
from backend.tools.base import DOC_ID_RE, Evidence

log = logging.getLogger(__name__)
MAX_TOOL_CHARS = 14_000
MAX_EVIDENCE_NODES = 150


@dataclass
class AgentSpec:
    name: str
    model: str
    prompt: str  # static system prompt (prompt-cached)
    context: Callable[[AuthContext], str]  # per-session context (persona, date), not cached
    tools: tuple[str, ...] | None = None
    max_steps: int = 8
    max_tokens: int = 2500


def to_openai_tool(t: Any) -> dict:
    return {"type": "function", "function": {
        "name": t.name, "description": (t.description or "").strip()[:1024],
        "parameters": t.input_schema or {"type": "object", "properties": {}}}}


def _summary(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    for key in ("results", "experts", "impacted", "paths", "timeline", "neighbors", "rows", "history", "decisions"):
        if isinstance(result.get(key), list):
            return f"{len(result[key])} {key}"
    return "ok"


def final_evidence(ev: Evidence, cited: set[str]) -> dict:
    """Prune the run's evidence to what supports the answer: cited documents (all documents if none were cited),
    every non-document node still connected to something kept, and the edges between kept nodes."""
    d = ev.to_dict()
    docs = {n["id"] for n in d["nodes"] if n.get("label") == "Document"}
    keep_docs = (cited & docs) or docs
    nodes = [n for n in d["nodes"] if n.get("label") != "Document" or n["id"] in keep_docs]
    ids = {n["id"] for n in nodes}
    edges = [e for e in d["edges"] if e["source"] in ids and e["target"] in ids]
    linked = {e["source"] for e in edges} | {e["target"] for e in edges}
    nodes = [n for n in nodes if n["id"] in linked or n["id"] in keep_docs]
    if len(nodes) > MAX_EVIDENCE_NODES:
        near = {e["target"] for e in edges if e["source"] in keep_docs} | \
               {e["source"] for e in edges if e["target"] in keep_docs}
        rank = {n["id"]: (0 if n["id"] in keep_docs else 1 if n["id"] in near else 2) for n in nodes}
        nodes = sorted(nodes, key=lambda n: rank[n["id"]])[:MAX_EVIDENCE_NODES]
        ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["source"] in ids and e["target"] in ids]
    for n in nodes:
        if n["id"] in cited:
            n["cited"] = True
    return {"nodes": nodes, "edges": edges}


async def run_agent(spec: AgentSpec, auth: AuthContext, message: str,
                    history: list[dict] | None = None) -> AsyncIterator[dict]:
    """Run one agent turn, yielding UI events: start, step, text, tool_call, tool_result, final | error."""
    t0 = time.perf_counter()
    token = sign_auth(auth, ttl=900)
    usage = Usage()
    evidence = Evidence()
    docs: dict[str, dict] = {}
    calls: list[dict] = []
    messages: list[dict] = [system_block(spec.prompt), {"role": "system", "content": spec.context(auth)}]
    for h in (history or [])[-12:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:6000]})
    messages.append({"role": "user", "content": message})
    yield {"type": "start", "agent": spec.name, "model": spec.model, "tenant": auth.tenant, "persona": auth.persona}

    final_text = ""
    step = 0
    try:
        async with Client(mcp_server, read_timeout_seconds=90) as client:
            listed = await client.list_tools()
            tools = [to_openai_tool(t) for t in listed.tools if spec.tools is None or t.name in spec.tools]

            async def call(tc: dict) -> dict:
                name = tc["function"]["name"]
                t1 = time.perf_counter()
                out: dict[str, Any] = {"id": tc["id"], "name": name, "args": {}, "ok": False}
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                    out["args"] = args
                    res = await client.call_tool(name, args, meta={"fb_auth": token})
                    text = "".join(getattr(c, "text", "") or "" for c in (res.content or []))
                    if res.is_error:
                        out["error"] = text[:2000] or "tool error"
                    else:
                        data = res.structured_content if isinstance(res.structured_content, dict) else json.loads(text)
                        out.update(ok=True, data=data)
                except Exception as e:  # noqa: BLE001
                    out["error"] = mask_secrets(f"{type(e).__name__}: {e}")[:2000]
                out["ms"] = round((time.perf_counter() - t1) * 1000)
                return out

            for step in range(1, spec.max_steps + 1):
                last = step == spec.max_steps
                if last and step > 1:
                    messages.append({"role": "user", "content": "(Tool budget exhausted: answer now from the evidence "
                                                                "gathered so far, without calling tools.)"})
                yield {"type": "step", "n": step}
                result = None
                async for ev in chat_stream(messages, model=spec.model, tools=tools, max_tokens=spec.max_tokens,
                                            purpose=f"agent:{spec.name}"):
                    if ev["type"] == "text":
                        yield ev
                    elif ev["type"] == "done":
                        result = ev["result"]
                assert result is not None
                usage = usage + result.usage
                messages.append(result.assistant_message())
                if not result.tool_calls or last:
                    final_text = result.content
                    break
                for tc in result.tool_calls:
                    try:
                        shown = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        shown = tc["function"]["arguments"]
                    yield {"type": "tool_call", "id": tc["id"], "name": tc["function"]["name"], "args": shown}
                outs = await asyncio.gather(*(call(tc) for tc in result.tool_calls))
                for out in outs:
                    if out["ok"]:
                        data = out.pop("data")
                        payload = data.get("result", data) if isinstance(data, dict) else data
                        evidence.merge(data.get("_evidence") or {})
                        for d in data.get("_docs") or []:
                            docs[d["id"]] = {**docs.get(d["id"], {}), **d}
                        trace = data.get("_trace") or []
                        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
                        if len(content) > MAX_TOOL_CHARS:
                            content = content[:MAX_TOOL_CHARS] + "...(truncated)"
                        summary = _summary(payload)
                    else:
                        trace, content, summary = [], f"ERROR: {out['error']}", out["error"][:200]
                    messages.append({"role": "tool", "tool_call_id": out["id"], "content": content})
                    entry = {**{k: out[k] for k in ("id", "name", "args", "ok", "ms")}, "summary": summary,
                             "trace": trace}
                    calls.append({k: v for k, v in entry.items() if k != "trace"})
                    yield {"type": "tool_result", **entry}
    except Exception as e:  # noqa: BLE001
        log.exception("agent %s failed", spec.name)
        yield {"type": "error", "message": mask_secrets(f"{type(e).__name__}: {e}")[:500]}
        return

    if not final_text.strip():
        final_text = "I could not complete an answer within the step budget."
    cited_ids = list(dict.fromkeys(DOC_ID_RE.findall(final_text)))
    citations = [{"n": i, **docs[d]} for i, d in enumerate((d for d in cited_ids if d in docs), start=1)]
    unverified = [d for d in cited_ids if d not in docs]
    yield {"type": "final", "agent": spec.name, "text": final_text, "citations": citations,
           "unverified_citations": unverified, "evidence": final_evidence(evidence, {c["id"] for c in citations}),
           "tool_calls": calls, "steps": step, "usage": asdict(usage), "cost_usd": round(usage.cost_usd, 5),
           "ms": round((time.perf_counter() - t0) * 1000)}
