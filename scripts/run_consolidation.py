"""Nightly consolidation runner (Phase 5).

Scrolls the whole `wiki_knowledge` collection *with vectors*, then runs
`consolidation.consolidate`. Dry-run by default; set APPLY=true to actually mark
duplicates obsolete (facts are never deleted). Intended for a nightly cron
(e.g. 2AM Lima).

    python -m scripts.run_consolidation           # dry-run, prints the plan
    APPLY=true python -m scripts.run_consolidation # applies status changes
"""
from __future__ import annotations
import os
import json
import httpx

from wiki_agent import config, consolidation, notify


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.QDRANT_API_KEY:
        h["api-key"] = config.QDRANT_API_KEY
    return h


def fetch_all_points(page_size: int = 256) -> list[dict]:
    """Scroll every point WITH its vector (consolidation needs vectors)."""
    url, coll = config.QDRANT_URL, config.WIKI_COLLECTION
    points: list[dict] = []
    offset = None
    while True:
        body: dict = {"limit": page_size, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        r = httpx.post(
            f"{url}/collections/{coll}/points/scroll",
            json=body, headers=_headers(), timeout=30,
        )
        r.raise_for_status()
        res = r.json()["result"]
        for p in res.get("points", []):
            points.append({"id": p["id"], "vector": p.get("vector"), "payload": p.get("payload", {})})
        offset = res.get("next_page_offset")
        if offset is None:
            break
    return points


def main() -> None:
    apply = os.environ.get("APPLY", "false").lower() == "true"
    contradiction = os.environ.get("CHECK_CONTRADICTIONS", "false").lower() == "true"
    points = fetch_all_points()
    summary = consolidation.consolidate(
        points, contradiction_check=contradiction, apply=apply
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    # Alert on contradictions (no-op if none found or Telegram creds absent).
    contradictions = summary.get("contradictions") or []
    if contradictions:
        notify.alert_contradictions(contradictions)


if __name__ == "__main__":
    main()
