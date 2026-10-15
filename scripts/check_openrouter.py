"""Phase 0 OpenRouter verification. Prints statuses, dims, usage, and cost only -- never the API key.

Run: uv run python scripts/check_openrouter.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings, load_openrouter_key, mask_secrets  # noqa: E402

S = get_settings()
BASE = S.openrouter_base_url
RESULTS: dict[str, object] = {}


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {load_openrouter_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": S.app_referer,
        "X-Title": S.app_title,
    }


def out(name: str, value: object) -> None:
    RESULTS[name] = value
    print(f"[{name}] {mask_secrets(json.dumps(value, default=str))}", flush=True)


def merge_reasoning(acc: dict[int, dict], parts: list[dict]) -> None:
    """Merge streamed reasoning_details fragments by index."""
    for p in parts:
        idx = p.get("index", 0)
        cur = acc.setdefault(idx, {})
        for k, v in p.items():
            if v is None:
                continue
            if k in ("text", "summary", "data") and isinstance(v, str):
                cur[k] = cur.get(k, "") + v
            else:
                cur[k] = v


async def stream_chat(client: httpx.AsyncClient, body: dict) -> dict:
    body = {**body, "stream": True, "usage": {"include": True}}
    text, tool_calls, reasoning, usage, provider, finish = "", {}, {}, None, None, None
    t0 = time.perf_counter()
    first_token = None
    async with client.stream("POST", f"{BASE}/chat/completions", json=body, headers=headers()) as r:
        if r.status_code != 200:
            raw = (await r.aread()).decode(errors="replace")
            return {"status": r.status_code, "error": mask_secrets(raw[:800])}
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if "error" in chunk:
                return {"status": "stream_error", "error": chunk["error"]}
            provider = chunk.get("provider", provider)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                d = ch.get("delta", {}) or {}
                if d.get("content"):
                    first_token = first_token or time.perf_counter()
                    text += d["content"]
                if d.get("reasoning_details"):
                    merge_reasoning(reasoning, d["reasoning_details"])
                for tc in d.get("tool_calls") or []:
                    slot = tool_calls.setdefault(tc["index"], {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    return {
        "status": 200,
        "provider": provider,
        "text": text,
        "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
        "reasoning_details": [reasoning[i] for i in sorted(reasoning)],
        "finish": finish,
        "usage": usage,
        "latency_s": round(time.perf_counter() - t0, 2),
        "ttft_s": round(first_token - t0, 2) if first_token else None,
    }


def brief_usage(u: dict | None) -> dict | None:
    if not u:
        return None
    return {
        "prompt": u.get("prompt_tokens"),
        "completion": u.get("completion_tokens"),
        "cost": u.get("cost"),
        "prompt_details": u.get("prompt_tokens_details"),
        "completion_details": u.get("completion_tokens_details"),
    }


async def check_key(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{BASE}/key", headers=headers())
    if r.status_code != 200:
        out("key", {"status": r.status_code, "error": mask_secrets(r.text[:300])})
        return
    d = r.json().get("data", {})
    safe = {k: d.get(k) for k in ("limit", "limit_remaining", "limit_reset", "usage", "usage_daily",
                                  "is_free_tier", "rate_limit") if k in d}
    out("key", {"status": 200, **safe})
    r2 = await client.get(f"{BASE}/credits", headers=headers())
    if r2.status_code == 200:
        cd = r2.json().get("data", {})
        out("credits", {"total_credits": cd.get("total_credits"), "total_usage": cd.get("total_usage")})
    else:
        out("credits", {"status": r2.status_code})


async def check_models(client: httpx.AsyncClient) -> None:
    for model in (S.model_deep, S.model_default, S.model_fast):
        res = await stream_chat(client, {
            "model": model,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply with exactly the word: READY"}],
        })
        out(f"chat:{model}", {k: res.get(k) for k in ("status", "error", "provider", "text", "finish",
                                                      "latency_s", "ttft_s")} | {"usage": brief_usage(res.get("usage"))})


TOOLS = [{
    "type": "function",
    "function": {
        "name": "who_owns",
        "description": "Look up the current owner (a person) of an internal system in the company graph.",
        "parameters": {
            "type": "object",
            "properties": {"system": {"type": "string", "description": "System name, e.g. PricingSvc"}},
            "required": ["system"],
        },
    },
}]


async def check_tool_loop(client: httpx.AsyncClient, model: str, reasoning: dict | None, label: str) -> None:
    messages: list[dict] = [
        {"role": "system", "content": "You answer questions about the company. Always use tools to look facts up."},
        {"role": "user", "content": "Who owns PricingSvc right now?"},
    ]
    body: dict = {"model": model, "max_tokens": 2048, "messages": messages, "tools": TOOLS, "tool_choice": "auto"}
    if reasoning:
        body["reasoning"] = reasoning
    r1 = await stream_chat(client, body)
    step1 = {"status": r1.get("status"), "error": r1.get("error"), "provider": r1.get("provider"),
             "finish": r1.get("finish"), "tool_calls": r1.get("tool_calls"),
             "reasoning_parts": [{k: (len(v) if isinstance(v, str) else v) for k, v in p.items()}
                                 for p in r1.get("reasoning_details") or []]}
    if r1.get("status") != 200 or not r1.get("tool_calls"):
        out(f"tools:{label}", {"step1": step1, "ok": False})
        return
    tc = r1["tool_calls"][0]
    try:
        args = json.loads(tc["arguments"] or "{}")
    except json.JSONDecodeError as e:
        out(f"tools:{label}", {"step1": step1, "ok": False, "bad_args": str(e)})
        return
    assistant = {
        "role": "assistant",
        "content": r1["text"] or None,
        "tool_calls": [{"id": tc["id"], "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"}}],
    }
    if r1.get("reasoning_details"):
        assistant["reasoning_details"] = r1["reasoning_details"]
    messages += [
        assistant,
        {"role": "tool", "tool_call_id": tc["id"],
         "content": json.dumps({"system": args.get("system"), "owner": "Priya Raman", "team": "Market Data",
                                "since": "2026-08-14"})},
    ]
    r2 = await stream_chat(client, {**body, "messages": messages})
    out(f"tools:{label}", {
        "step1": step1,
        "args": args,
        "step2": {"status": r2.get("status"), "error": r2.get("error"), "finish": r2.get("finish"),
                  "text": (r2.get("text") or "")[:200]},
        "ok": r2.get("status") == 200 and "Priya" in (r2.get("text") or ""),
        "usage2": brief_usage(r2.get("usage")),
    })


async def check_json_schema(client: httpx.AsyncClient) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["entities", "decisions"],
        "properties": {
            "entities": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "type"],
                "properties": {"name": {"type": "string"},
                               "type": {"type": "string", "enum": ["Person", "Team", "System", "Policy", "Client"]}}}},
            "decisions": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["summary", "date"],
                "properties": {"summary": {"type": "string"}, "date": {"type": "string"}}}},
        },
    }
    body = {
        "model": S.model_fast,
        "max_tokens": 512,
        "messages": [{"role": "user", "content":
                      "Extract entities and decisions: 'On 2026-08-03 Maria Lopez (Compliance) decided to pause "
                      "crypto client onboarding under the KYC Policy until ComplyTrack is upgraded.'"}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "extraction", "strict": True, "schema": schema}},
        "provider": {"require_parameters": True, "data_collection": "deny"},
        "usage": {"include": True},
    }
    r = await client.post(f"{BASE}/chat/completions", json=body, headers=headers(), timeout=120)
    if r.status_code != 200:
        out("json_schema", {"status": r.status_code, "error": mask_secrets(r.text[:600])})
        return
    d = r.json()
    content = d["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content)
        ok = isinstance(parsed.get("entities"), list)
    except json.JSONDecodeError:
        parsed, ok = content[:300], False
    out("json_schema", {"status": 200, "provider": d.get("provider"), "ok": ok, "parsed": parsed,
                        "usage": brief_usage(d.get("usage"))})


async def check_caching(client: httpx.AsyncClient) -> None:
    long_ctx = ("FinanceBrain graph schema reference. " + " ".join(
        f"Label L{i} has properties id, name, created_at, updated_at, tenant, sensitivity, source_doc_{i}."
        for i in range(400)))
    results = []
    for _ in range(2):
        body = {
            "model": S.model_fast,
            "max_tokens": 16,
            "messages": [
                {"role": "system", "content": [
                    {"type": "text", "text": long_ctx, "cache_control": {"type": "ephemeral"}}]},
                {"role": "user", "content": "Reply OK."},
            ],
            "provider": {"data_collection": "deny"},
            "usage": {"include": True},
        }
        r = await client.post(f"{BASE}/chat/completions", json=body, headers=headers(), timeout=120)
        if r.status_code != 200:
            results.append({"status": r.status_code, "error": mask_secrets(r.text[:400])})
            continue
        d = r.json()
        results.append({"status": 200, "provider": d.get("provider"), "usage": brief_usage(d.get("usage"))})
        await asyncio.sleep(1.5)
    out("caching", results)


async def check_embeddings(client: httpx.AsyncClient) -> None:
    texts = ["Wire transfer of $48,000 flagged for review", "Who owns the KYC pipeline?"]
    variants = [
        ("voyage-4:default", {"model": "voyageai/voyage-4", "input": texts}),
        ("voyage-4:dims1024", {"model": "voyageai/voyage-4", "input": texts, "dimensions": 1024}),
        ("voyage-4:input_type", {"model": "voyageai/voyage-4", "input": texts, "dimensions": 1024,
                                 "input_type": "query"}),
        ("oai-3-large:dims1024", {"model": "openai/text-embedding-3-large", "input": texts, "dimensions": 1024}),
    ]
    vecs = {}
    for label, body in variants:
        r = await client.post(f"{BASE}/embeddings", json=body, headers=headers(), timeout=60)
        if r.status_code != 200:
            out(f"embed:{label}", {"status": r.status_code, "error": mask_secrets(r.text[:400])})
            continue
        d = r.json()
        if "data" not in d:
            out(f"embed:{label}", {"status": 200, "unexpected": mask_secrets(json.dumps(d)[:400])})
            continue
        dims = [len(x["embedding"]) for x in d["data"]]
        vecs[label] = d["data"][0]["embedding"]
        norm = sum(v * v for v in vecs[label]) ** 0.5
        out(f"embed:{label}", {"status": 200, "n": len(dims), "dims": dims, "norm": round(norm, 4),
                               "provider": d.get("provider"), "usage": d.get("usage")})
    a, b = vecs.get("voyage-4:dims1024"), vecs.get("voyage-4:input_type")
    if a and b and len(a) == len(b):
        same = max(abs(x - y) for x, y in zip(a, b)) < 1e-6
        out("embed:input_type_changes_vector", not same)


async def main() -> None:
    which = set(sys.argv[1:]) or {"key", "models", "tools", "json", "cache", "embed"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
        if "key" in which:
            await check_key(client)
        if "models" in which:
            await check_models(client)
        if "tools" in which:
            await check_tool_loop(client, S.model_default, {"effort": "low"}, "sonnet+reasoning")
            await check_tool_loop(client, S.model_fast, None, "haiku")
            await check_tool_loop(client, S.model_deep, {"effort": "low"}, "opus+reasoning")
        if "tools_hi" in which:
            await check_tool_loop(client, S.model_default, {"effort": "high"}, "sonnet+reasoning_high")
            await check_tool_loop(client, S.model_default, {"max_tokens": 2048}, "sonnet+reasoning_budget")
        if "json" in which:
            await check_json_schema(client)
        if "cache" in which:
            await check_caching(client)
        if "embed" in which:
            await check_embeddings(client)
    Path("logs").mkdir(exist_ok=True)
    Path("logs/check_openrouter.json").write_text(mask_secrets(json.dumps(RESULTS, indent=2, default=str)))


if __name__ == "__main__":
    asyncio.run(main())
