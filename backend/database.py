"""SQLAlchemy engine + session factory. Single SQLite file in /data (bind-mounted).

Also hosts a tiny idempotent migration helper so new columns added to models can be
rolled out without wiping the DB.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("DB_PATH", "/data/jobs.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def get_session() -> Session:
    """Context-managed session with automatic commit/rollback/close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------- Migration helpers ----------

def _ensure_column(table: str, column: str, ddl: str) -> None:
    """Add a column if it doesn't yet exist. Idempotent."""
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))
    logger.info("Migration: added column %s.%s (%s)", table, column, ddl)


def _ensure_index(name: str, ddl: str) -> None:
    """Create an index if it doesn't yet exist. DDL should be a full CREATE INDEX statement."""
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _migrate() -> None:
    """Apply incremental column migrations. Order matters (least destructive first)."""
    # Phase 1 additions on jobs
    _ensure_column("jobs", "content_hash", "VARCHAR(64)")
    _ensure_column("jobs", "sources", "TEXT")
    _ensure_column("jobs", "geo_profile", "VARCHAR(32)")
    _ensure_column("jobs", "region", "VARCHAR(8)")

    # Phase 2 additions (enrichment + FX)
    _ensure_column("jobs", "work_mode", "VARCHAR(16)")
    _ensure_column("jobs", "language", "VARCHAR(8)")
    _ensure_column("jobs", "base_score", "FLOAT")
    _ensure_column("jobs", "salary_eur_min", "FLOAT")
    _ensure_column("jobs", "salary_eur_max", "FLOAT")
    _ensure_column("jobs", "salary_effective_eur", "FLOAT")

    # Phase 4 additions (multi-criteria scoring components)
    _ensure_column("jobs", "score_geo",       "FLOAT")
    _ensure_column("jobs", "score_salary",    "FLOAT")
    _ensure_column("jobs", "score_freshness", "FLOAT")

    # Phase 3 additions (Kanban pipeline)
    _ensure_column("jobs", "application_status", "VARCHAR(32)")
    _ensure_column("jobs", "applied_date", "DATE")
    _ensure_column("jobs", "notes", "TEXT")
    _ensure_column("jobs", "archived", "BOOLEAN DEFAULT 0 NOT NULL")

    # Indexes on the new columns (IF NOT EXISTS = no-op if already present)
    _ensure_index(
        "ix_jobs_content_hash",
        'CREATE INDEX IF NOT EXISTS "ix_jobs_content_hash" ON "jobs"("content_hash")',
    )
    _ensure_index(
        "ix_jobs_geo_profile",
        'CREATE INDEX IF NOT EXISTS "ix_jobs_geo_profile" ON "jobs"("geo_profile")',
    )
    _ensure_index(
        "ix_jobs_region",
        'CREATE INDEX IF NOT EXISTS "ix_jobs_region" ON "jobs"("region")',
    )
    _ensure_index(
        "ix_jobs_work_mode",
        'CREATE INDEX IF NOT EXISTS "ix_jobs_work_mode" ON "jobs"("work_mode")',
    )
    _ensure_index(
        "ix_jobs_language",
        'CREATE INDEX IF NOT EXISTS "ix_jobs_language" ON "jobs"("language")',
    )
    _ensure_index(
        "ix_jobs_application_status",
        'CREATE INDEX IF NOT EXISTS "ix_jobs_application_status" ON "jobs"("application_status")',
    )


def init_db() -> None:
    """Create tables if they don't exist + apply incremental migrations."""
    # Import models so they're registered on Base before create_all
    from models import Job, ScrapeLog  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate()
    # Backfill content_hash for pre-existing rows (lazy — done inside scraper on next run).
