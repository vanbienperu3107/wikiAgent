"""Query layer over wiki_knowledge — shared by the REST API and MCP server.

Two operations back the Phase 1 MCP tools:
    search_wiki(query, topic?, limit)   — semantic search
    list_wiki_topics()                  — deterministic topic aggregation
"""
from __future__ import annotations
from collections import defaultdict
from typing import List, Optional

from . import embeddings, qdrant_helper


def search_wiki(
    query: str,
    topic: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 5,
) -> List[dict]:
    """Semantic search over wiki_knowledge. Returns ranked facts."""
    qvec = embeddings.embed(query)
    hits = qdrant_helper.search(qvec, limit=limit, topic=topic, source=source)
    results = []
    for h in hits:
        p = h.get("payload", {})
        results.append(
            {
                "id": h["id"],
                "score": h["score"],
                "topic": p.get("topic"),
                "content": p.get("content"),
                "source": p.get("source"),
                "tags": p.get("tags", []),
                "confidence": p.get("confidence"),
                "updated_at": p.get("updated_at"),
                "ref": p.get("ref"),
            }
        )
    return results


def list_wiki_topics() -> List[dict]:
    """Aggregate stored facts by topic. Deterministic — no LLM.

    Returns [{topic, count, sources}] sorted by count desc.
    """
    points = qdrant_helper.scroll_topics()
    counts: dict = defaultdict(int)
    sources: dict = defaultdict(set)
    for pt in points:
        payload = pt.get("payload", {})
        topic = payload.get("topic")
        if not topic:
            continue
        counts[topic] += 1
        src = payload.get("source")
        if src:
            sources[topic].add(src)
    topics = [
        {"topic": t, "count": c, "sources": sorted(sources[t])}
        for t, c in counts.items()
    ]
    topics.sort(key=lambda x: x["count"], reverse=True)
    return topics
