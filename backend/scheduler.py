"""APScheduler — scrapes périodiques + nettoyage de la base.

Lance la recherche par défaut toutes les `scheduler.refresh_interval_hours`
(settings) sur chaque profil listé dans SCHEDULED_PROFILES (env, défaut "France").
Chaque profil produit sa propre ligne ScrapeLog.

La cadence, l'activation du scrape auto et la rétention viennent de la config
centralisée (settings, section `scheduler`) : un PUT /settings re-planifie le
job à chaud via apply_settings(), sans restart.

Job quotidien de cleanup (03:00 UTC) :
  - Supprime les offres > job_retention_days (défaut 90) sans application_status
    et non archivées.
  - Garde seulement les scrape_log_keep derniers ScrapeLog (défaut 100).
  - Lance VACUUM le 1er jour de chaque mois (après la purge) pour récupérer l'espace.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, text

import settings as app_settings
from constants import DEFAULT_PROFILE, GEO_PROFILES, is_company_blacklisted, is_title_blacklisted
from database import engine, get_session
from enrichment import compute_final_score, compute_freshness_score
from models import Job, ScrapeLog
from scraper import scrape_and_store
from schemas import SearchRequest

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Wrapper unique : datetime.utcnow() est deprecated en Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Liste de clés de profil séparées par virgule. Par défaut juste France pour limiter le coût.
_RAW_SCHEDULED = os.getenv("SCHEDULED_PROFILES", DEFAULT_PROFILE)
SCHEDULED_PROFILES: list[str] = [
    p.strip() for p in _RAW_SCHEDULED.split(",") if p.strip() in GEO_PROFILES
]
if not SCHEDULED_PROFILES:
    SCHEDULED_PROFILES = [DEFAULT_PROFILE]

# Référence au scheduler en cours (posée par build_scheduler) pour permettre
# la re-planification à chaud quand la config change (apply_settings).
_scheduler: AsyncIOScheduler | None = None


async def scheduled_refresh() -> None:
    """Exécute la recherche par défaut sur chaque profil activé, séquentiellement."""
    logger.info(
        "Scheduled refresh starting at %s — profiles=%s",
        _utcnow().isoformat(), SCHEDULED_PROFILES,
    )
    for profile_key in SCHEDULED_PROFILES:
        try:
            req = SearchRequest(profile=profile_key)
            result = await scrape_and_store(req, triggered_by="scheduler")
            logger.info(
                "Scheduled refresh [%s] done — scraped=%d new=%d dup=%d merged=%d",
                profile_key, result.scraped, result.new,
                result.duplicates, result.merged_sources,
            )
        except Exception:
            # On log mais on continue avec le profil suivant — un échec n'arrête pas le cycle.
            logger.exception("Scheduled refresh failed for profile=%s", profile_key)


def _cleanup_database_sync() -> dict:
    """Cleanup DB synchrone — exécuté dans un thread via asyncio.to_thread()."""
    cfg = app_settings.get().scheduler
    cutoff_age = _utcnow() - timedelta(days=cfg.job_retention_days)
    cutoff_seen = _utcnow() - timedelta(days=cfg.job_not_seen_days)
    result = {"jobs_deleted_age": 0, "jobs_deleted_unseen": 0, "jobs_deleted_blacklist": 0, "logs_deleted": 0, "vacuumed": False}

    with get_session() as session:
        # 1a) Purge des offres anciennes sans interaction utilisateur (rétention max)
        purge_age = delete(Job).where(
            Job.scraped_at < cutoff_age,
            Job.application_status.is_(None),
            Job.archived == False,  # noqa: E712  -- requis par SQLAlchemy pour les Boolean
        )
        res = session.execute(purge_age)
        result["jobs_deleted_age"] = res.rowcount or 0

        # 1b) Purge des offres "pourvues" — pas revues par le scraper depuis NOT_SEEN_DAYS.
        # On exclut les jobs très récents (< NOT_SEEN_DAYS depuis scraped_at) pour
        # éviter de supprimer un job juste inséré dans un profil rare.
        purge_unseen = delete(Job).where(
            Job.last_seen_at < cutoff_seen,
            Job.scraped_at < cutoff_seen,
            Job.application_status.is_(None),
            Job.archived == False,  # noqa: E712
        )
        res = session.execute(purge_unseen)
        result["jobs_deleted_unseen"] = res.rowcount or 0

        # 1c) Purge rétroactive blacklist titre/entreprise (cas où la blacklist
        # a été enrichie après l'insertion de certains jobs).
        candidates = session.query(Job.id, Job.title, Job.company).filter(
            Job.application_status.is_(None),
            Job.archived == False,  # noqa: E712
        ).all()
        to_purge_bl = [
            jid for jid, title, company in candidates
            if is_title_blacklisted(title) or is_company_blacklisted(company)
        ]
        if to_purge_bl:
            session.execute(delete(Job).where(Job.id.in_(to_purge_bl)))
        result["jobs_deleted_blacklist"] = len(to_purge_bl)

        # 2) Rotation des scrape_logs — on ne garde que les SCRAPE_LOG_KEEP plus récents
        threshold_row = session.execute(
            text(
                "SELECT id FROM scrape_logs "
                "ORDER BY started_at DESC LIMIT 1 OFFSET :keep"
            ),
            {"keep": cfg.scrape_log_keep},
        ).first()
        if threshold_row is not None:
            threshold_id = threshold_row[0]
            log_stmt = delete(ScrapeLog).where(ScrapeLog.id <= threshold_id)
            res = session.execute(log_stmt)
            result["logs_deleted"] = res.rowcount or 0
        # Le commit se fait automatiquement à la sortie du context manager (get_session)

    # 3) VACUUM mensuel — DOIT tourner hors transaction (SQLite restriction)
    if _utcnow().day == 1:
        with engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")
        result["vacuumed"] = True

    return result


def _refresh_freshness_sync() -> int:
    """Recalcule score_freshness + relevance_score pour toutes les offres scorées.

    Pourquoi : compute_freshness_score dépend de date.today(), donc un score
    figé au moment du scoring dérive — deux offres identiques scorées à 3 jours
    d'écart n'avaient pas la même fraîcheur stockée. Ce recalcul quotidien
    (déterministe, sans LLM) garde toute la base cohérente « au jour J ».
    Ne touche ni base_score, ni reasoning, ni statut/notes/archived.
    """
    updated = 0
    with get_session() as session:
        jobs = session.query(Job).filter(Job.base_score.isnot(None)).all()
        for job in jobs:
            fresh = compute_freshness_score(job.date_posted, fallback=job.scraped_at)
            if fresh == job.score_freshness:
                continue
            job.score_freshness = fresh
            job.relevance_score = compute_final_score(
                content=job.base_score,
                geo=job.score_geo,
                salary=job.score_salary,
                freshness=fresh,
            )
            updated += 1
    return updated


async def refresh_freshness() -> None:
    """Recalcul quotidien de la fraîcheur (03:30 UTC + au démarrage du backend)."""
    try:
        updated = await asyncio.to_thread(_refresh_freshness_sync)
        logger.info("Freshness refresh done — %d job(s) mis à jour", updated)
    except Exception:
        logger.exception("Freshness refresh failed")


async def cleanup_database() -> None:
    """Cleanup quotidien : purge, rotation logs, VACUUM mensuel."""
    cfg = app_settings.get().scheduler
    logger.info(
        "DB cleanup starting at %s — retention=%dd, not_seen=%dd, keep_logs=%d",
        _utcnow().isoformat(), cfg.job_retention_days, cfg.job_not_seen_days,
        cfg.scrape_log_keep,
    )
    try:
        result = await asyncio.to_thread(_cleanup_database_sync)
        logger.info(
            "DB cleanup done — deleted_age=%d deleted_unseen=%d deleted_blacklist=%d logs_deleted=%d vacuumed=%s",
            result["jobs_deleted_age"], result["jobs_deleted_unseen"],
            result["jobs_deleted_blacklist"], result["logs_deleted"], result["vacuumed"],
        )
    except Exception:
        logger.exception("DB cleanup failed")


def build_scheduler() -> AsyncIOScheduler:
    """Construit le scheduler APScheduler avec ses 3 jobs (scrape, cleanup, fraîcheur)."""
    global _scheduler
    cfg = app_settings.get().scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")

    first_run = _utcnow() + timedelta(
        seconds=30 if cfg.run_on_startup else 3600 * cfg.refresh_interval_hours
    )

    job = scheduler.add_job(
        scheduled_refresh,
        trigger=IntervalTrigger(hours=cfg.refresh_interval_hours, start_date=first_run),
        id="scheduled_refresh",
        max_instances=1,  # une seule exécution simultanée
        coalesce=True,    # fusionne les runs ratés en un seul si appli pausée
        replace_existing=True,
    )
    if not cfg.scrape_enabled:
        job.pause()
        logger.info("Scrape automatique DÉSACTIVÉ (settings scheduler.scrape_enabled=false)")

    # Cleanup quotidien à 03:00 UTC
    scheduler.add_job(
        cleanup_database,
        trigger=CronTrigger(hour=3, minute=0),
        id="cleanup_database",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Recalcul quotidien de la fraîcheur à 03:30 UTC (après le cleanup)
    scheduler.add_job(
        refresh_freshness,
        trigger=CronTrigger(hour=3, minute=30),
        id="refresh_freshness",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler = scheduler
    return scheduler


def apply_settings() -> None:
    """Re-planifie le scrape périodique après un changement de config.

    Appelé par settings.apply_runtime() sur chaque PUT /settings. Idempotent :
    no-op tant que build_scheduler n'a pas tourné (scripts CLI, tests).
    """
    if _scheduler is None:
        return
    cfg = app_settings.get().scheduler
    job = _scheduler.get_job("scheduled_refresh")
    if job is None:
        return
    _scheduler.reschedule_job(
        "scheduled_refresh",
        trigger=IntervalTrigger(
            hours=cfg.refresh_interval_hours,
            start_date=_utcnow() + timedelta(hours=cfg.refresh_interval_hours),
        ),
    )
    if cfg.scrape_enabled:
        _scheduler.resume_job("scheduled_refresh")
    else:
        _scheduler.pause_job("scheduled_refresh")
    logger.info(
        "Scheduler re-planifié — interval=%dh, scrape_enabled=%s",
        cfg.refresh_interval_hours, cfg.scrape_enabled,
    )


def next_scrape_run() -> Optional[str]:
    """Prochaine exécution planifiée du scrape (ISO), ou None (pause/CLI)."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job("scheduled_refresh")
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.isoformat()
