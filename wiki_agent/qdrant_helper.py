"""Qdrant wrapper for the `wiki_knowledge` collection.

Reuses the existing Qdrant instance (already running for the mem0 layer).
Keeps a dedicated collection so structured multi-source knowledge stays
cleanly separated from the `mem0_mcp_selfhosted` facts.

Payload schema (all sources write the same shape):
    topic      str        e.g. "OCS/charging"
    content    str        the fact itself
    source     str        "conversation" | "file" | "whatsapp"
    tags       list[str]
    confidence float      0.0–1.0
    created_at str        ISO 8601 UTC
    updated_at str        ISO 8601 UTC
    ref        str|None   source reference (session id / file path / thread)
"""
from __future__ import annotations
import httpx
from typing import List, Optional

from . import config

_URL = config.QDRANT_URL
_COLLECTION = config.WIKI_COLLECTION
_DIMS = config.EMBED_DIMS


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.QDRANT_API_KEY:
        h["api-key"] = config.QDRANT_API_KEY
    return h


def ensure_wiki_collection() -> None:
    """Create the `wiki_knowledge` collection if it does not exist.

    Idempotent — safe to call on every startup. Does not touch any other
    collection (mem0_mcp_selfhosted, chat_summaries stay untouched).
    """
    r = httpx.get(f"{_URL}/collections/{_COLLECTION}", headers=_headers(), timeout=30)
    if r.status_code == 200:
        return
    body = {"vectors": {"size": _DIMS, "distance": "Cosine"}}
    r = httpx.put(
        f"{_URL}/collections/{_COLLECTION}",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()

    # Payload index on `topic` so list_wiki_topics / topic filter stay fast.
    for field, schema in (("topic", "keyword"), ("source", "keyword")):
        httpx.put(
            f"{_URL}/collections/{_COLLECTION}/index?wait=true",
            json={"field_name": field, "field_schema": schema},
            headers=_headers(),
            timeout=30,
        )


def upsert(point_id: str, vector: List[float], payload: dict) -> str:
    """Insert or update a single knowledge point. Returns the point_id.

    A deterministic point_id (uuid5 of content) makes re-ingesting the same
    fact an idempotent overwrite — no LLM-side dedup needed.
    """
    body = {"points": [{"id": point_id, "vector": vector, "payload": payload}]}
    r = httpx.put(
        f"{_URL}/collections/{_COLLECTION}/points?wait=true",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return point_id


def search(
    vector: List[float],
    limit: int = 5,
    topic: Optional[str] = None,
    source: Optional[str] = None,
) -> List[dict]:
    """Semantic search. Optional exact-match filters on topic/source.

    Returns list of {id, score, payload}.
    """
    body: dict = {"vector": vector, "limit": limit, "with_payload": True}
    must = []
    if topic:
        must.append({"key": "topic", "match": {"value": topic}})
    if source:
        must.append({"key": "source", "match": {"value": source}})
    if must:
        body["filter"] = {"must": must}
    r = httpx.post(
        f"{_URL}/collections/{_COLLECTION}/points/search",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["result"]


def scroll_topics(page_size: int = 256) -> List[dict]:
    """Scroll all points (payload only, no vectors) to aggregate topics.

    Used by list_wiki_topics. Deterministic aggregation happens in code, not
    via an LLM.
    """
    points: List[dict] = []
    offset = None
    while True:
        body: dict = {
            "limit": page_size,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        r = httpx.post(
            f"{_URL}/collections/{_COLLECTION}/points/scroll",
            json=body,
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        result = r.json()["result"]
        points.extend(result.get("points", []))
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return points


def delete(point_id: str) -> None:
    body = {"points": [point_id]}
    r = httpx.post(
        f"{_URL}/collections/{_COLLECTION}/points/delete?wait=true",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
