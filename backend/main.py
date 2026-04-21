"""FastAPI entry point — endpoints, lifespan-managed scheduler, CORS for Streamlit."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, desc, func, or_

import notifier
from constants import GEO_PROFILES
from database import get_session, init_db
from models import Job, ScrapeLog
from schemas import (
    ApplicationStatusUpdate,
    ArchiveUpdate,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Helpers ----------

def _job_to_out(job: Job) -> JobOut:
    """Serialize a Job row, decoding the sources JSON."""
    sources_list: list[JobSourceEntry] = []
    if job.sources:
        try:
            raw = json.loads(job.sources)
            for entry in raw:
                try:
                    sources_list.append(JobSourceEntry(**entry))
                except Exception:
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
        errors=errors,
        fatal_error=log.fatal_error,
        sites=sites,
        search_terms_count=log.search_terms_count,
    )


# ---------- Endpoints ----------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


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
            total = s.query(Job.id).count()
            s.query(Job).update({
                "relevance_score": None,
                "base_score": None,
                "score_geo": None,
                "score_salary": None,
                "score_freshness": None,
                "relevance_reasoning": None,
            })
        background.add_task(rescore_all_force)
        return {"pending": total}
    else:
        with get_session() as s:
            pending = s.query(Job.id).filter(Job.relevance_score.is_(None)).count()
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
            job.applied_date = datetime.utcnow().date()
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


@app.post("/jobs/{job_id}/archive", response_model=JobOut)
def set_archived(job_id: int, body: ArchiveUpdate) -> JobOut:
    with get_session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        job.archived = body.archived
    with get_session() as s:
        return _job_to_out(s.get(Job, job_id))


@app.post("/telegram/test")
async def telegram_test() -> dict[str, object]:
    """Send a ping to the configured Telegram bot. Returns configuration + send status."""
    if not notifier.is_configured():
        raise HTTPException(
            400,
            "Telegram is not configured. Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env.",
        )
    ok = await notifier.send_markdown(
        f"✅ *JobScout* — ping de test\n"
        f"_Horodatage_: {datetime.utcnow().isoformat()}Z"
    )
    return {
        "configured": True,
        "min_score": notifier.get_min_score(),
        "sent": ok,
    }


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
