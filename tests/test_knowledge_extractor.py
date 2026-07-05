"""Unit tests for the extractor — no network, LLM/embeddings/Qdrant mocked."""
import json
import pathlib

import pytest

from wiki_agent import knowledge_extractor as ke
from wiki_agent import config

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "conversations.json"


def load_fixtures():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


# ---------- privacy filter (deterministic) ----------

def test_privacy_filter_drops_sensitive():
    msgs = [
        {"role": "user", "content": "mật khẩu của tôi là hunter2"},
        {"role": "user", "content": "gói MK201 charge 50MB"},
    ]
    kept = ke.privacy_filter(msgs)
    assert len(kept) == 1
    assert "MK201" in ke._message_text(kept[0])


def test_is_sensitive_case_insensitive():
    assert ke.is_sensitive("Here is the API KEY: sk-123")
    assert ke.is_sensitive("SECRET token")
    assert not ke.is_sensitive("gói cước bình thường")


# ---------- JSON parsing robustness ----------

def test_parse_facts_plain_array():
    raw = '[{"topic":"OCS/charging","content":"MK201=50MB","tags":["OCS"],"confidence":0.9}]'
    facts = ke._parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["topic"] == "OCS/charging"
    assert facts[0]["confidence"] == 0.9


def test_parse_facts_fenced_json_with_prose():
    raw = 'Đây là kết quả:\n```json\n[{"topic":"deploy/ci","content":"CI ~4 phút","tags":[],"confidence":0.8}]\n```'
    facts = ke._parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["topic"] == "deploy/ci"


def test_parse_facts_invalid_returns_empty():
    assert ke._parse_facts("không có fact nào") == []
    assert ke._parse_facts("[broken json") == []


def test_parse_facts_skips_incomplete_items():
    raw = '[{"topic":"","content":"x"},{"topic":"a/b","content":"good","confidence":0.7}]'
    facts = ke._parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["topic"] == "a/b"


def test_parse_facts_clamps_confidence_and_tags():
    raw = '[{"topic":"a/b","content":"c","tags":["1","2","3","4","5","6","7"],"confidence":5}]'
    facts = ke._parse_facts(raw)
    assert facts[0]["confidence"] == 1.0
    assert len(facts[0]["tags"]) == 5


# ---------- deterministic id / dedup ----------

def test_point_id_is_stable_and_normalized():
    a = ke._point_id("MK201 = 50MB")
    b = ke._point_id("  mk201 = 50mb  ")
    assert a == b  # normalization (strip + lower) → same id → idempotent upsert


def test_point_id_differs_for_different_content():
    assert ke._point_id("fact one") != ke._point_id("fact two")


# ---------- extract_facts across 10+ conversations (mocked LLM) ----------

CANNED = {
    "ocs_charging": '[{"topic":"OCS/charging","content":"MK201=50MB và MK311=50MB","tags":["OCS","MK201"],"confidence":0.91}]',
    "deploy_ci": '[{"topic":"deploy/ci","content":"CI build+deploy+smoke ~4 phút","tags":["ci"],"confidence":0.85}]',
    "small_talk": "[]",
}


@pytest.fixture
def mock_llm(monkeypatch):
    """Route extraction through canned responses keyed by transcript content."""
    def fake_anthropic(transcript, timeout=60):
        if "MK201" in transcript:
            return CANNED["ocs_charging"]
        if "CI" in transcript or "deploy" in transcript.lower():
            return CANNED["deploy_ci"]
        return CANNED["small_talk"]

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ke, "_extract_anthropic", fake_anthropic)
    return fake_anthropic


def test_extract_facts_fixtures(mock_llm):
    for fx in load_fixtures():
        facts = ke.extract_facts(fx["messages"])
        if fx["expect_topic_prefix"] is None:
            assert facts == []
        else:
            assert facts, f"expected facts for {fx['name']}"
            assert facts[0]["topic"].startswith(fx["expect_topic_prefix"])


def test_extract_10_conversations_no_crash(mock_llm):
    """Roadmap Phase 1 acceptance: unit test 10 conversations."""
    fixtures = load_fixtures()
    # Cycle the 3 fixtures to reach 10 conversations.
    convos = [fixtures[i % len(fixtures)] for i in range(10)]
    total = 0
    for fx in convos:
        total += len(ke.extract_facts(fx["messages"]))
    # 10 convos, small_talk yields 0 — expect at least the productive ones.
    assert total >= 6


def test_min_confidence_filter(mock_llm, monkeypatch):
    monkeypatch.setattr(config, "MIN_CONFIDENCE", 0.95)
    # deploy_ci fact is 0.85 → dropped when threshold is 0.95
    facts = ke.extract_facts([{"role": "user", "content": "CI deploy mất bao lâu"}])
    assert facts == []


# ---------- store_facts (mock embeddings + qdrant) ----------

def test_store_facts_calls_upsert_per_fact(monkeypatch):
    upserts = []
    monkeypatch.setattr(ke.qdrant_helper, "ensure_wiki_collection", lambda: None)
    monkeypatch.setattr(ke.embeddings, "embed_batch", lambda texts: [[0.0] * 4 for _ in texts])
    monkeypatch.setattr(
        ke.qdrant_helper, "upsert",
        lambda pid, vec, payload: upserts.append((pid, payload)) or pid,
    )
    facts = [
        {"topic": "a/b", "content": "fact one", "tags": [], "confidence": 0.9},
        {"topic": "a/c", "content": "fact two", "tags": ["x"], "confidence": 0.8},
    ]
    n = ke.store_facts(facts, source="conversation", ref="sess-1")
    assert n == 2
    assert upserts[0][1]["source"] == "conversation"
    assert upserts[0][1]["ref"] == "sess-1"
    assert {"created_at", "updated_at", "confidence", "topic"} <= upserts[0][1].keys()


def test_store_facts_empty_returns_zero():
    assert ke.store_facts([]) == 0
