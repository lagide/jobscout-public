"""APScheduler — scrapes périodiques + nettoyage de la base.

Lance la recherche par défaut tous les REFRESH_INTERVAL_HOURS sur chaque profil
listé dans SCHEDULED_PROFILES (défaut "France"). Mettre
SCHEDULED_PROFILES="France,Suisse,Luxembourg" pour scraper plusieurs zones par cycle.

Chaque profil produit sa propre ligne ScrapeLog.

Job quotidien de cleanup (03:00 UTC) :
  - Supprime les offres > JOB_RETENTION_DAYS (défaut 90) sans application_status
    et non archivées.
  - Garde seulement les SCRAPE_LOG_KEEP derniers ScrapeLog (défaut 100).
  - Lance VACUUM le 1er jour de chaque mois (après la purge) pour récupérer l'espace.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, text

from constants import DEFAULT_PROFILE, GEO_PROFILES
from database import engine, get_session
from models import Job, ScrapeLog
from scraper import scrape_and_store
from schemas import SearchRequest

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Wrapper unique : datetime.utcnow() est deprecated en Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


REFRESH_INTERVAL_HOURS = int(os.getenv("REFRESH_INTERVAL_HOURS", "24"))
RUN_ON_STARTUP = os.getenv("RUN_ON_STARTUP", "false").lower() == "true"
JOB_RETENTION_DAYS = int(os.getenv("JOB_RETENTION_DAYS", "90"))
SCRAPE_LOG_KEEP = int(os.getenv("SCRAPE_LOG_KEEP", "100"))

# Liste de clés de profil séparées par virgule. Par défaut juste France pour limiter le coût.
_RAW_SCHEDULED = os.getenv("SCHEDULED_PROFILES", DEFAULT_PROFILE)
SCHEDULED_PROFILES: list[str] = [
    p.strip() for p in _RAW_SCHEDULED.split(",") if p.strip() in GEO_PROFILES
]
if not SCHEDULED_PROFILES:
    SCHEDULED_PROFILES = [DEFAULT_PROFILE]


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
    cutoff = _utcnow() - timedelta(days=JOB_RETENTION_DAYS)
    result = {"jobs_deleted": 0, "logs_deleted": 0, "vacuumed": False}

    with get_session() as session:
        # 1) Purge des offres anciennes sans interaction utilisateur (pas de Kanban + non archivées)
        purge_stmt = delete(Job).where(
            Job.scraped_at < cutoff,
            Job.application_status.is_(None),
            Job.archived == False,  # noqa: E712  -- requis par SQLAlchemy pour les Boolean
        )
        res = session.execute(purge_stmt)
        result["jobs_deleted"] = res.rowcount or 0

        # 2) Rotation des scrape_logs — on ne garde que les SCRAPE_LOG_KEEP plus récents
        threshold_row = session.execute(
            text(
                "SELECT id FROM scrape_logs "
                "ORDER BY started_at DESC LIMIT 1 OFFSET :keep"
            ),
            {"keep": SCRAPE_LOG_KEEP},
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


async def cleanup_database() -> None:
    """Cleanup quotidien : purge, rotation logs, VACUUM mensuel."""
    logger.info(
        "DB cleanup starting at %s — retention=%dd, keep_logs=%d",
        _utcnow().isoformat(), JOB_RETENTION_DAYS, SCRAPE_LOG_KEEP,
    )
    try:
        result = await asyncio.to_thread(_cleanup_database_sync)
        logger.info(
            "DB cleanup done — jobs_deleted=%d logs_deleted=%d vacuumed=%s",
            result["jobs_deleted"], result["logs_deleted"], result["vacuumed"],
        )
    except Exception:
        logger.exception("DB cleanup failed")


def build_scheduler() -> AsyncIOScheduler:
    """Construit le scheduler APScheduler avec ses 2 jobs (scrape périodique + cleanup)."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    first_run = _utcnow() + timedelta(
        seconds=30 if RUN_ON_STARTUP else 3600 * REFRESH_INTERVAL_HOURS
    )

    scheduler.add_job(
        scheduled_refresh,
        trigger=IntervalTrigger(hours=REFRESH_INTERVAL_HOURS, start_date=first_run),
        id="scheduled_refresh",
        max_instances=1,  # une seule exécution simultanée
        coalesce=True,    # fusionne les runs ratés en un seul si appli pausée
        replace_existing=True,
    )

    # Cleanup quotidien à 03:00 UTC
    scheduler.add_job(
        cleanup_database,
        trigger=CronTrigger(hour=3, minute=0),
        id="cleanup_database",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler
