"""Central configuration — all env vars in one place.

Design principle (Roadmap 3.0): no new services. wikiAgent reuses the Qdrant
instance and OpenAI/Anthropic keys already provisioned for agentMem0.
"""
from __future__ import annotations
import os

# ----- Qdrant (reused instance) -----
QDRANT_URL = os.environ.get("QDRANT_INTERNAL_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")

# The single multi-source knowledge collection (Phase 1–3 all write here).
WIKI_COLLECTION = os.environ.get("WIKI_COLLECTION", "wiki_knowledge")

# ----- Embeddings (OpenAI) -----
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
EMBED_MODEL = os.environ.get("WIKI_EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = int(os.environ.get("WIKI_EMBED_DIMS", "1536"))

# ----- Extractor LLM (Claude Haiku, OpenAI fallback) -----
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("WIKI_EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
OPENAI_EXTRACTOR_MODEL = os.environ.get("WIKI_OPENAI_EXTRACTOR_MODEL", "gpt-4o-mini")

# ----- API auth -----
WIKI_AUTH_TOKEN = os.environ.get("WIKI_AUTH_TOKEN", "")
MCP_BEARER_TOKEN = os.environ.get("WIKI_MCP_BEARER_TOKEN", "")

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
MIN_CONFIDENCE = float(os.environ.get("WIKI_MIN_CONFIDENCE", "0.5"))
