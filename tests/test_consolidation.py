"""Unit tests for Phase 5 consolidation — fully offline (no network).

LLM and Qdrant calls are monkeypatched; the pure grouping/ranking logic runs
for real. A network call in any of these tests is a bug.
"""
import math

import pytest

from wiki_agent import consolidation as cons
from wiki_agent import config


# ---------- cosine ----------

def test_cosine_identical_vectors_is_one():
    assert cons.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cons.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_scaled_vectors_is_one():
    # Direction, not magnitude — a scaled copy is maximally similar.
    assert cons.cosine([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)


def test_cosine_zero_vector_is_zero():
    assert cons.cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


# ---------- find_duplicate_groups ----------

def _pt(pid, topic, vector, **payload):
    return {"id": pid, "vector": vector, "payload": {"topic": topic, **payload}}


def test_groups_only_same_topic_near_dups():
    points = [
        _pt("a", "OCS/charging", [1.0, 0.0, 0.0]),
        _pt("b", "OCS/charging", [1.0, 0.001, 0.0]),   # ~= a
        _pt("c", "deploy/ci", [1.0, 0.0, 0.0]),        # identical vector but other topic
    ]
    groups = cons.find_duplicate_groups(points, threshold=0.95)
    assert groups == [["a", "b"]]  # c is NOT merged despite matching vector


def test_respects_threshold():
    # Two vectors at ~45 degrees: cosine ~0.707.
    points = [
        _pt("a", "t", [1.0, 0.0]),
        _pt("b", "t", [1.0, 1.0]),
    ]
    assert cons.find_duplicate_groups(points, threshold=0.95) == []
    assert cons.find_duplicate_groups(points, threshold=0.7) == [["a", "b"]]


def test_singletons_excluded():
    points = [
        _pt("a", "t1", [1.0, 0.0]),
        _pt("b", "t2", [0.0, 1.0]),
    ]
    assert cons.find_duplicate_groups(points) == []


def test_transitive_union_into_one_group():
    # a~b and b~c (chain) → all three in one group even if a and c are further.
    points = [
        _pt("a", "t", [1.0, 0.0, 0.0]),
        _pt("b", "t", [1.0, 0.05, 0.0]),
        _pt("c", "t", [1.0, 0.10, 0.0]),
    ]
    groups = cons.find_duplicate_groups(points, threshold=0.99)
    assert len(groups) == 1
    assert sorted(groups[0]) == ["a", "b", "c"]


# ---------- detect_contradictions (monkeypatched LLM) ----------

def test_detect_contradictions_parses_plain_array(monkeypatch):
    monkeypatch.setattr(
        cons, "_detect_anthropic",
        lambda block, timeout=60: '[{"a":0,"b":1,"reason":"khác giá trị"}]',
    )
    facts = [
        {"topic": "OCS/charging", "content": "MK201 = 50MB"},
        {"topic": "OCS/charging", "content": "MK201 = 100MB"},
    ]
    pairs = cons.detect_contradictions(facts)
    assert pairs == [{"a": 0, "b": 1, "reason": "khác giá trị"}]


def test_detect_contradictions_parses_fenced_json_with_prose(monkeypatch):
    monkeypatch.setattr(
        cons, "_detect_anthropic",
        lambda block, timeout=60: 'Kết quả:\n```json\n[{"a":1,"b":0,"reason":"mâu thuẫn"}]\n```',
    )
    facts = [
        {"topic": "t", "content": "x"},
        {"topic": "t", "content": "y"},
    ]
    pairs = cons.detect_contradictions(facts)
    assert len(pairs) == 1
    assert pairs[0]["a"] == 1 and pairs[0]["b"] == 0


def test_detect_contradictions_invalid_returns_empty(monkeypatch):
    monkeypatch.setattr(
        cons, "_detect_anthropic",
        lambda block, timeout=60: "không có mâu thuẫn nào",
    )
    facts = [{"topic": "t", "content": "x"}, {"topic": "t", "content": "y"}]
    assert cons.detect_contradictions(facts) == []


def test_detect_contradictions_drops_out_of_range_indices(monkeypatch):
    monkeypatch.setattr(
        cons, "_detect_anthropic",
        lambda block, timeout=60: '[{"a":0,"b":9,"reason":"?"},{"a":0,"b":1,"reason":"ok"}]',
    )
    facts = [{"topic": "t", "content": "x"}, {"topic": "t", "content": "y"}]
    pairs = cons.detect_contradictions(facts)
    assert pairs == [{"a": 0, "b": 1, "reason": "ok"}]


def test_detect_contradictions_short_circuits_without_llm(monkeypatch):
    # Fewer than 2 facts → must not call the model at all.
    def boom(block, timeout=60):
        raise AssertionError("LLM should not be called for <2 facts")
    monkeypatch.setattr(cons, "_detect_anthropic", boom)
    assert cons.detect_contradictions([{"topic": "t", "content": "x"}]) == []


# ---------- consolidate(apply=False) plans without network ----------

def _no_network(monkeypatch):
    """Any Qdrant/LLM call in a dry run is a bug — make them explode."""
    def boom(*a, **k):
        raise AssertionError("no network call allowed in dry run")
    monkeypatch.setattr(cons, "set_status", boom)
    monkeypatch.setattr(cons.httpx, "post", boom)


def test_consolidate_dry_run_plans_obsoletions(monkeypatch):
    _no_network(monkeypatch)
    points = [
        _pt("a", "t", [1.0, 0.0, 0.0], confidence=0.8, updated_at="2026-01-01"),
        _pt("b", "t", [1.0, 0.001, 0.0], confidence=0.9, updated_at="2026-02-01"),
        _pt("c", "other", [0.0, 1.0, 0.0], confidence=0.7),
    ]
    summary = cons.consolidate(points, apply=False)
    assert summary["groups"] == 1
    assert summary["applied"] is False
    # b has the higher confidence → survivor; a is obsoleted.
    assert summary["kept"] == ["b"]
    assert summary["obsoleted"] == ["a"]
    assert "contradictions" not in summary


def test_consolidate_dry_run_ties_break_on_newest(monkeypatch):
    _no_network(monkeypatch)
    points = [
        _pt("a", "t", [1.0, 0.0], confidence=0.9, updated_at="2026-01-01"),
        _pt("b", "t", [1.0, 0.0], confidence=0.9, updated_at="2026-03-01"),
    ]
    summary = cons.consolidate(points, apply=False)
    assert summary["kept"] == ["b"]      # same confidence → newest survives
    assert summary["obsoleted"] == ["a"]


def test_consolidate_apply_calls_set_status(monkeypatch):
    calls = []
    monkeypatch.setattr(cons, "set_status", lambda pid, status: calls.append((pid, status)))
    points = [
        _pt("a", "t", [1.0, 0.0], confidence=0.8),
        _pt("b", "t", [1.0, 0.0], confidence=0.9),
    ]
    summary = cons.consolidate(points, apply=True)
    assert summary["applied"] is True
    assert ("b", "active") in calls
    assert ("a", "obsolete") in calls


def test_consolidate_with_contradiction_check(monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(
        cons, "_detect_anthropic",
        lambda block, timeout=60: '[{"a":0,"b":1,"reason":"khác nhau"}]',
    )
    points = [
        _pt("a", "OCS", [1.0, 0.0], content="MK201 = 50MB", confidence=0.8),
        _pt("b", "OCS", [0.0, 1.0], content="MK201 = 100MB", confidence=0.9),
    ]
    summary = cons.consolidate(points, contradiction_check=True, apply=False)
    assert summary["groups"] == 0          # different vectors → not duplicates
    assert len(summary["contradictions"]) == 1
    con = summary["contradictions"][0]
    assert con["topic"] == "OCS"
    assert {con["a"], con["b"]} == {"a", "b"}
