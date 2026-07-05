"""fact_crud.py — manual knowledge management for `wiki_knowledge`.

Human-curated CRUD over the same collection the extractor writes to. Facts
added here use source="manual" but share the exact payload schema (topic,
content, source, tags, confidence, created_at, updated_at, ref) so they are
indistinguishable to search / list.

Deterministic ids (uuid5 of normalized content) make re-adding identical
content idempotent — an overwrite, never a duplicate.

The REST/MCP endpoints are wired elsewhere; this module only exposes the
storage-level operations. Any Qdrant call not already in qdrant_helper is kept
self-contained here (retrieve-by-id, set-payload) so qdrant_helper stays lean.
"""
from __future__ import annotations
import datetime
from typing import List, Optional

import httpx

from . import config, embeddings, knowledge_extractor, qdrant_helper

_URL = config.QDRANT_URL
_COLLECTION = config.WIKI_COLLECTION


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.QDRANT_API_KEY:
        h["api-key"] = config.QDRANT_API_KEY
    return h


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def add_fact(
    topic: str,
    content: str,
    tags: Optional[List[str]] = None,
    confidence: float = 1.0,
    source: str = "manual",
    ref: Optional[str] = None,
) -> str:
    """Add (or idempotently overwrite) a single manual fact. Returns point id.

    Re-adding identical content yields the same deterministic id, so the upsert
    overwrites rather than duplicating.
    """
    topic = (topic or "").strip()
    content = (content or "").strip()
    if not topic:
        raise ValueError("topic must not be empty")
    if not content:
        raise ValueError("content must not be empty")

    qdrant_helper.ensure_wiki_collection()
    vector = embeddings.embed(content)
    fact = {
        "topic": topic,
        "content": content,
        "tags": list(tags) if tags else [],
        "confidence": confidence,
    }
    payload = knowledge_extractor.build_payload(fact, source, ref)
    point_id = knowledge_extractor._point_id(content)
    qdrant_helper.upsert(point_id, vector, payload)
    return point_id


def delete_fact(point_id: str) -> None:
    """Delete a single fact by its point id."""
    qdrant_helper.delete(point_id)


def _retrieve_payload(point_id: str) -> Optional[dict]:
    """Fetch a point's payload by id (self-contained, no vector). None if absent."""
    r = httpx.get(
        f"{_URL}/collections/{_COLLECTION}/points/{point_id}",
        headers=_headers(),
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    result = r.json().get("result")
    if not result:
        return None
    return result.get("payload") or {}


def _set_payload(point_id: str, payload: dict) -> None:
    """Overwrite a point's payload in place (metadata-only update)."""
    body = {"payload": payload, "points": [point_id]}
    r = httpx.post(
        f"{_URL}/collections/{_COLLECTION}/points/payload?wait=true",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()


def update_fact(
    point_id: str,
    content: Optional[str] = None,
    topic: Optional[str] = None,
    tags: Optional[List[str]] = None,
    confidence: Optional[float] = None,
) -> str:
    """Update an existing fact. Returns the resulting point id.

    Two cases:
      - content changes: the id is derived from content, so the old id can no
        longer address the new fact. We re-add via add_fact (fresh deterministic
        id), delete the old point, and return the new id.
      - metadata only (topic/tags/confidence): retrieve the existing payload,
        merge the provided fields, bump updated_at, and set-payload in place.
        The id is unchanged.
    """
    existing = _retrieve_payload(point_id)
    if existing is None:
        raise KeyError(f"fact not found: {point_id}")

    new_content = content.strip() if content is not None else None
    if new_content:
        new_id = add_fact(
            topic=(topic or existing.get("topic") or "").strip(),
            content=new_content,
            tags=tags if tags is not None else existing.get("tags"),
            confidence=confidence if confidence is not None else existing.get("confidence", 1.0),
            source=existing.get("source", "manual"),
            ref=existing.get("ref"),
        )
        if new_id != point_id:
            delete_fact(point_id)
        return new_id

    payload = dict(existing)
    if topic is not None:
        payload["topic"] = topic.strip()
    if tags is not None:
        payload["tags"] = list(tags)
    if confidence is not None:
        payload["confidence"] = confidence
    payload["updated_at"] = _now()
    _set_payload(point_id, payload)
    return point_id
