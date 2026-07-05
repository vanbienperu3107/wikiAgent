"""Central configuration — all env vars in one place.

Design principle (Roadmap 3.0): no new services. wikiAgent reuses the Qdrant
instance and OpenAI/Anthropic keys already provisioned for agentMem0.
"""
from __future__ import annotations
import os


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to `default` on missing/malformed value
    so a bad env var can't crash the service at import time."""
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to `default` on missing/malformed value."""
    try:
        return float(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


# ----- Qdrant (reused instance) -----
QDRANT_URL = os.environ.get("QDRANT_INTERNAL_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")

# The single multi-source knowledge collection (Phase 1–3 all write here).
WIKI_COLLECTION = os.environ.get("WIKI_COLLECTION", "wiki_knowledge")

# ----- Embeddings (OpenAI) -----
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
EMBED_MODEL = os.environ.get("WIKI_EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = _env_int("WIKI_EMBED_DIMS", 1536)

# ----- Extractor LLM (Claude Haiku, OpenAI fallback) -----
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("WIKI_EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
OPENAI_EXTRACTOR_MODEL = os.environ.get("WIKI_OPENAI_EXTRACTOR_MODEL", "gpt-4o-mini")

# ----- API auth -----
WIKI_AUTH_TOKEN = os.environ.get("WIKI_AUTH_TOKEN", "")
MCP_BEARER_TOKEN = os.environ.get("WIKI_MCP_BEARER_TOKEN", "")
# Optional least-privilege split: when set, destructive REST ops (DELETE) require
# THIS token instead of the read/write token. Unset → falls back to WIKI_AUTH_TOKEN.
WIKI_ADMIN_TOKEN = os.environ.get("WIKI_ADMIN_TOKEN", "")
# Whether the MCP server exposes the destructive delete_wiki_fact tool. Default
# off so a prompt-injected assistant can't be steered into deleting facts.
WIKI_MCP_ALLOW_DELETE = os.environ.get("WIKI_MCP_ALLOW_DELETE", "").lower() in ("1", "true", "yes")

# ----- Privacy filter -----
# Keywords that, if present in a message, block that message from ever being
# sent to an LLM or stored. Deterministic — no model judgment involved.
SKIP_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get(
        "WIKI_SKIP_KEYWORDS",
        "password,mật khẩu,matkhau,token,secret,api key,apikey,private key,seed phrase,cvv,otp",
    ).split(",")
    if k.strip()
]

# Minimum extractor confidence to persist a fact (drops low-value noise).
MIN_CONFIDENCE = _env_float("WIKI_MIN_CONFIDENCE", 0.5)

# REST rate limit: max requests per window per token (0 = disabled).
RATE_LIMIT = _env_int("WIKI_RATE_LIMIT", 120)
RATE_WINDOW = _env_float("WIKI_RATE_WINDOW", 60)

# ----- WhatsApp source (Phase 3) -----
# Cheap classifier that decides keep=true/false before the expensive Haiku
# extraction runs. Qwen 7B on DeepInfra (OpenAI-compatible API); falls back to
# OpenAI gpt-4o-mini when no DeepInfra key is set.
DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY")
WHATSAPP_CLASSIFIER_MODEL = os.environ.get(
    "WHATSAPP_CLASSIFIER_MODEL", "Qwen/Qwen2.5-7B-Instruct"
)
DEEPINFRA_URL = os.environ.get(
    "DEEPINFRA_URL", "https://api.deepinfra.com/v1/openai/chat/completions"
)

# Contacts (remoteJid / phone / name fragments) whose messages are never
# ingested. Deterministic — checked before any LLM call.
WHATSAPP_CONTACT_BLACKLIST = [
    c.strip().lower()
    for c in os.environ.get("WHATSAPP_CONTACT_BLACKLIST", "").split(",")
    if c.strip()
]
