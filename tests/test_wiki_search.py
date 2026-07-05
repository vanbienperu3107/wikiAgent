"""Tests for the query layer — Qdrant + embeddings mocked."""
from wiki_agent import wiki_search


def test_search_wiki_shapes_results(monkeypatch):
    monkeypatch.setattr(wiki_search.embeddings, "embed", lambda q: [0.1, 0.2, 0.3])

    def fake_search(vec, limit, topic=None, source=None):
        assert topic == "OCS/charging"
        return [
            {
                "id": "abc",
                "score": 0.87,
                "payload": {
                    "topic": "OCS/charging",
                    "content": "MK201=50MB",
                    "source": "conversation",
                    "tags": ["OCS"],
                    "confidence": 0.9,
                    "updated_at": "2026-07-05T00:00:00+00:00",
                    "ref": "sess-1",
                },
            }
        ]

    monkeypatch.setattr(wiki_search.qdrant_helper, "search", fake_search)
    out = wiki_search.search_wiki("OCS charge", topic="OCS/charging", limit=3)
    assert len(out) == 1
    assert out[0]["content"] == "MK201=50MB"
    assert out[0]["score"] == 0.87
    assert out[0]["tags"] == ["OCS"]


def test_list_wiki_topics_aggregates(monkeypatch):
    points = [
        {"payload": {"topic": "OCS/charging", "source": "conversation"}},
        {"payload": {"topic": "OCS/charging", "source": "file"}},
        {"payload": {"topic": "deploy/ci", "source": "conversation"}},
        {"payload": {"content": "no topic"}},  # ignored
    ]
    monkeypatch.setattr(wiki_search.qdrant_helper, "scroll_topics", lambda: points)
    topics = wiki_search.list_wiki_topics()
    assert topics[0]["topic"] == "OCS/charging"
    assert topics[0]["count"] == 2
    assert topics[0]["sources"] == ["conversation", "file"]
    assert {t["topic"] for t in topics} == {"OCS/charging", "deploy/ci"}
