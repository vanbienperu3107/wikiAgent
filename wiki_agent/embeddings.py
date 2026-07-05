"""OpenAI embeddings helper (text-embedding-3-small, 1536 dims).

Same model as the agentMem0 mem0 layer so vectors are comparable and no
re-indexing is needed if collections are ever merged.
"""
from __future__ import annotations
import httpx
from typing import List

from . import config

OPENAI_URL = "https://api.openai.com/v1/embeddings"

# OpenAI caps the number of inputs per embeddings request.
_MAX_BATCH = 512


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
    """Embed multiple texts (cheaper than N calls).

    Inputs are chunked into batches of at most ``_MAX_BATCH`` (OpenAI caps the
    request size). Within each response, items are reordered by their reported
    ``index`` so vectors always align with their input positions, and chunk
    results are concatenated in input order.
    """
    out: List[List[float]] = []
    for start in range(0, len(texts), _MAX_BATCH):
        chunk = texts[start:start + _MAX_BATCH]
        payload = {"input": chunk, "model": config.EMBED_MODEL}
        r = httpx.post(OPENAI_URL, json=payload, headers=_headers(), timeout=60)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        out.extend(d["embedding"] for d in data)
    return out
