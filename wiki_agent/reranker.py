"""Phase-4 reranker — optional cross-encoder reordering of search results.

Sits after retrieval (wiki_search / rag.hybrid_search): takes the candidate
pool and reorders it by query relevance using Cohere's rerank endpoint, which
scores each (query, document) pair jointly rather than via independent vectors.

Fully optional and fail-safe: with no COHERE_API_KEY, or on any HTTP error,
the original ordering is returned unchanged so retrieval never regresses.

Env (read locally, not via config):
    COHERE_API_KEY      — enables the stage; absent -> passthrough
    RERANK_MODEL        — default "rerank-english-v3.0"
    COHERE_RERANK_URL   — default "https://api.cohere.com/v1/rerank"
"""
from __future__ import annotations
import os
from typing import List, Optional

import httpx

_DEFAULT_MODEL = "rerank-english-v3.0"
_DEFAULT_URL = "https://api.cohere.com/v1/rerank"


def rerank(
    query: str,
    results: List[dict],
    top_n: Optional[int] = None,
) -> List[dict]:
    """Reorder `results` by query relevance via Cohere rerank.

    `results` are search_wiki-shaped dicts carrying a `content` field. When a
    Cohere key is configured each result gains a `rerank_score`; the list is
    returned best-first and capped at `top_n` (or full length when None).

    Fail-safe: no key -> results unchanged; empty input -> []; any HTTP error
    -> original order preserved. This function never raises into the caller.
    """
    if not results:
        return []

    api_key = os.environ.get("COHERE_API_KEY")
    if not api_key:
        # Passthrough — reranking is opt-in; honour top_n if requested.
        return results[:top_n] if top_n is not None else results

    model = os.environ.get("RERANK_MODEL", _DEFAULT_MODEL)
    url = os.environ.get("COHERE_RERANK_URL", _DEFAULT_URL)

    payload = {
        "model": model,
        "query": query,
        "documents": [r.get("content", "") for r in results],
    }
    if top_n is not None:
        payload["top_n"] = top_n

    try:
        r = httpx.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        r.raise_for_status()
        ranked = r.json().get("results", [])
    except Exception:
        # Network/HTTP/parse failure — degrade to the retrieval order.
        return results[:top_n] if top_n is not None else results

    reordered: List[dict] = []
    for item in ranked:
        idx = item.get("index")
        if idx is None or not (0 <= idx < len(results)):
            continue
        hit = dict(results[idx])
        hit["rerank_score"] = item.get("relevance_score")
        reordered.append(hit)

    # Defensive: if the response was malformed and yielded nothing usable,
    # fall back rather than silently dropping every result.
    if not reordered:
        return results[:top_n] if top_n is not None else results

    return reordered[:top_n] if top_n is not None else reordered
