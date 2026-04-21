"""Telegram notifier — called after each scrape to announce high-scoring new offers.

Silent no-op when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are unset. Errors are logged
but never raised — notifications are best-effort.

Env vars:
    TELEGRAM_BOT_TOKEN   — bot token from @BotFather.
    TELEGRAM_CHAT_ID     — numeric chat id (run GET /telegram/test after setting to confirm).
    TELEGRAM_MIN_SCORE   — default 7. Only offers with relevance_score >= this trigger a ping.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

import httpx

from models import Job

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _env(name: str) -> Optional[str]:
    val = os.getenv(name)
    return val.strip() if val and val.strip() else None


def is_configured() -> bool:
    return bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"))


def get_min_score() -> float:
    try:
        return float(os.getenv("TELEGRAM_MIN_SCORE", "7"))
    except ValueError:
        return 7.0


async def send_markdown(text: str) -> bool:
    """Send a Markdown-formatted message. Returns True on success."""
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = TELEGRAM_API.format(token=token)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
            )
            if r.status_code != 200:
                logger.warning(
                    "Telegram send failed (%d): %s", r.status_code, r.text[:200]
                )
                return False
            return True
    except Exception as e:
        logger.warning("Telegram send errored: %s", e)
        return False


def _format_jobs_message(jobs: list[Job], total_new: int, profile: Optional[str]) -> str:
    """Compose the notification body. Top offer highlighted, others in a list."""
    if not jobs:
        return ""
    # Sort by score desc (jobs passed in already filtered; we just re-order)
    jobs = sorted(jobs, key=lambda j: j.relevance_score or 0, reverse=True)
    top = jobs[0]
    prof_line = f" ({profile})" if profile else ""
    lines = [
        f"🔔 *{total_new} nouvelle(s) offre(s) pertinente(s) détectée(s)*{prof_line}",
        "",
        f"🏆 *Meilleure offre* — score {top.relevance_score:.1f}/10",
        f"*{_md_escape(top.title)}*",
        f"🏢 {_md_escape(top.company or '—')} · 📍 {_md_escape(top.location or '—')}",
        f"🔗 [Voir l'offre]({top.job_url})",
    ]
    if len(jobs) > 1:
        lines.append("")
        lines.append("*Autres offres pertinentes :*")
        for j in jobs[1:6]:  # cap at 5 extra
            score_str = f"{j.relevance_score:.1f}" if j.relevance_score is not None else "—"
            lines.append(
                f"• [{score_str}] {_md_escape(j.title)} — {_md_escape(j.company or '?')}"
            )
        if len(jobs) > 6:
            lines.append(f"… et {len(jobs) - 6} autre(s).")
    return "\n".join(lines)


def _md_escape(s: str) -> str:
    """Escape Markdown special chars for Telegram's old Markdown mode."""
    if not s:
        return ""
    for ch in ["*", "_", "`", "["]:
        s = s.replace(ch, f"\\{ch}")
    return s


async def notify_new_jobs(
    jobs: Iterable[Job], profile: Optional[str] = None
) -> bool:
    """Fire a notification for high-scoring new jobs. Returns True if sent."""
    if not is_configured():
        return False
    threshold = get_min_score()
    relevant = [
        j for j in jobs
        if j.relevance_score is not None and j.relevance_score >= threshold
    ]
    if not relevant:
        return False
    text = _format_jobs_message(relevant, len(relevant), profile)
    return await send_markdown(text)
