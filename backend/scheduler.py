"""APScheduler — periodic scraping.

By default runs the default search once per REFRESH_INTERVAL_HOURS on each profile
listed in SCHEDULED_PROFILES (defaults to "France" only). Set
SCHEDULED_PROFILES="France,Suisse,Luxembourg" to scrape multiple geos each cycle.

Each profile run writes its own ScrapeLog row.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from constants import DEFAULT_PROFILE, GEO_PROFILES
from scraper import scrape_and_store
from schemas import SearchRequest

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_HOURS = int(os.getenv("REFRESH_INTERVAL_HOURS", "24"))
RUN_ON_STARTUP = os.getenv("RUN_ON_STARTUP", "true").lower() == "true"

# Comma-separated list of profile keys. Defaults to just France to keep cost low.
_RAW_SCHEDULED = os.getenv("SCHEDULED_PROFILES", DEFAULT_PROFILE)
SCHEDULED_PROFILES: list[str] = [
    p.strip() for p in _RAW_SCHEDULED.split(",") if p.strip() in GEO_PROFILES
]
if not SCHEDULED_PROFILES:
    SCHEDULED_PROFILES = [DEFAULT_PROFILE]


async def scheduled_refresh() -> None:
    """Run the default search once per enabled profile, sequentially."""
    logger.info(
        "Scheduled refresh starting at %s — profiles=%s",
        datetime.utcnow().isoformat(), SCHEDULED_PROFILES,
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
            logger.exception("Scheduled refresh failed for profile=%s", profile_key)
            # continue to the next profile


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    first_run = datetime.utcnow() + timedelta(
        seconds=30 if RUN_ON_STARTUP else 3600 * REFRESH_INTERVAL_HOURS
    )

    scheduler.add_job(
        scheduled_refresh,
        trigger=IntervalTrigger(hours=REFRESH_INTERVAL_HOURS, start_date=first_run),
        id="scheduled_refresh",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler
