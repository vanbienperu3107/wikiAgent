"""RAG 2.0 — hybrid retrieval over wiki_knowledge.

Layers a dependency-free keyword signal on top of the existing dense vector
search and fuses the two rankings, so lexically exact matches (product codes,
IDs) surface even when the embedding under-weights them.

Pipeline:
    1. Dense candidate pool   embeddings.embed + qdrant_helper.search
    2. BM25 keyword score     self-contained, computed within the pool
    3. Reciprocal Rank Fusion vector-rank ⊕ keyword-rank  (k=60)  -> rrf_score
    4. Optional recency boost final = alpha*rrf + beta*exp(-age/halflife)

All math uses only the stdlib (math, datetime, re, collections).
"""
from __future__ import annotations
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

from . import embeddings, qdrant_helper

# RRF constant — the canonical value from the original TREC formulation.
_RRF_K = 60

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: Optional[str]) -> List[str]:
    """Lowercase and split on non-alphanumerics. Blank/None -> []."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _bm25_scores(query: str, docs: List[List[str]]) -> List[float]:
    """BM25 score of `query` against each pre-tokenized doc in the pool.

    k1=1.5, b=0.75. df/idf are computed within the candidate pool only, so
    this is a re-ranking signal over `docs`, not a corpus-wide statistic.
    """
    k1, b = 1.5, 0.75
    n = len(docs)
    if n == 0:
        return []
    lengths = [len(d) for d in docs]
    avgdl = (sum(lengths) / n) or 1.0

    q_terms = set(_tokenize(query))
    if not q_terms:
        return [0.0] * n

    # Document frequency within the pool.
    df: Counter = Counter()
    for terms in q_terms:
        df[terms] = sum(1 for d in docs if terms in d)

    idf = {
        t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
        for t in q_terms
    }

    scores: List[float] = []
    for terms, dl in zip(docs, lengths):
        tf = Counter(terms)
        s = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if not f:
                continue
            denom = f + k1 * (1 - b + b * dl / avgdl)
            s += idf[t] * (f * (k1 + 1)) / denom
        scores.append(s)
    return scores


def _rank_map(order: List[int]) -> dict:
    """Map candidate index -> 1-based rank given an ordering of indices."""
    return {idx: rank for rank, idx in enumerate(order, start=1)}


def _recency(updated_at: Optional[str], halflife_days: float) -> float:
    """exp(-age_days/halflife) from an ISO 8601 timestamp. Bad/blank -> 0.0."""
    if not updated_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    hl = halflife_days if halflife_days > 0 else 1.0
    return math.exp(-age_days / hl)


def hybrid_search(
    query: str,
    limit: int = 5,
    topic: Optional[str] = None,
    source: Optional[str] = None,
    alpha: float = 1.0,
    beta: float = 0.0,
    halflife_days: float = 30.0,
    candidate_k: int = 30,
) -> List[dict]:
    """Hybrid dense + BM25 retrieval with RRF fusion and optional recency.

    Returns search_wiki-shaped dicts (id, score, topic, content, source, tags,
    confidence, updated_at, ref) plus `rrf_score`, ranked best-first, capped at
    `limit`. `score` is the original dense vector score; `rrf_score` is the
    fused rank signal.
    """
    qvec = embeddings.embed(query)
    hits = qdrant_helper.search(qvec, limit=candidate_k, topic=topic, source=source)
    if not hits:
        return []

    # (a) Dense ranking is the order Qdrant already returns (score desc).
    vector_rank = _rank_map(list(range(len(hits))))

    # (b) BM25 keyword ranking over the same pool.
    docs = [_tokenize(h.get("payload", {}).get("content")) for h in hits]
    kw_scores = _bm25_scores(query, docs)
    kw_order = sorted(range(len(hits)), key=lambda i: kw_scores[i], reverse=True)
    keyword_rank = _rank_map(kw_order)

    # (c) Reciprocal Rank Fusion.
    rrf = [
        1.0 / (_RRF_K + vector_rank[i]) + 1.0 / (_RRF_K + keyword_rank[i])
        for i in range(len(hits))
    ]

    # (d) Optional time-aware re-rank.
    if beta > 0:
        max_rrf = max(rrf) or 1.0
        recency = [
            _recency(h.get("payload", {}).get("updated_at"), halflife_days)
            for h in hits
        ]
        final = [
            alpha * (rrf[i] / max_rrf) + beta * recency[i]
            for i in range(len(hits))
        ]
    else:
        final = rrf

    order = sorted(range(len(hits)), key=lambda i: final[i], reverse=True)

    results: List[dict] = []
    for i in order[:limit]:
        h = hits[i]
        p = h.get("payload", {})
        results.append(
            {
                "id": h["id"],
                "score": h.get("score"),
                "topic": p.get("topic"),
                "content": p.get("content"),
                "source": p.get("source"),
                "tags": p.get("tags", []),
                "confidence": p.get("confidence"),
                "updated_at": p.get("updated_at"),
                "ref": p.get("ref"),
                "rrf_score": rrf[i],
            }
        )
    return results
