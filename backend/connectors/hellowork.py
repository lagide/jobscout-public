"""HelloWork connector — https://www.hellowork.com

Les pages HelloWork sont protégées par DataDome, mais une requête avec
impersonation TLS Chrome (curl_cffi) depuis une IP résidentielle passe les
contrôles passifs. On parse ensuite les cartes server-rendered (data-cy=serpCard).

Requiert curl_cffi. Connecteur keyword.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseConnector, ConnectorResult, JobRecord

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except Exception:  # pragma: no cover
    _CFFI_OK = False

BASE = "https://www.hellowork.com"
SEARCH = BASE + "/fr-fr/emploi/recherche.html"
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

# "Voir offre de <titre> à <lieu>, chez <societe>, pour un <contrat>, en ...[, Teletravail ...]"
_ARIA = re.compile(r"Voir offre de (.+?) (?:à|a) (.+?), chez (.+?), pour un (.+?)(?:,|$)")
_ID = re.compile(r"/emplois/(\d+)\.html")


def _fetch(url: str) -> str:
    r = cffi_requests.get(url, headers=HEADERS, impersonate=IMPERSONATE,
                          timeout=25, allow_redirects=True)
    r.raise_for_status()
    return r.text


class HelloWorkConnector(BaseConnector):
    platform_name = "hellowork"
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
            result.errors.append("hellowork: curl_cffi indisponible — connecteur désactivé")
            return result
        if country and country.lower() not in ("france", "fr"):
            return result
        if not search_term:
            return result

        params = {"k": search_term, "l": "France"}
        from urllib.parse import urlencode
        url = f"{SEARCH}?{urlencode(params)}"

        try:
            html = await asyncio.to_thread(_fetch, url)
        except Exception as e:
            result.errors.append(f"hellowork request failed: {type(e).__name__}: {e}")
            return result

        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()

        for a in soup.select('a[data-cy="offerTitle"]'):
            try:
                href = a.get("href") or ""
                if "/emplois/" not in href:
                    continue
                job_url = href if href.startswith("http") else BASE + href
                if job_url in seen:
                    continue
                seen.add(job_url)

                aria = a.get("aria-label") or ""
                title_attr = a.get("title") or ""
                m = _ARIA.search(aria)
                if m:
                    title, loc, company, contract = (g.strip() for g in m.groups())
                else:
                    parts = title_attr.split(" - ")
                    title = parts[0].strip()
                    company = parts[1].strip() if len(parts) > 1 else None
                    loc, contract = "France", None

                if not title:
                    continue

                # Télétravail total → remote ; "partiel" reste hybride (None).
                is_remote = True if re.search(r"t[ée]l[ée]travail (total|complet)", aria, re.I) else None
                idm = _ID.search(href)

                rec: JobRecord = {
                    "site": self.platform_name,
                    "id": idm.group(1) if idm else job_url.rsplit("/", 1)[-1][:100],
                    "title": title[:400],
                    "company": company or None,
                    "location": loc or "France",
                    "description": None,
                    "job_url": job_url,
                    "date_posted": None,
                    "is_remote": is_remote,
                    "currency": None,
                    "min_amount": None,
                    "max_amount": None,
                    "interval": None,
                    "job_type": contract or None,
                }
                result.records.append(rec)
            except Exception as e:
                result.errors.append(f"hellowork parse: {type(e).__name__}: {e}")

        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result
