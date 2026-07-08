"""Himalayas connector — https://himalayas.app

Himalayas has a public GraphQL endpoint + a search page. For robustness we parse the
HTML search page which lists job cards with structured data (JSON-LD).

URL pattern:
    https://himalayas.app/jobs/{slug}  (search)
We actually use /remote-{category}-jobs for broader hits — fallback to /jobs search.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseConnector, ConnectorResult, JobRecord
from .utils import cutoff_ts

logger = logging.getLogger(__name__)

BASE = "https://himalayas.app"


class HimalayasConnector(BaseConnector):
    platform_name = "himalayas"

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

        # Himalayas indexes by keyword slug; we hit /jobs?search={term}
        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (JobScout)"},
            ) as client:
                r = await client.get(f"{BASE}/jobs", params={"search": search_term})
                r.raise_for_status()
        except Exception as e:
            result.errors.append(f"himalayas request failed: {type(e).__name__}: {e}")
            return result

        try:
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            result.errors.append(f"himalayas parse failed: {e}")
            return result

        cutoff = cutoff_ts(hours_old)

        # Primary path: Next.js hydration blob contains all jobs as JSON — parse <script id="__NEXT_DATA__">
        nd = soup.find("script", {"id": "__NEXT_DATA__"})
        if nd and nd.string:
            try:
                data = json.loads(nd.string)
                jobs = _find_jobs_in_next_data(data)
                for j in jobs[: results_wanted * 2]:
                    rec = _job_from_next(j, search_term)
                    if rec and rec.get("job_url"):
                        # Filter by date
                        posted = j.get("publishedDate") or j.get("pubDate") or j.get("createdAt")
                        if posted:
                            try:
                                t = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                                if t.timestamp() < cutoff:
                                    continue
                            except ValueError:
                                pass
                        result.records.append(rec)
                if result.records:
                    return result
            except Exception as e:
                logger.info("himalayas __NEXT_DATA__ parse failed: %s — falling back to HTML", e)

        # Fallback: parse job cards from the rendered HTML
        for a in soup.select('a[href^="/jobs/"]'):
            href = a.get("href") or ""
            if href.count("/") < 3:  # /jobs/<slug>
                continue
            title = a.get_text(strip=True)
            if not title or len(title) > 300:
                continue
            rec: JobRecord = {
                "site": "himalayas",
                "id": href.rsplit("/", 1)[-1],
                "title": title,
                "company": None,
                "location": "Remote",
                "description": None,
                "job_url": BASE + href,
                "date_posted": None,
                "is_remote": True,
                "currency": None,
                "min_amount": None,
                "max_amount": None,
                "interval": None,
                "job_type": None,
            }
            result.records.append(rec)

        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result


def _find_jobs_in_next_data(data: dict) -> list[dict]:
    """Walk Next.js props looking for job-like list. Returns first list found."""
    def walk(node):
        if isinstance(node, list) and node and isinstance(node[0], dict):
            sample = node[0]
            if "title" in sample and ("slug" in sample or "url" in sample):
                return node
        if isinstance(node, dict):
            for v in node.values():
                found = walk(v)
                if found:
                    return found
        return None
    return walk(data) or []


def _job_from_next(j: dict, term: str) -> Optional[JobRecord]:
    title = j.get("title") or j.get("name")
    slug = j.get("slug")
    if not title or not slug:
        return None
    company = None
    co = j.get("company") or {}
    if isinstance(co, dict):
        company = co.get("name")
    elif isinstance(co, str):
        company = co
    locations = j.get("locationRestrictions") or j.get("locations") or []
    loc_str = ", ".join(l.get("name", l) if isinstance(l, dict) else str(l) for l in locations) or "Remote"
    rec: JobRecord = {
        "site": "himalayas",
        "id": str(slug)[:200],
        "title": str(title)[:400],
        "company": company,
        "location": loc_str,
        "description": j.get("descriptionSnippet") or j.get("description"),
        "job_url": f"{BASE}/jobs/{slug}",
        "date_posted": (j.get("publishedDate") or j.get("createdAt") or "")[:10] or None,
        "is_remote": True,
        "currency": None,
        "min_amount": None,
        "max_amount": None,
        "interval": None,
        "job_type": None,
    }
    return rec
