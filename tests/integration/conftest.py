"""Fixtures for the end-to-end integration suite.

Two things happen here, and *only* these two:

  1. The OpenAI embedding network call is replaced with a deterministic local
     vectorizer (``fake_embed``). Nothing else is stubbed — Qdrant is real.
  2. A live Qdrant is located (env ``WIKI_TEST_QDRANT_URL``, default
     ``http://localhost:6333``). If it cannot be reached the whole module is
     skipped, so the suite is a no-op on machines without a vector DB.

The fake embedder is a bag-of-tokens vector hashed into ``config.EMBED_DIMS``
(1536) dims and L2-normalized. It is stable (same text -> same vector) and
roughly captures similarity (texts sharing tokens have high cosine), which is
all rag / consolidation need to behave sensibly.
"""
from __future__ import annotations

import hashlib
import math
import os
import re

import httpx
import pytest

from wiki_agent import config, embeddings, fact_crud, qdrant_helper


# --------------------------------------------------------------------------
# Deterministic local embedder (stubs OpenAI only)
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _dim(token: str, dims: int) -> int:
    return int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dims


def fake_embed(text: str) -> list[float]:
    """Stable bag-of-tokens embedding of length ``config.EMBED_DIMS``.

    Each token bumps two hashed dimensions (cuts collisions); the vector is
    L2-normalized so Qdrant's cosine distance is well-defined. Empty/blank text
    still yields a deterministic non-zero vector (Qdrant rejects zero vectors
    under cosine).
    """
    dims = config.EMBED_DIMS
    vec = [0.0] * dims
    toks = _tokens(text)
    if not toks:
        vec[_dim(text or "\x00", dims)] = 1.0
        return vec
    for t in toks:
        vec[_dim(t, dims)] += 1.0
        vec[_dim(t + "#2", dims)] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0.0:
        vec = [v / norm for v in vec]
    return vec


def fake_embed_batch(texts: list[str]) -> list[list[float]]:
    return [fake_embed(t) for t in texts]


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """Replace the OpenAI-backed embedders everywhere they are used.

    Every module reaches the embedder via the ``embeddings`` module attribute
    (``embeddings.embed`` / ``embeddings.embed_batch``), so patching the module
    object covers knowledge_extractor, fact_crud and rag at once.
    """
    monkeypatch.setattr(embeddings, "embed", fake_embed)
    monkeypatch.setattr(embeddings, "embed_batch", fake_embed_batch)
    yield


# --------------------------------------------------------------------------
# Live Qdrant + fresh throwaway collection
# --------------------------------------------------------------------------

TEST_COLLECTION = "wiki_test_e2e"


def _reachable(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/collections", timeout=2.0)
        return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def _drop_collection(url: str, collection: str) -> None:
    try:
        httpx.delete(f"{url}/collections/{collection}", timeout=30)
    except (httpx.HTTPError, OSError):
        pass


@pytest.fixture(scope="session", autouse=True)
def live_qdrant():
    """Point every module at a live Qdrant + a fresh throwaway collection.

    Skips the entire module when no Qdrant answers. Because several modules
    snapshot ``config.QDRANT_URL`` / ``config.WIKI_COLLECTION`` into module
    globals at import time, we rewrite both the config values and those
    snapshots so the whole stack talks to the test collection.
    """
    url = os.environ.get("WIKI_TEST_QDRANT_URL", "http://localhost:6333")
    if not _reachable(url):
        pytest.skip("no live Qdrant")

    # Redirect config + the import-time snapshots to the test target.
    config.QDRANT_URL = url
    config.WIKI_COLLECTION = TEST_COLLECTION
    qdrant_helper._URL = url
    qdrant_helper._COLLECTION = TEST_COLLECTION
    fact_crud._URL = url
    fact_crud._COLLECTION = TEST_COLLECTION

    # Fresh collection: drop any leftover, then create anew.
    _drop_collection(url, TEST_COLLECTION)
    qdrant_helper.ensure_wiki_collection()

    yield url

    _drop_collection(url, TEST_COLLECTION)
