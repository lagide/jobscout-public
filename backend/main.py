"""Point d'entrée FastAPI — endpoints REST, scheduler géré par lifespan, CORS pour le webui."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import delete as sqla_delete
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, desc, func, or_

import constants
import geo_scope
import settings as app_settings
from constants import GEO_PROFILES
from database import DB_PATH, get_session, init_db
from models import Job, ScrapeLog
from schemas import (
    ApplicationStatusUpdate,
    ArchiveUpdate,
    BulkActionRequest,
    BulkActionResponse,
    GeoProfileOut,
    JobOut,
    JobsListResponse,
    JobSourceEntry,
    NotesUpdate,
    ScrapeLogOut,
    ScrapeLogsResponse,
    SearchRequest,
    SearchResponse,
    StatsResponse,
)
import scheduler as scheduler_module
import scoring
from enrichment import compute_final_score
from scheduler import build_scheduler, refresh_freshness
from scoring import rescore_all_force, rescore_all_missing, score_jobs_background
from scraper import scrape_and_store

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


class _RedactSecretsFilter(logging.Filter):
    """Masque les valeurs des secrets d'environnement dans les logs.

    Le .env contient GROQ_API_KEY, OPENROUTER_API_KEY, FT_CLIENT_SECRET… ;
    les erreurs HTTP des providers peuvent embarquer ces valeurs (URL, headers).
    Sans ce filtre, un simple `docker logs jobscout-backend` suffirait à les
    exfiltrer. On collecte les VALEURS au démarrage et on les remplace partout.
    """

    _NAME_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD)", re.IGNORECASE)

    def __init__(self) -> None:
        super().__init__()
        self._secrets = [
            v for k, v in os.environ.items()
            if self._NAME_RE.search(k) and v and len(v) >= 8
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            redacted = msg
            for secret in self._secrets:
                if secret in redacted:
                    redacted = redacted.replace(secret, "[REDACTED]")
            if redacted != msg:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass  # un filtre de log ne doit jamais faire échouer le logging
        return True


# Posé sur les HANDLERS du root logger : s'applique ainsi à TOUS les records
# (un filtre posé sur le logger root ne filtre que ses logs directs).
for _h in logging.getLogger().handlers:
    _h.addFilter(_RedactSecretsFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initializing DB")
    init_db()
    # Amorçage de la config chaude (volume ./config) : settings.json, blacklist,
    # prompt. No-op si les fichiers existent déjà — on n'écrase jamais une
    # config éditée via la page Paramètres.
    app_settings.export_defaults()
    constants.export_default_blacklist()
    scoring.export_default_prompt()
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started — %d job(s) registered", len(scheduler.get_jobs()))
    # Rafraîchit les scores de fraîcheur au boot (ils dérivent d'un jour à
    # l'autre — recalcul déterministe, sans LLM, ~1s pour 1000 offres).
    asyncio.create_task(refresh_freshness())
    yield
    logger.info("Shutting down scheduler")
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="JobScout API",
    description="Aggregates senior IT job postings with Claude-based relevance scoring.",
    version="1.1.0",
    lifespan=lifespan,
)

# CORS — par défaut ouvert seulement aux origines listées dans ALLOWED_ORIGINS
# (séparées par virgule). Mettre "*" pour autoriser tout — déconseillé car cette
# API n'a pas d'authentification et expose /search, /rescore qui peuvent générer
# des coûts (OpenRouter) ou modifier l'état.
_DEFAULT_ORIGINS = "http://localhost:8501,http://localhost:8502"
_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS if _ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


def _utcnow() -> datetime:
    """Wrapper unique : datetime.utcnow() est deprecated en Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- Helpers ----------

def _job_to_out(job: Job) -> JobOut:
    """Sérialise une ligne Job pour l'API, en décodant le JSON `sources`.

    Tolère un JSON corrompu en silence (champ optionnel — pas critique de planter
    la liste pour une ligne mal formée).
    """
    sources_list: list[JobSourceEntry] = []
    if job.sources:
        try:
            raw = json.loads(job.sources)
            for entry in raw:
                try:
                    sources_list.append(JobSourceEntry(**entry))
                except Exception:
                    # Une entrée mal formée → on ignore, on n'échoue pas tout le serialize
                    pass
        except (TypeError, ValueError):
            pass
    return JobOut(
        id=job.id,
        platform=job.platform,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        min_salary=job.min_salary,
        max_salary=job.max_salary,
        currency=job.currency,
        salary_interval=job.salary_interval,
        is_remote=job.is_remote,
        job_type=job.job_type,
        date_posted=job.date_posted,
        scraped_at=job.scraped_at,
        job_url=job.job_url,
        relevance_score=job.relevance_score,
        relevance_reasoning=job.relevance_reasoning,
        content_hash=job.content_hash,
        sources=sources_list,
        geo_profile=job.geo_profile,
        region=job.region,
        work_mode=job.work_mode,
        language=job.language,
        base_score=job.base_score,
        salary_eur_min=job.salary_eur_min,
        salary_eur_max=job.salary_eur_max,
        salary_effective_eur=job.salary_effective_eur,
        score_geo=job.score_geo,
        score_salary=job.score_salary,
        score_freshness=job.score_freshness,
        application_status=job.application_status,
        applied_date=job.applied_date,
        notes=job.notes,
        archived=bool(job.archived),
    )


def _log_to_out(log: ScrapeLog) -> ScrapeLogOut:
    errors: list[str] = []
    if log.errors:
        try:
            errors = list(json.loads(log.errors))
        except (TypeError, ValueError):
            errors = [log.errors]
    sites: list[str] = []
    if log.sites:
        try:
            sites = list(json.loads(log.sites))
        except (TypeError, ValueError):
            pass
    return ScrapeLogOut(
        id=log.id,
        started_at=log.started_at,
        ended_at=log.ended_at,
        profile=log.profile,
        triggered_by=log.triggered_by,
        status=log.status,
        scraped=log.scraped,
        new_jobs=log.new_jobs,
        duplicates=log.duplicates,
        merged_sources=log.merged_sources,
        blacklisted=getattr(log, "blacklisted", 0) or 0,
        errors=errors,
        fatal_error=log.fatal_error,
        sites=sites,
        search_terms_count=log.search_terms_count,
    )


# ---------- Endpoints ----------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": _utcnow().isoformat()}

@app.get("/health/db-size")
def health_db_size() -> dict:
    """Return DB file size + row counts for monitoring retention growth."""
    db_path = str(DB_PATH)
    size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    wal_path = db_path + "-wal"
    shm_path = db_path + "-shm"
    wal_bytes = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    shm_bytes = os.path.getsize(shm_path) if os.path.exists(shm_path) else 0
    with get_session() as session:
        jobs_total = session.query(func.count(Job.id)).scalar() or 0
        jobs_archived = session.query(func.count(Job.id)).filter(
            Job.archived == True  # noqa: E712
        ).scalar() or 0
        jobs_applied = session.query(func.count(Job.id)).filter(
            Job.application_status.isnot(None)
        ).scalar() or 0
        logs_total = session.query(func.count(ScrapeLog.id)).scalar() or 0
    return {
        "db_path": db_path,
        "db_bytes": size_bytes,
        "db_mb": round(size_bytes / 1024 / 1024, 2),
        "wal_bytes": wal_bytes,
        "shm_bytes": shm_bytes,
        "total_bytes": size_bytes + wal_bytes + shm_bytes,
        "jobs_total": jobs_total,
        "jobs_archived": jobs_archived,
        "jobs_applied": jobs_applied,
        "scrape_logs_total": logs_total,
    }


@app.post("/config/reload")
def config_reload() -> dict:
    """Recharge la config chaude depuis /app/config (volume monté).

    Fichiers pris en compte (fallback sur les valeurs codées si absents) :
        config/settings.json        — configuration centralisée (page Paramètres)
        config/blacklist.json       — title_patterns / title_abbr / companies
        config/scoring_prompt.txt   — system prompt du scoring LLM
        config/geo_scope.json       — périmètre géographique des offres
    Permet de modifier la config SANS rebuild de l'image.
    """
    settings_result = app_settings.reload()
    app_settings.apply_runtime()
    blacklist = constants.reload_blacklist()
    prompt = scoring.reload_prompt()
    geo = geo_scope.reload_geo_scope()
    return {"settings": settings_result, "blacklist": blacklist,
            "prompt": prompt, "geo_scope": geo}


@app.get("/profiles", response_model=list[GeoProfileOut])
def list_profiles() -> list[GeoProfileOut]:
    """Return the list of available geographic scrape profiles for the UI."""
    return [
        GeoProfileOut(
            key=key,
            flag=cfg["flag"],
            location=cfg["location"],
            country=cfg["country"],
            region=cfg["region"],
        )
        for key, cfg in GEO_PROFILES.items()
    ]


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """Trigger an on-demand scrape for ONE geographic profile."""
    return await scrape_and_store(req, triggered_by="manual")


@app.get("/jobs", response_model=JobsListResponse)
def list_jobs(
    keywords: Optional[str] = Query(None, description="Substring match on title/company/description."),
    location: Optional[str] = None,
    platform: Optional[list[str]] = Query(None, description="Filter by one or more platforms."),
    region: Optional[list[str]] = Query(None, description="Filter by region code (FR/CH/LU/BE/CA-QC/RE/MQ)."),
    profile: Optional[list[str]] = Query(None, description="Filter by geo profile name."),
    work_mode: Optional[list[str]] = Query(None, description="full_remote / hybrid / onsite"),
    language: Optional[list[str]] = Query(None, description="Filter by detected language code (fr/en/de)."),
    min_salary: Optional[float] = Query(None, ge=0),
    min_score: Optional[float] = Query(None, ge=0, le=10),
    min_base_score: Optional[float] = Query(
        None, ge=0, le=10,
        description="Filtre sur le score de CONTENU Claude (base_score), avant pondération géo/salaire/fraîcheur.",
    ),
    remote_only: bool = False,
    application_status: Optional[list[str]] = Query(
        None,
        description="to_study / interesting / applied / interview / in_process / closed",
    ),
    in_pipeline: Optional[bool] = Query(
        None,
        description="true = only offers with a non-null application_status; false = the opposite.",
    ),
    include_archived: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    order_by: str = Query("relevance", pattern="^(relevance|content|date|scraped)$"),
    light: bool = Query(
        False,
        description="Si true : payload allégé (description + sources vidées) — utile pour "
                    "les vues tableaux qui n'en ont pas besoin. Réduit le poids ~80%.",
    ),
) -> JobsListResponse:
    """Paginated job list with filters."""
    with get_session() as s:
        q = s.query(Job)

        if keywords:
            pattern = f"%{keywords}%"
            q = q.filter(
                or_(
                    Job.title.ilike(pattern),
                    Job.company.ilike(pattern),
                    Job.description.ilike(pattern),
                )
            )
        if location:
            q = q.filter(Job.location.ilike(f"%{location}%"))
        if platform:
            q = q.filter(Job.platform.in_(platform))
        if region:
            q = q.filter(Job.region.in_(region))
        if profile:
            q = q.filter(Job.geo_profile.in_(profile))
        if work_mode:
            q = q.filter(Job.work_mode.in_(work_mode))
        if language:
            q = q.filter(Job.language.in_(language))
        if min_salary is not None:
            q = q.filter(
                or_(
                    Job.max_salary >= min_salary,
                    and_(Job.max_salary.is_(None), Job.min_salary >= min_salary),
                )
            )
        if min_score is not None:
            q = q.filter(Job.relevance_score >= min_score)
        if min_base_score is not None:
            q = q.filter(Job.base_score >= min_base_score)
        if remote_only:
            q = q.filter(Job.is_remote.is_(True))
        if application_status:
            q = q.filter(Job.application_status.in_(application_status))
        if in_pipeline is True:
            q = q.filter(Job.application_status.isnot(None))
        elif in_pipeline is False:
            q = q.filter(Job.application_status.is_(None))
        if not include_archived:
            q = q.filter(Job.archived.is_(False))

        total = q.count()

        if order_by == "relevance":
            q = q.order_by(Job.relevance_score.desc().nullslast(), Job.scraped_at.desc())
        elif order_by == "content":
            q = q.order_by(
                Job.base_score.desc().nullslast(),
                Job.relevance_score.desc().nullslast(),
                Job.scraped_at.desc(),
            )
        elif order_by == "date":
            q = q.order_by(Job.date_posted.desc().nullslast(), Job.scraped_at.desc())
        else:
            q = q.order_by(Job.scraped_at.desc())

        jobs = q.offset(offset).limit(limit).all()
        items = [_job_to_out(j) for j in jobs]

    # Mode light : on vide les champs lourds (description + sources) pour
    # alléger drastiquement le payload (~80% de réduction sur 200 lignes).
    # Utilisé par la page Triage qui n'affiche que des champs synthétiques.
    if light:
        for it in items:
            it.description = None
            it.sources = []

    return JobsListResponse(total=total, items=items)


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int) -> JobOut:
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        return _job_to_out(job)


@app.post("/rescore")
async def rescore(
    background: BackgroundTasks,
    force: bool = Query(
        default=False,
        description=(
            "When true, clears all existing scores and recomputes from scratch. "
            "Required after a scoring formula change."
        ),
    ),
) -> dict[str, int]:
    """Score jobs that have no relevance_score yet (background).

    Pass ?force=true to wipe and recompute ALL scores — use after a formula change.
    """
    if force:
        with get_session() as s:
            # COUNT direct (plus rapide que query(Job.id).count() qui charge la PK)
            total = s.query(func.count(Job.id)).scalar() or 0
            # synchronize_session=False : nécessaire pour bulk update sur SQLite
            # (évite que SQLAlchemy tente d'expirer chaque objet ORM en cache).
            s.query(Job).update(
                {
                    "relevance_score": None,
                    "base_score": None,
                    "score_geo": None,
                    "score_salary": None,
                    "score_freshness": None,
                    "relevance_reasoning": None,
                },
                synchronize_session=False,
            )
        background.add_task(rescore_all_force)
        return {"pending": total}
    else:
        with get_session() as s:
            pending = s.query(func.count(Job.id)).filter(
                Job.relevance_score.is_(None)
            ).scalar() or 0
        background.add_task(rescore_all_missing)
        return {"pending": pending}


@app.post("/jobs/{job_id}/rescore", response_model=JobOut)
async def rescore_one(job_id: int) -> JobOut:
    """Force-rescore a single job (synchronous — waits for the model)."""
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        job.relevance_score = None
        job.relevance_reasoning = None

    await score_jobs_background([job_id])

    with get_session() as s:
        job = s.get(Job, job_id)
        return _job_to_out(job)


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    with get_session() as s:
        total = s.query(Job).count()
        by_platform = dict(
            s.query(Job.platform, func.count(Job.id)).group_by(Job.platform).all()
        )
        by_region = dict(
            s.query(Job.region, func.count(Job.id))
            .filter(Job.region.isnot(None))
            .group_by(Job.region)
            .all()
        )
        avg_min = s.query(func.avg(Job.min_salary)).scalar()
        avg_max = s.query(func.avg(Job.max_salary)).scalar()
        last_scrape = s.query(func.max(Job.scraped_at)).scalar()
        scored = s.query(Job).filter(Job.relevance_score.isnot(None)).count()

    return StatsResponse(
        total_jobs=total,
        by_platform=by_platform,
        by_region=by_region,
        avg_min_salary=float(avg_min) if avg_min is not None else None,
        avg_max_salary=float(avg_max) if avg_max is not None else None,
        last_scrape=last_scrape,
        scored=scored,
        unscored=total - scored,
    )


@app.post("/jobs/{job_id}/status", response_model=JobOut)
def set_application_status(job_id: int, body: ApplicationStatusUpdate) -> JobOut:
    """Set (or clear) the pipeline status for an offer.

    Moving to "applied" auto-sets applied_date=today if still None.
    Moving to "closed" auto-archives the offer.
    """
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        job.application_status = body.status
        if body.status == "applied" and job.applied_date is None:
            job.applied_date = _utcnow().date()
        if body.status == "closed":
            job.archived = True
        if body.status is None:
            job.applied_date = None
            job.archived = False
    with get_session() as s:
        return _job_to_out(s.get(Job, job_id))


@app.post("/jobs/{job_id}/notes", response_model=JobOut)
def set_notes(job_id: int, body: NotesUpdate) -> JobOut:
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        job.notes = body.notes
    with get_session() as s:
        return _job_to_out(s.get(Job, job_id))


@app.delete("/jobs/{job_id}")
def delete_job(job_id: int) -> dict[str, int]:
    """Suppression définitive d'une offre (irréversible)."""
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        s.delete(job)
    return {"deleted": job_id}


@app.post("/jobs/bulk", response_model=BulkActionResponse)
def bulk_jobs(req: BulkActionRequest) -> BulkActionResponse:
    """Actions de masse sur une liste d'IDs.

    Actions disponibles :
        - delete         : suppression définitive
        - archive        : archived = True
        - unarchive      : archived = False
        - pipeline_in    : application_status = "to_study" si vide
        - pipeline_out   : application_status = None
    """
    if not req.ids:
        return BulkActionResponse(affected=0, skipped=0)

    with get_session() as s:
        # On compte d'abord ceux qui existent vraiment
        existing_ids = {
            jid for (jid,) in s.query(Job.id).filter(Job.id.in_(req.ids)).all()
        }
        skipped = len(req.ids) - len(existing_ids)

        if not existing_ids:
            return BulkActionResponse(affected=0, skipped=skipped)

        if req.action == "delete":
            stmt = sqla_delete(Job).where(Job.id.in_(existing_ids))
            res = s.execute(stmt)
            affected = res.rowcount or 0
        elif req.action == "archive":
            res = s.query(Job).filter(Job.id.in_(existing_ids)).update(
                {"archived": True}, synchronize_session=False,
            )
            affected = res or 0
        elif req.action == "unarchive":
            res = s.query(Job).filter(Job.id.in_(existing_ids)).update(
                {"archived": False}, synchronize_session=False,
            )
            affected = res or 0
        elif req.action == "pipeline_in":
            # Ajoute uniquement les offres pas déjà dans le pipeline
            res = s.query(Job).filter(
                Job.id.in_(existing_ids),
                Job.application_status.is_(None),
            ).update({"application_status": "to_study"}, synchronize_session=False)
            affected = res or 0
        elif req.action == "pipeline_out":
            res = s.query(Job).filter(Job.id.in_(existing_ids)).update(
                {"application_status": None, "applied_date": None},
                synchronize_session=False,
            )
            affected = res or 0
        else:
            raise HTTPException(400, f"Unknown action: {req.action}")

    return BulkActionResponse(affected=affected, skipped=skipped)


@app.post("/jobs/{job_id}/archive", response_model=JobOut)
def set_archived(job_id: int, body: ArchiveUpdate) -> JobOut:
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        job.archived = body.archived
    with get_session() as s:
        return _job_to_out(s.get(Job, job_id))


# ---------- Paramètres — configuration centralisée (page Paramètres du webui) ----------

# Fiche descriptive par source, affichée dans le catalogue de l'UI.
# desc = méthode d'accès ; note = retour d'expérience opérationnel ;
# requires = credentials/config nécessaires (noms de secrets ou de réglages).
_SOURCE_INFO: dict[str, dict] = {
    "linkedin":  {"desc": "via JobSpy — scraping des pages de recherche publiques",
                  "note": "source la plus volumineuse ; pas de date de publication fiable"},
    "indeed":    {"desc": "via JobSpy — API mobile non officielle",
                  "note": "liens jk éphémères ; le lien direct employeur est promu quand dispo"},
    "glassdoor": {"desc": "via JobSpy",
                  "note": "anti-bot DataDome (403) — historiquement inexploitable"},
    "zip_recruiter": {"desc": "via JobSpy",
                      "note": "orienté US/CA — peu de résultats FR"},
    "google":    {"desc": "via JobSpy — Google Jobs (paramètre google_search_term dédié)",
                  "note": "Google sert la version sans JS au NAS → 0 résultat (limite "
                          "upstream JobSpy, même regex en 1.1.82) — garder désactivé"},
    "francetravail": {"desc": "API officielle (OAuth2) — requête structurée par codes ROME "
                              "+ qualification cadre + départements, PAS par mots-clés",
                      "note": "source la plus propre ; config fine dans « connecteurs »",
                      "requires": ["FT_CLIENT_ID", "FT_CLIENT_SECRET"]},
    "freework":  {"desc": "API interne + fallback HTML", "note": "freelance/tech FR"},
    "himalayas": {"desc": "scraping HTML", "note": "Cloudflare challenge — historiquement 0 résultat"},
    "remotive":  {"desc": "API JSON publique", "note": "remote monde — 0 résultat sur profil FR"},
    "greenhouse": {"desc": "API JSON publique par board d'entreprise (ATS)",
                   "note": "boards à lister dans « connecteurs » ; filtre géo à l'ingestion",
                   "requires": ["greenhouse_boards"]},
    "workday":   {"desc": "API JSON par tenant d'entreprise (ATS)",
                  "note": "tenants à lister dans « connecteurs »",
                  "requires": ["workday_sites"]},
    "apec":      {"desc": "webservice JSON interne", "note": "cadres FR — actif depuis 2026-06"},
    "wttj":      {"desc": "API Algolia de Welcome to the Jungle", "note": "startups/scale-ups FR"},
    "hellowork": {"desc": "scraping HTML direct", "note": "généraliste FR"},
    "cadremploi": {"desc": "scraping HTML direct", "note": "HTTP 403 intermittents (anti-bot)"},
    "choisirservicepublic": {"desc": "API JSON — offres fonction publique, domaine SI",
                             "note": "domaine(s) via CSP_DOMAINS (.env)"},
}


def _sites_catalog() -> list[dict]:
    """Catalogue de toutes les sources connues, avec leur état effectif."""
    from connectors import get_connector, registered_platforms

    selected = set(app_settings.get().search.sites)
    catalog: list[dict] = []
    for name in app_settings.JOBSPY_SITES:
        info = _SOURCE_INFO.get(name, {})
        catalog.append({
            "name": name,
            "kind": "jobspy",
            "available": True,
            "selected": name in selected,
            "structured": False,
            "desc": info.get("desc"),
            "note": info.get("note"),
            "requires": info.get("requires", []),
        })
    for name in registered_platforms():
        conn = get_connector(name)
        info = _SOURCE_INFO.get(name, {})
        catalog.append({
            "name": name,
            "kind": "connector",
            "available": bool(conn and conn.is_enabled()),
            "selected": name in selected,
            "structured": not getattr(conn, "uses_search_terms", True),
            "desc": info.get("desc"),
            "note": info.get("note"),
            "requires": info.get("requires", []),
        })
    return catalog


def _settings_payload() -> dict:
    """Réponse commune GET/PUT /settings — état complet pour la page Paramètres."""
    return {
        "settings": app_settings.get().model_dump(),
        "defaults": app_settings.AppSettings().model_dump(),
        "secrets": app_settings.secret_status(),
        "sites": _sites_catalog(),
        "next_scrape": scheduler_module.next_scrape_run(),
    }


@app.get("/settings")
def get_settings() -> dict:
    """Configuration effective + catalogue des sources + statut (masqué) des clés."""
    return _settings_payload()


@app.put("/settings")
def put_settings(patch: dict[str, Any] = Body(...)) -> dict:
    """Applique un patch partiel (par section), valide, persiste, applique à chaud.

    Exemple de body : {"weights": {"content": 0.5, "geo": 0.25}}. Une validation
    en échec (somme des poids ≠ 1, source inconnue…) renvoie 422 et ne change rien.
    """
    try:
        app_settings.update(patch)
    except (ValidationError, ValueError) as e:
        raise HTTPException(422, detail=str(e))
    return _settings_payload()


@app.post("/settings/reset")
def reset_settings(section: Optional[str] = Query(
    None, description="Section à réinitialiser (weights/llm/search/scheduler) — vide = tout.",
)) -> dict:
    """Réinitialise une section (ou toute la config) aux défauts codés + .env."""
    try:
        app_settings.reset(section)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    return _settings_payload()


@app.post("/settings/secrets")
def set_settings_secret(body: dict[str, Any] = Body(...)) -> dict:
    """Pose (ou retire si value vide) une clé API dans config/secrets.json.

    Body : {"name": "GROQ_API_KEY", "value": "gsk_…"} — value vide/absente retire
    la surcharge (retour au .env). La valeur n'est JAMAIS renvoyée par l'API.
    """
    name = str(body.get("name", ""))
    value = body.get("value")
    try:
        app_settings.set_secret(name, value if value is None else str(value))
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    return {"secrets": app_settings.secret_status()}


@app.get("/config/prompt")
def get_prompt() -> dict:
    """System prompt de scoring effectif (pour édition dans la page Paramètres)."""
    return scoring.get_prompt()


@app.put("/config/prompt")
def put_prompt(body: dict[str, Any] = Body(...)) -> dict:
    """Écrit config/scoring_prompt.txt et recharge. Body : {"text": "..."}."""
    try:
        return scoring.save_prompt(str(body.get("text", "")))
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@app.post("/config/prompt/reset")
def reset_prompt() -> dict:
    """Restaure le prompt par défaut (réécrit le fichier depuis le code)."""
    scoring.export_default_prompt(force=True)
    return scoring.reload_prompt()


@app.get("/config/blacklist")
def get_blacklist() -> dict:
    """Blacklist effective (patterns titres, abréviations, entreprises)."""
    return constants.get_blacklist()


@app.put("/config/blacklist")
def put_blacklist(body: dict[str, Any] = Body(...)) -> dict:
    """Valide chaque regex, écrit config/blacklist.json, recharge.

    Body : {"title_patterns": [...], "title_abbr": [...], "companies": [...]}.
    """
    try:
        return constants.save_blacklist(
            [str(p) for p in body.get("title_patterns", [])],
            [str(a) for a in body.get("title_abbr", [])],
            [str(c) for c in body.get("companies", [])],
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ---------- Diagnostics — tests de connexion (clés API, sources, prompt) ----------

_TEST_SEARCH_TERM = "Technical Account Manager"
_SOURCE_TEST_TIMEOUT_S = 90


@app.post("/sources/{name}/test")
async def test_source(name: str) -> dict:
    """Sonde une source EN CONDITIONS RÉELLES : une mini-recherche (3 résultats max).

    Vérifie credentials, connectivité et anti-bot d'un coup. Un `ok` avec 0
    résultat signifie « joignable mais rien ne matche le terme de test » —
    normal pour les sources à faible volume.
    """
    import time as _time
    from connectors import get_connector

    t0 = _time.monotonic()

    def _done(ok: bool, records: int, detail: str, errors: list[str] | None = None) -> dict:
        return {
            "name": name, "ok": ok, "records": records, "detail": detail,
            "errors": (errors or [])[:3],
            "duration_s": round(_time.monotonic() - t0, 1),
        }

    try:
        if name in app_settings.JOBSPY_SITES:
            def _run_jobspy() -> int:
                import jobspy_patch  # noqa: F401  (timeout Indeed 30s)
                from jobspy import scrape_jobs
                cfg = app_settings.get().search
                df = scrape_jobs(
                    site_name=[name], search_term=_TEST_SEARCH_TERM,
                    # Google Jobs a son propre paramètre plein-texte (sinon 0 résultat).
                    google_search_term=f"{_TEST_SEARCH_TERM} emplois {cfg.location}",
                    location=cfg.location, results_wanted=3, hours_old=720,
                    country_indeed=cfg.country, description_format="markdown", verbose=0,
                )
                return 0 if df is None else len(df)

            count = await asyncio.wait_for(
                asyncio.to_thread(_run_jobspy), timeout=_SOURCE_TEST_TIMEOUT_S,
            )
            return _done(count > 0, count,
                         f"{count} offre(s) remontée(s) sur le terme de test"
                         if count else "joignable mais 0 résultat (anti-bot possible)")

        conn = get_connector(name)
        if conn is None:
            raise HTTPException(404, f"source inconnue : {name}")
        if not conn.is_enabled():
            return _done(False, 0, "désactivée — credentials/configuration manquants")

        term = None if not getattr(conn, "uses_search_terms", True) else _TEST_SEARCH_TERM
        res = await asyncio.wait_for(
            conn.scrape(search_term=term, location="France", country="France",
                        hours_old=720, results_wanted=3),
            timeout=_SOURCE_TEST_TIMEOUT_S,
        )
        n = len(res.records)
        if res.errors and not n:
            return _done(False, 0, "échec — voir erreurs", res.errors)
        return _done(True, n,
                     f"{n} offre(s) remontée(s)" if n
                     else "joignable, 0 résultat sur le terme de test",
                     res.errors)
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        return _done(False, 0, f"timeout après {_SOURCE_TEST_TIMEOUT_S}s")
    except Exception as e:
        return _done(False, 0, f"{type(e).__name__}: {e}")


@app.post("/settings/secrets/test")
async def test_secret(body: dict[str, Any] = Body(...)) -> dict:
    """Vérifie qu'une clé fonctionne par un appel réel léger (aucune valeur renvoyée).

    Body : {"name": "...", "value": "..."} — `value` absente = teste la clé
    actuellement configurée (fichier ou .env). Pour France Travail, la PAIRE
    id+secret est testée (l'autre moitié vient de la config courante).
    Permet le workflow « tester AVANT d'enregistrer ».
    """
    import time as _time
    import httpx

    name = str(body.get("name", ""))
    if name not in app_settings.SECRET_NAMES:
        raise HTTPException(422, f"secret inconnu : {name}")
    candidate = str(body.get("value") or "").strip() or None

    def effective(n: str) -> str:
        if n == name and candidate:
            return candidate
        return app_settings.get_secret(n)

    t0 = _time.monotonic()

    def _done(ok: bool, detail: str) -> dict:
        return {"name": name, "ok": ok, "detail": detail,
                "tested": "valeur candidate" if candidate else "valeur configurée",
                "latency_ms": int((_time.monotonic() - t0) * 1000)}

    async def _list_models(url: str, headers: dict, provider_label: str,
                           provider_name: str) -> dict:
        """Probe générique « liste des modèles » — vérifie aussi que le modèle
        configuré pour ce provider existe bien chez lui."""
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers=headers)
        if r.status_code != 200:
            return _done(False, f"refusée par {provider_label} (HTTP {r.status_code})")
        payload = r.json()
        models = {m.get("id") or m.get("name", "") for m in payload.get("data", [])}
        cfg = app_settings.get().llm.provider(provider_name)
        extra = ""
        if cfg and models and not any(cfg.model in m for m in models):
            extra = f" — ⚠ le modèle configuré « {cfg.model} » n'est pas dans la liste"
        return _done(True, f"valide — {len(models)} modèles accessibles{extra}")

    try:
        key = effective(name)

        if name == "GROQ_API_KEY":
            if not key:
                return _done(False, "aucune clé à tester")
            return await _list_models("https://api.groq.com/openai/v1/models",
                                      {"Authorization": f"Bearer {key}"}, "Groq", "groq")

        if name == "ANTHROPIC_API_KEY":
            if not key:
                return _done(False, "aucune clé à tester")
            return await _list_models("https://api.anthropic.com/v1/models",
                                      {"x-api-key": key, "anthropic-version": "2023-06-01"},
                                      "Anthropic", "anthropic")

        if name == "OPENAI_API_KEY":
            if not key:
                return _done(False, "aucune clé à tester")
            return await _list_models("https://api.openai.com/v1/models",
                                      {"Authorization": f"Bearer {key}"}, "OpenAI", "openai")

        if name == "GEMINI_API_KEY":
            if not key:
                return _done(False, "aucune clé à tester")
            return await _list_models(
                "https://generativelanguage.googleapis.com/v1beta/openai/models",
                {"Authorization": f"Bearer {key}"}, "Google Gemini", "google")

        if name == "OPENROUTER_API_KEY":
            if not key:
                return _done(False, "aucune clé à tester")
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get("https://openrouter.ai/api/v1/auth/key",
                                headers={"Authorization": f"Bearer {key}"})
            if r.status_code != 200:
                return _done(False, f"refusée par OpenRouter (HTTP {r.status_code})")
            data = r.json().get("data", {})
            usage = data.get("usage")
            limit = data.get("limit")
            return _done(True, f"valide — usage {usage} / limite {limit if limit is not None else '∞'} $")

        if name in ("WTTJ_ALGOLIA_APP_ID", "WTTJ_ALGOLIA_KEY"):
            # Vraie requête Algolia (1 hit max) avec la paire effective.
            from connectors import welcometothejungle as wttj
            app_id = effective("WTTJ_ALGOLIA_APP_ID") or wttj._DEFAULT_APP_ID
            api_key = effective("WTTJ_ALGOLIA_KEY") or wttj._DEFAULT_API_KEY
            headers = dict(wttj.algolia_headers())
            headers["x-algolia-application-id"] = app_id
            headers["x-algolia-api-key"] = api_key
            url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{wttj.JOBS_INDEX}/query"
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(url, headers=headers,
                                 json={"query": _TEST_SEARCH_TERM, "hitsPerPage": 1})
            if r.status_code != 200:
                return _done(False, f"refusée par Algolia (HTTP {r.status_code})")
            return _done(True, f"valide — {r.json().get('nbHits', '?')} hits sur le terme de test")

        # France Travail : le token OAuth2 valide la PAIRE id+secret.
        cid, cs = effective("FT_CLIENT_ID"), effective("FT_CLIENT_SECRET")
        if not cid or not cs:
            return _done(False, "il faut FT_CLIENT_ID ET FT_CLIENT_SECRET pour tester")
        from connectors.francetravail import TOKEN_URL
        scope = f"application_{cid} api_offresdemploiv2 o2dsoffre"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": cid,
                      "client_secret": cs, "scope": scope},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if r.status_code != 200 or "access_token" not in r.json():
            return _done(False, f"OAuth2 refusé (HTTP {r.status_code}) — "
                                "régénérer les credentials sur francetravail.io ?")
        return _done(True, "valide — token OAuth2 obtenu")
    except Exception as e:
        return _done(False, f"{type(e).__name__}: {e}")


@app.get("/scoring/samples")
def scoring_samples() -> dict:
    """Offres fictives calibrées pour le banc d'essai du prompt."""
    return {"samples": scoring.SAMPLE_JOBS}


@app.post("/scoring/test")
async def scoring_test(body: dict[str, Any] = Body(...)) -> dict:
    """Note une offre fictive (jamais persistée), au choix avec un prompt candidat.

    Body : {title, company, job_type, platform, description, prompt?}.
    `prompt` présent = testé SANS être enregistré (validation avant mise en prod).
    ⚠ Consomme un appel LLM réel (~3 200 tokens du quota Groq).
    """
    prompt_override = str(body.get("prompt") or "").strip() or None
    if prompt_override and len(prompt_override) < 200:
        raise HTTPException(422, "prompt candidat trop court (< 200 chars)")
    fields = {k: body.get(k) for k in ("title", "company", "job_type", "platform", "description")}
    if not str(fields.get("title") or "").strip():
        raise HTTPException(422, "titre requis")
    # 150 s < timeout du client webui (180 s) : l'appelant reçoit toujours ce
    # message clair plutôt qu'un timeout réseau. Les deux providers en 429
    # (quota Groq journalier + OpenRouter free saturé) mènent ici.
    try:
        return await asyncio.wait_for(
            scoring.score_adhoc(fields, prompt_override), timeout=150,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "les providers LLM ne répondent pas (quota journalier "
                                 "Groq épuisé et fallback OpenRouter saturé ?) — "
                                 "réessayer après le reset (~24 h)")
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/rescore/recompute")
def rescore_recompute() -> dict[str, int]:
    """Recombine le score final depuis les composantes stockées — SANS appel LLM.

    À utiliser après un changement de POIDS : base_score (LLM), score_geo,
    score_salary et score_freshness sont déjà en base, seule la pondération
    change. Instantané et gratuit, contrairement à /rescore?force=true qui
    repasse chaque offre au modèle.
    """
    updated = 0
    with get_session() as s:
        jobs = s.query(Job).filter(Job.base_score.isnot(None)).all()
        for job in jobs:
            new_score = compute_final_score(
                content=job.base_score,
                geo=job.score_geo,
                salary=job.score_salary,
                freshness=job.score_freshness,
            )
            if new_score != job.relevance_score:
                job.relevance_score = new_score
                updated += 1
    return {"scored": len(jobs) if jobs else 0, "updated": updated}


@app.get("/logs", response_model=ScrapeLogsResponse)
def list_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, pattern="^(running|success|failed)$"),
) -> ScrapeLogsResponse:
    """Return recent scrape runs, newest first."""
    with get_session() as s:
        q = s.query(ScrapeLog)
        if status:
            q = q.filter(ScrapeLog.status == status)
        total = q.count()
        logs = (
            q.order_by(desc(ScrapeLog.started_at))
            .offset(offset)
            .limit(limit)
            .all()
        )
        items = [_log_to_out(l) for l in logs]
    return ScrapeLogsResponse(total=total, items=items)
