"""Tests for least-privilege admin token + MCP delete-gating + search envelope."""
import pytest
from fastapi.testclient import TestClient

from wiki_agent import app as appmod, mcp_server, config, ratelimit


# ── WIKI_ADMIN_TOKEN gates destructive DELETE ──────────────────────────────

def test_delete_requires_admin_when_configured(monkeypatch):
    monkeypatch.setattr(config, "WIKI_AUTH_TOKEN", "rw")
    monkeypatch.setattr(config, "WIKI_ADMIN_TOKEN", "admin")
    monkeypatch.setattr(config, "RATE_LIMIT", 1000)
    monkeypatch.setattr(appmod.fact_crud, "delete_fact", lambda pid: None)
    ratelimit.reset()
    c = TestClient(appmod.app)
    # the read/write token can no longer delete
    assert c.delete("/wiki/fact/abc", headers={"Authorization": "Bearer rw"}).status_code == 403
    # the admin token can
    assert c.delete("/wiki/fact/abc", headers={"Authorization": "Bearer admin"}).status_code == 200


def test_delete_falls_back_to_normal_token_when_admin_unset(monkeypatch):
    monkeypatch.setattr(config, "WIKI_AUTH_TOKEN", "rw")
    monkeypatch.setattr(config, "WIKI_ADMIN_TOKEN", "")
    monkeypatch.setattr(config, "RATE_LIMIT", 1000)
    monkeypatch.setattr(appmod.fact_crud, "delete_fact", lambda pid: None)
    ratelimit.reset()
    c = TestClient(appmod.app)
    assert c.delete("/wiki/fact/abc", headers={"Authorization": "Bearer rw"}).status_code == 200


# ── MCP delete_wiki_fact is gated behind WIKI_MCP_ALLOW_DELETE ──────────────

def test_mcp_delete_hidden_and_blocked_by_default(monkeypatch):
    monkeypatch.setattr(mcp_server.config, "WIKI_MCP_ALLOW_DELETE", False)
    assert "delete_wiki_fact" not in [t["name"] for t in mcp_server._visible_tools()]
    with pytest.raises(ValueError):
        mcp_server.exec_tool("delete_wiki_fact", {"id": "x"})


def test_mcp_delete_enabled_when_opted_in(monkeypatch):
    monkeypatch.setattr(mcp_server.config, "WIKI_MCP_ALLOW_DELETE", True)
    monkeypatch.setattr(mcp_server.fact_crud, "delete_fact", lambda pid: None)
    assert "delete_wiki_fact" in [t["name"] for t in mcp_server._visible_tools()]
    assert mcp_server.exec_tool("delete_wiki_fact", {"id": "x"}) == {"deleted": "x"}


# ── MCP search results carry a data-not-instructions provenance envelope ───

def test_mcp_search_wraps_results_in_envelope(monkeypatch):
    monkeypatch.setattr(mcp_server.wiki_search, "search_wiki",
                        lambda q, topic=None, source=None, limit=5: [{"id": "1", "content": "c", "source": "whatsapp"}])
    monkeypatch.setattr(mcp_server.query_log, "log_query", lambda *a, **k: None)
    out = mcp_server.exec_tool("search_wiki", {"query": "x"})
    assert set(out.keys()) == {"note", "results"}
    assert out["results"][0]["id"] == "1"
    assert "instruction" in out["note"].lower()  # tells the model these are data
