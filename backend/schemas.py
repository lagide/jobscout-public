"""Pydantic schemas for request/response validation and Claude structured output."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

import settings as app_settings
from constants import DEFAULT_PROFILE

Platform = Literal[
    # JobSpy-native
    "linkedin", "indeed", "glassdoor", "zip_recruiter", "google",
    # Custom connectors
    "remotive", "francetravail", "freework", "himalayas", "greenhouse", "workday",
    "apec", "wttj", "hellowork", "cadremploi",
    "choisirservicepublic",
]


# ---------- API request/response ----------

class SearchRequest(BaseModel):
    """POST /search body.

    Les défauts (termes, sources, profondeur, fenêtre) viennent de la config
    centralisée (settings, section `search`) — éditables via la page Paramètres
    et résolus À CHAQUE requête, pas au chargement du module.

    Historique sources (utile avant de réactiver quoi que ce soit) :
      - glassdoor : DataDome 403 ; himalayas : Cloudflare challenge (__cf_chl)
      - remotive / greenhouse / workday : 0 résultat sur profil FR (retirés 2026-06-06)
      - apec / wttj réactivés 2026-06-06 (webservice JSON httpx / Playwright).
    """

    search_terms: list[str] = Field(
        default_factory=lambda: list(app_settings.get().search.search_terms),
        description="Search terms; one JobSpy query is issued per term.",
    )
    profile: str = Field(
        default=DEFAULT_PROFILE,
        description="Geographic profile name (see GEO_PROFILES).",
    )
    # These are legacy direct overrides — if the caller doesn't pass a profile, they can
    # still drive location+country manually. Kept for backward compat.
    location: Optional[str] = None
    country: Optional[str] = None

    sites: list[Platform] = Field(
        default_factory=lambda: list(app_settings.get().search.sites),
    )
    results_per_term: int = Field(
        default_factory=lambda: app_settings.get().search.results_per_term, ge=1, le=100,
    )
    hours_old: int = Field(
        default_factory=lambda: app_settings.get().search.hours_old, ge=1,
        description="Only jobs posted within N hours.",
    )
    score_new_jobs: bool = True


class SearchResponse(BaseModel):
    scraped: int
    new: int
    duplicates: int
    merged_sources: int = 0  # Same-content hash, new platform → merged into existing row
    blacklisted: int = 0     # Offres ignorées car titre matche la blacklist (sales, alternance, technicien…)
    errors: list[str] = []
    log_id: Optional[int] = None


class JobSourceEntry(BaseModel):
    """One platform/URL entry in Job.sources."""
    platform: str
    url: str
    scraped_at: Optional[datetime] = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    title: str
    company: Optional[str]
    location: Optional[str]
    description: Optional[str]
    min_salary: Optional[float]
    max_salary: Optional[float]
    currency: Optional[str]
    salary_interval: Optional[str]
    is_remote: Optional[bool]
    job_type: Optional[str]
    date_posted: Optional[date]
    scraped_at: datetime
    job_url: str
    relevance_score: Optional[float]
    relevance_reasoning: Optional[str]

    # --- Phase 1 additions ---
    content_hash: Optional[str] = None
    sources: list[JobSourceEntry] = []
    geo_profile: Optional[str] = None
    region: Optional[str] = None

    # --- Phase 2 additions ---
    work_mode: Optional[str] = None  # full_remote / hybrid / onsite
    language: Optional[str] = None
    base_score: Optional[float] = None  # Claude content score (role + company + description)
    salary_eur_min: Optional[float] = None
    salary_eur_max: Optional[float] = None
    salary_effective_eur: Optional[float] = None

    # --- Phase 4 additions (multi-criteria scoring components) ---
    score_geo: Optional[float] = None       # geographic accessibility component
    score_salary: Optional[float] = None    # salary competitiveness component
    score_freshness: Optional[float] = None  # freshness component

    # --- Phase 3 additions (Kanban) ---
    application_status: Optional[str] = None  # to_study / interesting / applied / interview / in_process / closed
    applied_date: Optional[date] = None
    notes: Optional[str] = None
    archived: bool = False


class ApplicationStatusUpdate(BaseModel):
    """POST /jobs/{id}/status body."""
    status: Optional[
        Literal["to_study", "interesting", "applied", "interview", "in_process", "closed"]
    ] = Field(
        None,
        description="New status. Pass null (or omit) to remove the offer from the pipeline.",
    )


class NotesUpdate(BaseModel):
    """POST /jobs/{id}/notes body."""
    notes: Optional[str] = Field(None, max_length=4000)


class ArchiveUpdate(BaseModel):
    """POST /jobs/{id}/archive body."""
    archived: bool


class BulkActionRequest(BaseModel):
    """POST /jobs/bulk body — applique une action sur N offres en un appel."""
    action: Literal["delete", "archive", "unarchive", "pipeline_in", "pipeline_out"]
    ids: list[int] = Field(min_length=1, max_length=500)


class BulkActionResponse(BaseModel):
    affected: int
    skipped: int = 0  # ids inconnus / protégés


class JobsListResponse(BaseModel):
    total: int
    items: list[JobOut]


class StatsResponse(BaseModel):
    total_jobs: int
    by_platform: dict[str, int]
    by_region: dict[str, int] = {}
    avg_min_salary: Optional[float]
    avg_max_salary: Optional[float]
    last_scrape: Optional[datetime]
    scored: int
    unscored: int


class ScrapeLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    ended_at: Optional[datetime]
    profile: Optional[str]
    triggered_by: str
    status: str
    scraped: int
    new_jobs: int
    duplicates: int
    merged_sources: int
    blacklisted: int = 0
    errors: list[str] = []
    fatal_error: Optional[str]
    sites: list[str] = []
    search_terms_count: Optional[int]


class ScrapeLogsResponse(BaseModel):
    total: int
    items: list[ScrapeLogOut]


class GeoProfileOut(BaseModel):
    """Describes an available geographic scrape profile for the UI."""
    key: str
    flag: str
    location: str
    country: str
    region: str


# ---------- Claude structured output ----------

class RelevanceScore(BaseModel):
    """Schema enforced on Claude's response via tool calling."""
    score: int = Field(ge=0, le=10, description="0 = not relevant, 10 = perfect match")
    reasoning: str = Field(
        max_length=400,
        description="1-2 sentence explanation in French — role fit, seniority, domain.",
    )
