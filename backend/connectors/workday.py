"""Workday careers — scrapes the JSON endpoints the JS frontend uses.

Workday sites have the pattern:
    https://{tenant}.wd{N}.myworkdayjobs.com/{site-id}/
where `tenant` is the company and `site-id` is something like "External" or
"Careers". The site exposes a JSON endpoint at:
    https://{host}/wday/cxs/{tenant}/{site-id}/jobs
accepting POST with a JSON body {"limit":20,"searchText":"..."}.

Configure via env var WORKDAY_SITES (semicolon-separated entries), each entry:
    company|https://tenant.wdN.myworkdayjobs.com/site-id

Example:
    WORKDAY_SITES=PaloAlto|https://paloaltonetworks.wd5.myworkdayjobs.com/PaloAltoNetworks;Fortinet|https://fortinet.wd1.myworkdayjobs.com/Fortinet_Careers
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord

logger = logging.getLogger(__name__)

# Relative time parser for Workday's "postedOn" string ("Posted 3 Days Ago", "Posted Today"…)
_POSTED_RE = re.compile(
    r"(\d+)\s+(day|days|week|weeks|month|months|hour|hours|minute|minutes)",
    re.IGNORECASE,
)


def _parse_posted_on(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    t = text.lower()
    now = datetime.now(timezone.utc)
    if "today" in t or "just posted" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = _POSTED_RE.search(t)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).rstrip("s")
    delta = {
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=n * 30),
    }.get(unit)
    if delta is None:
        return None
    return now - delta


def _parse_sites(raw: str) -> list[tuple[str, str]]:
    """Parse WORKDAY_SITES env var. Returns list of (company_display, base_url)."""
    sites: list[tuple[str, str]] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "|" not in entry:
            continue
        company, url = entry.split("|", 1)
        company = company.strip()
        url = url.strip().rstrip("/")
        if company and url:
            sites.append((company, url))
    return sites


class WorkdayConnector(BaseConnector):
    platform_name = "workday"

    def is_enabled(self) -> bool:
        return bool(self._sites())

    def _sites(self) -> list[tuple[str, str]]:
        return _parse_sites(os.getenv("WORKDAY_SITES", ""))

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
        sites = self._sites()
        if not sites:
            result.errors.append("workday: no sites configured (set WORKDAY_SITES)")
            return result

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_old)

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (JobScout)",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        ) as client:
            for company, base_url in sites:
                # Extract tenant + site-id from the URL pattern
                # https://{tenant}.wdN.myworkdayjobs.com/{site-id}
                m = re.match(
                    r"https?://([^.]+)\.wd\d+\.myworkdayjobs\.com/(.+?)/?$",
                    base_url,
                )
                if not m:
                    result.errors.append(f"workday/{company}: invalid URL {base_url}")
                    continue
                tenant, site_id = m.group(1), m.group(2)
                api_url = f"{base_url.replace(f'/{site_id}', '')}/wday/cxs/{tenant}/{site_id}/jobs"

                try:
                    r = await client.post(
                        api_url,
                        json={"limit": results_wanted, "offset": 0, "searchText": search_term},
                    )
                    if r.status_code != 200:
                        result.errors.append(
                            f"workday/{company}: HTTP {r.status_code}"
                        )
                        continue
                    payload = r.json()
                except Exception as e:
                    result.errors.append(f"workday/{company}: {type(e).__name__}: {e}")
                    continue

                for p in payload.get("jobPostings", []):
                    try:
                        posted = _parse_posted_on(p.get("postedOn"))
                        if posted is not None and posted < cutoff:
                            continue
                        ext = p.get("externalPath") or ""
                        job_url = f"{base_url}{ext}" if ext else ""
                        rec: JobRecord = {
                            "site": self.platform_name,
                            "id": f"wd_{tenant}_{p.get('bulletFields', [''])[0] if p.get('bulletFields') else ext}",
                            "title": p.get("title") or "",
                            "company": company,
                            "location": p.get("locationsText"),
                            "description": None,  # Workday requires a second fetch for description — skip
                            "job_url": job_url,
                            "date_posted": posted.strftime("%Y-%m-%d") if posted else None,
                            "is_remote": None,
                            "currency": None,
                            "min_amount": None,
                            "max_amount": None,
                            "interval": None,
                            "job_type": None,
                        }
                        if rec["title"] and rec["job_url"]:
                            result.records.append(rec)
                    except Exception as e:
                        result.errors.append(f"workday/{company} parse: {e}")

        if len(result.records) > results_wanted * 3:
            result.records = result.records[: results_wanted * 3]
        return result
