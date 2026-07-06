"""End-to-end integration test against a REAL Qdrant.

Only the OpenAI embedding call is stubbed (see conftest's deterministic
``fake_embed``). Everything else — qdrant_helper, rag, consolidation,
fact_crud — runs unmodified against a live vector DB, proving the round-trip
actually works.

The module auto-skips when no Qdrant is reachable.
"""
from __future__ import annotations

from collections import Counter

import httpx

from wiki_agent import (
    config,
    consolidation,
    embeddings,
    fact_crud,
    knowledge_extractor,
    qdrant_helper,
    rag,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _scroll_with_vectors() -> list[dict]:
    """Fetch every point (payload + vector) via a real Qdrant scroll."""
    points: list[dict] = []
    offset = None
    while True:
        body: dict = {"limit": 256, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        r = httpx.post(
            f"{config.QDRANT_URL}/collections/{config.WIKI_COLLECTION}/points/scroll",
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        result = r.json()["result"]
        points.extend(result.get("points", []))
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return points


def _seed():
    """Populate the collection and return the seeded point ids by label."""
    conv_facts = [
        {
            "topic": "OCS/charging",
            "content": "The OCS charging rate is 50 MB per day for prepaid users",
            "tags": ["OCS", "charging"],
            "confidence": 0.9,
        },
        {
            "topic": "deploy/ci",
            "content": "The CI pipeline deploys to staging on every merge to main",
            "tags": ["ci"],
            "confidence": 0.8,
        },
        {
            "topic": "network/dns",
            "content": "DNS resolution uses the internal resolver at 10 0 0 53",
            "tags": ["dns"],
            "confidence": 0.7,
        },
    ]
    stored = knowledge_extractor.store_facts(conv_facts, source="conversation")
    assert stored == 3

    billing_id = fact_crud.add_fact(
        topic="OCS/billing",
        content="Billing cycles reset at midnight UTC on the first of each month",
        tags=["billing"],
        confidence=0.95,
    )

    # Two near-identical facts on one exclusive topic -> a duplicate group.
    dup_id_a = fact_crud.add_fact(
        topic="cache/redis",
        content="Redis cache TTL is set to 3600 seconds for session keys",
        confidence=0.6,
    )
    dup_id_b = fact_crud.add_fact(
        topic="cache/redis",
        content="Redis cache TTL is set to 3600 seconds for session keys by default",
        confidence=0.9,
    )

    return {
        "ocs": knowledge_extractor._point_id(conv_facts[0]["content"], conv_facts[0]["topic"]),
        "ci": knowledge_extractor._point_id(conv_facts[1]["content"], conv_facts[1]["topic"]),
        "dns": knowledge_extractor._point_id(conv_facts[2]["content"], conv_facts[2]["topic"]),
        "billing": billing_id,
        "dup_a": dup_id_a,
        "dup_b": dup_id_b,
    }


# --------------------------------------------------------------------------
# the end-to-end test
# --------------------------------------------------------------------------

def test_full_round_trip():
    ids = _seed()

    # ---- 1. qdrant_helper.search returns stored facts -------------------
    qvec = embeddings.embed("OCS charging rate per day for prepaid users")
    hits = qdrant_helper.search(qvec, limit=10)
    hit_ids = {h["id"] for h in hits}
    assert ids["ocs"] in hit_ids
    # Semantically closest fact ranks first (search returns score-desc).
    assert hits[0]["id"] == ids["ocs"]

    # topic filter narrows results to exactly one topic
    filtered = qdrant_helper.search(qvec, limit=10, topic="deploy/ci")
    assert {h["payload"]["topic"] for h in filtered} == {"deploy/ci"}

    # ---- 2. rag.hybrid_search ranks the on-topic fact first -------------
    ranked = rag.hybrid_search(
        "OCS charging rate 50 MB per day prepaid", limit=5
    )
    assert ranked, "hybrid_search returned nothing"
    assert ranked[0]["id"] == ids["ocs"]
    assert ranked[0]["topic"] == "OCS/charging"
    assert all(r["rrf_score"] > 0 for r in ranked)

    # ---- 3. topic aggregation via scroll_topics -------------------------
    points = qdrant_helper.scroll_topics()
    topic_counts = Counter(p["payload"]["topic"] for p in points)
    assert topic_counts["OCS/charging"] == 1
    assert topic_counts["cache/redis"] == 2
    assert set(topic_counts) == {
        "OCS/charging",
        "deploy/ci",
        "network/dns",
        "OCS/billing",
        "cache/redis",
    }

    # ---- 4. consolidation groups the near-duplicates --------------------
    vec_points = _scroll_with_vectors()
    groups = consolidation.find_duplicate_groups(vec_points, threshold=0.8)
    assert len(groups) == 1
    assert set(groups[0]) == {ids["dup_a"], ids["dup_b"]}

    # ---- 5. delete_fact removes a point ---------------------------------
    fact_crud.delete_fact(ids["ci"])
    remaining = {p["id"] for p in qdrant_helper.scroll_topics()}
    assert ids["ci"] not in remaining
    assert ids["ocs"] in remaining  # unrelated points untouched

    ci_vec = embeddings.embed(
        "The CI pipeline deploys to staging on every merge to main"
    )
    assert ids["ci"] not in {h["id"] for h in qdrant_helper.search(ci_vec, limit=10)}
