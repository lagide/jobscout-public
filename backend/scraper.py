"""JobSpy wrapper — runs scrape_jobs in a thread and persists results.

Dedup strategy (Phase 1):
    1. (platform, job_url) uniqueness — prevents re-inserting the same URL twice on
       the same platform (enforced at DB level).
    2. content_hash = SHA256(normalized title + company + location) — collapses the
       *same* offer seen across multiple platforms into a single row whose ``sources``
       JSON field accumulates one entry per (platform, url) discovery.

Every scrape also writes a ScrapeLog row (running → success/failed) with counts and
per-term errors — surfaced in the frontend Logs tab.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from connectors import CONNECTOR_REGISTRY, get_connector, registered_platforms
from constants import GEO_PROFILES
from currency import compute_effective_eur, to_eur
from database import get_session
from enrichment import (
    compute_freshness_score,
    compute_geo_score,
    compute_salary_score,
    detect_language,
    detect_work_mode,
)
from models import Job, ScrapeLog
from schemas import SearchRequest, SearchResponse

# JobSpy-native platforms vs. custom connectors
_JOBSPY_PLATFORMS = {"linkedin", "indeed", "glassdoor", "zip_recruiter", "google"}

logger = logging.getLogger(__name__)


# ---------- Value normalization ----------

def _nan_to_none(v: Any) -> Any:
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


# ---------- Hash normalization ----------

_WS_RE = re.compile(r"\s+")


def _normalize(s: Optional[str]) -> str:
    """Lowercase, strip accents, collapse whitespace. Used for hashing only."""
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
    """SHA256 of normalized (title + company + location). Stable across platforms."""
    key = f"{_normalize(title)}|{_normalize(company)}|{_normalize(location)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------- Row mapping ----------

def _row_to_job_kwargs(row: pd.Series) -> dict[str, Any]:
    """Map a JobSpy DataFrame row to Job constructor kwargs."""
    return {
        "external_id": _nan_to_none(row.get("id")),
        "platform": str(row.get("site", "unknown")),
        "job_url": str(row.get("job_url") or row.get("job_url_direct") or ""),
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
    """Append entry to the sources JSON list, deduping by (platform, url)."""
    try:
        existing = json.loads(existing_json) if existing_json else []
    except (TypeError, ValueError):
        existing = []
    # Dedupe on (platform, url)
    key = (entry.get("platform"), entry.get("url"))
    for s in existing:
        if (s.get("platform"), s.get("url")) == key:
            return json.dumps(existing)
    existing.append(entry)
    return json.dumps(existing)


# ---------- Profile resolution ----------

def _resolve_profile(req: SearchRequest) -> tuple[str, str, Optional[str], Optional[str]]:
    """Pick location/country/region from either the profile or explicit overrides.

    Returns (location, country, geo_profile_name, region).
    """
    profile = GEO_PROFILES.get(req.profile) if req.profile else None

    location = req.location or (profile["location"] if profile else "France")
    country = req.country or (profile["country"] if profile else "France")
    profile_name = req.profile if profile else None
    region = profile["region"] if profile else None

    return location, country, profile_name, region


# ---------- Scrape execution ----------

def _scrape_jobspy_sync(
    req: SearchRequest, location: str, country: str, jobspy_sites: list[str]
) -> tuple[list[pd.DataFrame], list[str]]:
    """Run JobSpy for sites it supports natively."""
    from jobspy import scrape_jobs  # imported lazily to keep cold-start fast

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
    """Run all enabled custom connectors, one per term."""
    frames: list[pd.DataFrame] = []
    term_errors: list[str] = []

    # Filter to connectors that exist and are enabled (e.g. have creds)
    usable: list[tuple[str, object]] = []
    for name in connector_names:
        conn = get_connector(name)
        if conn is None:
            term_errors.append(f"[{name}] unknown connector")
            continue
        if not conn.is_enabled():
            term_errors.append(f"[{name}] disabled (missing credentials)")
            continue
        usable.append((name, conn))

    if not usable:
        return frames, term_errors

    for term in req.search_terms:
        for name, conn in usable:
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
    """Return (jobspy_sites, connector_sites) based on known registry."""
    jobspy = [s for s in sites if s in _JOBSPY_PLATFORMS]
    connectors = [s for s in sites if s in registered_platforms()]
    return jobspy, connectors


async def scrape_and_store(
    req: SearchRequest,
    triggered_by: str = "manual",
) -> SearchResponse:
    """Async entry point — scrape, dedup by (platform,url) + content_hash, persist."""
    location, country, profile_name, region = _resolve_profile(req)

    # Create the ScrapeLog row up-front so it's visible in the UI even if the run crashes.
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
    errors: list[str] = []
    new_ids: list[int] = []

    try:
        jobspy_sites, connector_sites = _split_sites(list(req.sites))

        frames: list[pd.DataFrame] = []

        # JobSpy path (runs off-thread — library is sync/blocking)
        if jobspy_sites:
            js_frames, js_errors = await asyncio.to_thread(
                _scrape_jobspy_sync, req, location, country, jobspy_sites
            )
            frames.extend(js_frames)
            errors.extend(js_errors)

        # Custom connectors path (already async)
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

            with get_session() as s:
                for _, row in combined.iterrows():
                    try:
                        kwargs = _row_to_job_kwargs(row)
                        if not kwargs["job_url"]:
                            continue

                        # (platform, job_url) guard — same URL seen again on same platform
                        existing_same_url = (
                            s.query(Job)
                            .filter(
                                Job.platform == kwargs["platform"],
                                Job.job_url == kwargs["job_url"],
                            )
                            .first()
                        )
                        if existing_same_url is not None:
                            dup_count += 1
                            continue

                        # Content hash for cross-platform dedup
                        c_hash = compute_content_hash(
                            kwargs["title"], kwargs["company"], kwargs["location"]
                        )

                        existing_by_hash = (
                            s.query(Job).filter(Job.content_hash == c_hash).first()
                        )

                        source_entry = {
                            "platform": kwargs["platform"],
                            "url": kwargs["job_url"],
                            "scraped_at": datetime.utcnow().isoformat(),
                        }

                        if existing_by_hash is not None:
                            # Same offer on another platform — merge sources only.
                            existing_by_hash.sources = _sources_append(
                                existing_by_hash.sources, source_entry
                            )
                            merged_count += 1
                            continue

                        # Enrichment: work mode, language, EUR conversion.
                        work_mode = detect_work_mode(
                            kwargs.get("description"), kwargs.get("is_remote")
                        )
                        language = detect_language(kwargs.get("description"))

                        sal_min_eur = to_eur(kwargs.get("min_salary"), kwargs.get("currency"))
                        sal_max_eur = to_eur(kwargs.get("max_salary"), kwargs.get("currency"))

                        cost_coef = 1.00
                        if profile_name and profile_name in GEO_PROFILES:
                            cost_coef = GEO_PROFILES[profile_name].get("cost_coef", 1.00)
                        # Use the upper bound when present (optimistic view of the offer),
                        # fall back to lower bound.
                        eff_base = sal_max_eur if sal_max_eur is not None else sal_min_eur
                        sal_eff_eur = compute_effective_eur(eff_base, cost_coef)

                        # Deterministic scoring components (fast, no API call)
                        sc_geo = compute_geo_score(
                            work_mode, kwargs.get("location"), kwargs.get("description")
                        )
                        sc_salary = compute_salary_score(
                            sal_min_eur, sal_max_eur, kwargs.get("salary_interval")
                        )
                        sc_freshness = compute_freshness_score(kwargs.get("date_posted"))

                        # Genuinely new offer
                        job = Job(
                            **kwargs,
                            content_hash=c_hash,
                            sources=json.dumps([source_entry]),
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
                    except Exception as e:
                        errors.append(f"{row.get('job_url', '?')}: {type(e).__name__}: {e}")

        # Fire-and-forget scoring
        if req.score_new_jobs and new_ids:
            from scoring import score_jobs_background
            asyncio.create_task(score_jobs_background(new_ids))

        # Close the log as success
        with get_session() as s:
            log = s.get(ScrapeLog, log_id)
            if log is not None:
                log.ended_at = datetime.utcnow()
                log.status = "success"
                log.scraped = scraped_total
                log.new_jobs = new_count
                log.duplicates = dup_count
                log.merged_sources = merged_count
                log.errors = json.dumps(errors[:50])

        return SearchResponse(
            scraped=scraped_total,
            new=new_count,
            duplicates=dup_count,
            merged_sources=merged_count,
            errors=errors[:20],
            log_id=log_id,
        )

    except Exception as fatal:
        logger.exception("scrape_and_store failed fatally")
        with get_session() as s:
            log = s.get(ScrapeLog, log_id)
            if log is not None:
                log.ended_at = datetime.utcnow()
                log.status = "failed"
                log.fatal_error = f"{type(fatal).__name__}: {fatal}"
                log.errors = json.dumps(errors[:50])
        return SearchResponse(
            scraped=scraped_total,
            new=new_count,
            duplicates=dup_count,
            merged_sources=merged_count,
            errors=errors[:20] + [f"FATAL: {fatal}"],
            log_id=log_id,
        )
