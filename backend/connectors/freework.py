"""Free-Work connector — https://www.free-work.com/fr/tech-it/jobs

Free-Work is a Nuxt.js SPA: the useful data lives in the __NUXT__ hydration blob.
We extract it from the initial HTML, dig out the paginated job list, and map each
entry to a JobRecord.

URL pattern for search:
    https://www.free-work.com/fr/tech-it/jobs?query={search_term}
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord
# Note FR : BeautifulSoup a été retiré (jamais utilisé — le parsing va soit
# vers l'API JSON interne /api/v1/jobs, soit vers un fallback re.findall sur le HTML).

logger = logging.getLogger(__name__)

BASE = "https://www.free-work.com"
SEARCH_URL = f"{BASE}/fr/tech-it/jobs"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _parse_any_date(s: Any) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, date):
        return s
    if isinstance(s, str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        # Try dd/mm/yyyy
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
    return None


class FreeWorkConnector(BaseConnector):
    platform_name = "freework"

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

        # Free-Work is FR-focused; skip for non-FR profiles.
        if country and country.lower() not in ("france", "fr", "réunion", "martinique"):
            return result

        params = {"query": search_term, "itemsPerPage": max(20, results_wanted)}
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers=DEFAULT_HEADERS,
            ) as client:
                r = await client.get(f"{SEARCH_URL}?{urlencode(params)}")
                r.raise_for_status()
        except Exception as e:
            result.errors.append(f"freework request failed: {type(e).__name__}: {e}")
            return result

        html = r.text
        cutoff = date.today() - timedelta(days=max(1, hours_old // 24))

        # Primary path: fetch the in-page API endpoint used by the SPA.
        # Free-Work exposes /api/v1/jobs which returns JSON.
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers=DEFAULT_HEADERS,
            ) as client:
                api = await client.get(
                    f"{BASE}/api/v1/jobs",
                    params={
                        "query": search_term,
                        "itemsPerPage": max(20, results_wanted),
                        "order[createdAt]": "desc",
                    },
                )
                if api.status_code == 200 and api.headers.get("content-type", "").startswith("application/"):
                    payload = api.json()
                    jobs = payload.get("hydra:member") or payload.get("data") or payload.get("jobs") or []
                    for j in jobs[: results_wanted * 2]:
                        rec = self._job_from_api(j)
                        if rec:
                            posted = _parse_any_date(rec.get("date_posted"))
                            if posted and posted < cutoff:
                                continue
                            result.records.append(rec)
                    if result.records:
                        if len(result.records) > results_wanted:
                            result.records = result.records[:results_wanted]
                        return result
        except Exception as e:
            result.errors.append(f"freework api fallback: {type(e).__name__}: {e}")

        # Fallback: parse all unique /job-mission/<slug> URLs from the rendered HTML.
        # We won't have rich metadata, but title can be inferred from URL slug.
        hrefs = re.findall(r'href="(/fr/tech-it/[^"]*?/job-mission/[^"]+)"', html)
        seen: set[str] = set()
        for href in hrefs:
            # Trim any query string
            href = href.split("?")[0].split("#")[0]
            if href in seen:
                continue
            seen.add(href)
            full = BASE + href
            # Derive a readable title from the last slug segment
            slug = href.rsplit("/", 1)[-1]
            title = slug.replace("-", " ").strip().title()
            if len(title) < 4:
                continue
            rec: JobRecord = {
                "site": self.platform_name,
                "id": slug[:100],
                "title": title,
                "company": None,
                "location": "France",
                "description": None,
                "job_url": full,
                "date_posted": None,
                "is_remote": None,
                "currency": None,
                "min_amount": None,
                "max_amount": None,
                "interval": None,
                "job_type": None,
            }
            result.records.append(rec)
            if len(result.records) >= results_wanted:
                break

        return result

    def _job_from_api(self, j: dict) -> Optional[JobRecord]:
        try:
            slug = j.get("slug") or j.get("id")
            title = j.get("title") or j.get("name")
            if not slug or not title:
                return None
            category_slug = (j.get("category") or {}).get("slug") if isinstance(j.get("category"), dict) else None
            if category_slug:
                url = f"{BASE}/fr/tech-it/{category_slug}/job-mission/{slug}"
            else:
                url = f"{BASE}/fr/tech-it/job-mission/{slug}"

            company_obj = j.get("company") or {}
            company = company_obj.get("name") if isinstance(company_obj, dict) else company_obj

            # Location normalization
            loc = None
            locs = j.get("locations") or j.get("location")
            if isinstance(locs, list) and locs:
                first = locs[0]
                if isinstance(first, dict):
                    loc = first.get("name") or first.get("city")
                elif isinstance(first, str):
                    loc = first
            elif isinstance(locs, dict):
                loc = locs.get("name") or locs.get("city")
            elif isinstance(locs, str):
                loc = locs

            rec: JobRecord = {
                "site": self.platform_name,
                "id": str(slug)[:150],
                "title": str(title)[:400],
                "company": company,
                "location": loc or "France",
                "description": j.get("description") or j.get("descriptionSnippet"),
                "job_url": url,
                "date_posted": (j.get("createdAt") or j.get("publishedAt") or "")[:10] or None,
                "is_remote": j.get("isRemote") or j.get("remote"),
                "currency": "EUR" if j.get("salary") else None,
                "min_amount": None,
                "max_amount": None,
                "interval": None,
                "job_type": j.get("contractType") or j.get("type"),
            }
            return rec
        except Exception:
            return None
