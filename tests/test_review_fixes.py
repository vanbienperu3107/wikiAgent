"""Regression tests for the code-review findings (all offline)."""
import pytest

from wiki_agent import reranker, rag, whatsapp, ratelimit, fact_crud, consolidation
from wiki_agent import knowledge_extractor, config


# ── reranker: partial Cohere response must not drop documents ──────────────

def test_reranker_partial_response_keeps_all(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "x")
    results = [{"content": "a"}, {"content": "b"}, {"content": "c"}]

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"results": [{"index": 2, "relevance_score": 0.9}]}

    monkeypatch.setattr(reranker.httpx, "post", lambda *a, **k: _Resp())
    out = reranker.rerank("q", results, top_n=None)
    assert len(out) == 3                       # nothing dropped
    assert out[0]["content"] == "c"            # reranked one first
    assert {r["content"] for r in out} == {"a", "b", "c"}


# ── rag._recency: non-string updated_at must degrade to 0.0, not crash ─────

def test_recency_non_string_updated_at():
    assert rag._recency(1720000000, 30.0) == 0.0   # int → TypeError caught
    assert rag._recency(None, 30.0) == 0.0
    assert rag._recency("2020-01-01T00:00:00+00:00", 30.0) > 0.0


# ── whatsapp: cost-gate must fail CLOSED on a stringified keep ─────────────

@pytest.mark.parametrize("val,expected", [
    (True, True), (False, False), ("true", True), ("false", False),
    ("no", False), ("yes", True), (1, True), (0, False), ("", False), (None, False),
])
def test_coerce_keep(val, expected):
    assert whatsapp._coerce_keep(val) is expected


def test_parse_classification_stringified_false():
    assert whatsapp._parse_classification('{"keep":"false","topic":"gossip"}')["keep"] is False


def test_whatsapp_topic_rebias_only_generic(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_CONTACT_BLACKLIST", [])
    monkeypatch.setattr(whatsapp, "classify", lambda m: {"keep": True, "topic": "OCS/charging"})
    monkeypatch.setattr(knowledge_extractor, "extract_facts", lambda m, backend=None: [
        {"topic": "misc", "content": "x", "tags": [], "confidence": 0.9},
        {"topic": "kubernetes", "content": "y", "tags": [], "confidence": 0.9},
    ])
    stored = {}
    monkeypatch.setattr(knowledge_extractor, "store_facts",
                        lambda facts, source, ref=None: stored.setdefault("t", [f["topic"] for f in facts]) or len(facts))
    whatsapp.process_thread([{"role": "user", "content": "hello"}], thread_id="t")
    assert stored["t"] == ["OCS/charging", "kubernetes"]  # misc rebiased, kubernetes kept


# ── ratelimit: idle keys evicted (no empty-deque accumulation) ─────────────

def test_ratelimit_evicts_after_window(monkeypatch):
    ratelimit.reset()
    t = {"now": 100.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t["now"])
    assert ratelimit.check_rate("c", 5, 10)
    t["now"] += 11                                     # window fully expires
    assert ratelimit.check_rate("c", 5, 10)
    assert len(ratelimit._hits["c"]) == 1              # recreated fresh, not stale-stacked


# ── fact_crud.update_fact: both branches (previously untested) ─────────────

def test_update_fact_metadata_only(monkeypatch):
    monkeypatch.setattr(fact_crud, "_retrieve_payload",
                        lambda pid: {"topic": "a/b", "content": "keep", "tags": [], "confidence": 0.5, "source": "manual"})
    captured = {}
    monkeypatch.setattr(fact_crud, "_set_payload", lambda pid, payload: captured.update(id=pid, payload=payload))
    out = fact_crud.update_fact("id1", confidence=0.9, topic="a/c")
    assert out == "id1"                                # id unchanged
    assert captured["payload"]["confidence"] == 0.9
    assert captured["payload"]["topic"] == "a/c"
    assert "updated_at" in captured["payload"]


def test_update_fact_content_change_deletes_old(monkeypatch):
    monkeypatch.setattr(fact_crud, "_retrieve_payload",
                        lambda pid: {"topic": "a/b", "content": "old", "tags": [], "confidence": 1.0, "source": "manual"})
    monkeypatch.setattr(fact_crud.qdrant_helper, "ensure_wiki_collection", lambda: None)
    monkeypatch.setattr(fact_crud.embeddings, "embed", lambda c: [0.0] * 4)
    monkeypatch.setattr(fact_crud.qdrant_helper, "upsert", lambda pid, v, p: pid)
    deleted = []
    monkeypatch.setattr(fact_crud.qdrant_helper, "delete", lambda pid: deleted.append(pid))
    new_id = fact_crud.update_fact("old-id", content="brand new content")
    assert new_id == knowledge_extractor._point_id("brand new content")
    assert new_id != "old-id"
    assert deleted == ["old-id"]                       # stale point removed


# ── consolidation: topicless / singleton buckets skipped in contradiction ──

def test_consolidation_skips_topicless_and_singletons(monkeypatch):
    calls = []
    monkeypatch.setattr(consolidation, "detect_contradictions",
                        lambda facts, **k: calls.append([f["topic"] for f in facts]) or [])
    points = [
        {"id": "1", "vector": [1, 0], "payload": {"topic": "", "content": "u"}},
        {"id": "2", "vector": [0, 1], "payload": {"topic": "", "content": "v"}},
        {"id": "3", "vector": [1, 1], "payload": {"topic": "net/x", "content": "p"}},
        {"id": "4", "vector": [1, 2], "payload": {"topic": "net/x", "content": "q"}},
        {"id": "5", "vector": [2, 0], "payload": {"topic": "solo", "content": "s"}},
    ]
    consolidation.consolidate(points, contradiction_check=True, apply=False)
    # only the 2-member non-empty topic bucket is analyzed
    assert len(calls) == 1
    assert calls[0] == ["net/x", "net/x"]
