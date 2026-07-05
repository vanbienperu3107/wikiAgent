"""Tests for the in-memory rate limiter."""
from wiki_agent import ratelimit


def setup_function(_):
    ratelimit.reset()


def test_allows_up_to_limit_then_blocks():
    for _ in range(3):
        assert ratelimit.check_rate("k", 3, 60) is True
    assert ratelimit.check_rate("k", 3, 60) is False  # 4th over the limit


def test_keys_are_independent():
    assert ratelimit.check_rate("a", 1, 60) is True
    assert ratelimit.check_rate("a", 1, 60) is False
    assert ratelimit.check_rate("b", 1, 60) is True   # different key unaffected


def test_zero_limit_disables():
    for _ in range(1000):
        assert ratelimit.check_rate("k", 0, 60) is True


def test_window_expiry(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t["now"])
    assert ratelimit.check_rate("k", 1, 10) is True
    assert ratelimit.check_rate("k", 1, 10) is False   # within window
    t["now"] += 11                                       # window passed
    assert ratelimit.check_rate("k", 1, 10) is True


def test_reset():
    ratelimit.check_rate("k", 1, 60)
    assert ratelimit.check_rate("k", 1, 60) is False
    ratelimit.reset("k")
    assert ratelimit.check_rate("k", 1, 60) is True
