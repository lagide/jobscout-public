"""Modèles ORM SQLAlchemy.

Tables :
    jobs         — une ligne par offre distincte (dédoublonnée par content_hash entre sources).
    scrape_logs  — historique des runs de scrape (started_at, compteurs, erreurs).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _utcnow_naive() -> datetime:
    """Default factory pour les colonnes DateTime — évite datetime.utcnow() deprecated."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # JobSpy provides its own id but it can be None for some platforms, so we dedupe
    # on (platform, job_url) AND on content_hash.
    external_id: Mapped[Optional[str]] = mapped_column(String(256), index=True, nullable=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    job_url: Mapped[str] = mapped_column(String(1024))

    title: Mapped[str] = mapped_column(String(512))
    company: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(256), index=True, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    min_salary: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_salary: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    salary_interval: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    is_remote: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    job_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    date_posted: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)

    # Marqué à chaque scrape qui revoit cette offre. Si > JOB_NOT_SEEN_DAYS sans
    # être revu → considéré pourvu/retiré et purgé par cleanup_database.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)

    # Claude-computed relevance (populated asynchronously post-insertion)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float, index=True, nullable=True)
    relevance_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Phase 1 additions ---
    # Stable hash of (normalized title + company + location). Used to collapse the same
    # offer seen across multiple platforms into a single row.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)

    # JSON-encoded list of source entries, one per platform/URL where we saw this offer:
    #   [{"platform": "linkedin", "url": "https://...", "scraped_at": "2026-04-19T..."}]
    # When the same content_hash is encountered on another platform, we append here
    # instead of creating a new row.
    sources: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Geographic scrape profile that captured this offer (e.g. "France", "Suisse").
    geo_profile: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)

    # Short region code derived from geo_profile ("FR", "CH", "LU", "BE", "CA-QC", ...).
    region: Mapped[Optional[str]] = mapped_column(String(8), index=True, nullable=True)

    # --- Phase 2 additions (enrichment + FX) ---
    # "full_remote" / "hybrid" / "onsite" — inferred from description text.
    work_mode: Mapped[Optional[str]] = mapped_column(String(16), index=True, nullable=True)

    # ISO-639-1 language code inferred from the description ("fr", "en", "de", ...).
    language: Mapped[Optional[str]] = mapped_column(String(8), index=True, nullable=True)

    # Raw score returned by Claude/Haiku (before title-based adjustments).
    # relevance_score itself is the *boosted* score used across the UI and filters.
    base_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Salary normalized to EUR (from min_salary/max_salary + currency via Frankfurter).
    salary_eur_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    salary_eur_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Effective salary accounting for country cost-of-living coefficient (vs. France=1.00).
    salary_effective_eur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # --- Phase 4 additions (multi-criteria scoring) ---
    # Geographic accessibility component (0-10), computed at insertion from work_mode + location.
    score_geo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Salary competitiveness component (0-10), based on annualised EUR salary thresholds.
    score_salary: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Freshness component (0-10), temporal decay from date_posted.
    score_freshness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # --- Phase 3 additions (Kanban pipeline) ---
    # One of: None (not tracked) / "to_study" / "interesting" / "applied" / "interview" / "in_process" / "closed"
    # ("in_process" = entretien passé, étapes suivantes du processus en cours — ajouté 2026-07-02)
    application_status: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)

    # Date of actual application (set when status moves to "applied" or later).
    applied_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Free-text notes kept by the user on this offer.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Hide closed/archived offers from the main pipeline view.
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("platform", "job_url", name="uq_platform_url"),
        Index("ix_platform_scraped", "platform", "scraped_at"),
    )


class ScrapeLog(Base):
    """One row per scrape run — successful or failed, scheduled or manual."""

    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow_naive, index=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    profile: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(16), default="manual")  # manual / scheduler

    # Terminal status — "running" while in flight, then "success" or "failed".
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)

    scraped: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    merged_sources: Mapped[int] = mapped_column(Integer, default=0)  # même contenu, nouvelle plateforme
    blacklisted: Mapped[int] = mapped_column(Integer, default=0)  # offres skippées par la blacklist titre

    # JSON list of human-readable error strings (from individual source failures).
    errors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Optional single top-level error if the whole run failed.
    fatal_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sites: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    search_terms_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
