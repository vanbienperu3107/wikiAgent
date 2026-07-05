"""Unit tests for the Phase-4 reranker — fully offline (no network).

Cohere HTTP calls are monkeypatched; the reordering / fail-safe logic runs for
real. A real network call in any of these tests is a bug.
"""
import httpx
import pytest

from wiki_agent import reranker


def _results():
    return [
        {"id": "a", "content": "alpha doc"},
        {"id": "b", "content": "bravo doc"},
        {"id": "c", "content": "charlie doc"},
    ]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# ---------- passthrough when key unset ----------

def test_passthrough_when_no_key(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("httpx.post must not be called without a key")

    monkeypatch.setattr(reranker.httpx, "post", boom)

    results = _results()
    out = reranker.rerank("q", results)
    assert out == results
    assert all("rerank_score" not in r for r in out)


# ---------- reordering per mocked Cohere response ----------

def test_reorders_and_attaches_scores(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    # Cohere returns results best-first: c, then a, then b.
    fake = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.5},
            {"index": 1, "relevance_score": 0.1},
        ]
    }

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(fake)

    monkeypatch.setattr(reranker.httpx, "post", fake_post)

    out = reranker.rerank("q", _results())
    assert [r["id"] for r in out] == ["c", "a", "b"]
    assert out[0]["rerank_score"] == 0.9
    assert out[1]["rerank_score"] == 0.5
    # documents are the content strings, in original order.
    assert captured["json"]["documents"] == ["alpha doc", "bravo doc", "charlie doc"]
    assert captured["headers"]["Authorization"] == "Bearer test-key"


# ---------- empty results ----------

def test_empty_results_returns_empty(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    def boom(*a, **k):
        raise AssertionError("httpx.post must not be called for empty input")

    monkeypatch.setattr(reranker.httpx, "post", boom)

    assert reranker.rerank("q", []) == []


# ---------- HTTP error -> original order preserved ----------

def test_http_error_preserves_original_order(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    def boom(*a, **k):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(reranker.httpx, "post", boom)

    results = _results()
    out = reranker.rerank("q", results)
    assert [r["id"] for r in out] == ["a", "b", "c"]
    assert all("rerank_score" not in r for r in out)


def test_status_error_preserves_original_order(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    class _ErrResponse:
        def raise_for_status(self):
            raise httpx.HTTPStatusError("500", request=None, response=None)

        def json(self):  # pragma: no cover - should not be reached
            raise AssertionError("json() must not run after a status error")

    monkeypatch.setattr(reranker.httpx, "post", lambda *a, **k: _ErrResponse())

    out = reranker.rerank("q", _results())
    assert [r["id"] for r in out] == ["a", "b", "c"]


# ---------- top_n truncation ----------

def test_top_n_truncates_reranked(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    fake = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.5},
        ]
    }

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(fake)

    monkeypatch.setattr(reranker.httpx, "post", fake_post)

    out = reranker.rerank("q", _results(), top_n=2)
    assert [r["id"] for r in out] == ["c", "a"]
    assert captured["json"]["top_n"] == 2


def test_top_n_truncates_passthrough(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    out = reranker.rerank("q", _results(), top_n=1)
    assert [r["id"] for r in out] == ["a"]
