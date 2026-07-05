"""Unit tests for manual fact CRUD — fully offline (embeddings/Qdrant mocked)."""
import pytest

from wiki_agent import fact_crud
from wiki_agent import knowledge_extractor as ke


@pytest.fixture
def offline(monkeypatch):
    """Stub out embeddings + Qdrant so add/delete run without any network."""
    calls = {"ensure": 0, "upserts": [], "deletes": []}

    monkeypatch.setattr(
        fact_crud.qdrant_helper, "ensure_wiki_collection",
        lambda: calls.__setitem__("ensure", calls["ensure"] + 1),
    )
    monkeypatch.setattr(fact_crud.embeddings, "embed", lambda text: [0.0] * 4)
    monkeypatch.setattr(
        fact_crud.qdrant_helper, "upsert",
        lambda pid, vec, payload: calls["upserts"].append((pid, vec, payload)) or pid,
    )
    monkeypatch.setattr(
        fact_crud.qdrant_helper, "delete",
        lambda pid: calls["deletes"].append(pid),
    )
    return calls


# ---------- add_fact ----------

def test_add_fact_returns_deterministic_id(offline):
    pid1 = fact_crud.add_fact("OCS/charging", "MK201 = 50MB")
    pid2 = fact_crud.add_fact("OCS/charging", "  mk201 = 50mb  ")
    # id is now derived from (topic, content), so the topic must be passed too.
    assert pid1 == pid2 == ke._point_id("MK201 = 50MB", "OCS/charging")


def test_add_fact_rejects_sensitive_content(offline):
    # MCP's add_wiki_fact calls add_fact directly, so the privacy filter must
    # live in add_fact itself (not only the REST layer). "password" is a
    # default SKIP_KEYWORD.
    with pytest.raises(ValueError):
        fact_crud.add_fact("a/b", "the password is hunter2")
    assert offline["upserts"] == []


def test_add_fact_calls_ensure_collection(offline):
    fact_crud.add_fact("a/b", "some fact")
    assert offline["ensure"] == 1


def test_add_fact_payload_schema(offline):
    fact_crud.add_fact("a/b", "a fact", tags=["x", "y"], confidence=0.7)
    _, _, payload = offline["upserts"][-1]
    assert payload["source"] == "manual"
    assert payload["topic"] == "a/b"
    assert payload["content"] == "a fact"
    assert payload["tags"] == ["x", "y"]
    assert payload["confidence"] == 0.7
    assert payload["created_at"] and payload["updated_at"]
    assert payload["ref"] is None


def test_add_fact_defaults(offline):
    fact_crud.add_fact("a/b", "another fact")
    _, _, payload = offline["upserts"][-1]
    assert payload["confidence"] == 1.0
    assert payload["source"] == "manual"
    assert payload["tags"] == []


def test_add_fact_custom_source_and_ref(offline):
    fact_crud.add_fact("a/b", "f", source="file", ref="doc.md")
    _, _, payload = offline["upserts"][-1]
    assert payload["source"] == "file"
    assert payload["ref"] == "doc.md"


@pytest.mark.parametrize(
    "topic, content",
    [("", "content"), ("   ", "content"), ("topic", ""), ("topic", "   ")],
)
def test_add_fact_rejects_empty(offline, topic, content):
    with pytest.raises(ValueError):
        fact_crud.add_fact(topic, content)
    assert offline["upserts"] == []


# ---------- delete_fact ----------

def test_delete_fact_calls_delete_with_id(offline):
    fact_crud.delete_fact("point-123")
    assert offline["deletes"] == ["point-123"]
