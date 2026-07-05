"""notify.py — Telegram alerting for wikiAgent maintenance jobs.

A tiny, dependency-light bridge to Telegram's Bot API. Used by the nightly
consolidation runner to surface contradictions a human should look at. Reads
credentials straight from the environment (no coupling to config.py) so it can
be dropped into any job:

    TELEGRAM_BOT_TOKEN   — bot token from @BotFather
    TELEGRAM_CHAT_ID     — chat/channel id to post into

Design principle: alerting must never crash the job it is reporting on. Every
public function swallows errors and returns a bool — send failures degrade to
False, they never raise.
"""
from __future__ import annotations
import os
from typing import List

import httpx

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text: str, timeout: float = 15) -> bool:
    """POST ``text`` to the configured Telegram chat.

    Returns True on a 2xx response. Returns False (never raises) when the bot
    token or chat id is missing, or when the HTTP request fails for any reason.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = httpx.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=timeout,
        )
        r.raise_for_status()
    except Exception:
        return False
    return True


def alert_contradictions(contradictions: List[dict]) -> bool:
    """Format consolidation contradiction pairs into a message and send it.

    Each contradiction is ``{"topic", "a", "b", "reason"}`` as produced by
    ``consolidation.consolidate(contradiction_check=True)``. Returns False
    without sending when the list is empty (nothing to report).
    """
    if not contradictions:
        return False

    n = len(contradictions)
    lines = [f"⚠️ wikiAgent: {n} contradiction(s) detected during consolidation", ""]
    for i, c in enumerate(contradictions, 1):
        topic = c.get("topic", "") or "(no topic)"
        a = c.get("a", "?")
        b = c.get("b", "?")
        reason = c.get("reason", "") or "(no reason given)"
        lines.append(f"{i}. [{topic}] {a} ↔ {b}")
        lines.append(f"   {reason}")
    return send_telegram("\n".join(lines))
