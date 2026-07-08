"""Configuration centralisée de JobScout — persistée dans config/settings.json.

Tout ce qui est personnalisable sans toucher au code vit ici : poids de la
formule de scoring, paramètres LLM (modèles, débits, concurrence), termes de
recherche, sources scrapées, scheduler/rétention. Le prompt de scoring et la
blacklist gardent leurs fichiers dédiés (scoring_prompt.txt / blacklist.json),
gérés par scoring.py / constants.py — mais exposés par la même page Paramètres.

Hiérarchie de résolution (du plus faible au plus fort) :
    1. défauts codés (ici et dans constants.py)
    2. variables d'environnement (.env) — compatibilité historique
    3. config/settings.json — édité par la page Paramètres, rechargeable à chaud

Les SECRETS (clés API) ne transitent JAMAIS par settings.json :
    - lecture : config/secrets.json (surcharge posée par l'UI) puis .env ;
    - l'API ne renvoie qu'un statut masqué (« configurée · …a1b2 »), jamais la valeur.

Toutes les lectures runtime passent par get() — pas de constantes figées au
chargement du module — pour qu'un PUT /settings s'applique immédiatement.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from constants import CONFIG_DIR, SEARCH_TERMS

logger = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
SECRETS_FILE = "secrets.json"

# Clés API gérées par l'UI. Liste fermée : on n'accepte pas d'écrire une
# variable arbitraire dans secrets.json.
SECRET_NAMES: tuple[str, ...] = (
    # Scoring LLM (un provider par clé — activation/modèle dans llm.providers)
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    # Sources de scraping
    "FT_CLIENT_ID",
    "FT_CLIENT_SECRET",
    "WTTJ_ALGOLIA_APP_ID",
    "WTTJ_ALGOLIA_KEY",
)

# label + groupe fonctionnel (llm = scoring, scraping = sources) — l'UI sépare les deux.
SECRET_META: dict[str, dict] = {
    "GROQ_API_KEY": {"label": "Groq", "group": "llm",
                     "help": "https://console.groq.com/keys"},
    "OPENROUTER_API_KEY": {"label": "OpenRouter", "group": "llm",
                           "help": "https://openrouter.ai/keys"},
    "ANTHROPIC_API_KEY": {"label": "Anthropic (Claude)", "group": "llm",
                          "help": "https://console.anthropic.com/settings/keys"},
    "OPENAI_API_KEY": {"label": "OpenAI", "group": "llm",
                       "help": "https://platform.openai.com/api-keys"},
    "GEMINI_API_KEY": {"label": "Google (Gemini)", "group": "llm",
                       "help": "https://aistudio.google.com/apikey"},
    "FT_CLIENT_ID": {"label": "France Travail — client ID", "group": "scraping",
                     "help": "https://francetravail.io/data/api"},
    "FT_CLIENT_SECRET": {"label": "France Travail — client secret", "group": "scraping",
                         "help": "https://francetravail.io/data/api"},
    "WTTJ_ALGOLIA_APP_ID": {"label": "WTTJ — Algolia app ID (optionnel, défaut public intégré)",
                            "group": "scraping"},
    "WTTJ_ALGOLIA_KEY": {"label": "WTTJ — Algolia API key (optionnel, défaut public intégré)",
                         "group": "scraping"},
}

# Sources connues : JobSpy natif + connecteurs custom (registry). Sert à
# valider search.sites et à construire le catalogue pour l'UI.
JOBSPY_SITES: tuple[str, ...] = ("linkedin", "indeed", "glassdoor", "zip_recruiter", "google")

_DEFAULT_SITES: list[str] = [
    "linkedin", "indeed", "francetravail", "freework", "apec",
    "wttj", "hellowork", "cadremploi", "choisirservicepublic",
]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes")


# ============================================================================
# Modèle de configuration (validation Pydantic)
# ============================================================================

class ScoringWeights(BaseModel):
    """Poids de la formule finale (enrichment.compute_final_score). Somme = 1."""
    content: float = Field(0.60, ge=0.0, le=1.0, description="Contenu (note LLM)")
    geo: float = Field(0.15, ge=0.0, le=1.0, description="Accessibilité géographique")
    salary: float = Field(0.10, ge=0.0, le=1.0, description="Salaire annualisé EUR")
    freshness: float = Field(0.10, ge=0.0, le=1.0, description="Fraîcheur de l'annonce")
    competition: float = Field(0.05, ge=0.0, le=1.0, description="Concurrence (neutre 5.0)")

    @model_validator(mode="after")
    def _sum_to_one(self) -> "ScoringWeights":
        total = self.content + self.geo + self.salary + self.freshness + self.competition
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"la somme des poids doit faire 1.0 (actuellement {total:.3f})")
        return self


# Providers de scoring supportés. Tous exposent une API OpenAI-compatible avec
# tool calling (Anthropic et Google via leurs endpoints de compatibilité
# officiels) — un seul client, une seule primitive dans scoring.py.
KNOWN_LLM_PROVIDERS: tuple[str, ...] = ("groq", "openrouter", "anthropic", "openai", "google")

# Modèles par défaut. Groq/OpenRouter : free tier ; les trois autres sont les
# modèles "cheap tier" de chaque maison, calibrés pour du scoring 0-10.
DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.5-flash",
}


class LLMProviderConfig(BaseModel):
    """Un provider de scoring : activation, modèle et débit. L'ordre dans la
    liste llm.providers EST l'ordre de priorité (le 1er configuré+activé est
    le primaire, les suivants des fallbacks)."""
    name: str
    enabled: bool = False
    model: str = ""
    rpm: int = Field(10, ge=1, le=300)

    @field_validator("name")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        if v not in KNOWN_LLM_PROVIDERS:
            raise ValueError(f"provider inconnu : {v} (connus : {list(KNOWN_LLM_PROVIDERS)})")
        return v

    @model_validator(mode="after")
    def _default_model(self) -> "LLMProviderConfig":
        if not self.model.strip():
            self.model = DEFAULT_PROVIDER_MODELS.get(self.name, "")
        return self


def _default_providers() -> list[LLMProviderConfig]:
    """Défauts historiques : Groq primaire + OpenRouter fallback (env-seedés),
    les providers payants présents mais désactivés."""
    return [
        LLMProviderConfig(
            name="groq", enabled=True,
            model=os.getenv("GROQ_MODEL", DEFAULT_PROVIDER_MODELS["groq"]),
            rpm=_env_int("GROQ_RPM", 6)),
        LLMProviderConfig(
            name="openrouter", enabled=True,
            model=os.getenv("OPENROUTER_FALLBACK_MODEL", DEFAULT_PROVIDER_MODELS["openrouter"]),
            rpm=_env_int("OPENROUTER_FALLBACK_RPM", 18)),
        LLMProviderConfig(name="anthropic", rpm=30),
        LLMProviderConfig(name="openai", rpm=60),
        LLMProviderConfig(name="google", rpm=10),
    ]


class LLMSettings(BaseModel):
    """Scoring LLM — liste ordonnée de providers + paramètres transverses."""
    providers: list[LLMProviderConfig] = Field(default_factory=_default_providers)
    concurrency: int = Field(default_factory=lambda: _env_int("SCORING_CONCURRENCY", 1), ge=1, le=8)
    retries: int = Field(default_factory=lambda: _env_int("SCORING_RETRIES", 3), ge=1, le=6)
    max_description_chars: int = Field(2000, ge=500, le=8000)
    temperature: float = Field(0.2, ge=0.0, le=1.0)

    @field_validator("providers")
    @classmethod
    def _unique_providers(cls, v: list[LLMProviderConfig]) -> list[LLMProviderConfig]:
        names = [p.name for p in v]
        if len(names) != len(set(names)):
            raise ValueError(f"providers en double : {names}")
        return v

    def provider(self, name: str) -> Optional[LLMProviderConfig]:
        return next((p for p in self.providers if p.name == name), None)


class SearchSettings(BaseModel):
    """Quoi scraper, où, et avec quelle profondeur."""
    search_terms: list[str] = Field(default_factory=lambda: list(SEARCH_TERMS), min_length=1)
    sites: list[str] = Field(default_factory=lambda: list(_DEFAULT_SITES), min_length=1)
    results_per_term: int = Field(10, ge=1, le=100)
    hours_old: int = Field(28, ge=1, le=24 * 30)
    # Ciblage géographique des sources JobSpy (linkedin/indeed/google/…) :
    # `location` est la localité de recherche, `country` le pays Indeed/Glassdoor.
    # Les connecteurs structurés ont leur propre ciblage (section connectors).
    location: str = Field("France", min_length=2)
    country: str = Field("France", min_length=2)
    # Filtre géo à l'INGESTION (geo_scope.json) : full remote = France entière ;
    # hybride/présentiel = périmètre défini dans config/geo_scope.json.
    # À désactiver si on élargit location/country au-delà de ce périmètre.
    geo_filter_enabled: bool = True

    @field_validator("search_terms")
    @classmethod
    def _clean_terms(cls, v: list[str]) -> list[str]:
        terms = [t.strip() for t in v if t and t.strip()]
        if not terms:
            raise ValueError("au moins un terme de recherche est requis")
        # dédoublonnage en conservant l'ordre
        seen: set[str] = set()
        out: list[str] = []
        for t in terms:
            key = t.lower()
            if key not in seen:
                seen.add(key)
                out.append(t)
        return out

    @field_validator("sites")
    @classmethod
    def _known_sites(cls, v: list[str]) -> list[str]:
        known = set(JOBSPY_SITES) | set(_connector_names())
        bad = [s for s in v if s not in known]
        if bad:
            raise ValueError(f"sources inconnues : {bad} (connues : {sorted(known)})")
        return v


def _env_csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or list(default)


class ConnectorSettings(BaseModel):
    """Configuration fine des connecteurs (au-delà de l'activation par source)."""
    # Connecteurs Playwright (lourds sur le NAS 1.7 Go — désactivés par défaut).
    playwright_enabled: bool = Field(default_factory=lambda: _env_bool("PLAYWRIGHT_ENABLED", False))
    # France Travail : requête structurée par codes ROME + qualification + départements.
    ft_rome_codes: list[str] = Field(
        default_factory=lambda: _env_csv("FT_ROME_CODES", ["M1802", "M1803", "M1806", "M1810"]))
    ft_qualification: str = Field(
        default_factory=lambda: os.getenv("FT_QUALIFICATION", "9"),
        pattern=r"^[0-9]?$",
        description="Niveau France Travail — 9 = cadre, vide = pas de filtre.")
    idf_departments: list[str] = Field(
        default_factory=lambda: _env_csv("IDF_DEPARTMENTS",
                                         ["75", "77", "78", "91", "92", "93", "94", "95"]))
    # Boards Greenhouse (slugs) et tenants Workday ("Société|https://…"), un par entrée.
    greenhouse_boards: list[str] = Field(default_factory=lambda: _env_csv("GREENHOUSE_BOARDS", []))
    workday_sites: list[str] = Field(default_factory=lambda: _env_csv("WORKDAY_SITES", []))

    @field_validator("ft_rome_codes")
    @classmethod
    def _rome_format(cls, v: list[str]) -> list[str]:
        import re as _re
        codes = [c.strip().upper() for c in v if c.strip()]
        bad = [c for c in codes if not _re.fullmatch(r"[A-Z]\d{4}", c)]
        if bad:
            raise ValueError(f"codes ROME invalides (format X9999) : {bad}")
        return codes

    @field_validator("idf_departments")
    @classmethod
    def _dep_format(cls, v: list[str]) -> list[str]:
        deps = [d.strip() for d in v if d.strip()]
        bad = [d for d in deps if not d.isdigit() or len(d) not in (2, 3)]
        if bad:
            raise ValueError(f"départements invalides : {bad}")
        return deps

    @field_validator("workday_sites")
    @classmethod
    def _workday_format(cls, v: list[str]) -> list[str]:
        entries = [e.strip() for e in v if e.strip()]
        bad = [e for e in entries if "|" not in e or not e.split("|", 1)[1].strip().startswith("http")]
        if bad:
            raise ValueError(f"entrées Workday invalides (attendu « Société|https://… ») : {bad}")
        return entries


class SchedulerSettings(BaseModel):
    """Cadence du scrape automatique et rétention de la base."""
    scrape_enabled: bool = True
    refresh_interval_hours: int = Field(
        default_factory=lambda: _env_int("REFRESH_INTERVAL_HOURS", 24), ge=1, le=24 * 7)
    run_on_startup: bool = Field(default_factory=lambda: _env_bool("RUN_ON_STARTUP", False))
    job_retention_days: int = Field(
        default_factory=lambda: _env_int("JOB_RETENTION_DAYS", 90), ge=7, le=365)
    job_not_seen_days: int = Field(
        default_factory=lambda: _env_int("JOB_NOT_SEEN_DAYS", 14), ge=3, le=90)
    scrape_log_keep: int = Field(
        default_factory=lambda: _env_int("SCRAPE_LOG_KEEP", 100), ge=10, le=1000)


class AppSettings(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)


def _connector_names() -> list[str]:
    """Noms des connecteurs custom. Import paresseux (évite un cycle au boot)."""
    try:
        from connectors import registered_platforms
        return registered_platforms()
    except Exception:  # pragma: no cover — settings importé hors backend (claude_score.py)
        return ["remotive", "francetravail", "freework", "himalayas", "greenhouse",
                "workday", "apec", "wttj", "hellowork", "cadremploi", "choisirservicepublic"]


# ============================================================================
# État courant + persistance
# ============================================================================

_lock = threading.Lock()
_current: AppSettings = AppSettings()


def _settings_path() -> Path:
    return CONFIG_DIR / SETTINGS_FILE


def get() -> AppSettings:
    """Configuration courante (objet validé, ne pas muter)."""
    return _current


def reload() -> dict:
    """(Re)charge config/settings.json. Fichier absent → défauts (env inclus).

    Un fichier invalide est ignoré avec erreur remontée — on ne remplace jamais
    une config valide par un état cassé.
    """
    global _current
    path = _settings_path()
    if not path.is_file():
        with _lock:
            _current = AppSettings()
        return {"source": "defaults", "error": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fresh = AppSettings.model_validate(data)
    except (ValueError, OSError) as e:
        logger.warning("settings.json invalide (%s) — config courante conservée", e)
        return {"source": str(path), "error": f"{type(e).__name__}: {e}"}
    with _lock:
        _current = fresh
    logger.info("Settings chargés depuis %s", path)
    return {"source": str(path), "error": None}


def save() -> str:
    """Persiste la configuration courante vers config/settings.json."""
    path = _settings_path()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_current.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def update(patch: dict[str, Any]) -> AppSettings:
    """Applique un patch partiel (par section), valide, persiste et applique.

    Lève ValueError/pydantic.ValidationError si le résultat est invalide —
    dans ce cas la config courante reste inchangée.
    """
    global _current
    merged = _deep_merge(_current.model_dump(), patch)
    fresh = AppSettings.model_validate(merged)  # lève si invalide
    with _lock:
        _current = fresh
    save()
    apply_runtime()
    return _current


def reset(section: Optional[str] = None) -> AppSettings:
    """Réinitialise une section (ou tout) aux défauts codés + env."""
    global _current
    defaults = AppSettings()
    if section is None:
        fresh = defaults
    else:
        if section not in AppSettings.model_fields:
            raise ValueError(f"section inconnue : {section}")
        merged = _current.model_dump()
        merged[section] = getattr(defaults, section).model_dump()
        fresh = AppSettings.model_validate(merged)
    with _lock:
        _current = fresh
    save()
    apply_runtime()
    return _current


def apply_runtime() -> None:
    """Propage la config aux composants qui gardent de l'état (scheduler).

    scoring.py et schemas.py lisent get() à chaque appel — rien à pousser.
    Import paresseux pour éviter les cycles (scheduler importe settings).
    """
    try:
        import scheduler as _sched
        _sched.apply_settings()
    except Exception:
        # Hors contexte serveur (scripts CLI) le scheduler n'existe pas — normal.
        logger.debug("apply_runtime: scheduler non applicable", exc_info=True)


def export_defaults(force: bool = False) -> Optional[str]:
    """Amorce config/settings.json avec les valeurs effectives (défauts + env).

    No-op si le fichier existe déjà (sauf force=True) — on n'écrase jamais une
    config éditée. Même convention qu'export_default_blacklist/prompt.
    """
    path = _settings_path()
    if path.exists() and not force:
        return None
    return save()


# ============================================================================
# Secrets — config/secrets.json (surcharge UI) puis variables d'environnement
# ============================================================================

def _secrets_path() -> Path:
    return CONFIG_DIR / SECRETS_FILE


def _load_secrets() -> dict[str, str]:
    path = _secrets_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if k in SECRET_NAMES and v}
    except (ValueError, OSError) as e:
        logger.warning("secrets.json illisible (%s) — surcharges ignorées", e)
        return {}


def get_secret(name: str) -> str:
    """Valeur effective d'un secret : fichier (UI) > env (.env) > ''."""
    if name in SECRET_NAMES:
        override = _load_secrets().get(name)
        if override:
            return override
    return os.getenv(name, "") or ""


def set_secret(name: str, value: Optional[str]) -> None:
    """Pose (ou retire si value falsy) une surcharge dans config/secrets.json."""
    if name not in SECRET_NAMES:
        raise ValueError(f"secret inconnu : {name} (autorisés : {list(SECRET_NAMES)})")
    secrets = _load_secrets()
    if value and value.strip():
        secrets[name] = value.strip()
    else:
        secrets.pop(name, None)
    path = _secrets_path()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover — FS exotique
        pass


def secret_status() -> list[dict]:
    """Statut masqué de chaque secret pour l'UI — jamais la valeur complète."""
    overrides = _load_secrets()
    out = []
    for name in SECRET_NAMES:
        value = overrides.get(name) or os.getenv(name, "") or ""
        source = "fichier" if name in overrides else ("env" if value else None)
        meta = SECRET_META.get(name, {})
        out.append({
            "name": name,
            "label": meta.get("label", name),
            "group": meta.get("group", "autre"),
            "help": meta.get("help"),
            "configured": bool(value),
            "source": source,
            "hint": f"…{value[-4:]}" if len(value) >= 8 else None,
        })
    return out


# Chargement initial (fichier si présent, sinon défauts+env).
reload()
