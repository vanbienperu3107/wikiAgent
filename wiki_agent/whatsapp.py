"""whatsapp.py — WhatsApp messages → knowledge (Phase 3, source #3).

Pipeline for one buffered thread (buffering itself happens client-side in the
Baileys agent; the server receives a batch of messages for one thread):

    1. contact blacklist + privacy filter   — deterministic, drop before any LLM
    2. classify()   — cheap Qwen 7B: keep=true/false + topic (gate)
    3. extract      — only if keep: reuse knowledge_extractor (Haiku)
    4. store        — source="whatsapp", deterministic content-hash dedup

Design principles (Roadmap 3.0):
    - Cheap classifier gates the expensive extractor (cost control).
    - LLM only for judgment (keep? / what is a fact?); routing/filter/dedup are code.
    - Privacy-first: blacklist + keyword filter run before anything leaves the box.
"""
from __future__ import annotations
import json
import re
from typing import Iterable, List, Optional

import httpx

from . import config, knowledge_extractor

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Placeholder topics the extractor emits when it can't infer a real one — only
# these get rebiased to the classifier's topic.
_GENERIC_TOPICS = {"", "misc", "general", "other", "unknown", "chat", "whatsapp", "n/a"}

CLASSIFY_PROMPT = """Bạn phân loại một đoạn hội thoại WhatsApp có đáng lưu vào wiki kỹ thuật không.

Trả về DUY NHẤT một object JSON, không giải thích:
{{"keep": true/false, "topic": "domain/sub hoặc null"}}

keep=true CHỈ khi đoạn chat chứa thông tin kỹ thuật có giá trị tái sử dụng
(cấu hình, cách xử lý lỗi, quyết định, thông số, quy trình). Chat xã giao,
hỏi thăm, cảm xúc, thông tin cá nhân → keep=false.

Hội thoại:
{transcript}
"""


# ============================================================
# 1. Deterministic gate: blacklist + privacy
# ============================================================

def is_blacklisted(sender: Optional[str]) -> bool:
    """True if the sender matches any blacklisted contact fragment."""
    if not sender:
        return False
    low = sender.lower()
    return any(frag in low for frag in config.WHATSAPP_CONTACT_BLACKLIST)


def _prefilter(messages: Iterable[dict]) -> List[dict]:
    """Drop messages containing sensitive keywords (reuse the shared filter)."""
    return knowledge_extractor.privacy_filter(messages)


# ============================================================
# 2. Classifier (cheap LLM gate)
# ============================================================

def _coerce_keep(v) -> bool:
    """Coerce the model's `keep` to a bool. Fail CLOSED (keep=false) on doubt —
    note `bool("false")` is True in Python, so a stringified 'false' must be
    handled explicitly or the cheap cost-gate leaks open."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "keep", "y")
    return False


def _parse_classification(raw: str) -> dict:
    """Parse the {keep, topic} JSON object; default to keep=false on any doubt."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"keep": False, "topic": None}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"keep": False, "topic": None}
    keep = _coerce_keep(data.get("keep", False))
    topic = data.get("topic")
    if not isinstance(topic, str) or not topic.strip() or topic.strip().lower() == "null":
        topic = None
    return {"keep": keep, "topic": topic}


def _call_classifier(transcript: str, timeout: float = 30) -> str:
    """Call Qwen on DeepInfra, or OpenAI as fallback. Both OpenAI-compatible."""
    prompt = CLASSIFY_PROMPT.format(transcript=transcript)
    if config.DEEPINFRA_API_KEY:
        url, key, model = (
            config.DEEPINFRA_URL,
            config.DEEPINFRA_API_KEY,
            config.WHATSAPP_CLASSIFIER_MODEL,
        )
    elif config.OPENAI_API_KEY:
        url, key, model = OPENAI_URL, config.OPENAI_API_KEY, config.OPENAI_EXTRACTOR_MODEL
    else:
        raise RuntimeError("No DEEPINFRA_API_KEY or OPENAI_API_KEY set")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def classify(messages: Iterable[dict]) -> dict:
    """Classify a thread: {keep: bool, topic: str|None}. Empty input → keep=false."""
    msgs = list(messages)
    if not msgs:
        return {"keep": False, "topic": None}
    transcript = knowledge_extractor._format_transcript(msgs)
    return _parse_classification(_call_classifier(transcript))


# ============================================================
# 3 + 4. Full pipeline
# ============================================================

def process_thread(
    messages: Iterable[dict],
    thread_id: Optional[str] = None,
    sender: Optional[str] = None,
    backend: Optional[str] = None,
) -> dict:
    """Run the full WhatsApp pipeline for one buffered thread.

    Returns {blacklisted, kept, stored, topic}. Storage dedups by content hash
    (via knowledge_extractor.store_facts), so re-sending a thread is idempotent.
    """
    if is_blacklisted(sender):
        return {"blacklisted": True, "kept": False, "stored": 0, "topic": None}

    safe = _prefilter(messages)
    if not safe:
        return {"blacklisted": False, "kept": False, "stored": 0, "topic": None}

    verdict = classify(safe)
    if not verdict["keep"]:
        return {"blacklisted": False, "kept": False, "stored": 0, "topic": verdict["topic"]}

    facts = knowledge_extractor.extract_facts(safe, backend=backend)
    # Bias facts toward the classifier's topic ONLY when the extractor left a
    # generic placeholder — never overwrite a specific topic just because it
    # happens to be flat (e.g. "kubernetes" must not become "OCS/charging").
    if verdict["topic"]:
        for f in facts:
            if f["topic"].strip().lower() in _GENERIC_TOPICS:
                f["topic"] = verdict["topic"]
    stored = knowledge_extractor.store_facts(facts, source="whatsapp", ref=thread_id)
    return {
        "blacklisted": False,
        "kept": True,
        "stored": stored,
        "topic": verdict["topic"],
    }
