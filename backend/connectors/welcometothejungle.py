"""Welcome to the Jungle connector — https://www.welcometothejungle.com

Les pages HTML de WTTJ sont protégées par DataDome (202 corps vide), mais le
moteur de recherche s'appuie sur Algolia, dont l'endpoint n'est PAS derrière
DataDome. La clé de recherche (read-only) est restreinte par Referer : il suffit
d'envoyer `Referer: https://www.welcometothejungle.com/` pour interroger l'index
jobs directement en httpx — sans navigateur ni Playwright.

App ID / clé sont surchargeables via .env (WTTJ_ALGOLIA_APP_ID / WTTJ_ALGOLIA_KEY)
au cas où WTTJ ferait tourner la clé publique. Connecteur keyword.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord
from .utils import cutoff_date

logger = logging.getLogger(__name__)

BASE = "https://www.welcometothejungle.com"

# Défauts publics (clé search restreinte par Referer, extraite du site).
# Surchargeables via la page Paramètres (secrets WTTJ_ALGOLIA_*) puis le .env.
_DEFAULT_APP_ID = "CSEKHVMS53"
_DEFAULT_API_KEY = "4bd8f6215d0cc52b26430765769e65a0"
JOBS_INDEX = os.getenv("WTTJ_ALGOLIA_INDEX", "wk_cms_jobs_production")


def algolia_credentials() -> tuple[str, str]:
    """(app_id, api_key) effectifs — secrets UI > .env > défauts publics."""
    from settings import get_secret
    app_id = get_secret("WTTJ_ALGOLIA_APP_ID") or _DEFAULT_APP_ID
    api_key = get_secret("WTTJ_ALGOLIA_KEY") or _DEFAULT_API_KEY
    return app_id, api_key


def algolia_url() -> str:
    app_id, _ = algolia_credentials()
    return f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{JOBS_INDEX}/query"


def algolia_headers() -> dict[str, str]:
    app_id, api_key = algolia_credentials()
    return {
        "x-algolia-application-id": app_id,
        "x-algolia-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        # La clé est restreinte par Referer — indispensable.
        "Referer": "https://www.welcometothejungle.com/",
        "Origin": "https://www.welcometothejungle.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

_REMOTE_MAP = {"fulltime": True, "full": True}
_PERIOD_MAP = {"yearly": "yearly", "monthly": "monthly", "daily": "daily", "hourly": "hourly"}


def _parse_date(s: Any) -> Optional[date]:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _office_location(hit: dict) -> str:
    offices = hit.get("offices") or []
    if offices and isinstance(offices[0], dict):
        o = offices[0]
        parts = [o.get("city"), o.get("country")]
        loc = ", ".join(p for p in parts if p)
        if loc:
            return loc
    off = hit.get("office")
    if isinstance(off, dict):
        return off.get("city") or off.get("country") or "France"
    return "France"


class WelcomeToTheJungleConnector(BaseConnector):
    platform_name = "wttj"
    uses_search_terms = True

    async def scrape(
        self,
        *,
        search_term: Optional[str],
        location: str,
        country: str,
        hours_old: int,
        results_wanted: int,
    ) -> ConnectorResult:
        result = ConnectorResult()

        if country and country.lower() not in ("france", "fr"):
            return result
        if not search_term:
            return result

        body = {
            "query": search_term,
            "facetFilters": [["offices.country_code:FR"]],
            "hitsPerPage": max(20, results_wanted),
            "page": 0,
        }

        try:
            async with httpx.AsyncClient(timeout=20, headers=algolia_headers()) as client:
                r = await client.post(algolia_url(), json=body)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            result.errors.append(f"wttj algolia request failed: {type(e).__name__}: {e}")
            return result

        hits = data.get("hits") or []
        cutoff = cutoff_date(hours_old)
        seen: set[str] = set()

        for hit in hits:
            try:
                if not isinstance(hit, dict):
                    continue
                title = (hit.get("name") or "").strip()
                slug = hit.get("slug")
                org = hit.get("organization") or {}
                org_slug = org.get("slug") if isinstance(org, dict) else None
                company = org.get("name") if isinstance(org, dict) else None
                if not title or not slug:
                    continue

                job_url = (
                    f"{BASE}/fr/companies/{org_slug}/jobs/{slug}" if org_slug
                    else f"{BASE}/fr/jobs/{slug}"
                )
                if job_url in seen:
                    continue
                seen.add(job_url)

                posted = _parse_date(hit.get("published_at"))
                if posted and posted < cutoff:
                    continue

                cur = hit.get("salary_currency")
                lo = hit.get("salary_minimum")
                hi = hit.get("salary_maximum")
                period = _PERIOD_MAP.get(str(hit.get("salary_period") or "").lower())

                rec: JobRecord = {
                    "site": self.platform_name,
                    "id": str(hit.get("reference") or slug)[:100],
                    "title": title[:400],
                    "company": company,
                    "location": _office_location(hit),
                    "description": None,  # pas dans le hit Algolia → enrichi côté scoring
                    "job_url": job_url,
                    "date_posted": posted.isoformat() if posted else None,
                    "is_remote": _REMOTE_MAP.get(str(hit.get("remote") or "").lower()),
                    "currency": cur if (lo or hi) else None,
                    "min_amount": float(lo) if lo else None,
                    "max_amount": float(hi) if hi else None,
                    "interval": period if (lo or hi) else None,
                    "job_type": hit.get("contract_type"),
                }
                result.records.append(rec)
            except Exception as e:
                result.errors.append(f"wttj parse: {type(e).__name__}: {e}")

        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result
