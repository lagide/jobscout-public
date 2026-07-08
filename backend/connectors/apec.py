"""APEC connector — https://www.apec.fr

Au lieu de Playwright (ancienne approche, lourde et bloquée par le mur de consentement),
on tape directement le webservice JSON interne que le front interroge :

    POST https://www.apec.fr/cms/webservices/rechercheOffre

Cet endpoint répond en JSON sans authentification (le login APEC n'est requis que
pour *postuler*), et n'est pas bloqué par DataDome depuis une IP résidentielle.
Connecteur keyword : un appel par terme de recherche.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord
from .utils import cutoff_date

logger = logging.getLogger(__name__)

BASE = "https://www.apec.fr"
WS_URL = f"{BASE}/cms/webservices/rechercheOffre"
DETAIL_URL = f"{BASE}/candidat/recherche-emploi.html/emploi/detail-offre"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": f"{BASE}/candidat/recherche-emploi.html",
}

# "65 - 85 k€ brut annuel" / "46 k€ brut annuel" / "à partir de 50 k€ ..."
_SALARY_RE = re.compile(r"(\d{1,3})\s*(?:-\s*(\d{1,3})\s*)?k€", re.IGNORECASE)


def _parse_apec_date(s: Any) -> Optional[date]:
    """'2026-06-06T20:44:35.000+0000' → date."""
    if not s or not isinstance(s, str):
        return None
    txt = s.strip().replace("Z", "+0000")
    # Normalise le fuseau '+0000' → '+00:00' pour fromisoformat.
    m = re.search(r"([+-]\d{2})(\d{2})$", txt)
    if m:
        txt = txt[: m.start()] + f"{m.group(1)}:{m.group(2)}"
    for fmt in (None, "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            if fmt is None:
                return datetime.fromisoformat(txt).date()
            return datetime.strptime(txt, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _parse_salary(txt: Any) -> tuple[Optional[float], Optional[float]]:
    if not txt or not isinstance(txt, str):
        return None, None
    m = _SALARY_RE.search(txt)
    if not m:
        return None, None
    lo = float(m.group(1)) * 1000
    hi = float(m.group(2)) * 1000 if m.group(2) else lo
    return lo, hi


class ApecConnector(BaseConnector):
    platform_name = "apec"
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

        # APEC est FR-only.
        if country and country.lower() not in ("france", "fr"):
            return result
        if not search_term:
            return result

        body = {
            "motsCles": search_term,
            "pagination": {"range": max(20, results_wanted), "startIndex": 0},
            "sorts": [{"type": "DATE", "direction": "DESCENDING"}],
            "activeFiltre": True,
        }

        try:
            async with httpx.AsyncClient(timeout=25, headers=HEADERS, follow_redirects=True) as client:
                r = await client.post(WS_URL, json=body)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            result.errors.append(f"apec request failed: {type(e).__name__}: {e}")
            return result

        offres = data.get("resultats") or []
        cutoff = cutoff_date(hours_old)

        for o in offres:
            try:
                num = o.get("numeroOffre") or str(o.get("id") or "")
                title = (o.get("intitule") or "").strip()
                if not num or not title:
                    continue

                posted = _parse_apec_date(o.get("datePublication") or o.get("dateValidation"))
                if posted and posted < cutoff:
                    continue

                lo, hi = _parse_salary(o.get("salaireTexte"))
                loc = (o.get("lieuTexte") or "France").strip()

                rec: JobRecord = {
                    "site": self.platform_name,
                    "id": str(num)[:100],
                    "title": title[:400],
                    "company": (o.get("nomCommercial") or None),
                    "location": loc,
                    "description": (o.get("texteOffre") or None),
                    "job_url": f"{DETAIL_URL}/{num}",
                    "date_posted": posted.isoformat() if posted else None,
                    "is_remote": None,  # enrichment.detect_work_mode déduira depuis le texte
                    "currency": "EUR" if (lo or hi) else None,
                    "min_amount": lo,
                    "max_amount": hi,
                    "interval": "yearly" if (lo or hi) else None,
                    "job_type": None,
                }
                result.records.append(rec)
            except Exception as e:
                result.errors.append(f"apec parse: {type(e).__name__}: {e}")

        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result
