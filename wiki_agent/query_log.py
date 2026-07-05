"""Append-only query log — capture real search queries for RAG tuning.

Roadmap: "collect 50 real queries before optimizing". This is pure telemetry:
`log_query` must NEVER raise into the caller (a broken log path must not break
search). Storage is a JSONL file; each line is one query event with a UTC
timestamp. Stdlib only — no external services (Roadmap 3.0: no new services).
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import List, Optional


def _log_path() -> str:
    return os.environ.get("WIKI_QUERY_LOG_PATH", "./data/query_log.jsonl")


def log_query(
    query: str,
    result_count: int,
    *,
    mode: str = "semantic",
    topic: Optional[str] = None,
    took_ms: Optional[float] = None,
    top_ids: Optional[List[str]] = None,
) -> None:
    """Append one query event as a JSON line. Best-effort — never raises.

    Any failure (unwritable path, serialization error, ...) is swallowed:
    telemetry must never break the caller.
    """
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "result_count": result_count,
            "mode": mode,
            "topic": topic,
            "took_ms": took_ms,
            "top_ids": list(top_ids) if top_ids is not None else None,
        }
        line = json.dumps(record, ensure_ascii=False)
        path = _log_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        # Telemetry is best-effort; swallow everything.
        return


def read_queries(limit: int = 1000) -> List[dict]:
    """Read back logged queries (most recent last), tolerating bad lines.

    Returns at most `limit` records, keeping the most recent ones.
    """
    path = _log_path()
    records: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except FileNotFoundError:
        return []
    except Exception:
        return records
    if limit is not None and len(records) > limit:
        records = records[-limit:]
    return records


def _normalize(query: object) -> str:
    if not isinstance(query, str):
        return ""
    return " ".join(query.lower().split())


def stats() -> dict:
    """Aggregate the query log. Pure — reads the file, computes summary."""
    records = read_queries(limit=10**9)
    total = len(records)
    by_mode: Counter = Counter()
    result_sum = 0.0
    result_n = 0
    zero_results = 0
    query_counts: Counter = Counter()

    for r in records:
        by_mode[r.get("mode")] += 1
        rc = r.get("result_count")
        if isinstance(rc, (int, float)) and not isinstance(rc, bool):
            result_sum += rc
            result_n += 1
            if rc == 0:
                zero_results += 1
        norm = _normalize(r.get("query"))
        if norm:
            query_counts[norm] += 1

    avg_result_count = (result_sum / result_n) if result_n else 0.0

    return {
        "total": total,
        "by_mode": dict(by_mode),
        "avg_result_count": avg_result_count,
        "top_queries": query_counts.most_common(10),
        "zero_result_count": zero_results,
    }
