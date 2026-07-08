"""FranceTravail connector — https://api.francetravail.io/partenaire/offresdemploi/v2/

Official API. Requires inscription (free, takes 5 minutes):
    1. Register at https://francetravail.io/data/api
    2. Subscribe to the "Offres d'emploi" API
    3. Create an application → receive FT_CLIENT_ID + FT_CLIENT_SECRET
    4. Add both to your .env:
           FT_CLIENT_ID=PAR_YOURAPP_xxx
           FT_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxx

The connector silently self-disables when credentials are missing — so you can ship
without FT set up, then enable it later by dropping env vars in.
"""
from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Optional

import httpx

from constants import get_ft_qualification, get_ft_rome_codes, get_idf_departments

from .base import BaseConnector, ConnectorResult, JobRecord

logger = logging.getLogger(__name__)

TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    "?realm=%2Fpartenaire"
)
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"


class _TokenCache:
    """OAuth2 client_credentials token cache (single-threaded, in-memory)."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = Lock()

    async def get(self, client_id: str, client_secret: str) -> Optional[str]:
        with self._lock:
            if self._token and time.time() < self._expires_at - 30:
                return self._token
        try:
            # FranceTravail requires the scope to include application_<CLIENT_ID>
            # in addition to the API-specific scopes.
            scope = f"application_{client_id} api_offresdemploiv2 o2dsoffre"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": scope,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if r.status_code != 200:
                    logger.warning(
                        "FranceTravail OAuth2 failed (%d): %s", r.status_code, r.text[:300]
                    )
                    r.raise_for_status()
                payload = r.json()
            with self._lock:
                self._token = payload["access_token"]
                self._expires_at = time.time() + payload.get("expires_in", 1500)
            return self._token
        except Exception as e:
            logger.warning("FranceTravail token fetch failed: %s", e)
            return None


_token_cache = _TokenCache()


class FranceTravailConnector(BaseConnector):
    platform_name = "francetravail"

    # Connector structuré : on n'utilise pas le texte libre. La requête est
    # construite depuis des codes ROME + qualification cadre + départements IDF.
    # Le scraper n'appelle scrape() qu'une fois par profil (search_term=None).
    uses_search_terms = False

    def is_enabled(self) -> bool:
        # settings.get_secret : surcharge UI (config/secrets.json) puis .env.
        # Import paresseux — le module settings est initialisé après les connecteurs.
        from settings import get_secret
        return bool(get_secret("FT_CLIENT_ID") and get_secret("FT_CLIENT_SECRET"))

    @staticmethod
    def _publiee_depuis(hours_old: int) -> int:
        # API : publieeDepuis ∈ {1,3,7,14,31}. Plus petite période qui couvre hours_old.
        for days_opt in (1, 3, 7, 14, 31):
            if days_opt * 24 >= hours_old:
                return days_opt
        return 31

    def _parse_offer(self, o: dict) -> Optional[JobRecord]:
        try:
            salary_raw = (o.get("salaire") or {}).get("libelle") or ""
            lieu = o.get("lieuTravail") or {}
            rec: JobRecord = {
                "site": self.platform_name,
                "id": o.get("id"),
                "title": o.get("intitule") or "Unknown",
                "company": (o.get("entreprise") or {}).get("nom"),
                "location": lieu.get("libelle"),
                "description": o.get("description"),
                "job_url": o.get("origineOffre", {}).get("urlOrigine")
                          or f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}",
                "currency": "EUR" if salary_raw else None,
                "min_amount": None,
                "max_amount": None,
                "interval": None,
                "is_remote": None,  # not directly exposed — Claude will infer from description
                "job_type": o.get("typeContrat"),
                "date_posted": (o.get("dateCreation") or "")[:10] or None,
            }
            if rec["job_url"] and rec["title"]:
                return rec
        except Exception:
            return None
        return None

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

        from settings import get_secret
        client_id = get_secret("FT_CLIENT_ID")
        client_secret = get_secret("FT_CLIENT_SECRET")
        if not client_id or not client_secret:
            # Should have been filtered earlier by is_enabled, but be defensive.
            result.errors.append("francetravail: missing FT_CLIENT_ID / FT_CLIENT_SECRET")
            return result

        token = await _token_cache.get(client_id, client_secret)
        if not token:
            result.errors.append("francetravail: token acquisition failed")
            return result

        publiee_depuis = self._publiee_depuis(hours_old)
        rome_codes = get_ft_rome_codes()
        departements = get_idf_departments()
        # Géo "Remote + IDF large" : on filtre les départements IDF à la source.
        # Le full-remote hors IDF est récupéré par les autres connectors ; FT borne
        # ici la cadre onsite/hybride aux dépts franciliens.
        # France Travail limite le param?tre departement ? 5 valeurs max.
        # IDF = 8 d?partements, donc on d?coupe en deux appels par code ROME.
        departement_chunks = [departements[i:i + 5] for i in range(0, len(departements), 5)] or [[]]
        # R?partir results_wanted sur les codes ROME x chunks interrog?s.
        per_code = max(1, results_wanted // max(1, len(rome_codes) * len(departement_chunks)))
        seen_ids: set[str] = set()

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                for code in rome_codes:
                    for dep_chunk in departement_chunks:
                        params = {
                            "codeROME": code,
                            "qualification": get_ft_qualification(),
                            "publieeDepuis": publiee_depuis,
                            "range": f"0-{min(149, max(1, per_code - 1))}",
                        }
                        if dep_chunk:
                            params["departement"] = ",".join(dep_chunk)
                        try:
                            r = await client.get(
                                SEARCH_URL,
                                params=params,
                                headers={"Authorization": f"Bearer {token}"},
                            )
                            # 206 Partial Content is normal here; 204 = no result.
                            if r.status_code == 204:
                                continue
                            if r.status_code not in (200, 206):
                                result.errors.append(
                                    f"francetravail HTTP {r.status_code} (ROME {code}, deps {','.join(dep_chunk)}): {r.text[:200]}"
                                )
                                continue
                            payload = r.json()
                        except Exception as e:
                            result.errors.append(f"francetravail request error (ROME {code}, deps {','.join(dep_chunk)}): {e}")
                            continue

                        for o in (payload.get("resultats", []) or []):
                            oid = o.get("id")
                            if oid and oid in seen_ids:
                                continue
                            rec = self._parse_offer(o)
                            if rec is None:
                                continue
                            if oid:
                                seen_ids.add(oid)
                            result.records.append(rec)
        except Exception as e:
            result.errors.append(f"francetravail request error: {e}")

        return result
