"""Tests for the WhatsApp pipeline — classifier/extractor/storage mocked."""
import pytest

from wiki_agent import whatsapp, knowledge_extractor, config


# ---------- blacklist (deterministic) ----------

def test_blacklist(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_CONTACT_BLACKLIST", ["boss", "84999"])
    assert whatsapp.is_blacklisted("Boss Nguyen")
    assert whatsapp.is_blacklisted("84999123@s.whatsapp.net")
    assert not whatsapp.is_blacklisted("colleague@s.whatsapp.net")
    assert not whatsapp.is_blacklisted(None)


# ---------- classification parsing ----------

def test_parse_classification_variants():
    assert whatsapp._parse_classification('{"keep": true, "topic": "OCS/charging"}') == {
        "keep": True, "topic": "OCS/charging"}
    assert whatsapp._parse_classification('junk {"keep": false, "topic": null} tail') == {
        "keep": False, "topic": None}
    assert whatsapp._parse_classification("not json") == {"keep": False, "topic": None}
    # topic "null" string normalized to None
    assert whatsapp._parse_classification('{"keep": true, "topic": "null"}')["topic"] is None


# ---------- pipeline gating ----------

@pytest.fixture
def stub_pipeline(monkeypatch):
    """Stub classify + extract + store so the pipeline runs offline."""
    calls = {"extract": 0, "store": []}

    def fake_classify(messages):
        text = " ".join(knowledge_extractor._message_text(m) for m in messages)
        if "MK201" in text:
            return {"keep": True, "topic": "OCS/charging"}
        return {"keep": False, "topic": None}

    def fake_extract(messages, backend=None):
        calls["extract"] += 1
        return [{"topic": "misc", "content": "MK201=50MB", "tags": ["OCS"], "confidence": 0.9}]

    def fake_store(facts, source, ref=None):
        calls["store"].append((source, ref, [f["topic"] for f in facts]))
        return len(facts)

    monkeypatch.setattr(whatsapp, "classify", fake_classify)
    monkeypatch.setattr(knowledge_extractor, "extract_facts", fake_extract)
    monkeypatch.setattr(knowledge_extractor, "store_facts", fake_store)
    monkeypatch.setattr(config, "WHATSAPP_CONTACT_BLACKLIST", [])
    return calls


def test_pipeline_keeps_technical_thread(stub_pipeline):
    msgs = [{"role": "user", "content": "gói MK201 charge bao nhiêu?"},
            {"role": "user", "content": "MK201 charge 50MB nhé"}]
    r = whatsapp.process_thread(msgs, thread_id="t1")
    assert r["kept"] is True
    assert r["stored"] == 1
    assert stub_pipeline["extract"] == 1
    src, ref, topics = stub_pipeline["store"][0]
    assert src == "whatsapp" and ref == "t1"
    # generic extractor topic 'misc' rebiased to the classifier topic
    assert topics == ["OCS/charging"]


def test_pipeline_skips_small_talk(stub_pipeline):
    msgs = [{"role": "user", "content": "chào buổi sáng nhé"}]
    r = whatsapp.process_thread(msgs, thread_id="t2")
    assert r["kept"] is False
    assert r["stored"] == 0
    # extractor must NOT run when classifier says keep=false (cost gate)
    assert stub_pipeline["extract"] == 0


def test_pipeline_blacklisted_sender_short_circuits(stub_pipeline, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_CONTACT_BLACKLIST", ["boss"])
    r = whatsapp.process_thread(
        [{"role": "user", "content": "MK201 charge 50MB"}], sender="Boss")
    assert r["blacklisted"] is True
    assert r["stored"] == 0
    assert stub_pipeline["extract"] == 0


def test_pipeline_privacy_filter_drops_before_llm(stub_pipeline, monkeypatch):
    monkeypatch.setattr(config, "SKIP_KEYWORDS", ["password"])
    # single sensitive message → nothing left after prefilter → no classify/extract
    r = whatsapp.process_thread([{"role": "user", "content": "my password is hunter2"}])
    assert r["kept"] is False
    assert r["stored"] == 0
    assert stub_pipeline["extract"] == 0
