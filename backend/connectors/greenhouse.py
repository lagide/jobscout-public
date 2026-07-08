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
from .utils import cutoff_ts

logger = logging.getLogger(__name__)

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseConnector(BaseConnector):
    platform_name = "greenhouse"

    def is_enabled(self) -> bool:
        return bool(self._slugs())

    def _slugs(self) -> list[str]:
        # Page Paramètres > connecteurs (env GREENHOUSE_BOARDS = défaut au premier boot).
        from settings import get
        return list(get().connectors.greenhouse_boards)

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
        cutoff = cutoff_ts(hours_old)
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
                    # Filtre géo : les boards Greenhouse (Okta, Datadog…) sont
                    # mondiaux. On ne garde que France + remote Europe/EMEA,
                    # sinon des TAM NYC/Tokyo/Bengaluru polluent le profil France
                    # (la region est ensuite forcée à FR par le profil de scrape).
                    if not _location_targeted(loc):
                        continue
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


# Marqueurs de localisation France / Europe — un poste Greenhouse n'est conservé
# que si sa location contient l'un d'eux (ou est un remote explicitement EU/EMEA).
_FR_LOCATION_MARKERS: tuple[str, ...] = (
    "france", "paris", "lyon", "marseille", "toulouse", "bordeaux", "lille",
    "nantes", "strasbourg", "rennes", "nice", "grenoble", "sophia antipolis",
    "île-de-france", "ile-de-france",
)
_EU_LOCATION_MARKERS: tuple[str, ...] = (
    "emea", "europe", "european union", " eu ",
    "united kingdom", "london", "ireland", "dublin",
    "germany", "berlin", "munich", "frankfurt",
    "netherlands", "amsterdam", "belgium", "brussels",
    "spain", "madrid", "barcelona", "italy", "milan", "rome",
    "switzerland", "zurich", "geneva", "luxembourg",
    "portugal", "lisbon", "poland", "warsaw", "sweden", "stockholm",
)


def _location_targeted(location: str | None) -> bool:
    """True si la location vaut la peine d'être stockée pour le profil France.

    Garde France + remote Europe/EMEA. Rejette le reste (US, Canada, APAC,
    LATAM…). Une location absente est rejetée : les boards Greenhouse exposent
    presque toujours une localisation, donc l'absence est suspecte.
    """
    if not location:
        return False
    lower = " " + location.lower() + " "
    if any(m in lower for m in _FR_LOCATION_MARKERS):
        return True
    if any(m in lower for m in _EU_LOCATION_MARKERS):
        return True
    return False
