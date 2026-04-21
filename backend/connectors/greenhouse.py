"""Greenhouse public job boards API.

Many security/SaaS companies host their careers on Greenhouse with a public JSON API:
    https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true

The connector scrapes one or more boards listed in GREENHOUSE_BOARDS env var
(comma-separated slugs), then filters client-side by search term match on the title.

Example:
    GREENHOUSE_BOARDS=sentinelone,anthropic,palo_alto_networks

Discovering slugs: look at a company career URL — if it contains
boards.greenhouse.io/{slug}, that's the slug. Many editors publish this publicly.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord

logger = logging.getLogger(__name__)

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseConnector(BaseConnector):
    platform_name = "greenhouse"

    def is_enabled(self) -> bool:
        return bool(self._slugs())

    def _slugs(self) -> list[str]:
        raw = os.getenv("GREENHOUSE_BOARDS", "") or ""
        return [s.strip() for s in raw.split(",") if s.strip()]

    async def scrape(
        self,
        *,
        search_term: str,
        location: str,
        country: str,
        hours_old: int,
        results_wanted: int,
    ) -> ConnectorResult:
        result = ConnectorResult()
        slugs = self._slugs()
        if not slugs:
            result.errors.append("greenhouse: no boards configured (set GREENHOUSE_BOARDS)")
            return result

        # Fetch all boards in parallel; filter locally on title match with search_term.
        cutoff = datetime.now(timezone.utc).timestamp() - hours_old * 3600
        term_re = re.compile(re.escape(search_term), re.IGNORECASE)

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for slug in slugs:
                try:
                    r = await client.get(
                        BOARD_URL.format(slug=slug),
                        params={"content": "true"},
                    )
                    if r.status_code != 200:
                        result.errors.append(
                            f"greenhouse/{slug}: HTTP {r.status_code}"
                        )
                        continue
                    payload = r.json()
                except Exception as e:
                    result.errors.append(f"greenhouse/{slug}: {type(e).__name__}: {e}")
                    continue

                for j in payload.get("jobs", []):
                    title = j.get("title") or ""
                    if not term_re.search(title):
                        continue
                    updated_at = j.get("updated_at")
                    if updated_at:
                        try:
                            t = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                            if t.timestamp() < cutoff:
                                continue
                        except ValueError:
                            pass

                    loc = (j.get("location") or {}).get("name")
                    rec: JobRecord = {
                        "site": self.platform_name,
                        "id": f"gh_{slug}_{j.get('id')}",
                        "title": title,
                        "company": (j.get("company") or {}).get("name") or slug.replace("_", " ").title(),
                        "location": loc,
                        "description": _strip_html(j.get("content") or ""),
                        "job_url": j.get("absolute_url") or "",
                        "date_posted": (updated_at or "")[:10] or None,
                        "is_remote": _looks_remote(loc),
                        "currency": None,
                        "min_amount": None,
                        "max_amount": None,
                        "interval": None,
                        "job_type": None,
                    }
                    if rec["job_url"]:
                        result.records.append(rec)

        if len(result.records) > results_wanted * 3:
            # Cap per-term volume (across all boards)
            result.records = result.records[: results_wanted * 3]
        return result


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    # Very light HTML → text: remove tags, collapse whitespace.
    text = _HTML_TAG_RE.sub(" ", html)
    return " ".join(text.split())


def _looks_remote(location: str | None) -> bool | None:
    if not location:
        return None
    lower = location.lower()
    if "remote" in lower or "anywhere" in lower:
        return True
    return None
