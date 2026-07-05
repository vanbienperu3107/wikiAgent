"""OpenAI embeddings helper (text-embedding-3-small, 1536 dims).

Same model as the agentMem0 mem0 layer so vectors are comparable and no
re-indexing is needed if collections are ever merged.
"""
from __future__ import annotations
import httpx
from typing import List

from . import config

OPENAI_URL = "https://api.openai.com/v1/embeddings"


def _headers() -> dict:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    return {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


def embed(text: str) -> List[float]:
    """Return the embedding vector for a single text."""
    payload = {"input": text, "model": config.EMBED_MODEL}
    r = httpx.post(OPENAI_URL, json=payload, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed multiple texts in one API call (cheaper than N calls)."""
    payload = {"input": texts, "model": config.EMBED_MODEL}
    r = httpx.post(OPENAI_URL, json=payload, headers=_headers(), timeout=60)
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]
