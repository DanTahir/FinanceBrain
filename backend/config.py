"""Central configuration. All model slugs and endpoints live here, overridable via FB_* env vars."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent

_KEY_FILES = (
    REPO_ROOT / "Openrouterkey.txt",
    Path("/run/secrets/openrouter_api_key"),
)

_SECRET_RE = re.compile(r"sk-or-[A-Za-z0-9_\-]+")


def mask_secrets(text: str) -> str:
    """Mask anything that looks like an OpenRouter key before it reaches a log or response."""
    return _SECRET_RE.sub("sk-or-***", text)


def load_openrouter_key() -> str:
    """Load the OpenRouter key from env, then from the gitignored key file. Never log the result."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        for path in _KEY_FILES:
            if path.is_file():
                key = path.read_text(encoding="utf-8-sig").strip()
                if key:
                    break
    # Tolerate a "OPENROUTER_API_KEY=..." style file.
    if "=" in key and not key.startswith("sk-"):
        key = key.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise RuntimeError(
            "OpenRouter API key not found. Set OPENROUTER_API_KEY or create Openrouterkey.txt in the repo root."
        )
    return key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FB_", env_file=".env", extra="ignore")

    # --- OpenRouter ---
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    app_referer: str = "https://github.com/DanTahir/FinanceBrain"
    app_title: str = "FinanceBrain"
    model_default: str = "anthropic/claude-sonnet-5.5"
    model_deep: str = "anthropic/claude-opus-5.5"
    model_fast: str = "anthropic/claude-haiku-5.5"
    embed_model: str = "voyageai/voyage-4"
    embed_dims: int = 1024
    llm_concurrency: int = 8
    daily_budget_usd: float = 25.0

    # --- FalkorDB (also used as the Redis for vault / counters) ---
    falkordb_host: str = "127.0.0.1"  # not "localhost": avoids a ~2 s IPv6 fallback per connection on Windows
    falkordb_port: int = 6379
    falkordb_password: str | None = None

    # --- App ---
    env: str = "dev"
    kms_key_alias: str = "alias/financebrain-vault"
    vault_kms: bool = False  # prod: wrap the vault data key with KMS; dev: local key in secrets/
    pii_hmac_key: str | None = None  # prod: from SSM /financebrain/pii_hmac_key; dev: secrets/pii_hmac.key
    spacy_model: str = "en_core_web_md"
    aws_region: str = "us-east-1"

    # --- Agents / API / MCP ---
    session_secret: str | None = None  # HMAC key for signed auth tokens (prod: SSM /financebrain/session_secret)
    demo_mcp_token: str | None = None  # read-only public MCP token (prod: SSM /financebrain/demo_mcp_token)
    demo_today: str = "2026-10-15"  # "today" inside the synthetic world
    agent_max_steps: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
