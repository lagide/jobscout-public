"""Offer enrichment — pure helpers that infer additional fields from raw text.

All functions are synchronous and side-effect free: they read a job's title/description
and return a best-effort classification. When signals are ambiguous they return None
rather than guessing.

Scoring components (Phase 4):
  compute_geo_score()       — geographic accessibility from the user's home location
                              (default calibration: Northern France, Paris-accessible)
  compute_salary_score()    — annualised EUR salary vs. seniority thresholds
  compute_freshness_score() — temporal decay from posting date
  compute_final_score()     — weighted combination of all four axes (incl. Claude content)

NOTE — Geographic calibration:
  The `_PARIS_RE` regex and the scoring ladder in `compute_geo_score` are tuned
  for a user commuting to Paris from the Hauts-de-France region. To adapt to
  another context, edit the regex (or replace with your own "target hub" pattern)
  and adjust the score ladder accordingly.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal, Optional

WorkMode = Literal["full_remote", "hybrid", "onsite"]


# ============================================================
# Work mode detection
# ============================================================

_REMOTE_RE = re.compile(
    r"\b(100\s*%\s*remote|full[\s\-]*remote|remote[\s\-]*first|"
    r"t[ée]l[ée]travail\s+(complet|total|int[ée]gral|100\s*%)|"
    r"enti[èe]rement\s+[àa]\s+distance|"
    r"work\s+from\s+anywhere|wfh\s+full)\b",
    re.IGNORECASE,
)

_HYBRID_RE = re.compile(
    r"\b(hybrid(e)?|"
    r"t[ée]l[ée]travail\s+partiel|"
    r"[1-4]\s*j(ours?)?\s*(par|/)\s*semaine|"
    r"[1-4]\s*days?\s*(per|/)\s*week|"
    r"mix\s+pr[ée]sentiel)\b",
    re.IGNORECASE,
)

_ONSITE_RE = re.compile(
    r"\b(sur[\s\-]*site|"
    r"pr[ée]sentiel\s+(uniquement|requis|obligatoire|complet)|"
    r"no\s+remote|"
    r"pas\s+de\s+t[ée]l[ée]travail|"
    r"on[\s\-]*site\s+only)\b",
    re.IGNORECASE,
)


def detect_work_mode(
    text: Optional[str], is_remote_hint: Optional[bool] = None
) -> Optional[WorkMode]:
    """Detect work arrangement. Order: explicit remote → hybrid → onsite → hint fallback."""
    t = text or ""
    if _REMOTE_RE.search(t):
        return "full_remote"
    if _HYBRID_RE.search(t):
        return "hybrid"
    if _ONSITE_RE.search(t):
        return "onsite"
    # Fallback to the JobSpy is_remote field when present
    if is_remote_hint is True:
        return "full_remote"
    if is_remote_hint is False:
        return "onsite"
    return None


# ============================================================
# Language detection
# ============================================================

_FR_MARKERS = {
    "le", "la", "les", "des", "une", "aux", "pour", "avec", "dans", "sur",
    "être", "avoir", "vous", "nous", "notre", "votre", "sécurité", "réseau",
    "responsable", "directeur", "cyber", "télétravail", "équipe", "poste",
    "candidat", "candidate", "mission", "société", "entreprise", "expérience",
    "années", "environnement", "cadre", "informatique", "compétences",
}
_EN_MARKERS = {
    "the", "and", "for", "with", "about", "our", "your", "their",
    "security", "network", "manager", "engineer", "skills", "experience",
    "years", "remote", "team", "position", "role", "responsibilities",
    "requirements", "working", "client", "business", "company", "environment",
}
_DE_MARKERS = {
    "der", "die", "das", "und", "mit", "für", "sie", "ihr", "sicherheit",
    "netzwerk", "verantwortlich", "erfahrung", "unternehmen",
}

_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)


def detect_language(text: Optional[str]) -> Optional[str]:
    """Return 'fr' / 'en' / 'de' / None. Looks at the first ~200 tokens."""
    if not text:
        return None
    tokens = _WORD_RE.findall(text.lower())[:200]
    if not tokens:
        return None
    fr = sum(1 for t in tokens if t in _FR_MARKERS)
    en = sum(1 for t in tokens if t in _EN_MARKERS)
    de = sum(1 for t in tokens if t in _DE_MARKERS)
    total_hits = fr + en + de
    if total_hits < 3:
        return None
    ranked = sorted([("fr", fr), ("en", en), ("de", de)], key=lambda x: x[1], reverse=True)
    top, second = ranked[0], ranked[1]
    if top[1] >= max(3, second[1] * 1.2):
        return top[0]
    return None


# ============================================================
# Seniority detection (kept for reference / backward compat)
# ============================================================

JUNIOR_RE = re.compile(
    r"\b(junior|d[ée]butant(e)?|apprenti(e)?|"
    r"alternan(t|ce)|"
    r"stage(?!r)|stagiaire|"
    r"intern|internship|"
    r"bac\s*\+\s*2|"
    r"sans\s+exp[ée]rience|"
    r"0\s*[-–]\s*2\s+ans)\b",
    re.IGNORECASE,
)

SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|lead|head|directeur|directrice|"
    r"responsable|architecte|expert(e)?|principal|"
    r"rssi|ciso|dsi|cto)\b",
    re.IGNORECASE,
)


def classify_seniority(title: str) -> tuple[bool, bool]:
    """Return (is_senior, is_junior) flags from the title."""
    t = title or ""
    return (bool(SENIOR_RE.search(t)), bool(JUNIOR_RE.search(t)))


def adjust_score(
    base_score: Optional[float], title: str, description: Optional[str] = None
) -> Optional[float]:
    """Legacy title-based adjustment — kept for backward compatibility.

    In the new multi-criteria scoring pipeline this is NOT called:
    compute_final_score() is used instead. This function remains available
    for any old code paths that still reference it.
    """
    if base_score is None:
        return None
    is_senior, is_junior = classify_seniority(title)
    s = float(base_score)
    if is_junior:
        s = min(s, 2.0)
    if is_senior and not is_junior:
        s = min(10.0, s + 2.0)
    return round(s, 2)


# ============================================================
# Geographic scoring — home-based, Paris-accessibility calibrated
# ============================================================
# Default calibration: user lives in Northern France (Hauts-de-France /
# Picardie zone), can commute to Paris a few days a week but full-remote
# is preferred. Adapt the regex below to your own "target hub" (e.g.
# Lyon, Geneva, Luxembourg) and tweak the ladder in compute_geo_score().

# Paris / Île-de-France location patterns (dept numbers, major cities)
_PARIS_RE = re.compile(
    r"\b(paris|île[- ]de[- ]france|ile[- ]de[- ]france|idf|"
    r"hauts[- ]de[- ]seine|seine[- ]saint[- ]denis|val[- ]de[- ]marne|"
    r"val[- ]d[''`]oise|yvelines|essonne|seine[- ]et[- ]marne|"
    r"boulogne[- ]billancourt|neuilly[- ]sur[- ]seine|courbevoie|"
    r"la[- ]d[ée]fense|nanterre|vincennes|saint[- ]cloud|levallois|"
    r"issy[- ]les[- ]moulineaux|massy|vélizy|clichy|montrouge|versailles|"
    r"meudon|châtillon|gennevilliers|asnières)\b",
    re.IGNORECASE,
)

# "4j/5", "3 jours/semaine", "80% remote" — remote day extraction
_HYBRID_DAYS_RE = re.compile(
    r"(\d)\s*j(?:ours?)?\s*(?:/|sur|par)\s*(?:5|semaine|sem\.?)"
    r"|(\d)\s*days?\s*(?:/|per|a)\s*week"
    r"|(\d{2})\s*%\s*(?:remote|t[ée]l[ée]travail|[àa]\s+distance)",
    re.IGNORECASE,
)


def _extract_remote_days(text: Optional[str]) -> Optional[int]:
    """Parse remote day count: '4j/5' → 4, '3 days/week' → 3, '80% remote' → 4."""
    if not text:
        return None
    for m in _HYBRID_DAYS_RE.finditer(text[:2000]):
        if m.group(1):          # "4j/5" pattern
            d = int(m.group(1))
            return d if 1 <= d <= 5 else None
        if m.group(2):          # "3 days/week" pattern
            d = int(m.group(2))
            return d if 1 <= d <= 5 else None
        if m.group(3):          # "80% remote" pattern
            pct = int(m.group(3))
            return round(pct / 20)  # 80 → 4, 60 → 3, 40 → 2
    return None


def compute_geo_score(
    work_mode: Optional[str],
    location: Optional[str],
    description: Optional[str] = None,
) -> float:
    """Geographic accessibility score (0-10) from the user's home location.

    Default calibration assumes the user lives outside Paris but within
    commute range, and prefers remote-heavy arrangements.

    Priority ladder (descending):
        full_remote (10) > hybrid high-ratio (9) > hybrid Paris (8) >
        hybrid unknown (6–8) > onsite Paris (5) > onsite elsewhere (1).
    """
    if work_mode == "full_remote":
        return 10.0

    # Scan location string + first 500 chars of description for Paris/IDF signals
    loc_text = (location or "") + " " + (description or "")[:500]
    is_paris = bool(_PARIS_RE.search(loc_text))
    loc_lower = (location or "").lower().strip()

    if work_mode == "hybrid":
        remote_days = _extract_remote_days(description)
        if remote_days is not None:
            if remote_days >= 4:
                return 9.0          # ≥4j/5 remote — highly accessible
            if remote_days >= 3:
                return 8.0 if is_paris else 5.5  # 3j/5
            # 1-2j/5: low remote ratio
            return 6.5 if is_paris else 3.5
        # Hybrid, ratio unknown
        return 8.0 if is_paris else 6.0

    if work_mode == "onsite":
        return 5.0 if is_paris else 1.0

    # Unknown work mode — conservative default
    if is_paris:
        return 4.5  # probably onsite Paris — feasible but uncertain
    if not loc_lower or loc_lower in ("france", "fr", "national", "télétravail", "remote"):
        return 5.5  # vague / national — could be remote-friendly
    return 2.0  # specific non-Paris location, mode unknown → likely onsite far


# ============================================================
# Salary scoring
# ============================================================

# Annual EUR thresholds for senior cyber/IT/management roles in France.
# (threshold, score) — first row where annual_eur < threshold wins.
_SALARY_BRACKETS: list[tuple[float, float]] = [
    (40_000,        1.0),
    (50_000,        3.0),
    (60_000,        5.0),
    (70_000,        6.5),
    (80_000,        7.5),
    (95_000,        8.5),
    (115_000,       9.0),
    (float("inf"), 10.0),
]

# Multiplier to convert native interval to annualised value
_INTERVAL_MULTIPLIERS: dict[str, float] = {
    "yearly":        1.0,
    "annually":      1.0,
    "annual":        1.0,
    "year":          1.0,
    "monthly":      12.0,
    "mensuel":      12.0,
    "month":        12.0,
    "daily":       220.0,   # ~220 working days/year
    "journalier":  220.0,
    "day":         220.0,
    "tjm":         220.0,   # taux journalier moyen
    "hourly":     1820.0,   # 35h/week × 52 weeks
    "horaire":    1820.0,
    "hour":       1820.0,
    "weekly":       52.0,
    "hebdomadaire": 52.0,
}


def compute_salary_score(
    salary_eur_min: Optional[float],
    salary_eur_max: Optional[float],
    interval: Optional[str] = None,
) -> float:
    """Score 0-10 based on annualised EUR salary.

    Returns 4.0 (neutral-negative) when no salary info is available.
    Uses the median when a min/max range is provided.
    """
    if salary_eur_min is None and salary_eur_max is None:
        return 4.0  # slight negative: absence of salary info is a weak signal

    mult = _INTERVAL_MULTIPLIERS.get((interval or "yearly").lower().strip(), 1.0)

    mn = (salary_eur_min or 0.0) * mult
    mx = (salary_eur_max or salary_eur_min or 0.0) * mult

    # Use median if both bounds available; otherwise use the single known value
    if salary_eur_min is not None and salary_eur_max is not None:
        annual = (mn + mx) / 2.0
    else:
        annual = mn or mx

    for threshold, score in _SALARY_BRACKETS:
        if annual < threshold:
            return score
    return 10.0


# ============================================================
# Freshness scoring
# ============================================================

def compute_freshness_score(date_posted: Optional[date]) -> float:  # type: ignore[type-arg]
    """Score 0-10 with temporal decay from today.

    Returns 5.0 (neutral) when the posting date is unknown.
    """
    if date_posted is None:
        return 5.0

    # Accept ISO string as well (connectors sometimes pass strings)
    if isinstance(date_posted, str):
        try:
            date_posted = date.fromisoformat(date_posted)
        except (ValueError, AttributeError):
            return 5.0

    age_days = (date.today() - date_posted).days  # type: ignore[operator]
    if age_days < 0:    return 10.0  # future date (timezone artifact) → brand new
    if age_days <= 2:   return 10.0
    if age_days <= 6:   return 9.0
    if age_days <= 13:  return 7.5
    if age_days <= 29:  return 5.5
    if age_days <= 59:  return 3.0
    return 1.0


# ============================================================
# Weighted final score
# ============================================================

# Must sum to 1.0.
_W_CONTENT:     float = 0.30   # Claude: role relevance + company quality + description richness
_W_GEO:         float = 0.30   # Geographic accessibility from user's home location
_W_SALARY:      float = 0.20   # Salary competitiveness (annualised EUR)
_W_FRESHNESS:   float = 0.15   # Posting freshness (temporal decay)
_W_COMPETITION: float = 0.05   # Competition level (defaults to neutral 5.0)


def compute_final_score(
    content: Optional[float],
    geo: Optional[float],
    salary: Optional[float],
    freshness: Optional[float],
    competition: float = 5.0,
) -> Optional[float]:
    """Weighted combination of all scoring axes → 0-10.

    Returns None if the Claude content score is not yet available.

    Fallback neutrals when a component is missing:
        geo        → 5.0 (neutral)
        salary     → 4.0 (slight negative: no info)
        freshness  → 5.0 (neutral)
        competition→ 5.0 (neutral — rarely available)
    """
    if content is None:
        return None

    c  = float(content)
    g  = float(geo)        if geo        is not None else 5.0
    sa = float(salary)     if salary     is not None else 4.0
    fr = float(freshness)  if freshness  is not None else 5.0

    raw = (
        c  * _W_CONTENT     +
        g  * _W_GEO         +
        sa * _W_SALARY      +
        fr * _W_FRESHNESS   +
        competition * _W_COMPETITION
    )
    return round(min(10.0, max(0.0, raw)), 2)
