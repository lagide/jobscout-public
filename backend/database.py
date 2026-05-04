"""SQLAlchemy engine + session factory. Single SQLite file in /data (bind-mounted).

Also hosts a tiny idempotent migration helper so new columns added to models can be
rolled out without wiping the DB.

Note FR : on active le mode WAL (Write-Ahead Logging) au boot pour permettre les
lectures concurrentes pendant qu'un scrape écrit — utile car scraper et UI lisent
la même base.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
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


# Active les PRAGMAs SQLite à chaque nouvelle connexion :
#   journal_mode=WAL  → permet les lectures concurrentes pendant l'écriture
#   synchronous=NORMAL→ bon compromis durabilité/perf (vs FULL trop lent)
#   foreign_keys=ON   → contrainte FK active (par défaut OFF en SQLite)
@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: D401
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


class Base(DeclarativeBase):
    pass


@contextmanager
def get_session() -> Session:
    """Session avec commit/rollback/close automatiques (context manager)."""
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
    """Ajoute une colonne si elle n'existe pas encore. Idempotent."""
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))
    logger.info("Migration: added column %s.%s (%s)", table, column, ddl)


def _ensure_index(ddl: str) -> None:
    """Crée un index si absent. Le DDL doit contenir IF NOT EXISTS pour être idempotent."""
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _migrate() -> None:
    """Migrations colonnes incrémentales (ordre = du moins destructif au plus)."""
    # Phase 1 — additions sur jobs
    _ensure_column("jobs", "content_hash", "VARCHAR(64)")
    _ensure_column("jobs", "sources", "TEXT")
    _ensure_column("jobs", "geo_profile", "VARCHAR(32)")
    _ensure_column("jobs", "region", "VARCHAR(8)")

    # Phase 2 — enrichissement + FX
    _ensure_column("jobs", "work_mode", "VARCHAR(16)")
    _ensure_column("jobs", "language", "VARCHAR(8)")
    _ensure_column("jobs", "base_score", "FLOAT")
    _ensure_column("jobs", "salary_eur_min", "FLOAT")
    _ensure_column("jobs", "salary_eur_max", "FLOAT")
    _ensure_column("jobs", "salary_effective_eur", "FLOAT")

    # Phase 4 — composantes du scoring multi-critères
    _ensure_column("jobs", "score_geo", "FLOAT")
    _ensure_column("jobs", "score_salary", "FLOAT")
    _ensure_column("jobs", "score_freshness", "FLOAT")

    # Phase 3 — pipeline Kanban
    _ensure_column("jobs", "application_status", "VARCHAR(32)")
    _ensure_column("jobs", "applied_date", "DATE")
    _ensure_column("jobs", "notes", "TEXT")
    _ensure_column("jobs", "archived", "BOOLEAN DEFAULT 0 NOT NULL")

    # Phase 5 — métrique blacklist sur les ScrapeLog
    _ensure_column("scrape_logs", "blacklisted", "INTEGER DEFAULT 0 NOT NULL")

    # Index sur les nouvelles colonnes (IF NOT EXISTS = no-op si déjà présent)
    for ddl in (
        'CREATE INDEX IF NOT EXISTS "ix_jobs_content_hash" ON "jobs"("content_hash")',
        'CREATE INDEX IF NOT EXISTS "ix_jobs_geo_profile" ON "jobs"("geo_profile")',
        'CREATE INDEX IF NOT EXISTS "ix_jobs_region" ON "jobs"("region")',
        'CREATE INDEX IF NOT EXISTS "ix_jobs_work_mode" ON "jobs"("work_mode")',
        'CREATE INDEX IF NOT EXISTS "ix_jobs_language" ON "jobs"("language")',
        'CREATE INDEX IF NOT EXISTS "ix_jobs_application_status" ON "jobs"("application_status")',
    ):
        _ensure_index(ddl)


def init_db() -> None:
    """Crée les tables si absentes + applique les migrations incrémentales."""
    # Import des modèles pour qu'ils soient enregistrés sur Base avant create_all
    from models import Job, ScrapeLog  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate()
