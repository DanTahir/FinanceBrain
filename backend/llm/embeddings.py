"""Embeddings via OpenRouter (voyage-4 primary, text-embedding-3-large fallback), with a disk cache.

The cache makes seeding and "Reset demo" free of embedding calls: vectors are keyed by
sha256(model|kind|text) and stored as float16 base64 in an append-only JSONL per model.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Literal

import numpy as np

from backend.config import REPO_ROOT, get_settings
from backend.llm.client import COSTS, LLMError, Usage, _get, _raise_body_error, _with_retries, get_client
from backend.llm.guard import check_outbound

log = logging.getLogger(__name__)

Kind = Literal["document", "query"]
BATCH = 64
CACHE_DIR = REPO_ROOT / "data" / "extracted" / "embeddings"


def _slug(model: str) -> str:
    return model.replace("/", "__").replace(":", "__")


async def embed(texts: list[str], kind: Kind = "document", *, model: str | None = None) -> list[list[float]]:
    s = get_settings()
    model = model or s.embed_model
    if not texts:
        return []
    clean = [(t or "").strip() or " " for t in texts]
    check_outbound(clean, where="embed")
    out: list[list[float]] = []
    for i in range(0, len(clean), BATCH):
        batch = clean[i : i + BATCH]
        extra: dict[str, Any] = {"provider": {"data_collection": "deny", "allow_fallbacks": True}}
        if model.startswith("voyageai/"):
            extra["input_type"] = kind

        async def call(batch: list[str] = batch, extra: dict = extra) -> Any:
            resp = await get_client().embeddings.create(model=model, input=batch, dimensions=s.embed_dims,
                                                        encoding_format="float", extra_body=extra)
            if not resp.data:
                _raise_body_error(resp)
            return resp

        resp = await _with_retries(call, what="embed")
        data = sorted(resp.data, key=lambda d: d.index)
        if len(data) != len(batch):
            raise LLMError(f"embed returned {len(data)} vectors for {len(batch)} inputs")
        for d in data:
            if len(d.embedding) != s.embed_dims:
                raise LLMError(f"embed returned {len(d.embedding)} dims, expected {s.embed_dims} ({model})")
            out.append([float(x) for x in d.embedding])
        usage = Usage(prompt_tokens=int(_get(resp.usage, "prompt_tokens", 0) or 0),
                      cost_usd=float(_get(resp.usage, "cost", 0.0) or 0.0))
        await COSTS.record(model, "embed", usage)
    return out


def _encode(vec: list[float]) -> str:
    return base64.b64encode(np.asarray(vec, dtype="<f2").tobytes()).decode("ascii")


def _decode(s: str) -> list[float]:
    return np.frombuffer(base64.b64decode(s), dtype="<f2").astype(np.float32).tolist()


class EmbeddingCache:
    def __init__(self, path: Path):
        self.path = path
        self._d: dict[str, str] | None = None

    @staticmethod
    def key(model: str, kind: str, text: str) -> str:
        return hashlib.sha256(f"{model}|{kind}|{text}".encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, str]:
        if self._d is None:
            self._d = {}
            if self.path.is_file():
                with self.path.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            rec = json.loads(line)
                            self._d[rec["k"]] = rec["v"]
        return self._d

    def __len__(self) -> int:
        return len(self._load())

    def get(self, key: str) -> list[float] | None:
        v = self._load().get(key)
        return None if v is None else _decode(v)

    def put_many(self, items: dict[str, list[float]]) -> None:
        d = self._load()
        new = {k: _encode(v) for k, v in items.items() if k not in d}
        if not new:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for k, v in new.items():
                f.write(json.dumps({"k": k, "v": v}) + "\n")
            f.flush()
        d.update(new)


_caches: dict[str, EmbeddingCache] = {}


def get_cache(model: str) -> EmbeddingCache:
    if model not in _caches:
        _caches[model] = EmbeddingCache(CACHE_DIR / f"{_slug(model)}.jsonl")
    return _caches[model]


async def embed_cached(texts: list[str], kind: Kind = "document", *, model: str | None = None) -> list[list[float]]:
    model = model or get_settings().embed_model
    cache = get_cache(model)
    clean = [(t or "").strip() or " " for t in texts]
    keys = [EmbeddingCache.key(model, kind, t) for t in clean]
    found: dict[str, list[float]] = {}
    misses: dict[str, str] = {}
    for k, t in zip(keys, clean):
        if k in found or k in misses:
            continue
        v = cache.get(k)
        if v is None:
            misses[k] = t
        else:
            found[k] = v
    if misses:
        vecs = await embed(list(misses.values()), kind, model=model)
        new = dict(zip(misses.keys(), vecs))
        cache.put_many(new)
        found.update(new)
    log.info("embed_cached: %d texts, %d cache hits, %d embedded", len(texts), len(set(keys)) - len(misses), len(misses))
    return [found[k] for k in keys]


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom else 0.0


if __name__ == "__main__":
    import tempfile

    rng = np.random.default_rng(0)
    vec = (rng.standard_normal(1024) / 32).tolist()
    with tempfile.TemporaryDirectory() as td:
        c = EmbeddingCache(Path(td) / "t.jsonl")
        k = EmbeddingCache.key("m", "document", "hello")
        c.put_many({k: vec})
        c2 = EmbeddingCache(Path(td) / "t.jsonl")
        back = c2.get(k)
        assert back is not None and len(back) == 1024 and cosine(vec, back) > 0.9999, cosine(vec, back or [0.0])
    print("embeddings self-test OK")
