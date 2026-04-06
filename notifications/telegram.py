"""
notifications/telegram.py — Free Telegram notifications for daily summary.
Setup (takes 2 minutes, completely free):
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Message @userinfobot on Telegram → copy your chat_id
  3. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to your .env
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
from typing import Dict

from utils.logger import get_logger

log = get_logger("notifications")


def _send(token: str, chat_id: str, text: str) -> bool:
    """Send message using plain urllib (no extra dependencies)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
        return False


def send_daily_summary(token: str, chat_id: str,
                       applied: list, skipped: list,
                       stats: Dict, llm_usage: Dict) -> bool:
    """
    Send a concise daily summary to your Telegram.
    """
    if not token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return False

    applied_count = len(applied)
    skipped_count = len(skipped)
    avg_score = (
        sum(j.get("score", 0) for j in applied) / applied_count
        if applied_count else 0
    )

    lines = [
        "<b>🤖 Naukri Agent — Daily Report</b>",
        "",
        f"✅ Applied: <b>{applied_count}</b> jobs",
        f"❌ Skipped: {skipped_count} (low score / blacklisted)",
        f"📊 Avg match score: <b>{avg_score:.0f}/100</b>",
        "",
        f"💰 LLM API calls today: {llm_usage.get('api_calls', 0)} "
        f"(cache saved {llm_usage.get('cache_hits', 0)} calls)",
        "",
    ]

    if applied:
        lines.append("<b>Jobs applied to:</b>")
        for job in applied[:8]:
            lines.append(f"  • {job['title']} @ {job['company']} [{job.get('score', 0)}]")
        if applied_count > 8:
            lines.append(f"  ... and {applied_count - 8} more")

    lines += [
        "",
        f"📈 Total applied (all time): {stats.get('total_applied', 0)}",
        f"🎯 Total interviews: {stats.get('total_interviews', 0)}",
    ]

    return _send(token, chat_id, "\n".join(lines))
