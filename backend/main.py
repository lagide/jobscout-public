"""Point d'entrée FastAPI — endpoints, scheduler géré par lifespan, CORS pour Streamlit."""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from sqlalchemy import delete as sqla_delete
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, desc, func, or_

from constants import GEO_PROFILES
from database import get_session, init_db
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
from scheduler import build_scheduler
from scoring import rescore_all_force, rescore_all_missing, score_jobs_background
from scraper import scrape_and_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initializing DB")
    init_db()
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started — %d job(s) registered", len(scheduler.get_jobs()))
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
_DEFAULT_ORIGINS = "http://localhost:8501"
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
    db_path = "/data/jobs.db"
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
    remote_only: bool = False,
    application_status: Optional[list[str]] = Query(
        None,
        description="to_study / interesting / applied / interview / closed",
    ),
    in_pipeline: Optional[bool] = Query(
        None,
        description="true = only offers with a non-null application_status; false = the opposite.",
    ),
    include_archived: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    order_by: str = Query("relevance", pattern="^(relevance|date|scraped)$"),
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
