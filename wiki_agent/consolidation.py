"""consolidation.py — Phase 5 multi-source consolidation.

Once several sources (conversation / file / whatsapp) write into the same
`wiki_knowledge` collection, the same fact tends to land more than once and,
occasionally, two facts about one topic disagree. This module reconciles that
WITHOUT ever deleting knowledge:

    1. find_duplicate_groups()  — deterministic near-duplicate grouping (code)
    2. detect_contradictions()  — Claude Haiku flags contradictory pairs (LLM judgment)
    3. set_status()             — mark a point active/obsolete (payload only)
    4. consolidate()            — orchestrate: keep the best of each dup group,
                                  mark the rest obsolete (never delete)

Design principles (Roadmap 3.0):
    - LLM is used ONLY for judgment (which facts contradict).
    - Grouping / ranking / status routing are deterministic code.
    - Nothing is ever deleted — superseded facts are flagged, not removed.
"""
from __future__ import annotations
import json
import math
import re
from typing import List, Optional, Sequence

import httpx

from . import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

CONTRADICTION_PROMPT = """Dưới đây là danh sách các FACT (đã đánh số) về cùng một chủ đề.
Hãy tìm những CẶP fact MÂU THUẪN trực tiếp với nhau (không thể cùng đúng).

Trả về DUY NHẤT một mảng JSON hợp lệ, không kèm giải thích:
[
  {{"a": <index>, "b": <index>, "reason": "vì sao mâu thuẫn"}}
]

Trong đó a, b là số thứ tự (index) của hai fact mâu thuẫn.
BỎ QUA các cặp chỉ khác cách diễn đạt nhưng cùng ý (đó là trùng lặp, không phải mâu thuẫn).
Nếu không có cặp nào mâu thuẫn, trả về [].

Các fact:
{facts}
"""


# ============================================================
# 1. Similarity + duplicate grouping (deterministic, no network)
# ============================================================

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors. 0.0 when either has zero magnitude."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def find_duplicate_groups(points: List[dict], threshold: float = 0.95) -> List[List]:
    """Group near-duplicate points by topic.

    Input: list of points, each ``{"id", "vector", "payload": {"topic", ...}}``.
    Points are grouped by exact ``topic``; within a topic, any two points whose
    cosine similarity >= ``threshold`` are unioned into the same group.

    Returns a list of groups, each a list of point ids, including only groups
    with 2+ members. Deterministic and network-free — ids keep input order.
    """
    # Bucket points by topic, preserving input order for determinism.
    buckets: "dict[str, List[dict]]" = {}
    for p in points:
        topic = (p.get("payload") or {}).get("topic", "")
        buckets.setdefault(topic, []).append(p)

    groups: List[List] = []
    for topic in buckets:
        members = buckets[topic]
        n = len(members)
        # Union-find over the members of this topic.
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                # Attach the larger root index under the smaller → stable order.
                parent[max(ri, rj)] = min(ri, rj)

        for i in range(n):
            vi = members[i].get("vector") or []
            for j in range(i + 1, n):
                vj = members[j].get("vector") or []
                if cosine(vi, vj) >= threshold:
                    union(i, j)

        # Collect members per root, keeping input order.
        by_root: "dict[int, List]" = {}
        for i in range(n):
            by_root.setdefault(find(i), []).append(members[i]["id"])
        for root in sorted(by_root):
            ids = by_root[root]
            if len(ids) >= 2:
                groups.append(ids)
    return groups


# ============================================================
# 2. Contradiction detection (LLM judgment only)
# ============================================================

def _parse_pairs(raw: str) -> List[dict]:
    """Robustly parse the JSON array of contradiction pairs the model returns.

    Models sometimes wrap JSON in prose or ```json fences — extract the first
    top-level array. Invalid output yields an empty list rather than raising.
    """
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    pairs: List[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            a = int(item["a"])
            b = int(item["b"])
        except (KeyError, TypeError, ValueError):
            continue
        reason = str(item.get("reason", "")).strip()
        pairs.append({"a": a, "b": b, "reason": reason})
    return pairs


def _detect_anthropic(facts_block: str, timeout: float = 60) -> str:
    payload = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": CONTRADICTION_PROMPT.format(facts=facts_block)}
        ],
    }
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    r = httpx.post(ANTHROPIC_URL, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def detect_contradictions(facts: List[dict], timeout: float = 60) -> List[dict]:
    """Ask Claude Haiku which facts (same topic) contradict each other.

    facts: list of ``{"topic", "content"}``. Returns a list of
    ``{"a": <index>, "b": <index>, "reason": "..."}`` referencing the input
    positions. Fewer than two facts short-circuits to []. Model output is
    parsed defensively — malformed responses yield [] rather than raising.
    """
    if len(facts) < 2:
        return []
    numbered = "\n".join(
        f'{i}. [{f.get("topic", "")}] {f.get("content", "")}'
        for i, f in enumerate(facts)
    )
    raw = _detect_anthropic(numbered, timeout=timeout)
    pairs = _parse_pairs(raw)
    # Keep only pairs whose indices are valid and distinct.
    n = len(facts)
    return [
        p for p in pairs
        if 0 <= p["a"] < n and 0 <= p["b"] < n and p["a"] != p["b"]
    ]


# ============================================================
# 3. Status flag (payload only — never deletes)
# ============================================================

def _qdrant_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.QDRANT_API_KEY:
        h["api-key"] = config.QDRANT_API_KEY
    return h


def set_status(point_id, status: str, timeout: float = 30) -> None:
    """Set the ``status`` payload field on a single point (e.g. active/obsolete).

    Uses Qdrant's set-payload REST endpoint — a payload merge that leaves the
    vector and every other field intact. This NEVER deletes the fact; a
    superseded point stays searchable but flagged.
    """
    body = {"payload": {"status": status}, "points": [point_id]}
    r = httpx.post(
        f"{config.QDRANT_URL}/collections/{config.WIKI_COLLECTION}/points/payload?wait=true",
        json=body,
        headers=_qdrant_headers(),
        timeout=timeout,
    )
    r.raise_for_status()


# ============================================================
# 4. Orchestrator
# ============================================================

def _rank_key(point: dict):
    """Sort key for picking the survivor of a duplicate group.

    Highest confidence wins; ties broken by newest ``updated_at`` (then
    ``created_at``). Returns a tuple where larger is better.
    """
    payload = point.get("payload") or {}
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    updated = str(payload.get("updated_at", "") or payload.get("created_at", ""))
    return (confidence, updated)


def consolidate(
    points: List[dict],
    contradiction_check: bool = False,
    threshold: float = 0.95,
    apply: bool = False,
) -> dict:
    """Reconcile near-duplicate facts; optionally flag contradictions.

    For each duplicate group the highest-confidence (tie: newest) point is kept
    active and the rest are planned for ``status="obsolete"``. ``apply`` gates
    only the *writes*: when False (the default) no ``set_status`` calls are made
    (a dry run for the dedup plan). NOTE: ``contradiction_check=True`` still
    issues (billed, read-only) LLM calls regardless of ``apply`` — it is opt-in
    analysis, not a write.

    Returns a summary dict::

        {"groups": <n groups>,
         "obsoleted": [point ids planned/set obsolete],
         "kept": [surviving point ids],
         "contradictions": [...]}   # only when contradiction_check=True
    """
    by_id = {p["id"]: p for p in points}
    groups = find_duplicate_groups(points, threshold=threshold)

    kept: List = []
    obsoleted: List = []
    for ids in groups:
        members = [by_id[i] for i in ids]
        survivor = max(members, key=_rank_key)
        kept.append(survivor["id"])
        for m in members:
            if m["id"] == survivor["id"]:
                continue
            obsoleted.append(m["id"])

    if apply:
        for pid in kept:
            set_status(pid, "active")
        for pid in obsoleted:
            set_status(pid, "obsolete")

    summary: dict = {
        "groups": len(groups),
        "kept": kept,
        "obsoleted": obsoleted,
        "applied": apply,
    }

    if contradiction_check:
        contradictions: List[dict] = []
        # Check contradictions per topic across all points in that topic.
        by_topic: "dict[str, List[dict]]" = {}
        for p in points:
            topic = (p.get("payload") or {}).get("topic", "")
            by_topic.setdefault(topic, []).append(p)
        for topic in by_topic:
            group = by_topic[topic]
            # Skip topicless buckets — unrelated facts all land in "" and must
            # not be compared as if they share a subject — and singletons.
            if not topic.strip() or len(group) < 2:
                continue
            facts = [
                {"topic": topic, "content": (p.get("payload") or {}).get("content", "")}
                for p in group
            ]
            for pair in detect_contradictions(facts):
                a, b = pair.get("a"), pair.get("b")
                if not (isinstance(a, int) and isinstance(b, int)
                        and 0 <= a < len(group) and 0 <= b < len(group)):
                    continue  # ignore out-of-range indices from the model
                contradictions.append(
                    {
                        "topic": topic,
                        "a": group[a]["id"],
                        "b": group[b]["id"],
                        "reason": pair.get("reason"),
                    }
                )
        summary["contradictions"] = contradictions

    return summary
