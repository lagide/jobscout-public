"""Cadremploi connector — https://www.cadremploi.fr

Pages protégées par DataDome (groupe Figaro). Une requête avec impersonation TLS
Chrome (curl_cffi) depuis une IP résidentielle passe les contrôles passifs. On
parse ensuite les cartes Vue.js server-rendered (div.job-posting-card).

⚠️ Le paramètre de recherche par mot-clé est `motscles` (pluriel) ; tout autre nom
(motscle, keywords, q...) est ignoré et renvoie les 22k offres non filtrées.

Requiert curl_cffi. Connecteur keyword.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .base import BaseConnector, ConnectorResult, JobRecord
from .utils import cutoff_date

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except Exception:  # pragma: no cover
    _CFFI_OK = False

BASE = "https://www.cadremploi.fr"
SEARCH = BASE + "/emploi/liste_offres"
IMPERSONATE = "chrome"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

_CONTRACTS = {"cdi", "cdd", "interim", "intérim", "alternance", "stage",
             "freelance", "indépendant", "independant", "apprentissage"}
_SALARY = re.compile(r"(\d+)\s*k€(?:\s*[-–]\s*(\d+)\s*k€)?", re.I)
_DAYS = re.compile(r"il y a (\d+)\s*jour", re.I)
_OFFREID = re.compile(r"offreId=(\d+)")


def _fetch(url: str) -> str:
    r = cffi_requests.get(url, headers=HEADERS, impersonate=IMPERSONATE,
                          timeout=25, allow_redirects=True)
    r.raise_for_status()
    return r.text


def _classify_badges(card) -> tuple[Optional[str], Optional[str], Optional[float], Optional[float]]:
    """Retourne (location, contract, salary_min, salary_max) depuis les badges."""
    loc = None
    contract = None
    lo = hi = None
    for span in card.select(".d-flex.flex-wrap.ga-2 .text-grey-800"):
        txt = span.get_text(strip=True)
        if not txt:
            continue
        low = txt.lower()
        sal = _SALARY.search(txt)
        if sal:
            lo = float(sal.group(1)) * 1000
            hi = float(sal.group(2)) * 1000 if sal.group(2) else lo
        elif low in _CONTRACTS:
            contract = txt
        elif loc is None:
            loc = txt
    return loc, contract, lo, hi


class CadremploiConnector(BaseConnector):
    platform_name = "cadremploi"
    uses_search_terms = True

    def is_enabled(self) -> bool:
        return _CFFI_OK

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
        if not _CFFI_OK:
            result.errors.append("cadremploi: curl_cffi indisponible — connecteur désactivé")
            return result
        if country and country.lower() not in ("france", "fr"):
            return result
        if not search_term:
            return result

        url = f"{SEARCH}?{urlencode({'motscles': search_term})}"
        try:
            html = await asyncio.to_thread(_fetch, url)
        except Exception as e:
            result.errors.append(f"cadremploi request failed: {type(e).__name__}: {e}")
            return result

        soup = BeautifulSoup(html, "html.parser")
        cutoff = cutoff_date(hours_old)
        seen: set[str] = set()

        for card in soup.select("div.job-posting-card"):
            try:
                link = card.select_one("a.job-posting-card__link")
                href = link.get("href") if link else None
                if not href:
                    continue
                job_url = href if href.startswith("http") else BASE + href
                if job_url in seen:
                    continue
                seen.add(job_url)

                title_el = card.select_one(".job-title")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue
                comp_el = card.select_one(".company-name")
                company = comp_el.get_text(strip=True) if comp_el else None

                loc, contract, lo, hi = _classify_badges(card)

                posted = None
                date_el = card.select_one(".text-pale-grey-40")
                if date_el:
                    dm = _DAYS.search(date_el.get_text(strip=True))
                    if dm:
                        posted = date.today() - timedelta(days=int(dm.group(1)))
                if posted and posted < cutoff:
                    continue

                idm = _OFFREID.search(href)

                rec: JobRecord = {
                    "site": self.platform_name,
                    "id": idm.group(1) if idm else job_url[:100],
                    "title": title[:400],
                    "company": company or None,
                    "location": loc or "France",
                    "description": None,
                    "job_url": job_url,
                    "date_posted": posted.isoformat() if posted else None,
                    "is_remote": None,
                    "currency": "EUR" if (lo or hi) else None,
                    "min_amount": lo,
                    "max_amount": hi,
                    "interval": "yearly" if (lo or hi) else None,
                    "job_type": contract or None,
                }
                result.records.append(rec)
            except Exception as e:
                result.errors.append(f"cadremploi parse: {type(e).__name__}: {e}")

        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result
