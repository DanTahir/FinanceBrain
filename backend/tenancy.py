"""Tenants, demo personas and the signed auth context carried by every tool call.

Identity never comes from the LLM. In-process agents attach an HMAC-signed token (``fb1.<payload>.<sig>``) to the
``_meta`` of every MCP request; external MCP clients send it (or the read-only public demo token) as
``Authorization: Bearer``. The MCP server verifies the signature and derives the tenant graph, persona (whose
ACL edges filter every query) and scopes from it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

from backend.config import REPO_ROOT, get_settings

TENANTS: dict[str, str] = {"halcyon": "Halcyon Ridge Capital", "brightwater": "Brightwater Credit Union"}

PERSONA_LABELS: dict[str, str] = {
    "compliance_officer": "Compliance officer",
    "aml_analyst": "AML analyst",
    "ma_banker": "M&A banker",
    "lender": "Lending manager",
    "engineer": "Engineer",
}

# read: query tools; act: open cases / propose actions; approve: approve proposed actions; reveal: vault reveal
_DEFAULT_SCOPES: dict[str, tuple[str, ...]] = {
    "compliance_officer": ("read", "act", "approve", "reveal"),
    "aml_analyst": ("read", "act"),
}
_FALLBACK_SCOPES = ("read",)  # everyone else: read-only (no investigations, cases or approvals)


class AuthError(Exception):
    """Missing, malformed, expired or forged credentials, or an unknown tenant/persona."""


@dataclass(frozen=True)
class Persona:
    key: str
    person_id: str
    name: str
    title: str
    team_id: str
    label: str

    def public(self) -> dict:
        return {"key": self.key, "person_id": self.person_id, "name": self.name, "title": self.title,
                "team": self.team_id, "label": self.label}


@lru_cache
def personas(tenant: str) -> dict[str, Persona]:
    """Demo personas of a tenant, from the committed synthetic world (names and titles only)."""
    if tenant not in TENANTS:
        raise AuthError(f"unknown tenant {tenant!r}")
    gt = json.loads((REPO_ROOT / "data" / "raw" / tenant / "ground_truth.json").read_text(encoding="utf-8"))
    world = json.loads((REPO_ROOT / "data" / "world" / tenant / "world.json").read_text(encoding="utf-8"))
    people = {p["id"]: p for p in world["people"]}
    out: dict[str, Persona] = {}
    for key, pid in gt["refs"]["personas"].items():
        p = people[pid]
        out[key] = Persona(key, pid, p["name"], p["title"], p["team_id"],
                           PERSONA_LABELS.get(key, key.replace("_", " ").title()))
    order = list(PERSONA_LABELS)
    return dict(sorted(out.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)))


@dataclass(frozen=True)
class AuthContext:
    tenant: str
    persona: str
    person_id: str
    user: str = "anon"
    scopes: tuple[str, ...] = ("read",)

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def persona_info(self) -> Persona:
        return personas(self.tenant)[self.persona]


def make_auth(tenant: str, persona: str, user: str = "anon", scopes: tuple[str, ...] | list[str] | None = None
              ) -> AuthContext:
    ps = personas(tenant)
    if persona not in ps:
        raise AuthError(f"unknown persona {persona!r} for tenant {tenant!r}")
    sc = tuple(scopes) if scopes is not None else _DEFAULT_SCOPES.get(persona, _FALLBACK_SCOPES)
    return AuthContext(tenant, persona, ps[persona].person_id, user, sc)


_secret_cache: bytes | None = None


def _secret() -> bytes:
    """HMAC key: FB_SESSION_SECRET (prod, from SSM) or a generated dev key in the gitignored secrets/ folder."""
    global _secret_cache
    if _secret_cache is None:
        s = get_settings().session_secret
        if s:
            _secret_cache = s.encode()
        else:
            path = REPO_ROOT / "secrets" / "session.key"
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            _secret_cache = path.read_text(encoding="utf-8").strip().encode()
    return _secret_cache


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(body: str) -> str:
    return _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def sign_auth(a: AuthContext, ttl: int = 900) -> str:
    payload = {"t": a.tenant, "p": a.persona, "u": a.user, "s": list(a.scopes), "exp": int(time.time()) + ttl}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"fb1.{body}.{_mac(body)}"


def verify_auth(token: str) -> AuthContext:
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != "fb1":
        raise AuthError("malformed token")
    _, body, sig = parts
    if not hmac.compare_digest(_mac(body), sig):
        raise AuthError("bad token signature")
    try:
        data = json.loads(_unb64(body))
    except ValueError:
        raise AuthError("malformed token payload") from None
    if int(data.get("exp", 0)) < time.time():
        raise AuthError("token expired")
    return make_auth(data["t"], data["p"], data.get("u", "anon"), data.get("s") or ("read",))


def demo_mcp_auth() -> AuthContext:
    """Identity behind the public read-only MCP demo token: Halcyon engineer persona, read scope only."""
    return make_auth("halcyon", "engineer", user="mcp-demo", scopes=("read",))


def auth_from_bearer(token: str) -> AuthContext:
    token = (token or "").strip()
    demo = get_settings().demo_mcp_token
    if demo and hmac.compare_digest(token.encode(), demo.encode()):
        return demo_mcp_auth()
    if token.startswith("fb1."):
        return verify_auth(token)
    raise AuthError("unknown bearer token")
