"""Unit tests for notify.py — fully offline (no network).

httpx.post is monkeypatched in every test; a real network call is a bug. Env
credentials are set/cleared with monkeypatch.setenv/delenv so tests are
hermetic regardless of the runner's environment.
"""
import pytest

from wiki_agent import notify


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "boom", request=None, response=None
            )


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")


# ---------- send_telegram ----------

def test_send_telegram_success(monkeypatch, creds):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(200)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.send_telegram("hello") is True
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert "test-token" in url
    assert kwargs["json"]["chat_id"] == "12345"
    assert kwargs["json"]["text"] == "hello"


def test_send_telegram_missing_creds_returns_false_no_call(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def boom(*a, **k):
        raise AssertionError("must not call httpx.post without creds")

    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.send_telegram("hello") is False


def test_send_telegram_missing_chat_id_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def boom(*a, **k):
        raise AssertionError("must not call httpx.post without chat id")

    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.send_telegram("hello") is False


def test_send_telegram_http_error_returns_false(monkeypatch, creds):
    def fake_post(url, **kwargs):
        return _FakeResponse(500)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.send_telegram("hello") is False


def test_send_telegram_network_exception_returns_false(monkeypatch, creds):
    def fake_post(url, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.send_telegram("hello") is False


# ---------- alert_contradictions ----------

def test_alert_contradictions_empty_does_not_send(monkeypatch, creds):
    def boom(*a, **k):
        raise AssertionError("must not send for empty contradictions")

    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.alert_contradictions([]) is False


def test_alert_contradictions_formats_and_sends(monkeypatch, creds):
    captured = {}

    def fake_post(url, **kwargs):
        captured["text"] = kwargs["json"]["text"]
        return _FakeResponse(200)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    contradictions = [
        {"topic": "diet", "a": "id-1", "b": "id-2", "reason": "vegan vs meat"},
        {"topic": "city", "a": "id-3", "b": "id-4", "reason": "Lima vs Hanoi"},
    ]
    assert notify.alert_contradictions(contradictions) is True
    text = captured["text"]
    assert "2 contradiction" in text
    assert "diet" in text
    assert "vegan vs meat" in text
    assert "id-3" in text
