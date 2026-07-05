"""MCP HTTP server — exposes the 2 Phase 1 wiki tools to any MCP client.

Transport: Streamable HTTP (MCP spec 2025-03-26), same shape as the agentMem0
mcp-http-server so it can sit behind the same Caddy/OAuth front door.

Tools:
    search_wiki(query, topic?, source?, limit=5)
    list_wiki_topics()

Auth: clients must send `Authorization: Bearer <WIKI_MCP_BEARER_TOKEN>`.
"""
from __future__ import annotations
import json

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import config, wiki_search, rag, fact_crud, query_log

app = FastAPI(
    title="wikiAgent MCP HTTP",
    description="Remote MCP server exposing the wiki_knowledge layer.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)

TOOLS = [
    {
        "name": "search_wiki",
        "description": (
            "Semantic search over the personal wiki knowledge base "
            "(facts distilled from conversations, files, and chat). "
            "Use to recall a technical fact, config value, or decision from the past."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "topic": {"type": "string", "description": "Optional exact topic filter, e.g. 'OCS/charging'"},
                "source": {"type": "string", "description": "Optional: conversation | file | whatsapp | manual"},
                "limit": {"type": "integer", "default": 5},
                "hybrid": {"type": "boolean", "default": False, "description": "RAG 2.0: hybrid dense+BM25 (RRF) reranking"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_wiki_fact",
        "description": (
            "Manually add a fact to the wiki knowledge base (e.g. a runbook step you "
            "want remembered). Stored with source='manual' and high confidence. "
            "Re-adding identical content is idempotent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Hierarchical, e.g. 'deploy/ci'"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "default": 1.0},
            },
            "required": ["topic", "content"],
        },
    },
    {
        "name": "delete_wiki_fact",
        "description": "Delete a wiki fact by its id (from search_wiki results).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_wiki_topics",
        "description": (
            "List all topics in the wiki knowledge base with fact counts and "
            "which sources contributed. Use to discover what knowledge exists."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _check_auth(request: Request) -> bool:
    if not config.MCP_BEARER_TOKEN:
        return False
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    return token == config.MCP_BEARER_TOKEN


def exec_tool(name: str, args: dict):
    if name == "search_wiki":
        limit = args.get("limit", 5)
        hybrid = bool(args.get("hybrid"))
        if hybrid:
            results = rag.hybrid_search(
                args["query"], topic=args.get("topic"),
                source=args.get("source"), limit=limit,
            )
        else:
            results = wiki_search.search_wiki(
                args["query"], topic=args.get("topic"),
                source=args.get("source"), limit=limit,
            )
        query_log.log_query(
            args["query"], len(results),
            mode=("hybrid" if hybrid else "semantic"), topic=args.get("topic"),
            top_ids=[r.get("id") for r in results[:5]],
        )
        return results
    if name == "list_wiki_topics":
        return wiki_search.list_wiki_topics()
    if name == "add_wiki_fact":
        fid = fact_crud.add_fact(
            args["topic"], args["content"],
            tags=args.get("tags", []), confidence=args.get("confidence", 1.0),
        )
        return {"stored": 1, "id": fid, "source": "manual"}
    if name == "delete_wiki_fact":
        fact_crud.delete_fact(args["id"])
        return {"deleted": args["id"]}
    raise ValueError(f"Unknown tool: {name}")


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "wikiAgent", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return Response(status_code=204)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = body.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            result = exec_tool(name, args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ],
                    "isError": False,
                },
            }
        except Exception as e:  # noqa: BLE001 — surface as MCP error content
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
