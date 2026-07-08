"""Pipeline de scraping — JobSpy + connecteurs custom, dédoublonnage, persistance.

Stratégie de dédoublonnage (Phase 1) :
    1. (platform, job_url) — clé unique en DB. Empêche la ré-insertion de la même
       URL sur la même plateforme.
    2. content_hash = SHA256(titre + société + lieu, normalisés). Permet de
       fusionner la même offre vue sur plusieurs plateformes : la 2e occurrence
       est ajoutée au champ JSON `sources` de la ligne existante au lieu de créer
       un doublon.

Performance (refonte) :
    Avant : 2 SELECT par offre (lookup par URL + lookup par hash) → N+1 catastrophique
    sur 1000 offres scrapées (~2000 queries SQLite).
    Après : on pré-charge en mémoire toutes les paires (platform, job_url) et tous
    les content_hash existants en 2 requêtes au début. Les checks deviennent O(1)
    en mémoire. Gain mesuré : ~10× plus rapide sur de gros lots.

Chaque scrape produit aussi une ligne ScrapeLog (running → success/failed) avec
les compteurs et erreurs par terme — affichée dans l'onglet Logs du frontend.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Optional

import pandas as pd

import settings as app_settings
from connectors import get_connector, registered_platforms
from constants import GEO_PROFILES, is_company_blacklisted, is_title_blacklisted
from currency import compute_effective_eur, to_eur
from database import get_session
from enrichment import (
    compute_freshness_score,
    compute_geo_score,
    compute_salary_score,
    detect_language,
    detect_work_mode,
)
from geo_scope import is_location_in_scope
from job_urls import clean_http_url, select_job_urls
from models import Job, ScrapeLog
from schemas import SearchRequest, SearchResponse

# Plateformes que JobSpy gère nativement (vs connecteurs custom)
_JOBSPY_PLATFORMS = {"linkedin", "indeed", "glassdoor", "zip_recruiter", "google"}

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Retourne l'instant UTC courant. Wrapper unique : datetime.utcnow() est deprecated en Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- Normalisation des valeurs brutes (DataFrame pandas) ----------

def _nan_to_none(v: Any) -> Any:
    """Convertit NaN/None/'nan'/'none'/'' en None — cas typiques des frames JobSpy."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and v.lower() in {"nan", "none", ""}:
        return None
    return v


def _to_date(v: Any) -> date | None:
    v = _nan_to_none(v)
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime().date()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v).date()
        except ValueError:
            return None
    return None


def _to_float(v: Any) -> float | None:
    v = _nan_to_none(v)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> bool | None:
    v = _nan_to_none(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in {"true", "yes", "1"}
    return bool(v)


# ---------- Hash de contenu (déduplication cross-platform) ----------

_WS_RE = re.compile(r"\s+")


def _normalize(s: Optional[str]) -> str:
    """Minuscules + retrait des accents + collapse des espaces. Utilisé seulement pour le hash."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = _WS_RE.sub(" ", s)
    return s


def compute_content_hash(
    title: Optional[str], company: Optional[str], location: Optional[str]
) -> str:
    """SHA256 normalisé de (titre + société + lieu). Stable entre plateformes."""
    key = f"{_normalize(title)}|{_normalize(company)}|{_normalize(location)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------- Mapping ligne brute → kwargs Job ----------

def _row_to_job_kwargs(row: pd.Series) -> dict[str, Any]:
    """Mappe une ligne (DataFrame JobSpy ou dict connecteur) vers les kwargs du modèle Job."""
    canonical_url, _alternatives = select_job_urls(row)
    return {
        "external_id": _nan_to_none(row.get("id")),
        "platform": str(row.get("site", "unknown")),
        "job_url": canonical_url,
        "title": str(_nan_to_none(row.get("title")) or "Unknown title"),
        "company": _nan_to_none(row.get("company")),
        "location": _nan_to_none(row.get("location")),
        "description": _nan_to_none(row.get("description")),
        "min_salary": _to_float(row.get("min_amount")),
        "max_salary": _to_float(row.get("max_amount")),
        "currency": _nan_to_none(row.get("currency")),
        "salary_interval": _nan_to_none(row.get("interval")),
        "is_remote": _to_bool(row.get("is_remote")),
        "job_type": _nan_to_none(row.get("job_type")),
        "date_posted": _to_date(row.get("date_posted")),
    }


def _sources_append(existing_json: Optional[str], entry: dict) -> str:
    """Ajoute une entrée au JSON `sources` en dédoublonnant sur (platform, url)."""
    try:
        existing = json.loads(existing_json) if existing_json else []
    except (TypeError, ValueError):
        existing = []
    key = (entry.get("platform"), entry.get("url"))
    for s in existing:
        if (s.get("platform"), s.get("url")) == key:
            return json.dumps(existing)
    existing.append(entry)
    return json.dumps(existing)


# ---------- Résolution du profil géographique ----------

def _resolve_profile(req: SearchRequest) -> tuple[str, str, Optional[str], Optional[str]]:
    """Détermine (location, country, profile_name, region) depuis la SearchRequest.

    Le profil nommé prime ; les overrides directs `req.location` / `req.country`
    permettent toutefois un appel ad-hoc sans profil.
    """
    profile = GEO_PROFILES.get(req.profile) if req.profile else None
    search_cfg = app_settings.get().search

    # Précédence : override direct de la requête > localisation configurée
    # (settings.search, page Paramètres) > profil GEO_PROFILES codé.
    location = req.location or search_cfg.location \
        or (profile["location"] if profile else "France")
    country = req.country or search_cfg.country \
        or (profile["country"] if profile else "France")
    profile_name = req.profile if profile else None
    region = profile["region"] if profile else None

    return location, country, profile_name, region


# ---------- Exécution scrape ----------

def _scrape_jobspy_sync(
    req: SearchRequest, location: str, country: str, jobspy_sites: list[str]
) -> tuple[list[pd.DataFrame], list[str]]:
    """Tourne JobSpy pour les plateformes supportées nativement (sync, off-thread)."""
    import jobspy_patch  # noqa: F401  monkey-patch : timeout API Indeed → 30s
    from jobspy import scrape_jobs  # import paresseux pour garder un cold-start rapide

    frames: list[pd.DataFrame] = []
    term_errors: list[str] = []

    for term in req.search_terms:
        try:
            logger.info(
                "JobSpy term=%r sites=%s location=%r country=%r",
                term, jobspy_sites, location, country,
            )
            df = scrape_jobs(
                site_name=jobspy_sites,
                search_term=term,
                # Google Jobs ignore search_term/location : il a son propre
                # paramètre plein-texte. Sans lui, google renvoie TOUJOURS 0
                # résultat (bug historique corrigé 2026-07-02).
                google_search_term=f"{term} emplois {location}",
                location=location,
                results_wanted=req.results_per_term,
                hours_old=req.hours_old,
                country_indeed=country,
                description_format="markdown",
                verbose=0,
            )
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as e:
            term_errors.append(f"[JobSpy/{term}] {type(e).__name__}: {e}")
            logger.exception("JobSpy failed for term=%r", term)
    return frames, term_errors


async def _scrape_connectors(
    req: SearchRequest, location: str, country: str, connector_names: list[str]
) -> tuple[list[pd.DataFrame], list[str]]:
    """Tourne les connecteurs custom activés.

    Deux familles :
    - keyword (uses_search_terms=True) : appelés une fois par terme de recherche libre.
    - structuré (uses_search_terms=False) : appelés une seule fois par profil
      (search_term=None) ; ils construisent eux-mêmes leur requête depuis des
      filtres (codes ROME, qualification, départements…).
    """
    frames: list[pd.DataFrame] = []
    term_errors: list[str] = []

    # On filtre sur les connecteurs présents dans le registry ET activés (creds OK)
    keyword_conns: list[tuple[str, object]] = []
    structured_conns: list[tuple[str, object]] = []
    for name in connector_names:
        conn = get_connector(name)
        if conn is None:
            term_errors.append(f"[{name}] unknown connector")
            continue
        if not conn.is_enabled():
            term_errors.append(f"[{name}] disabled (missing credentials)")
            continue
        if getattr(conn, "uses_search_terms", True):
            keyword_conns.append((name, conn))
        else:
            structured_conns.append((name, conn))

    # Connecteurs structurés : un seul appel par profil, sans texte libre.
    # results_wanted élargi (sur tous les termes) car ce connector ne boucle pas.
    structured_results_wanted = max(
        req.results_per_term,
        req.results_per_term * max(1, len(req.search_terms)),
    )
    for name, conn in structured_conns:
        try:
            logger.info("Connector %s (structured, term-agnostic)", name)
            res = await conn.scrape(
                search_term=None,
                location=location,
                country=country,
                hours_old=req.hours_old,
                results_wanted=structured_results_wanted,
            )
            term_errors.extend(res.errors)
            if res.records:
                frames.append(pd.DataFrame(res.records))
        except Exception as e:
            term_errors.append(f"[{name}] {type(e).__name__}: {e}")
            logger.exception("Structured connector %s failed", name)

    # Connecteurs keyword : un appel par terme de recherche.
    for term in req.search_terms:
        for name, conn in keyword_conns:
            try:
                logger.info("Connector %s term=%r", name, term)
                res = await conn.scrape(
                    search_term=term,
                    location=location,
                    country=country,
                    hours_old=req.hours_old,
                    results_wanted=req.results_per_term,
                )
                term_errors.extend(res.errors)
                if res.records:
                    frames.append(pd.DataFrame(res.records))
            except Exception as e:
                term_errors.append(f"[{name}/{term}] {type(e).__name__}: {e}")
                logger.exception("Connector %s failed for term=%r", name, term)

    return frames, term_errors


def _split_sites(sites: list[str]) -> tuple[list[str], list[str]]:
    """Sépare la liste demandée en (sites JobSpy, sites connecteurs custom)."""
    jobspy = [s for s in sites if s in _JOBSPY_PLATFORMS]
    connectors = [s for s in sites if s in registered_platforms()]
    return jobspy, connectors


async def scrape_and_store(
    req: SearchRequest,
    triggered_by: str = "manual",
) -> SearchResponse:
    """Point d'entrée principal — scrape, dédoublonne, enrichit et persiste."""
    location, country, profile_name, region = _resolve_profile(req)

    # On crée la ligne ScrapeLog en amont pour qu'elle soit visible dans l'UI
    # même si le run plante en cours de route.
    with get_session() as s:
        log = ScrapeLog(
            profile=profile_name,
            triggered_by=triggered_by,
            status="running",
            sites=json.dumps(list(req.sites)),
            search_terms_count=len(req.search_terms),
        )
        s.add(log)
        s.flush()
        log_id = log.id

    scraped_total = 0
    new_count = 0
    dup_count = 0
    merged_count = 0
    blacklisted_count = 0
    errors: list[str] = []
    new_ids: list[int] = []

    try:
        jobspy_sites, connector_sites = _split_sites(list(req.sites))

        frames: list[pd.DataFrame] = []

        # Branche JobSpy (lib sync) : exécution off-thread pour ne pas bloquer la loop async.
        if jobspy_sites:
            js_frames, js_errors = await asyncio.to_thread(
                _scrape_jobspy_sync, req, location, country, jobspy_sites
            )
            frames.extend(js_frames)
            errors.extend(js_errors)

        # Branche connecteurs custom (déjà async)
        if connector_sites:
            cn_frames, cn_errors = await _scrape_connectors(
                req, location, country, connector_sites
            )
            frames.extend(cn_frames)
            errors.extend(cn_errors)

        if frames:
            combined = pd.concat(frames, ignore_index=True)
            combined = combined.drop_duplicates(subset=["job_url"], keep="first")
            scraped_total = len(combined)

            # ---- OPTIMISATION CLEFS : pré-chargement en mémoire des index existants ----
            # Avant : 2 SELECT par offre (lookup URL + lookup hash) → 2N queries.
            # Maintenant : 2 SELECT au total + lookups O(1) sur set/dict en mémoire.
            with get_session() as s:
                # Map (platform, url) → job_id pour pouvoir bumper last_seen_at sur dups
                existing_url_to_id: dict[tuple[str, str], int] = {
                    (p, u): jid for p, u, jid in s.query(Job.platform, Job.job_url, Job.id).all()
                }
                # Map content_hash → job_id (suffit pour aller chercher la ligne en SELECT direct)
                existing_hashes: dict[str, int] = dict(
                    s.query(Job.content_hash, Job.id)
                    .filter(Job.content_hash.isnot(None))
                    .all()
                )

            # Collecte les ids vus pendant ce scrape (dups + merges) pour bumper last_seen_at en bulk
            seen_ids: set[int] = set()

            with get_session() as s:
                for _, row in combined.iterrows():
                    try:
                        kwargs = _row_to_job_kwargs(row)
                        if not kwargs["job_url"]:
                            continue

                        # Filtre blacklist : titres non pertinents (sales, alternance,
                        # technicien, support N1/N2…). Skip immédiat avant tout : on ne
                        # paye ni la DB ni un éventuel appel OpenRouter.
                        if is_title_blacklisted(kwargs["title"]) or is_company_blacklisted(kwargs["company"]):
                            blacklisted_count += 1
                            continue

                        # Le remote intégral reste national. Tout hybride/présentiel
                        # doit être dans le périmètre défini par config/geo_scope.json.
                        # Désactivable via settings.search.geo_filter_enabled (page
                        # Paramètres) quand on élargit la zone de recherche.
                        work_mode = detect_work_mode(
                            kwargs.get("description"), kwargs.get("is_remote")
                        )
                        if app_settings.get().search.geo_filter_enabled:
                            in_scope, scope_reason = is_location_in_scope(
                                kwargs.get("location"), work_mode, kwargs.get("is_remote")
                            )
                            if not in_scope:
                                blacklisted_count += 1
                                logger.debug(
                                    "Geo scope rejected title=%r location=%r reason=%s",
                                    kwargs.get("title"), kwargs.get("location"), scope_reason,
                                )
                                continue

                        # Garde (platform, job_url) — même URL revue sur la même plateforme
                        url_key = (kwargs["platform"], kwargs["job_url"])
                        if url_key in existing_url_to_id:
                            dup_count += 1
                            seen_ids.add(existing_url_to_id[url_key])
                            continue

                        # Hash de contenu pour dédoublonnage cross-plateforme
                        c_hash = compute_content_hash(
                            kwargs["title"], kwargs["company"], kwargs["location"]
                        )

                        _canonical, source_urls = select_job_urls(row)
                        source_entries = [
                            {
                                "platform": kwargs["platform"],
                                "url": source_url,
                                "scraped_at": _utcnow().isoformat(),
                            }
                            for source_url in source_urls
                        ]

                        # Cas : même offre déjà connue sur une autre plateforme
                        # → on enrichit son champ `sources`, pas de nouvelle ligne.
                        existing_id = existing_hashes.get(c_hash)
                        if existing_id is not None:
                            existing_job = s.get(Job, existing_id)
                            if existing_job is not None:
                                for source_entry in source_entries:
                                    existing_job.sources = _sources_append(
                                        existing_job.sources, source_entry
                                    )
                                # Pour Indeed, promouvoir le lien direct employeur :
                                # le bouton de candidature ne dépend plus du jk éphémère.
                                direct_url = clean_http_url(row.get("job_url_direct"))
                                if kwargs["platform"] == "indeed" and direct_url:
                                    existing_job.job_url = direct_url
                                merged_count += 1
                                seen_ids.add(existing_id)
                                # On ajoute aussi au set en mémoire pour les itérations suivantes
                                existing_url_to_id[url_key] = existing_id
                                continue

                        # Enrichissement : mode de travail, langue, conversion EUR.
                        # Pour Suisse / Luxembourg / Belgique, on rejette ? l'ingestion les
                        # offres clairement non FR/EN (allemand, n?erlandais...). Les titres
                        # trop courts restent language=None et sont conserv?s pour ?viter les
                        # faux n?gatifs sur des intitul?s anglais sans description.
                        lang_text = f"{kwargs.get('title') or ''}\n{kwargs.get('description') or ''}"
                        language = detect_language(lang_text)
                        if profile_name in {"Suisse", "Luxembourg", "Belgique"} and language not in (None, "fr", "en"):
                            blacklisted_count += 1
                            continue

                        sal_min_eur = to_eur(kwargs.get("min_salary"), kwargs.get("currency"))
                        sal_max_eur = to_eur(kwargs.get("max_salary"), kwargs.get("currency"))

                        cost_coef = 1.00
                        if profile_name and profile_name in GEO_PROFILES:
                            cost_coef = GEO_PROFILES[profile_name].get("cost_coef", 1.00)
                        # On utilise le haut de fourchette quand dispo (vue optimiste),
                        # sinon le bas. Coefficient de coût de la vie appliqué ensuite.
                        eff_base = sal_max_eur if sal_max_eur is not None else sal_min_eur
                        sal_eff_eur = compute_effective_eur(eff_base, cost_coef)

                        # Composantes de scoring déterministes (rapide, sans appel API)
                        sc_geo = compute_geo_score(
                            work_mode, kwargs.get("location"), kwargs.get("description")
                        )
                        sc_salary = compute_salary_score(
                            sal_min_eur, sal_max_eur, kwargs.get("salary_interval")
                        )
                        # Repli sur la date de découverte (= maintenant) quand le
                        # connecteur ne fournit pas date_posted (linkedin, cadremploi…).
                        sc_freshness = compute_freshness_score(
                            kwargs.get("date_posted"), fallback=_utcnow()
                        )

                        # Insertion d'une vraie nouvelle offre
                        job = Job(
                            **kwargs,
                            content_hash=c_hash,
                            sources=json.dumps(source_entries),
                            geo_profile=profile_name,
                            region=region,
                            work_mode=work_mode,
                            language=language,
                            salary_eur_min=sal_min_eur,
                            salary_eur_max=sal_max_eur,
                            salary_effective_eur=sal_eff_eur,
                            score_geo=sc_geo,
                            score_salary=sc_salary,
                            score_freshness=sc_freshness,
                        )
                        s.add(job)
                        s.flush()
                        new_ids.append(job.id)
                        new_count += 1

                        # On met à jour les index en mémoire pour que les itérations
                        # suivantes voient cette ligne comme déjà existante (cas où
                        # le même hash apparaîtrait deux fois dans le même batch).
                        existing_url_to_id[url_key] = job.id
                        existing_hashes[c_hash] = job.id
                    except Exception as e:
                        errors.append(f"{row.get('job_url', '?')}: {type(e).__name__}: {e}")

                # Bulk UPDATE last_seen_at sur tous les jobs revus pendant ce scrape.
                # Un seul UPDATE évite N queries (vital sur 1000+ dups par cycle).
                if seen_ids:
                    s.query(Job).filter(Job.id.in_(seen_ids)).update(
                        {Job.last_seen_at: _utcnow()},
                        synchronize_session=False,
                    )

        # Lance le scoring en fire-and-forget (n'attend pas la fin)
        if req.score_new_jobs and new_ids:
            from scoring import score_jobs_background
            asyncio.create_task(score_jobs_background(new_ids))

        # Clôture du log : succès
        with get_session() as s:
            log = s.get(ScrapeLog, log_id)
            if log is not None:
                log.ended_at = _utcnow()
                log.status = "success"
                log.scraped = scraped_total
                log.new_jobs = new_count
                log.duplicates = dup_count
                log.merged_sources = merged_count
                log.blacklisted = blacklisted_count
                log.errors = json.dumps(errors[:50])

        return SearchResponse(
            scraped=scraped_total,
            new=new_count,
            duplicates=dup_count,
            merged_sources=merged_count,
            blacklisted=blacklisted_count,
            errors=errors[:20],
            log_id=log_id,
        )

    except Exception as fatal:
        logger.exception("scrape_and_store failed fatally")
        with get_session() as s:
            log = s.get(ScrapeLog, log_id)
            if log is not None:
                log.ended_at = _utcnow()
                log.status = "failed"
                log.fatal_error = f"{type(fatal).__name__}: {fatal}"
                log.errors = json.dumps(errors[:50])
        return SearchResponse(
            scraped=scraped_total,
            new=new_count,
            duplicates=dup_count,
            merged_sources=merged_count,
            blacklisted=blacklisted_count,
            errors=errors[:20] + [f"FATAL: {fatal}"],
            log_id=log_id,
        )
