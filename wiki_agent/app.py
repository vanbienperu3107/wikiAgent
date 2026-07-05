"""wikiAgent REST API — ingestion + query endpoints (FastAPI).

Endpoints:
    POST /ingest/conversation   Hướng B — AI conversation → extract → store
    POST /ingest/file           Hướng A — Markdown file → store (confidence=1.0)
    GET  /wiki/search           semantic search (backs search_wiki)
    GET  /wiki/topics           topic list (backs list_wiki_topics)
    GET  /health

Auth: every non-health endpoint needs `Authorization: Bearer <WIKI_AUTH_TOKEN>`.
"""
from __future__ import annotations
import uuid
import datetime
from typing import Optional, List

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from . import config, knowledge_extractor, embeddings, qdrant_helper, wiki_search

app = FastAPI(
    title="wikiAgent — Wiki Knowledge Layer",
    description="Multi-source structured knowledge for the Personal AI Knowledge System.",
    version="0.1.0",
)

# Deterministic namespace for file-source ids (uuid5 of path → auto-dedup).
_FILE_NS = uuid.UUID("00000000-0000-0000-0000-000000000042")


def check(token: Optional[str]) -> None:
    if not config.WIKI_AUTH_TOKEN:
        raise HTTPException(503, "WIKI_AUTH_TOKEN not configured")
    if not token or token != f"Bearer {config.WIKI_AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")


class ConversationIn(BaseModel):
    transcript: List[dict]
    session_id: Optional[str] = None
    backend: Optional[str] = None  # 'anthropic' | 'openai'


class FileIn(BaseModel):
    path: str
    content: str
    topic: Optional[str] = None
    tags: List[str] = []


@app.post("/ingest/conversation")
def ingest_conversation(body: ConversationIn, authorization: str = Header(None)):
    """Hướng B: extract structured facts from a conversation and store them."""
    check(authorization)
    n = knowledge_extractor.extract_and_store(
        body.transcript, session_id=body.session_id, backend=body.backend
    )
    return {"stored": n, "source": "conversation", "session_id": body.session_id}


@app.post("/ingest/file")
def ingest_file(body: FileIn, authorization: str = Header(None)):
    """Hướng A (Phase 2 ready): index a Markdown file as a confidence=1.0 fact.

    Deterministic uuid5(path) id → re-syncing the same file overwrites in place.
    """
    check(authorization)
    if knowledge_extractor.is_sensitive(body.content):
        raise HTTPException(422, "File content flagged by privacy filter")
    qdrant_helper.ensure_wiki_collection()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    topic = body.topic or body.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    payload = {
        "topic": topic,
        "content": body.content,
        "source": "file",
        "tags": body.tags,
        "confidence": 1.0,
        "created_at": now,
        "updated_at": now,
        "ref": body.path,
    }
    point_id = str(uuid.uuid5(_FILE_NS, body.path))
    vector = embeddings.embed(body.content)
    qdrant_helper.upsert(point_id, vector, payload)
    return {"stored": 1, "source": "file", "id": point_id}


@app.get("/wiki/search")
def wiki_search_endpoint(
    q: str = Query(..., description="Search query"),
    topic: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 5,
    authorization: str = Header(None),
):
    check(authorization)
    return wiki_search.search_wiki(q, topic=topic, source=source, limit=limit)


@app.get("/wiki/topics")
def wiki_topics_endpoint(authorization: str = Header(None)):
    check(authorization)
    return wiki_search.list_wiki_topics()


@app.get("/health")
def health():
    return {"status": "ok", "collection": config.WIKI_COLLECTION}
