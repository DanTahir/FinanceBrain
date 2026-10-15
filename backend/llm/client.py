"""OpenRouter chat client: retries, concurrency limits, cost accounting, outbound PII guard.

All LLM traffic goes through this module. Behaviour verified in Phase 0 (docs/FALKORDB_NOTES.md):
- Claude 5.5 on OpenRouter does not support forced tool_choice, so tools always use "auto".
- Structured output uses response_format json_schema plus provider.require_parameters.
- usage.cost is reported per response (also on the final streaming chunk).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import re
import threading
from collections import defaultdict
from dataclasses import astuple, dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from backend.config import REPO_ROOT, get_settings, load_openrouter_key, mask_secrets
from backend.llm.guard import OutboundPIIBlocked, check_outbound, iter_message_texts

__all__ = [
    "LLMError", "OutboundPIIBlocked", "Usage", "ChatResult", "COSTS", "get_client", "system_block", "chat",
    "chat_stream", "chat_json", "parse_json_text",
]

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 520, 522, 524, 529}
MAX_ATTEMPTS = 6


class LLMError(Exception):
    pass


class _ProviderError(Exception):
    def __init__(self, msg: str, retryable: bool):
        super().__init__(msg)
        self.retryable = retryable


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncOpenAI(
            base_url=s.openrouter_base_url,
            api_key=load_openrouter_key(),
            default_headers={"HTTP-Referer": s.app_referer, "X-Title": s.app_title},
            max_retries=0,
            timeout=180.0,
        )
    return _client


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from an SDK object, its model_extra, or a dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(name)
    else:
        v = getattr(obj, name, None)
        if v is None:
            extra = getattr(obj, "model_extra", None) or {}
            v = extra.get(name)
    return default if v is None else v


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @classmethod
    def from_api(cls, u: Any) -> "Usage":
        if u is None:
            return cls()
        ptd = _get(u, "prompt_tokens_details")
        ctd = _get(u, "completion_tokens_details")
        return cls(
            prompt_tokens=int(_get(u, "prompt_tokens", 0) or 0),
            completion_tokens=int(_get(u, "completion_tokens", 0) or 0),
            cached_tokens=int(_get(ptd, "cached_tokens", 0) or 0),
            cache_write_tokens=int(_get(ptd, "cache_write_tokens", 0) or 0),
            reasoning_tokens=int(_get(ctd, "reasoning_tokens", 0) or 0),
            cost_usd=float(_get(u, "cost", 0.0) or 0.0),
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(*(a + b for a, b in zip(astuple(self), astuple(other))))


class CostTracker:
    """Process-wide USD accounting fed by OpenRouter usage.cost."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_model: dict[str, float] = defaultdict(float)
        self._by_purpose: dict[str, float] = defaultdict(float)
        self._calls = 0
        self.on_cost_hooks: list[Callable[[float], Awaitable[None]]] = []
        self._log_path = REPO_ROOT / "logs" / "llm_usage.jsonl"

    async def record(self, model: str, purpose: str, usage: Usage) -> None:
        with self._lock:
            self._by_model[model] += usage.cost_usd
            self._by_purpose[purpose] += usage.cost_usd
            self._calls += 1
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model, "purpose": purpose,
                    "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                    "cached_tokens": usage.cached_tokens, "cost_usd": round(usage.cost_usd, 6),
                }) + "\n")
        except OSError:
            log.debug("could not write usage log", exc_info=True)
        for hook in list(self.on_cost_hooks):
            try:
                await hook(usage.cost_usd)
            except Exception:  # noqa: BLE001 - hooks must never break a call
                log.exception("cost hook failed")

    def total_usd(self) -> float:
        with self._lock:
            return sum(self._by_model.values())

    def by_model(self) -> dict[str, float]:
        with self._lock:
            return dict(self._by_model)

    def by_purpose(self) -> dict[str, float]:
        with self._lock:
            return dict(self._by_purpose)

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self._calls, "total_usd": round(sum(self._by_model.values()), 4),
                    "by_model": {k: round(v, 4) for k, v in self._by_model.items()},
                    "by_purpose": {k: round(v, 4) for k, v in self._by_purpose.items()}}


COSTS = CostTracker()

_sems: dict[int, asyncio.Semaphore] = {}


def _sem() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    sem = _sems.get(key)
    if sem is None:
        sem = _sems[key] = asyncio.Semaphore(get_settings().llm_concurrency)
    return sem


def _classify(e: Exception) -> tuple[bool, float | None]:
    retry_after: float | None = None
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            ra = resp.headers.get("retry-after")
            retry_after = min(60.0, float(ra)) if ra else None
        except (ValueError, AttributeError):
            retry_after = None
    if isinstance(e, _ProviderError):
        return e.retryable, retry_after
    if isinstance(e, (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
                      openai.InternalServerError)):
        return True, retry_after
    if isinstance(e, openai.APIStatusError):
        return e.status_code in RETRY_STATUS, retry_after
    return False, None


async def _with_retries(fn: Callable[[], Awaitable[Any]], *, what: str) -> Any:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with _sem():
                return await fn()
        except OutboundPIIBlocked:
            raise
        except Exception as e:  # noqa: BLE001
            retry, after = _classify(e)
            detail = mask_secrets(f"{type(e).__name__}: {e}")[:800]
            if not retry or attempt == MAX_ATTEMPTS:
                raise LLMError(f"{what} failed after {attempt} attempt(s): {detail}") from None
            delay = after if after is not None else random.uniform(0, min(30.0, 2.0 ** (attempt - 1)))
            log.warning("%s attempt %d failed (%s); retrying in %.1fs", what, attempt, detail[:200], delay)
            await asyncio.sleep(delay)
    raise LLMError(f"{what} failed")  # pragma: no cover


def _raise_body_error(resp: Any) -> None:
    err = _get(resp, "error") or {}
    code = _get(err, "code")
    try:
        code_i = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_i = None
    msg = mask_secrets(str(_get(err, "message", "empty response (no choices)")))
    raise _ProviderError(f"provider error {code}: {msg}", retryable=code_i is None or code_i in RETRY_STATUS)


def _extra_body(require_parameters: bool, extra_body: dict | None, reasoning: dict | None) -> dict:
    provider: dict[str, Any] = {"data_collection": "deny", "allow_fallbacks": True}
    if require_parameters:
        provider["require_parameters"] = True
    body: dict[str, Any] = {"provider": provider, "usage": {"include": True}}
    if reasoning:
        body["reasoning"] = reasoning
    for k, v in (extra_body or {}).items():
        if k == "provider" and isinstance(v, dict):
            provider.update(v)
        else:
            body[k] = v
    return body


def system_block(text: str, cache: bool = True) -> dict:
    """System message; with cache=True the block is marked for Anthropic prompt caching."""
    if not cache:
        return {"role": "system", "content": text}
    return {"role": "system", "content": [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]}


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str | None = None
    reasoning_details: list | None = None

    def assistant_message(self) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.reasoning_details:
            msg["reasoning_details"] = self.reasoning_details
        return msg


def _tool_calls_to_dicts(tcs: Any) -> list[dict]:
    out = []
    for i, tc in enumerate(tcs or []):
        fn = _get(tc, "function")
        out.append({"id": _get(tc, "id") or f"call_{i}", "type": "function",
                    "function": {"name": _get(fn, "name", ""), "arguments": _get(fn, "arguments") or "{}"}})
    return out


def _kwargs(messages: list[dict], model: str, tools: list[dict] | None, max_tokens: int, temperature: float | None,
            reasoning: dict | None, response_format: dict | None, require_parameters: bool,
            extra_body: dict | None) -> dict:
    kw: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        kw["tools"] = tools
        kw["tool_choice"] = "auto"
    if temperature is not None:
        kw["temperature"] = temperature
    if response_format:
        kw["response_format"] = response_format
    kw["extra_body"] = _extra_body(require_parameters, extra_body, reasoning)
    return kw


async def chat(
    messages: list[dict], *, model: str | None = None, tools: list[dict] | None = None, max_tokens: int = 2048,
    temperature: float | None = None, reasoning: dict | None = None, response_format: dict | None = None,
    require_parameters: bool = False, purpose: str = "chat", extra_body: dict | None = None, guard: bool = True,
) -> ChatResult:
    """Non-streaming chat completion."""
    model = model or get_settings().model_default
    if guard:
        check_outbound(iter_message_texts(messages), where=purpose)
    kw = _kwargs(messages, model, tools, max_tokens, temperature, reasoning, response_format, require_parameters,
                 extra_body)

    async def call() -> Any:
        resp = await get_client().chat.completions.create(**kw)
        if not resp.choices:
            _raise_body_error(resp)
        return resp

    resp = await _with_retries(call, what=f"chat[{purpose}]")
    choice = resp.choices[0]
    msg = choice.message
    usage = Usage.from_api(resp.usage)
    used = resp.model or model
    await COSTS.record(used, purpose, usage)
    return ChatResult(
        content=msg.content or "", tool_calls=_tool_calls_to_dicts(msg.tool_calls), finish_reason=choice.finish_reason,
        usage=usage, model=used, provider=_get(resp, "provider"), reasoning_details=_get(msg, "reasoning_details"),
    )


async def chat_stream(
    messages: list[dict], *, model: str | None = None, tools: list[dict] | None = None, max_tokens: int = 2048,
    temperature: float | None = None, reasoning: dict | None = None, response_format: dict | None = None,
    require_parameters: bool = False, purpose: str = "chat", extra_body: dict | None = None, guard: bool = True,
) -> AsyncIterator[dict]:
    """Streaming chat. Yields {'type':'text','delta'} events, then one {'type':'done','result': ChatResult}."""
    model = model or get_settings().model_default
    if guard:
        check_outbound(iter_message_texts(messages), where=purpose)
    kw = _kwargs(messages, model, tools, max_tokens, temperature, reasoning, response_format, require_parameters,
                 extra_body)
    kw["stream"] = True
    kw["stream_options"] = {"include_usage": True}

    async def open_stream() -> Any:
        return await get_client().chat.completions.create(**kw)

    stream = await _with_retries(open_stream, what=f"chat_stream[{purpose}]")
    parts: list[str] = []
    tools_acc: dict[int, dict] = {}
    finish: str | None = None
    usage = Usage()
    used = model
    provider: str | None = None
    reasoning_details: list = []
    try:
        async for chunk in stream:
            err = _get(chunk, "error")
            if err:
                raise LLMError(f"stream error: {mask_secrets(str(err))[:500]}")
            used = chunk.model or used
            provider = _get(chunk, "provider") or provider
            if chunk.usage:
                usage = Usage.from_api(chunk.usage)
            for ch in chunk.choices or []:
                d = ch.delta
                if d is not None:
                    if d.content:
                        parts.append(d.content)
                        yield {"type": "text", "delta": d.content}
                    for tc in d.tool_calls or []:
                        slot = tools_acc.setdefault(tc.index, {"id": None, "type": "function",
                                                               "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function is not None:
                            if tc.function.name and not slot["function"]["name"]:
                                slot["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments
                    rd = _get(d, "reasoning_details")
                    if rd:
                        reasoning_details.extend(rd if isinstance(rd, list) else [rd])
                if ch.finish_reason:
                    finish = ch.finish_reason
    finally:
        try:
            await stream.close()
        except Exception:  # noqa: BLE001
            pass
    tool_calls = []
    for idx in sorted(tools_acc):
        slot = tools_acc[idx]
        slot["id"] = slot["id"] or f"call_{idx}"
        slot["function"]["arguments"] = slot["function"]["arguments"] or "{}"
        tool_calls.append(slot)
    await COSTS.record(used, purpose, usage)
    yield {"type": "done", "result": ChatResult(
        content="".join(parts), tool_calls=tool_calls, finish_reason=finish, usage=usage, model=used,
        provider=provider, reasoning_details=reasoning_details or None,
    )}


# --------------------------------------------------------------------------- structured output


def strict_schema(schema: dict) -> dict:
    """Make a JSON schema strict-mode compatible: closed objects, all properties required, no defaults."""
    schema = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        props = node.get("properties")
        if isinstance(props, dict):
            node["additionalProperties"] = False
            node["required"] = list(props)
            for v in props.values():
                walk(v)
        for key in ("items", "anyOf", "allOf", "oneOf", "prefixItems"):
            if key in node:
                walk(node[key])
        for key in ("$defs", "definitions"):
            if isinstance(node.get(key), dict):
                for v in node[key].values():
                    walk(v)
        if isinstance(node.get("additionalProperties"), dict):
            walk(node["additionalProperties"])

    walk(schema)
    return schema


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_json_text(text: str) -> Any:
    t = (text or "").strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON object found in model output")
    obj, _ = json.JSONDecoder().raw_decode(t[min(starts):])
    return obj


async def chat_json(
    messages: list[dict], schema: dict | type[BaseModel], *, model: str | None = None, name: str = "result",
    max_tokens: int = 4096, purpose: str = "json", temperature: float | None = None, guard: bool = True,
) -> Any:
    """Structured output via json_schema response_format; validates and repairs once on failure."""
    model_cls = schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None
    raw_schema = model_cls.model_json_schema() if model_cls else schema
    rf = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": strict_schema(raw_schema)}}
    msgs = list(messages)
    structured = True
    attempt = 0
    while attempt < 2:
        try:
            res = await chat(msgs, model=model or get_settings().model_fast, max_tokens=max_tokens,
                             temperature=temperature, response_format=rf if structured else None,
                             require_parameters=structured, purpose=purpose, guard=guard)
        except LLMError as e:
            # No structured-output endpoint available right now: fall back to prompt-level JSON + validation.
            if structured and "No endpoints found" in str(e):
                log.warning("chat_json[%s]: no structured-output endpoint, falling back to plain JSON", purpose)
                structured = False
                msgs = msgs + [{"role": "user", "content": "Reply with only a JSON value (no prose, no code "
                                                            "fences) matching this JSON schema:\n"
                                                            + json.dumps(rf["json_schema"]["schema"])}]
                continue
            raise
        try:
            obj = parse_json_text(res.content)
            return model_cls.model_validate(obj) if model_cls else obj
        except (ValueError, ValidationError) as e:
            err = str(e)[:600]
            attempt += 1
            if attempt == 2:
                raise LLMError(f"chat_json[{purpose}] invalid output after repair (finish={res.finish_reason}): {err}")
            log.warning("chat_json[%s] invalid output, repairing: %s", purpose, err[:200])
            msgs = msgs + [
                {"role": "assistant", "content": res.content or ""},
                {"role": "user", "content": "Your previous reply was not valid JSON for the schema. "
                                            f"Error: {err}. Reply with only the corrected JSON."},
            ]
    raise LLMError("unreachable")  # pragma: no cover
