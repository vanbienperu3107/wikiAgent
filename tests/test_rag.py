"""Tests for RAG 2.0 hybrid retrieval — fully offline (embed + Qdrant mocked)."""
from datetime import datetime, timedelta, timezone

import pytest

from wiki_agent import rag


def _hit(id, score, content, updated_at="2026-01-01T00:00:00+00:00", **extra):
    payload = {
        "topic": "OCS/charging",
        "content": content,
        "source": "conversation",
        "tags": ["OCS"],
        "confidence": 0.9,
        "updated_at": updated_at,
        "ref": "sess-1",
    }
    payload.update(extra)
    return {"id": id, "score": score, "payload": payload}


@pytest.fixture(autouse=True)
def stub_embed(monkeypatch):
    monkeypatch.setattr(rag.embeddings, "embed", lambda q: [0.0, 0.1, 0.2])


def _patch_search(monkeypatch, hits, capture=None):
    def fake_search(vec, limit, topic=None, source=None):
        if capture is not None:
            capture["limit"] = limit
            capture["topic"] = topic
            capture["source"] = source
        return hits

    monkeypatch.setattr(rag.qdrant_helper, "search", fake_search)


# ---------- BM25 keyword boost ----------

def test_bm25_boost_reorders_vs_pure_vector(monkeypatch):
    # Vector order is a > b > c. BM25 lifts 'b' (exact match) to keyword rank 1,
    # so fusion promotes it past the vector-rank-1 doc that has no match.
    hits = [
        _hit("a", 0.90, "general notes about data plans and pricing"),
        _hit("b", 0.80, "MK201 charge charge MK201 billing 50MB daily"),
        _hit("c", 0.70, "charge cycle overview"),
    ]
    _patch_search(monkeypatch, hits)
    out = rag.hybrid_search("MK201 charge", limit=3)
    assert out[0]["id"] == "b"
    # Pure vector would have ranked 'a' first.
    assert out[0]["rrf_score"] > out[1]["rrf_score"]


def test_pure_vector_when_no_keyword_overlap(monkeypatch):
    # No query term appears -> BM25 all-zero -> vector order preserved.
    hits = [
        _hit("a", 0.90, "alpha bravo charlie"),
        _hit("b", 0.80, "delta echo foxtrot"),
    ]
    _patch_search(monkeypatch, hits)
    out = rag.hybrid_search("zzz nonexistent", limit=2)
    assert [r["id"] for r in out] == ["a", "b"]


# ---------- RRF fusion ----------

def test_rrf_score_present_and_matches_formula(monkeypatch):
    hits = [
        _hit("a", 0.90, "MK201 charge charge"),
        _hit("b", 0.80, "unrelated content here"),
    ]
    _patch_search(monkeypatch, hits)
    out = rag.hybrid_search("MK201", limit=2)
    top = next(r for r in out if r["id"] == "a")
    # 'a' is rank 1 by vector and rank 1 by keyword -> 2/(60+1).
    assert top["rrf_score"] == pytest.approx(2.0 / 61.0)


# ---------- time-aware re-rank ----------

def test_recency_favors_newer_when_beta_positive(monkeypatch):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    fresh = (now - timedelta(days=1)).isoformat()
    # Same content so keyword/vector fusion is near-symmetric; recency decides.
    hits = [
        _hit("old", 0.90, "MK201 charge", updated_at=old),
        _hit("new", 0.85, "MK201 charge", updated_at=fresh),
    ]
    _patch_search(monkeypatch, hits)

    # Without recency, vector order keeps 'old' on top.
    base = rag.hybrid_search("MK201 charge", limit=2, beta=0.0)
    assert base[0]["id"] == "old"

    # With a strong recency weight, the fresh doc wins.
    boosted = rag.hybrid_search(
        "MK201 charge", limit=2, alpha=1.0, beta=2.0, halflife_days=30.0
    )
    assert boosted[0]["id"] == "new"


# ---------- edge cases / passthrough ----------

def test_empty_pool_returns_empty(monkeypatch):
    _patch_search(monkeypatch, [])
    assert rag.hybrid_search("anything", limit=5) == []


def test_topic_and_source_filter_passed_through(monkeypatch):
    capture = {}
    _patch_search(monkeypatch, [_hit("a", 0.9, "MK201")], capture=capture)
    rag.hybrid_search(
        "MK201", limit=5, topic="OCS/charging", source="file", candidate_k=17
    )
    assert capture["topic"] == "OCS/charging"
    assert capture["source"] == "file"
    assert capture["limit"] == 17  # candidate_k drives the pool size


def test_blank_content_does_not_crash(monkeypatch):
    hits = [
        _hit("a", 0.90, ""),
        _hit("b", 0.80, None),
        _hit("c", 0.70, "MK201 charge"),
    ]
    _patch_search(monkeypatch, hits)
    out = rag.hybrid_search("MK201", limit=3)
    assert len(out) == 3
    # Blank/None content is tokenized to [] without error; all docs survive
    # with a well-formed, positive fused score.
    assert {r["id"] for r in out} == {"a", "b", "c"}
    assert all(r["rrf_score"] > 0 for r in out)
