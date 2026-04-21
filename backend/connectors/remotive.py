"""Remotive connector — https://remotive.com/api/remote-jobs

Public JSON API, no auth. Returns ~20 most recent jobs per query, filtered by search term.
All offers are by definition 100% remote.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveConnector(BaseConnector):
    platform_name = "remotive"

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

        # Remotive supports a `search` parameter but no explicit date filter.
        # We post-filter on posting date against `hours_old`.
        params = {"search": search_term, "limit": max(100, results_wanted * 2)}

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(API_URL, params=params)
                r.raise_for_status()
                payload = r.json()
        except Exception as e:
            result.errors.append(f"remotive request failed: {type(e).__name__}: {e}")
            return result

        jobs = payload.get("jobs", []) or []
        cutoff = datetime.now(timezone.utc).timestamp() - hours_old * 3600

        for j in jobs[: results_wanted * 2]:  # cap further
            try:
                pub = j.get("publication_date")
                if pub:
                    # Format: "2026-04-18T14:22:33"
                    try:
                        t = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        if t.timestamp() < cutoff:
                            continue
                    except ValueError:
                        pass

                rec: JobRecord = {
                    "site": self.platform_name,
                    "id": str(j.get("id")) if j.get("id") is not None else None,
                    "title": j.get("title") or "Unknown",
                    "company": j.get("company_name"),
                    "location": j.get("candidate_required_location") or "Remote",
                    "description": j.get("description"),
                    "job_url": j.get("url") or "",
                    "is_remote": True,
                    "job_type": j.get("job_type"),
                    "date_posted": (j.get("publication_date") or "")[:10] or None,
                    "currency": None,
                    "min_amount": None,
                    "max_amount": None,
                    "interval": None,
                }

                # Remotive exposes a `salary` string like "$120k - $160k" — leave to
                # the Claude description scoring rather than parsing here.
                if rec["job_url"] and rec["title"]:
                    result.records.append(rec)
            except Exception as e:
                result.errors.append(f"remotive parse error: {e}")

        # Cap to results_wanted
        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result
