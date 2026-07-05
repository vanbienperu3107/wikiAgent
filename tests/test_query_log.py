"""Tests for the append-only query log. Fully offline (stdlib only)."""
import json
import os
import threading

from wiki_agent import query_log


def _use_temp_log(monkeypatch, tmp_path, name="query_log.jsonl"):
    path = tmp_path / name
    monkeypatch.setenv("WIKI_QUERY_LOG_PATH", str(path))
    return path


def test_log_then_read_round_trips(monkeypatch, tmp_path):
    _use_temp_log(monkeypatch, tmp_path)
    query_log.log_query("OCS charge", 3, mode="semantic", topic="OCS/charging",
                        took_ms=12.5, top_ids=["a", "b"])
    query_log.log_query("second query", 0, mode="keyword")

    rows = query_log.read_queries()
    assert len(rows) == 2
    # most recent last
    assert rows[0]["query"] == "OCS charge"
    assert rows[1]["query"] == "second query"
    assert rows[0]["result_count"] == 3
    assert rows[0]["mode"] == "semantic"
    assert rows[0]["topic"] == "OCS/charging"
    assert rows[0]["took_ms"] == 12.5
    assert rows[0]["top_ids"] == ["a", "b"]
    # UTC ISO timestamp present
    assert rows[0]["ts"].endswith("+00:00")


def test_creates_parent_dirs(monkeypatch, tmp_path):
    path = _use_temp_log(monkeypatch, tmp_path, name="nested/deep/log.jsonl")
    query_log.log_query("hi", 1)
    assert path.exists()
    assert len(query_log.read_queries()) == 1


def test_malformed_line_tolerated(monkeypatch, tmp_path):
    path = _use_temp_log(monkeypatch, tmp_path)
    query_log.log_query("good one", 2)
    # inject junk + a blank line
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
        fh.write("\n")
        fh.write('{"query": "also good", "result_count": 5, "mode": "semantic"}\n')

    rows = query_log.read_queries()
    assert len(rows) == 2
    assert rows[0]["query"] == "good one"
    assert rows[1]["query"] == "also good"


def test_read_missing_file_returns_empty(monkeypatch, tmp_path):
    _use_temp_log(monkeypatch, tmp_path, name="does_not_exist.jsonl")
    assert query_log.read_queries() == []


def test_read_respects_limit(monkeypatch, tmp_path):
    _use_temp_log(monkeypatch, tmp_path)
    for i in range(5):
        query_log.log_query(f"q{i}", i)
    rows = query_log.read_queries(limit=2)
    assert len(rows) == 2
    # keeps the most recent ones
    assert rows[0]["query"] == "q3"
    assert rows[1]["query"] == "q4"


def test_stats_counts_modes_zero_and_top(monkeypatch, tmp_path):
    _use_temp_log(monkeypatch, tmp_path)
    query_log.log_query("Alpha Query", 3, mode="semantic")
    query_log.log_query("alpha  query", 0, mode="semantic")  # normalizes to same
    query_log.log_query("beta", 0, mode="keyword")
    query_log.log_query("gamma", 4, mode="semantic")

    s = query_log.stats()
    assert s["total"] == 4
    assert s["by_mode"] == {"semantic": 3, "keyword": 1}
    assert s["zero_result_count"] == 2
    assert s["avg_result_count"] == (3 + 0 + 0 + 4) / 4
    # top query normalized + counted
    top = dict(s["top_queries"])
    assert top["alpha query"] == 2
    assert top["beta"] == 1


def test_stats_empty(monkeypatch, tmp_path):
    _use_temp_log(monkeypatch, tmp_path, name="empty.jsonl")
    s = query_log.stats()
    assert s["total"] == 0
    assert s["by_mode"] == {}
    assert s["avg_result_count"] == 0.0
    assert s["top_queries"] == []
    assert s["zero_result_count"] == 0


def test_concurrent_writes_do_not_interleave(monkeypatch, tmp_path):
    # The module-level write lock must keep concurrent threadpool writers from
    # interleaving partial lines — every line must remain valid JSON.
    path = _use_temp_log(monkeypatch, tmp_path)
    n_threads, per_thread = 8, 50

    def worker(tid):
        for i in range(per_thread):
            query_log.log_query(f"t{tid}-{i}", i, mode="semantic")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    assert len(lines) == n_threads * per_thread
    for ln in lines:
        json.loads(ln)  # raises if a line was interleaved / truncated


def test_stats_caps_to_recent_window(monkeypatch, tmp_path):
    # stats() must not scan the whole file — only the most recent window.
    _use_temp_log(monkeypatch, tmp_path)
    monkeypatch.setattr(query_log, "_STATS_WINDOW", 3)
    for i in range(10):
        query_log.log_query(f"q{i}", 1, mode="semantic")

    s = query_log.stats()
    assert s["total"] == 3  # only the most-recent 3 events counted


def test_log_query_never_raises_on_unwritable_path(monkeypatch, tmp_path):
    # Point at an impossible path: a file used as a directory component.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    impossible = blocker / "sub" / "log.jsonl"
    monkeypatch.setenv("WIKI_QUERY_LOG_PATH", str(impossible))

    # Must not raise despite unwritable path.
    query_log.log_query("should be swallowed", 1)
    # And nothing was written.
    assert not os.path.exists(str(impossible))
