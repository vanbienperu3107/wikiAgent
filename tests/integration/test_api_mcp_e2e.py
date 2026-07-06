"""End-to-end HTTP test of the REST API and MCP server, against a REAL Qdrant.

Drives the actual FastAPI app objects (wiki_agent.app / wiki_agent.mcp_server)
through Starlette's TestClient — real request parsing, auth, routing, JSON-RPC
shaping — with only the OpenAI embedding call stubbed (conftest's fake_embed).
Complements test_e2e.py, which drives the same live Qdrant but calls the
internal modules directly rather than over HTTP.

Auto-skips (via conftest's live_qdrant fixture) when no Qdrant is reachable.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from wiki_agent import app as appmod, mcp_server, config, ratelimit

AUTH_TOKEN = "e2e-rest-token"
MCP_TOKEN = "e2e-mcp-token"


def setup_function(_):
    ratelimit.reset()


def _auth_config(monkeypatch):
    monkeypatch.setattr(config, "WIKI_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setattr(config, "WIKI_ADMIN_TOKEN", "")
    monkeypatch.setattr(config, "MCP_BEARER_TOKEN", MCP_TOKEN)
    monkeypatch.setattr(config, "RATE_LIMIT", 1000)


# ── REST ─────────────────────────────────────────────────────────────────

def test_rest_add_search_topics_delete_round_trip(monkeypatch):
    _auth_config(monkeypatch)
    c = TestClient(appmod.app)
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    r = c.post(
        "/wiki/fact",
        json={
            "topic": "e2e/api-roundtrip",
            "content": "The staging load balancer is nginx on port 8443",
            "tags": ["e2e"],
        },
        headers=headers,
    )
    assert r.status_code == 200
    fact_id = r.json()["id"]

    r = c.get("/wiki/search", params={"q": "staging load balancer nginx port"}, headers=headers)
    assert r.status_code == 200
    assert any(h["id"] == fact_id for h in r.json())

    r = c.get("/wiki/topics", headers=headers)
    assert r.status_code == 200
    assert any(t["topic"] == "e2e/api-roundtrip" for t in r.json())

    assert c.delete(f"/wiki/fact/{fact_id}", headers=headers).status_code == 200

    r = c.get("/wiki/search", params={"q": "staging load balancer nginx port"}, headers=headers)
    assert fact_id not in {h["id"] for h in r.json()}


def test_rest_requires_auth(monkeypatch):
    _auth_config(monkeypatch)
    c = TestClient(appmod.app)
    assert c.get("/wiki/topics").status_code == 401
    assert c.get("/wiki/topics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_health_needs_no_auth(monkeypatch):
    _auth_config(monkeypatch)
    c = TestClient(appmod.app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── MCP ──────────────────────────────────────────────────────────────────

def _mcp_call(c, method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return c.post("/mcp", json=body, headers={"Authorization": f"Bearer {MCP_TOKEN}"})


def test_mcp_initialize_and_tools_list(monkeypatch):
    _auth_config(monkeypatch)
    c = TestClient(mcp_server.app)

    r = _mcp_call(c, "initialize")
    assert r.status_code == 200
    assert r.json()["result"]["protocolVersion"] == "2025-03-26"

    r = _mcp_call(c, "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"search_wiki", "list_wiki_topics", "add_wiki_fact"} <= names
    assert "delete_wiki_fact" not in names  # opt-in, off by default


def test_mcp_add_and_search_round_trip(monkeypatch):
    _auth_config(monkeypatch)
    c = TestClient(mcp_server.app)

    r = _mcp_call(c, "tools/call", {
        "name": "add_wiki_fact",
        "arguments": {"topic": "e2e/mcp-roundtrip", "content": "Redis eviction policy is allkeys-lru in prod"},
    })
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is False

    r = _mcp_call(c, "tools/call", {
        "name": "search_wiki",
        "arguments": {"query": "redis eviction policy prod", "hybrid": True},
    })
    result = r.json()["result"]
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert "note" in body and "results" in body  # provenance envelope present
    assert any(x["topic"] == "e2e/mcp-roundtrip" for x in body["results"])


def test_mcp_requires_auth(monkeypatch):
    _auth_config(monkeypatch)
    c = TestClient(mcp_server.app)
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
