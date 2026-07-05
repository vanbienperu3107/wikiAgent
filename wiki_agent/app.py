"""wikiAgent REST API — ingestion + query endpoints (FastAPI).

Endpoints:
    POST /ingest/conversation   Hướng B — AI conversation → extract → store
    POST /ingest/file           Hướng A — Markdown file → store (confidence=1.0)
    POST /ingest/whatsapp       Phase 3 — WhatsApp thread → classify → extract → store
    GET  /wiki/search           semantic search (?hybrid RAG 2.0, ?rerank Cohere); logged
    POST /wiki/fact             manual add (source="manual", conf=1.0)
    DELETE /wiki/fact/{id}      manual delete
    GET  /wiki/query-stats      search-query telemetry (for RAG tuning)
    GET  /wiki/topics           topic list (backs list_wiki_topics)
    GET  /health

Auth: every non-health endpoint needs `Authorization: Bearer <WIKI_AUTH_TOKEN>`.
"""
from __future__ import annotations
import uuid
import datetime
from typing import Optional, List

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import (
    config, knowledge_extractor, embeddings, qdrant_helper, wiki_search,
    whatsapp, rag, fact_crud, reranker, query_log, ratelimit,
)

app = FastAPI(
    title="wikiAgent — Wiki Knowledge Layer",
    description="Multi-source structured knowledge for the Personal AI Knowledge System.",
    version="0.3.1",
)

# CORS so the static dashboard (and other browser clients) can call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)

# Deterministic namespace for file-source ids (uuid5 of path → auto-dedup).
_FILE_NS = uuid.UUID("00000000-0000-0000-0000-000000000042")


def check(token: Optional[str]) -> None:
    if not config.WIKI_AUTH_TOKEN:
        raise HTTPException(503, "WIKI_AUTH_TOKEN not configured")
    if not token or token != f"Bearer {config.WIKI_AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    # One global bucket (single shared token); tune via WIKI_RATE_LIMIT/WINDOW.
    if not ratelimit.check_rate("rest", config.RATE_LIMIT, config.RATE_WINDOW):
        raise HTTPException(429, "Rate limit exceeded")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Return JSON on unexpected errors instead of leaking a stack trace.

    Uses an exception handler (not BaseHTTPMiddleware) to avoid the Starlette
    Content-Length bug with multi-byte UTF-8 bodies.
    """
    print(f"Unhandled error on {request.method} {request.url.path}: {exc}")
    return JSONResponse({"error": "internal_error"}, status_code=500)


class ConversationIn(BaseModel):
    transcript: List[dict]
    session_id: Optional[str] = None
    backend: Optional[str] = None  # 'anthropic' | 'openai'


class FileIn(BaseModel):
    path: str
    content: str
    topic: Optional[str] = None
    tags: List[str] = []


class WhatsAppIn(BaseModel):
    messages: List[dict]           # buffered messages for one thread
    thread_id: Optional[str] = None  # remoteJid
    sender: Optional[str] = None     # for contact blacklist
    backend: Optional[str] = None    # extractor backend override


class FactIn(BaseModel):
    topic: str
    content: str
    tags: List[str] = []
    confidence: float = 1.0
    source: str = "manual"
    ref: Optional[str] = None


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


@app.post("/ingest/whatsapp")
def ingest_whatsapp(body: WhatsAppIn, authorization: str = Header(None)):
    """Phase 3: WhatsApp thread → blacklist/privacy → classify → extract → store.

    The heavy Haiku extraction runs only when the cheap classifier says keep=true.
    """
    check(authorization)
    result = whatsapp.process_thread(
        body.messages,
        thread_id=body.thread_id,
        sender=body.sender,
        backend=body.backend,
    )
    return {"source": "whatsapp", **result}


@app.get("/wiki/search")
def wiki_search_endpoint(
    q: str = Query(..., description="Search query"),
    topic: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 5,
    hybrid: bool = Query(False, description="RAG 2.0: hybrid dense+BM25 with RRF"),
    beta: float = Query(0.0, description="Recency weight (0=off) for time-aware re-rank"),
    rerank: bool = Query(False, description="Apply Cohere reranker (needs COHERE_API_KEY)"),
    authorization: str = Header(None),
):
    check(authorization)
    if hybrid:
        # widen the pool a bit when reranking so the reranker has candidates to sort
        pool = max(limit, 20) if rerank else limit
        results = rag.hybrid_search(q, limit=pool, topic=topic, source=source, beta=beta)
    else:
        results = wiki_search.search_wiki(q, topic=topic, source=source, limit=limit)
    if rerank:
        results = reranker.rerank(q, results, top_n=limit)
    query_log.log_query(
        q, len(results),
        mode=("hybrid" if hybrid else "semantic"), topic=topic,
        top_ids=[r.get("id") for r in results[:5]],
    )
    return results


@app.post("/wiki/fact")
def add_fact_endpoint(body: FactIn, authorization: str = Header(None)):
    """Manually add a knowledge fact (source defaults to 'manual', confidence 1.0)."""
    check(authorization)
    if knowledge_extractor.is_sensitive(body.content):
        raise HTTPException(422, "Fact content flagged by privacy filter")
    fid = fact_crud.add_fact(
        body.topic, body.content, tags=body.tags,
        confidence=body.confidence, source=body.source, ref=body.ref,
    )
    return {"stored": 1, "id": fid, "source": body.source}


@app.delete("/wiki/fact/{point_id}")
def delete_fact_endpoint(point_id: str, authorization: str = Header(None)):
    check(authorization)
    fact_crud.delete_fact(point_id)
    return {"deleted": point_id}


@app.get("/wiki/query-stats")
def query_stats_endpoint(authorization: str = Header(None)):
    """Aggregated search-query telemetry — the data behind 'measure before RAG tuning'."""
    check(authorization)
    return query_log.stats()


@app.get("/wiki/topics")
def wiki_topics_endpoint(authorization: str = Header(None)):
    check(authorization)
    return wiki_search.list_wiki_topics()


@app.get("/health")
def health():
    return {"status": "ok", "collection": config.WIKI_COLLECTION}
