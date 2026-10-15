"""Consistent pseudonymization tokens and the encrypted token vault (stored in FalkorDB's Redis keyspace).

* The same (tenant, type, normalized value) always maps to the same token, e.g. ``⟨PHONE_0042⟩``. Lookup goes
  through an HMAC fingerprint, so the index never stores raw values.
* Token numbers are sequential per type in first-seen order, so redacting the same corpus in the same order
  yields identical tokens on any machine (which keeps the extraction cache valid across dev and prod).
* Original values are stored AES-256-GCM encrypted (AAD = tenant|token). The data key is envelope-encrypted:
  with AWS KMS (``FB_VAULT_KMS=1``, production) or with a local master key in ``secrets/`` (dev).

Redis keys per tenant: ``pii:idx:<t>`` (fingerprint -> token), ``pii:vault:<t>`` (token -> ciphertext),
``pii:seq:<t>`` (type -> counter), ``pii:dek:<t>`` (wrapped data key).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import os
import re
from datetime import datetime

import redis.asyncio as aioredis
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import REPO_ROOT, get_settings
from backend.redaction.validators import digits

SECRETS_DIR = REPO_ROOT / "secrets"
TOKEN_PREFIX = {"CLIENT_NAME": "NAME", "ACCOUNT_NUMBER": "ACCT", "ROUTING_NUMBER": "ROUTING",
                "CARD_NUMBER": "CARD", "DEVICE_ID": "DEVICE"}
PREFIX_TYPE = {v: k for k, v in TOKEN_PREFIX.items()}
TOKEN_RE = re.compile(r"⟨([A-Z]+)_(\d{4,})⟩")


def token_type(token: str) -> str | None:
    m = TOKEN_RE.fullmatch(token)
    return PREFIX_TYPE.get(m.group(1), m.group(1)) if m else None


def _iso_date(v: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(v.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize(typ: str, value: str) -> str:
    v = value.strip()
    if typ == "PHONE":
        return digits(v)[-10:]
    if typ in ("SSN", "CARD_NUMBER", "ACCOUNT_NUMBER", "ROUTING_NUMBER", "EIN"):
        return digits(v)
    if typ in ("IBAN", "SWIFT", "DEVICE_ID"):
        return re.sub(r"\s", "", v).upper()
    if typ == "EMAIL":
        return v.lower()
    if typ == "DOB":
        return _iso_date(v) or v
    if typ == "SALARY":
        return digits(v.split(".")[0])
    return " ".join(v.casefold().split()).strip(" .,")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def local_secret(name: str) -> bytes:
    """32-byte dev secret under secrets/ (gitignored), created on first use."""
    p = SECRETS_DIR / name
    if p.is_file():
        return base64.b64decode(p.read_text().strip())
    SECRETS_DIR.mkdir(exist_ok=True)
    key = os.urandom(32)
    p.write_text(_b64(key))
    return key


def redis_client() -> aioredis.Redis:
    s = get_settings()
    return aioredis.Redis(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password,
                          decode_responses=True)


def _kms():
    import boto3

    return boto3.client("kms", region_name=get_settings().aws_region)


def _ctx(tenant: str) -> dict[str, str]:
    return {"app": "financebrain", "purpose": "pii-vault", "tenant": tenant}


async def _load_dek(r: aioredis.Redis, tenant: str) -> tuple[bytes, str]:
    key = f"pii:dek:{tenant}"
    rec = await r.get(key)
    if rec is None:
        if get_settings().vault_kms:
            resp = await asyncio.to_thread(_kms().generate_data_key, KeyId=get_settings().kms_key_alias,
                                           KeySpec="AES_256", EncryptionContext=_ctx(tenant))
            new_rec = "kms:" + _b64(resp["CiphertextBlob"])
        else:
            nonce = os.urandom(12)
            plain = os.urandom(32)
            wrapped = AESGCM(local_secret("vault_master.key")).encrypt(nonce, plain, tenant.encode())
            new_rec = "local:" + _b64(nonce + wrapped)
        await r.set(key, new_rec, nx=True)  # first writer wins; everyone then loads the stored record
        rec = await r.get(key)
    mode, blob = rec.split(":", 1)
    raw = base64.b64decode(blob)
    if mode == "kms":
        resp = await asyncio.to_thread(_kms().decrypt, CiphertextBlob=raw, EncryptionContext=_ctx(tenant))
        return resp["Plaintext"], mode
    return AESGCM(local_secret("vault_master.key")).decrypt(raw[:12], raw[12:], tenant.encode()), mode


class Vault:
    def __init__(self, r: aioredis.Redis, tenant: str, hmac_key: bytes, dek: bytes, mode: str):
        self.r, self.tenant, self.mode = r, tenant, mode
        self._hk = hmac_key
        self._aead = AESGCM(dek)
        self._cache: dict[str, str] = {}
        self.k_idx, self.k_vault, self.k_seq = f"pii:idx:{tenant}", f"pii:vault:{tenant}", f"pii:seq:{tenant}"

    @classmethod
    async def open(cls, r: aioredis.Redis, tenant: str) -> "Vault":
        s = get_settings()
        hk = s.pii_hmac_key.encode() if s.pii_hmac_key else local_secret("pii_hmac.key")
        dek, mode = await _load_dek(r, tenant)
        return cls(r, tenant, hk, dek, mode)

    @staticmethod
    async def reset(r: aioredis.Redis, tenant: str) -> None:
        """Drop tokens (keeps the wrapped data key)."""
        await r.delete(f"pii:idx:{tenant}", f"pii:vault:{tenant}", f"pii:seq:{tenant}")

    def fingerprint(self, typ: str, value: str) -> str:
        msg = f"{self.tenant}|{typ}|{normalize(typ, value)}".encode()
        return hmac.new(self._hk, msg, hashlib.sha256).hexdigest()[:32]

    async def tokenize(self, typ: str, value: str) -> str:
        fp = self.fingerprint(typ, value)
        tok = self._cache.get(fp)
        if tok:
            return tok
        tok = await self.r.hget(self.k_idx, fp)
        if tok is None:
            n = await self.r.hincrby(self.k_seq, typ, 1)
            cand = f"⟨{TOKEN_PREFIX.get(typ, typ)}_{n:04d}⟩"
            await self.r.hset(self.k_vault, cand, self._encrypt(cand, value.strip()))
            if await self.r.hsetnx(self.k_idx, fp, cand):
                tok = cand
            else:  # lost a race with a concurrent writer: use theirs
                await self.r.hdel(self.k_vault, cand)
                tok = await self.r.hget(self.k_idx, fp)
        self._cache[fp] = tok
        return tok

    def _encrypt(self, token: str, value: str) -> str:
        nonce = os.urandom(12)
        return _b64(nonce + self._aead.encrypt(nonce, value.encode(), f"{self.tenant}|{token}".encode()))

    async def reveal(self, token: str) -> str | None:
        blob = await self.r.hget(self.k_vault, token)
        if not blob:
            return None
        raw = base64.b64decode(blob)
        return self._aead.decrypt(raw[:12], raw[12:], f"{self.tenant}|{token}".encode()).decode()

    async def stats(self) -> dict:
        return {"tokens": await self.r.hlen(self.k_vault), "by_type": await self.r.hgetall(self.k_seq),
                "key_mode": self.mode}


async def _selftest(tenant: str, kms: bool) -> None:
    """Round-trip check on a throwaway tenant namespace; prints no PII (values are dummy strings)."""
    if kms:
        get_settings().vault_kms = True
    r = redis_client()
    try:
        await r.delete(f"pii:dek:{tenant}")
        await Vault.reset(r, tenant)
        v = await Vault.open(r, tenant)
        a = await v.tokenize("PHONE", "(201) 234-5678")
        b = await v.tokenize("PHONE", "+1 201 234 5678")
        c = await v.tokenize("PHONE", "201-234-9999")
        v2 = await Vault.open(r, tenant)  # fresh process view: unwraps the stored data key again
        ok = a == b != c and await v2.reveal(a) == "(201) 234-5678" and await v2.tokenize("PHONE", "201.234.5678") == a
        print(f"vault selftest mode={v.mode} same-value-same-token={a == b} distinct={a != c} reveal_ok={ok} "
              f"tokens={a},{c}")
        if not ok:
            raise SystemExit(1)
    finally:
        await r.delete(f"pii:dek:{tenant}")
        await Vault.reset(r, tenant)
        await r.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--kms", action="store_true", help="wrap the data key with AWS KMS (needs AWS credentials)")
    a = ap.parse_args()
    if a.selftest:
        asyncio.run(_selftest("selftest_kms" if a.kms else "selftest_local", a.kms))
