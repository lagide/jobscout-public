"""ChoisirServicePublic connector — https://choisirleservicepublic.gouv.fr

Portail officiel des 3 fonctions publiques (Etat, territoriale, hospitaliere),
ex-BIEP / Place de l'Emploi Public. Site WordPress (theme "biep"), HTML rendu
serveur, **sans anti-bot** : httpx pur suffit (pas de curl_cffi/Playwright).
~47k offres au total.

Connecteur *structure* (uses_search_terms=False) : on ne passe PAS de texte libre.
Le mot-cle n'est filtrable que via l'AJAX a nonce du site (fragile) ; a la place on
filtre en amont par la **facette domaine** exposee dans l'URL :
    /nos-offres/filtres/domaine/<id>/page/<n>/
`domaine=3522` ("Numerique") fait passer de ~47k offres a ~2,2k. On affine ensuite
les titres cote connecteur (INCLUDE/EXCLUDE). Le tri par defaut du site etant la
date decroissante, on arrete de paginer des qu'une page est entierement plus vieille
que `hours_old`.

Config (.env, tout est optionnel — injecte au runtime, un simple restart suffit,
pas de rebuild) :
    CSP_DOMAINS=3522,3527     # ids de domaines a scraper (defaut: 3522 Numerique)
    CSP_MAX_PAGES=20          # garde-fou pages/domaine
    CSP_TITLE_INCLUDE=...     # regex (override) des titres a garder
    CSP_TITLE_EXCLUDE=...     # regex (override) des titres a rejeter

Domaines utiles pour un profil cyber/SI : 3522 Numerique * 3527 Renseignement *
3511 Defense * 3529 Securite (ces deux derniers melent du non-cyber -> echantillonner).
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import date
from typing import Optional

import httpx

from .base import BaseConnector, ConnectorResult, JobRecord
from .utils import cutoff_date

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    _BS4_OK = True
except Exception:  # pragma: no cover
    _BS4_OK = False

BASE = "https://choisirleservicepublic.gouv.fr"
LISTING = BASE + "/nos-offres/filtres/domaine/{domain}/page/{page}/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# Filtres titre par defaut (sur titre normalise : minuscules + accents retires).
# Pensés pour le profil cyber / reseau / securite / infra / responsabilite SI senior.
_DEFAULT_INCLUDE = (
    r"cyber|securit|\bssi\b|rssi|ciso|\bsoc\b|secops|siem|\bedr\b|\bxdr\b|\bmdr\b|"
    r"pentest|forensic|\bpki\b|\biam\b|\bsoar\b|"
    r"reseau|firewall|pare.?feu|zero.?trust|sd.?wan|\bvpn\b|"
    r"architecte|infrastructur|systeme.?d.?information|"
    r"administrateur.*(systeme|reseau|infrastructur)|"
    r"ingenieur.*(systeme|reseau|securit|infrastructur|cloud)|"
    r"responsable.*(securit|infrastructur|reseau|informatique|systeme|\bsi\b)|"
    r"\bdsi\b|directeur.*(systeme|informatique|\bsi\b)|expert.*(cyber|securit|reseau)"
)
_DEFAULT_EXCLUDE = (
    r"apprenti|alternan|\bstage\b|stagiaire|\bvae\b|junior|debutant|"
    r"technicien|support.*(informatique|utilisateur|proximite|\bn1\b|\bn2\b)|"
    r"hotline|help.?desk|developpeu|\bdev\b|data.?(analyst|scientist|engineer)|"
    r"webmaster|graphis|agent de securite|gardien|videoprotection|"
    r"maitre.?chien|gendarme|policier|pompier|secretaire|assistant"
)

_REF_RE = re.compile(r"reference-([^/\"?]+)", re.I)
_DATE_RE = re.compile(r"(\d{1,2})\s+([a-zA-Zéûôùàè]+)\s+(\d{4})")
_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}


def _fold(s: str) -> str:
    """minuscules + suppression des accents (pour matcher des regex ASCII)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower())
        if not unicodedata.combining(c)
    )


def _parse_fr_date(text: str) -> Optional[date]:
    """'En ligne depuis le 19 juin 2026' -> date(2026, 6, 19)."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, mon, year = m.group(1), _fold(m.group(2)), m.group(3)
    month = _MONTHS.get(mon)
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _env_domains() -> list[str]:
    raw = os.getenv("CSP_DOMAINS", "3522")
    return [d.strip() for d in raw.split(",") if d.strip()] or ["3522"]


def _env_max_pages() -> int:
    try:
        return max(1, int(os.getenv("CSP_MAX_PAGES", "20")))
    except ValueError:
        return 20


class ChoisirServicePublicConnector(BaseConnector):
    platform_name = "choisirservicepublic"
    # Structure : pas de texte libre, on filtre par domaine + titre. Le scraper
    # n'appelle scrape() qu'une fois par profil (search_term=None).
    uses_search_terms = False

    def __init__(self) -> None:
        self._include = re.compile(os.getenv("CSP_TITLE_INCLUDE", _DEFAULT_INCLUDE), re.I)
        self._exclude = re.compile(os.getenv("CSP_TITLE_EXCLUDE", _DEFAULT_EXCLUDE), re.I)

    def is_enabled(self) -> bool:
        return _BS4_OK

    def _parse_card(self, card) -> Optional[JobRecord]:
        link = card.select_one("h3.fr-card__title a")
        if not link:
            return None
        href = link.get("href") or ""
        title = link.get_text(strip=True)
        if not href or not title:
            return None
        job_url = href if href.startswith("http") else BASE + href

        location = None
        company = None
        posted: Optional[date] = None
        for li in card.select("ul.fr-card__desc > li"):
            classes = " ".join(li.get("class") or [])
            for sp in li.select("span.sr-only"):  # retire le libelle ("Localisation :")
                sp.extract()
            txt = li.get_text(" ", strip=True)
            if "fr-icon-map-pin" in classes:
                location = txt or None
            elif "fr-icon-user-line" in classes:
                company = txt or None
            elif "fr-icon-calendar-line" in classes:
                posted = _parse_fr_date(txt)

        ref_m = _REF_RE.search(href)
        rec: JobRecord = {
            "site": self.platform_name,
            "id": ref_m.group(1) if ref_m else job_url[:100],
            "title": title[:400],
            "company": company,
            "location": location or "France",
            "description": None,  # absente de la liste -> scoring sur titre+employeur
            "job_url": job_url,
            "date_posted": posted.isoformat() if posted else None,
            "is_remote": None,
            "currency": None,
            "min_amount": None,
            "max_amount": None,
            "interval": None,
            "job_type": None,
        }
        return rec

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
        if not _BS4_OK:
            result.errors.append("choisirservicepublic: bs4 indisponible — connecteur desactive")
            return result
        # Source 100% France (fonction publique) : on ignore les autres profils geo.
        if country and country.lower() not in ("france", "fr"):
            return result

        cutoff = cutoff_date(hours_old)
        max_pages = _env_max_pages()
        seen: set[str] = set()

        try:
            async with httpx.AsyncClient(timeout=25, headers=HEADERS, follow_redirects=True) as client:
                for domain in _env_domains():
                    for page in range(1, max_pages + 1):
                        url = LISTING.format(domain=domain, page=page)
                        try:
                            r = await client.get(url)
                            if r.status_code != 200:
                                result.errors.append(
                                    f"choisirservicepublic HTTP {r.status_code} (domaine {domain} p{page})"
                                )
                                break
                            html = r.text
                        except Exception as e:
                            result.errors.append(
                                f"choisirservicepublic request error (domaine {domain} p{page}): {type(e).__name__}: {e}"
                            )
                            break

                        soup = BeautifulSoup(html, "html.parser")
                        cards = soup.select("div.fr-card--offer")
                        if not cards:
                            break

                        page_fresh = 0  # offres fraiches OU sans date (tri date desc -> stop si 0)
                        for card in cards:
                            try:
                                rec = self._parse_card(card)
                            except Exception as e:
                                result.errors.append(f"choisirservicepublic parse: {type(e).__name__}: {e}")
                                continue
                            if rec is None:
                                continue

                            d = (
                                date.fromisoformat(rec["date_posted"])
                                if rec.get("date_posted")
                                else None
                            )
                            if d is not None and d < cutoff:
                                continue  # trop vieille
                            page_fresh += 1

                            norm = _fold(rec["title"])
                            if not self._include.search(norm) or self._exclude.search(norm):
                                continue
                            if rec["job_url"] in seen:
                                continue
                            seen.add(rec["job_url"])
                            result.records.append(rec)
                            if len(result.records) >= results_wanted:
                                return result

                        if page_fresh == 0:
                            break  # page entierement hors fenetre -> pages suivantes plus vieilles
        except Exception as e:
            result.errors.append(f"choisirservicepublic fatal: {type(e).__name__}: {e}")

        return result
