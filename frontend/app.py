"""JobScout — interface Streamlit, refonte design system PDS (terminal CRT).

Architecture :
    - 5 pages : Cockpit | Offres | Pipeline | Insights | Système
    - Navigation par sidebar custom (radio stylisé)
    - Composants réutilisables : terminal_card, score_pill, status_line, etc.
    - Theme injecté via CSS (Space Grotesk + JetBrains Mono, palette PDS)

Toutes les communications passent par l'API FastAPI backend (BACKEND_URL).
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ============================================================================
# Configuration globale
# ============================================================================

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
REQUEST_TIMEOUT = 180  # /search peut prendre plus d'une minute par profil

st.set_page_config(
    page_title="JobScout",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================================
# Theme — CSS injecté (palette PDS / brand guide)
# ============================================================================

THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');

:root {
    --green-primary: #00FF41;
    --green-dim: #00CC33;
    --green-glow: rgba(0,255,65,0.15);
    --cyan-accent: #00D4FF;
    --yellow-accent: #FFD700;
    --red-accent: #FF4444;
    --orange-accent: #FF8800;

    --bg-primary: #000000;
    --bg-secondary: #0A0E18;
    --bg-terminal: #080D16;
    --bg-terminal-header: #141E2A;

    --text-primary: #D8E2EC;
    --text-secondary: #8899AA;
    --text-heading: #FFFFFF;
    --text-dim: #4A5868;

    --border-default: #1E3044;
    --border-green: rgba(0,255,65,0.2);
    --border-active: #00FF41;

    --radius-sm: 3px;
    --radius-md: 6px;
    --radius-lg: 8px;

    --t-quick: 160ms;
    --t-base: 300ms;
}

/* Reset & fond global */
html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"],
[data-testid="stSidebar"] {
    background: var(--bg-primary) !important;
    color: var(--text-primary) !important;
    font-family: 'JetBrains Mono', 'Courier New', monospace !important;
}
[data-testid="stHeader"], [data-testid="stToolbar"] { display: none; }
[data-testid="collapsedControl"] { color: var(--green-primary) !important; }

.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 4rem !important;
    max-width: 1400px;
}

/* Scanline subtile (CRT) */
[data-testid="stAppViewContainer"]::after {
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: repeating-linear-gradient(
        0deg, rgba(0,0,0,0) 0px, rgba(0,0,0,0) 1px,
        rgba(0,255,65,0.008) 1px, rgba(0,255,65,0.008) 2px
    );
    pointer-events: none;
    z-index: 9998;
}

/* Typo */
h1, h2, h3, h4, h5, h6 {
    font-family: 'Space Grotesk', sans-serif !important;
    color: var(--text-heading) !important;
    letter-spacing: -0.5px !important;
}
h1 { font-size: 2rem !important; }
h2 { font-size: 1.5rem !important; }
h3 { font-size: 1.25rem !important; }

p, span, li, label, div { font-family: 'JetBrains Mono', monospace !important; }

a, a:visited {
    color: var(--green-primary) !important;
    text-decoration: none !important;
    border-bottom: 1px dashed var(--border-green);
    transition: all var(--t-quick);
}
a:hover { color: var(--cyan-accent) !important; border-color: var(--cyan-accent); }

/* Sidebar */
[data-testid="stSidebar"] {
    background: var(--bg-secondary) !important;
    border-right: 1px solid var(--border-default);
}
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--text-secondary) !important;
    margin-bottom: 0.5rem;
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 500 !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] > div { gap: 4px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-md);
    padding: 8px 12px !important;
    transition: all var(--t-quick);
    cursor: pointer;
    font-size: 0.875rem !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: var(--bg-terminal);
    border-color: var(--border-default);
}
[data-testid="stSidebar"] [data-testid="stRadio"] label[data-checked="true"] {
    background: var(--bg-terminal);
    border-color: var(--green-primary);
}
[data-testid="stSidebar"] [data-testid="stRadio"] label > div:first-child { display: none; }

/* Boutons */
.stButton > button, .stDownloadButton > button, .stLinkButton > a {
    background: transparent !important;
    color: var(--green-primary) !important;
    border: 1px solid var(--green-primary) !important;
    border-radius: var(--radius-sm) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
    font-size: 0.75rem !important;
    padding: 0.5rem 1rem !important;
    transition: all var(--t-quick) !important;
    box-shadow: none !important;
    text-decoration: none !important;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover {
    background: var(--green-glow) !important;
    box-shadow: 0 0 12px var(--green-glow) !important;
    color: var(--green-primary) !important;
}
.stButton > button[kind="primary"] {
    background: var(--green-primary) !important;
    color: #000 !important;
}

/* Inputs */
input, textarea, select,
.stTextInput input, .stTextArea textarea, .stSelectbox > div > div,
.stMultiSelect > div > div, .stNumberInput input, .stDateInput input {
    background: var(--bg-terminal) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--border-default) !important;
    border-radius: var(--radius-sm) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.875rem !important;
}
input:focus, textarea:focus,
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--green-primary) !important;
    box-shadow: 0 0 0 1px var(--green-glow) !important;
}

.stSlider [data-baseweb="slider"] > div { background: var(--border-default) !important; }
.stSlider [data-baseweb="slider"] > div > div { background: var(--green-primary) !important; }
.stSlider [data-baseweb="slider"] [role="slider"] {
    background: var(--green-primary) !important;
    border: 2px solid #000 !important;
}

.stCheckbox label, .stToggle label { color: var(--text-primary) !important; }

/* Tabs */
[data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid var(--border-default) !important;
    gap: 4px !important;
}
[data-baseweb="tab"] {
    background: transparent !important;
    color: var(--text-secondary) !important;
    border-radius: var(--radius-sm) var(--radius-sm) 0 0 !important;
    padding: 8px 16px !important;
    font-size: 0.75rem !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
}
[data-baseweb="tab"][aria-selected="true"] {
    color: var(--green-primary) !important;
    border-bottom: 2px solid var(--green-primary) !important;
}

/* DataFrame */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    background: var(--bg-terminal) !important;
    border: 1px solid var(--border-default) !important;
    border-radius: var(--radius-md) !important;
}
[data-testid="stDataFrame"] table {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.8rem !important;
}

/* Composants custom */
.term-card {
    background: var(--bg-terminal);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-lg);
    margin-bottom: 1rem;
    overflow: hidden;
    transition: border-color var(--t-quick);
}
.term-card:hover { border-color: var(--border-green); }
.term-card__header {
    background: var(--bg-terminal-header);
    padding: 8px 14px;
    display: flex;
    align-items: center;
    gap: 10px;
    border-bottom: 1px solid var(--border-default);
}
.term-dots { display: flex; gap: 6px; }
.term-dot { width: 11px; height: 11px; border-radius: 50%; }
.term-dot--r { background: #FF5F57; }
.term-dot--y { background: #FEBC2E; }
.term-dot--g { background: #28C840; }
.term-card__title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 2px;
    flex: 1;
    margin: 0;
}
.term-card__body { padding: 16px 18px; }

.kpi {
    background: var(--bg-terminal);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-lg);
    padding: 16px 18px;
    height: 100%;
}
.kpi__label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 1.8px;
    margin-bottom: 8px;
}
.kpi__value {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 2rem;
    font-weight: 700;
    color: var(--text-heading);
    line-height: 1;
    margin-bottom: 4px;
}
.kpi__delta {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: var(--text-secondary);
}
.kpi__delta--pos { color: var(--green-primary); }
.kpi__delta--neg { color: var(--red-accent); }

.status-row {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    color: var(--text-primary);
}
.status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    flex-shrink: 0;
}
.status-dot--ok { background: var(--green-primary); box-shadow: 0 0 6px var(--green-glow); }
.status-dot--warn { background: var(--yellow-accent); }
.status-dot--err { background: var(--red-accent); }
.status-dot--off { background: var(--text-dim); }
.status-row__label {
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 1.5px;
    font-size: 0.7rem;
    min-width: 130px;
}
.status-row__value { color: var(--text-primary); flex: 1; }

.score-pill {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    font-weight: 600;
    border: 1px solid currentColor;
    border-radius: var(--radius-sm);
    line-height: 1.3;
}
.score-pill--high { color: var(--green-primary); }
.score-pill--med { color: var(--yellow-accent); }
.score-pill--low { color: var(--text-secondary); }
.score-pill--none { color: var(--text-dim); }

.score-bar { margin: 6px 0; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; }
.score-bar__row { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.score-bar__label {
    color: var(--text-secondary); width: 90px;
    text-transform: uppercase; letter-spacing: 1px; font-size: 0.65rem;
}
.score-bar__track {
    flex: 1; height: 6px; background: var(--border-default);
    border-radius: 2px; overflow: hidden;
}
.score-bar__fill { height: 100%; border-radius: 2px; transition: width var(--t-base); }
.score-bar__val {
    color: var(--text-primary); font-weight: 600;
    min-width: 40px; text-align: right;
}

.job-row {
    background: var(--bg-terminal);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-md);
    padding: 10px 14px;
    margin-bottom: 6px;
    transition: all var(--t-quick);
}
.job-row:hover { border-color: var(--border-green); background: var(--bg-terminal-header); }
.job-row--active { border-color: var(--green-primary); background: var(--bg-terminal-header); }
.job-row__title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.9rem;
    color: var(--text-heading);
    font-weight: 500;
    margin-bottom: 4px;
    display: -webkit-box;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
.job-row__meta {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: var(--text-secondary);
}

.tag {
    display: inline-block;
    padding: 2px 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1px;
    background: rgba(30,48,68,0.5);
    color: var(--text-secondary);
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-default);
}
.tag--green { color: var(--green-primary); border-color: var(--border-green); }
.tag--cyan { color: var(--cyan-accent); border-color: rgba(0,212,255,0.3); }
.tag--yellow { color: var(--yellow-accent); border-color: rgba(255,215,0,0.3); }
.tag--red { color: var(--red-accent); border-color: rgba(255,68,68,0.3); }

.prompt {
    font-family: 'JetBrains Mono', monospace;
    color: var(--text-secondary);
    font-size: 0.85rem;
    margin: 0 0 0.5rem 0;
}
.prompt::before { content: '> '; color: var(--green-primary); }

.kanban-col {
    background: var(--bg-terminal);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-lg);
    height: 100%;
    overflow: hidden;
    margin-bottom: 0.5rem;
}
.kanban-col__header {
    background: var(--bg-terminal-header);
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-default);
    display: flex; align-items: center; justify-content: space-between;
}
.kanban-col__title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 1.8px;
    margin: 0;
}
.kanban-col__count {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: var(--green-primary);
    font-weight: 600;
}

.kc {
    background: var(--bg-secondary);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    margin-bottom: 6px;
    transition: border-color var(--t-quick);
}
.kc:hover { border-color: var(--green-primary); }
.kc__title {
    font-size: 0.78rem;
    color: var(--text-heading);
    font-weight: 500;
    margin-bottom: 4px;
    line-height: 1.3;
}
.kc__meta {
    font-size: 0.65rem;
    color: var(--text-secondary);
    display: flex; gap: 6px; flex-wrap: wrap;
}

.desc {
    background: rgba(8,13,22,0.6);
    border-left: 2px solid var(--green-primary);
    padding: 12px 16px;
    color: var(--text-primary);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    line-height: 1.6;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    margin: 8px 0;
    white-space: pre-wrap;
    max-height: 400px;
    overflow-y: auto;
}

.alert {
    padding: 10px 14px;
    border-radius: var(--radius-md);
    border-left: 3px solid;
    margin: 8px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
}
.alert--info { background: rgba(0,212,255,0.05); border-color: var(--cyan-accent); color: var(--cyan-accent); }
.alert--ok   { background: rgba(0,255,65,0.05); border-color: var(--green-primary); color: var(--green-primary); }
.alert--warn { background: rgba(255,215,0,0.05); border-color: var(--yellow-accent); color: var(--yellow-accent); }
.alert--err  { background: rgba(255,68,68,0.05); border-color: var(--red-accent); color: var(--red-accent); }

.brand {
    display: flex; align-items: baseline; gap: 12px;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
}
.brand__mark {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.4rem;
    color: var(--green-primary);
    font-weight: 700;
}
.brand__title {
    font-family: 'Space Grotesk', sans-serif;
    color: var(--text-heading);
    font-size: 1.4rem;
    font-weight: 600;
    margin: 0;
}
.brand__meta {
    color: var(--text-secondary);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-left: auto;
}

.modebar { background: transparent !important; }

.footer {
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border-default);
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    text-align: center;
    letter-spacing: 1.5px;
}

.stSpinner > div { border-top-color: var(--green-primary) !important; }

[data-testid="stAlert"] {
    background: var(--bg-terminal) !important;
    border-radius: var(--radius-md) !important;
    border-left: 3px solid var(--green-primary) !important;
    color: var(--text-primary) !important;
    font-family: 'JetBrains Mono', monospace !important;
}

[data-testid="stExpander"] {
    background: var(--bg-terminal) !important;
    border: 1px solid var(--border-default) !important;
    border-radius: var(--radius-md) !important;
}
[data-testid="stExpander"] summary {
    color: var(--text-primary) !important;
    font-family: 'JetBrains Mono', monospace !important;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    font-size: 0.75rem !important;
}

[data-testid="stMetricValue"] {
    font-family: 'Space Grotesk', sans-serif !important;
    color: var(--text-heading) !important;
}
[data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    font-size: 0.7rem !important;
}

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg-secondary); }
::-webkit-scrollbar-thumb { background: var(--border-default); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-green); }
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)


# ============================================================================
# Helpers HTTP
# ============================================================================

def api_get(path: str, params: Optional[dict] = None) -> Any:
    """GET sur l'API backend."""
    r = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json: Optional[dict] = None,
             params: Optional[dict] = None) -> dict:
    """POST sur l'API backend."""
    r = requests.post(f"{BACKEND_URL}{path}", json=json, params=params,
                      timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ============================================================================
# Caches (TTL adaptés au type de donnée)
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def fetch_jobs(params: dict) -> dict:
    return api_get("/jobs", params)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_jobs_light(params: dict) -> dict:
    """Variante allégée pour la page Triage — payload sans description ni sources.
    Cache TTL plus long (60s) car les colonnes affichées sont stables."""
    p = dict(params)
    p["light"] = True
    return api_get("/jobs", p)


@st.cache_data(ttl=30, show_spinner=False)
def fetch_stats() -> dict:
    return api_get("/stats")


@st.cache_data(ttl=300, show_spinner=False)
def fetch_profiles() -> list[dict]:
    return api_get("/profiles")


@st.cache_data(ttl=15, show_spinner=False)
def fetch_logs(params: dict) -> dict:
    return api_get("/logs", params)


@st.cache_data(ttl=10, show_spinner=False)
def fetch_health_db() -> dict:
    return api_get("/health/db-size")


def clear_caches() -> None:
    """Vide tous les caches Streamlit pour forcer un re-fetch."""
    fetch_jobs.clear()
    fetch_jobs_light.clear()
    fetch_stats.clear()
    fetch_logs.clear()
    fetch_health_db.clear()


# ============================================================================
# Helpers de mutation (modifient la DB côté backend)
# ============================================================================

def set_status(job_id: int, status: Optional[str]) -> None:
    api_post(f"/jobs/{job_id}/status", {"status": status})
    clear_caches()


def set_archived(job_id: int, archived: bool) -> None:
    api_post(f"/jobs/{job_id}/archive", {"archived": archived})
    clear_caches()


def set_notes(job_id: int, notes: str) -> None:
    api_post(f"/jobs/{job_id}/notes", {"notes": notes})
    clear_caches()


# ============================================================================
# Helpers de formatage
# ============================================================================

def score_class(score: Optional[float]) -> str:
    """Classe CSS selon la valeur du score."""
    if score is None:
        return "score-pill--none"
    if score >= 7.0:
        return "score-pill--high"
    if score >= 5.0:
        return "score-pill--med"
    return "score-pill--low"


def score_color_hex(score: Optional[float]) -> str:
    """Hex pour les remplissages de barre."""
    if score is None:
        return "#4A5868"
    if score >= 7.0:
        return "#00FF41"
    if score >= 5.0:
        return "#FFD700"
    return "#8899AA"


def fmt_score(score: Optional[float]) -> str:
    return f"{score:.1f}" if score is not None else "—"


# ============================================================================
# Sécurité — escape HTML + validation URL
# ============================================================================

def safe(s: Any) -> str:
    """Escape HTML d'une string user-data avant injection via unsafe_allow_html.

    OBLIGATOIRE pour tout champ provenant du backend (titres, sociétés, descriptions,
    lieux, raisonnements Claude…) : ces données ont été scrapées sans sanitization
    et peuvent contenir du HTML/JS malveillant. Sans cet escape, on s'expose à du XSS
    persistant (un titre `<script>...</script>` exécuterait dans le navigateur).
    """
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def safe_url(u: Optional[str]) -> str:
    """Valide qu'une URL utilise un protocole sûr (http/https) avant de l'exposer.

    Streamlit filtre normalement les protocoles dangereux dans link_button, mais on
    valide en amont pour bloquer `javascript:`, `data:` etc. côté UI.
    """
    if not u:
        return "#"
    try:
        parsed = urlparse(u)
        if parsed.scheme in ("http", "https"):
            return u
    except Exception:
        pass
    return "#"


def fmt_age(date_str: Any) -> str:
    """Affiche l'âge de l'offre : 'AUJ', '3j', '21/04', etc."""
    if not date_str:
        return "—"
    try:
        if isinstance(date_str, str):
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        else:
            d = date_str
        today = datetime.now(timezone.utc).date()
        days = (today - d).days
        if days < 0:
            return d.strftime("%d/%m")
        if days == 0:
            return "AUJ"
        if days == 1:
            return "1j"
        return f"{days}j"
    except Exception:
        return "—"


def fmt_salary(lo: Any, hi: Any, cur: Any) -> Optional[str]:
    """Formate un salaire en k compact (ex: '50–70 k€')."""
    def k(v):
        try:
            v = float(v)
            if v >= 1000:
                return f"{v/1000:.0f}k"
            return f"{v:.0f}"
        except (TypeError, ValueError):
            return None
    a, b = k(lo), k(hi)
    sym = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}.get(
        (cur or "").upper(), cur or ""
    )
    if a and b and a != b:
        return f"{a}–{b} {sym}".strip()
    if a or b:
        return f"{a or b} {sym}".strip()
    return None


def work_mode_tag(mode: Optional[str]) -> str:
    """Renvoie un tag HTML coloré selon le mode de travail."""
    if not mode:
        return ""
    m = {
        "full_remote": ("REMOTE", "tag tag--green"),
        "hybrid": ("HYBRID", "tag tag--cyan"),
        "onsite": ("ONSITE", "tag"),
    }.get(mode, (mode.upper(), "tag"))
    return f'<span class="{m[1]}">{m[0]}</span>'


# ============================================================================
# Composants UI réutilisables (HTML injecté)
# ============================================================================

def term_card_open(title: str, dots: str = "ryg") -> None:
    """Ouvre une terminal-card avec header dots + titre. Doit être suivi de term_card_close()."""
    dot_html = "".join(
        f'<span class="term-dot term-dot--{d}"></span>' for d in dots
    )
    st.markdown(
        f'<div class="term-card"><div class="term-card__header">'
        f'<div class="term-dots">{dot_html}</div>'
        f'<p class="term-card__title">{title}</p>'
        f'</div><div class="term-card__body">',
        unsafe_allow_html=True,
    )


def term_card_close() -> None:
    st.markdown("</div></div>", unsafe_allow_html=True)


def kpi(label: str, value: Any, delta: Optional[str] = None,
        delta_kind: str = "") -> str:
    """Retourne le HTML d'une tuile KPI (label + valeur + delta optionnel)."""
    delta_cls = f"kpi__delta--{delta_kind}" if delta_kind else ""
    delta_html = f'<div class="kpi__delta {delta_cls}">{delta}</div>' if delta else ""
    return (
        f'<div class="kpi">'
        f'<div class="kpi__label">{label}</div>'
        f'<div class="kpi__value">{value}</div>'
        f'{delta_html}'
        f'</div>'
    )


def status_line(label: str, value: str, state: str = "ok") -> str:
    """Ligne 'API: ONLINE' avec dot coloré. state ∈ ok/warn/err/off."""
    return (
        f'<div class="status-row">'
        f'<span class="status-dot status-dot--{state}"></span>'
        f'<span class="status-row__label">{label}</span>'
        f'<span class="status-row__value">{value}</span>'
        f'</div>'
    )


def score_pill(score: Optional[float]) -> str:
    """Badge score 0-10 colorié."""
    cls = score_class(score)
    return f'<span class="score-pill {cls}">◆ {fmt_score(score)}/10</span>'


def score_breakdown_html(content: Any, geo: Any, salary: Any, fresh: Any) -> str:
    """4 barres horizontales : content / geo / salary / freshness."""
    rows = [
        ("CONTENT", content),
        ("GEO",     geo),
        ("SALARY",  salary),
        ("FRESH",   fresh),
    ]
    out = ['<div class="score-bar">']
    for label, val in rows:
        v = val if val is not None else 0
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0
        pct = max(0, min(100, v * 10))
        color = score_color_hex(v if val is not None else None)
        out.append(
            f'<div class="score-bar__row">'
            f'<span class="score-bar__label">{label}</span>'
            f'<div class="score-bar__track"><div class="score-bar__fill" '
            f'style="width:{pct}%;background:{color}"></div></div>'
            f'<span class="score-bar__val">{fmt_score(val)}</span>'
            f'</div>'
        )
    out.append('</div>')
    return "".join(out)


def page_header(prompt: str, title: str, meta: str = "") -> None:
    """En-tête standard de page : ligne prompt + titre + meta à droite."""
    st.markdown(
        f'<div class="brand">'
        f'<span class="brand__mark">>js</span>'
        f'<h1 class="brand__title">{title}</h1>'
        f'<span class="brand__meta">{meta}</span>'
        f'</div>'
        f'<p class="prompt">{prompt}</p>',
        unsafe_allow_html=True,
    )


def alert(msg: str, kind: str = "info") -> None:
    """Bandeau d'info coloré. kind ∈ info/ok/warn/err.

    Le contenu N'EST PAS escapé pour permettre l'affichage de simples balises
    de mise en forme ; ne jamais y passer du contenu user/backend brut sans
    safe() préalable.
    """
    st.markdown(f'<div class="alert alert--{kind}">{msg}</div>',
                unsafe_allow_html=True)


# Liste partagée des plateformes utilisées dans les filtres (Offres + Triage).
# Synchroniser avec backend/schemas.py:Platform si la liste évolue.
PLATFORM_OPTIONS = [
    "linkedin", "indeed", "francetravail", "freework",
    "remotive", "greenhouse", "workday",
]
WORK_MODE_OPTIONS = ["full_remote", "hybrid", "onsite"]
ORDER_BY_OPTIONS = ["relevance", "date", "scraped"]


def build_jobs_params(
    *,
    keywords: str = "",
    min_score: float = 0.0,
    scored_only: bool = False,
    platform: Optional[list[str]] = None,
    region: Optional[list[str]] = None,
    work_mode: Optional[list[str]] = None,
    remote_only: bool = False,
    include_archived: bool = False,
    order_by: str = "relevance",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Construit le dict de query-string pour /jobs depuis les valeurs des filtres UI.

    Mutualise la logique de page_offres et page_triage. Les paramètres listes
    vides / scalaires nuls ne sont PAS envoyés (laissent l'API à son défaut).
    Cas particulier `scored_only` : on force min_score=0.01 pour exclure les
    NULL (NULL >= 0 = False en SQL).
    """
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "order_by": order_by,
        "include_archived": include_archived,
    }
    if keywords:
        params["keywords"] = keywords
    if scored_only and min_score == 0:
        params["min_score"] = 0.01
    elif min_score > 0:
        params["min_score"] = min_score
    if platform:
        params["platform"] = platform
    if region:
        params["region"] = region
    if work_mode:
        params["work_mode"] = work_mode
    if remote_only:
        params["remote_only"] = True
    return params


# ============================================================================
# Theming Plotly (vert phosphore PDS)
# ============================================================================

PLOTLY_LAYOUT = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "#080D16",
    "font": {"family": "JetBrains Mono, monospace",
             "color": "#D8E2EC", "size": 11},
    "title": {"font": {"family": "Space Grotesk, sans-serif", "color": "#FFFFFF"}},
    "xaxis": {
        "gridcolor": "#1E3044", "zerolinecolor": "#1E3044",
        "linecolor": "#1E3044", "tickfont": {"color": "#8899AA"},
    },
    "yaxis": {
        "gridcolor": "#1E3044", "zerolinecolor": "#1E3044",
        "linecolor": "#1E3044", "tickfont": {"color": "#8899AA"},
    },
    "colorway": ["#00FF41", "#00D4FF", "#FFD700", "#FF8800",
                 "#FF4444", "#00CC33", "#8899AA"],
    "legend": {"font": {"color": "#D8E2EC"}, "bgcolor": "rgba(0,0,0,0)"},
    "margin": {"t": 40, "b": 40, "l": 50, "r": 20},
}


def theme_plot(fig: go.Figure) -> go.Figure:
    """Applique le theme PDS au figure Plotly."""
    fig.update_layout(**PLOTLY_LAYOUT)
    return fig


# ============================================================================
# Sidebar — navigation + status système
# ============================================================================

with st.sidebar:
    # Brand block
    st.markdown(
        '<div style="padding:0 12px 12px 12px;border-bottom:1px solid var(--border-default);'
        'margin-bottom:1rem;">'
        '<div style="display:flex;align-items:baseline;gap:8px;">'
        '<span style="font-family:JetBrains Mono;font-size:1.5rem;'
        'color:var(--green-primary);font-weight:700;">>js</span>'
        '<span style="font-family:Space Grotesk;color:var(--text-heading);'
        'font-weight:600;">jobscout</span>'
        '</div>'
        '<div style="font-family:JetBrains Mono;font-size:0.65rem;'
        'color:var(--text-secondary);text-transform:uppercase;letter-spacing:2px;'
        'margin-top:4px;">senior IT · cyber · ops</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div style="padding:0 12px;">', unsafe_allow_html=True)

    PAGES = {
        "🎯 Cockpit": "cockpit",
        "📋 Offres": "offres",
        "🗃  Triage": "triage",
        "🗂  Pipeline": "pipeline",
        "📊 Insights": "insights",
        "⚙️  Système": "systeme",
    }

    if "current_page" not in st.session_state:
        st.session_state["current_page"] = "cockpit"

    nav_choice = st.radio(
        "Navigation",
        list(PAGES.keys()),
        index=list(PAGES.values()).index(st.session_state["current_page"]),
        label_visibility="collapsed",
        key="_nav",
    )
    st.session_state["current_page"] = PAGES[nav_choice]

    st.markdown('</div>', unsafe_allow_html=True)

    # Status système court (toujours visible en bas de sidebar)
    try:
        _stats = fetch_stats()
        _hdb = fetch_health_db()
        st.markdown(
            f'<div style="padding:1rem 12px 0 12px;border-top:1px solid var(--border-default);'
            f'margin-top:1.5rem;">'
            f'<div style="font-family:JetBrains Mono;font-size:0.65rem;'
            f'color:var(--text-secondary);text-transform:uppercase;letter-spacing:2px;'
            f'margin-bottom:0.5rem;">SYSTEM</div>'
            + status_line("INDEXED", str(_stats.get("total_jobs", 0)), "ok")
            + status_line("SCORED",
                          f'{_stats.get("scored", 0)}/{_stats.get("total_jobs", 0)}',
                          "ok" if _stats.get("unscored", 0) < 100 else "warn")
            + status_line("DB", f'{_hdb.get("db_mb", 0)} MB', "ok")
            + '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.markdown(
            '<div style="padding:1rem 12px;">'
            + status_line("BACKEND", "OFFLINE", "err")
            + '</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div style="padding:0.5rem 12px;">', unsafe_allow_html=True)
    if st.button("↻ refresh cache", use_container_width=True, key="_refresh_sidebar"):
        clear_caches()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================================
# PAGE — COCKPIT (briefing)
# ============================================================================

def page_cockpit() -> None:
    page_header(
        "uptime · briefing du jour",
        "cockpit",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    try:
        stats = fetch_stats()
        hdb = fetch_health_db()
    except Exception as e:
        alert(f"backend injoignable : {e}", "err")
        return

    # 5 KPIs principaux
    cols = st.columns(5)
    cols[0].markdown(
        kpi("OFFRES INDEXÉES", stats.get("total_jobs", 0)),
        unsafe_allow_html=True,
    )
    cols[1].markdown(
        kpi("SCORÉES", stats.get("scored", 0),
            f"{stats.get('unscored', 0)} en attente",
            "neg" if stats.get("unscored", 0) > 100 else ""),
        unsafe_allow_html=True,
    )
    cols[2].markdown(
        kpi("EN PIPELINE", hdb.get("jobs_applied", 0), "Kanban actif"),
        unsafe_allow_html=True,
    )
    cols[3].markdown(
        kpi("ARCHIVÉES", hdb.get("jobs_archived", 0)),
        unsafe_allow_html=True,
    )
    cols[4].markdown(
        kpi("BASE", f'{hdb.get("db_mb", 0)} MB',
            f'{hdb.get("scrape_logs_total", 0)} logs'),
        unsafe_allow_html=True,
    )

    # Mini-stat blacklist : somme des derniers 50 scrapes (pour donner un ordre d'idée)
    try:
        _last_logs = fetch_logs({"limit": 50}).get("items", [])
        _bl_total = sum(int(l.get("blacklisted", 0) or 0) for l in _last_logs)
        if _bl_total > 0:
            st.markdown(
                f'<p class="prompt" style="margin-top:0.5rem;">'
                f'🛡️ {_bl_total} offres filtrées par la blacklist (50 derniers runs)</p>',
                unsafe_allow_html=True,
            )
    except Exception:
        pass

    st.markdown("<br>", unsafe_allow_html=True)

    # 2 colonnes : status + actions
    c1, c2 = st.columns([1, 1])

    with c1:
        term_card_open("system_status.json")
        last_scrape = stats.get("last_scrape")
        if last_scrape:
            try:
                dt = datetime.fromisoformat(last_scrape.replace("Z", "+00:00"))
                ago_min = int(
                    (datetime.now(timezone.utc).replace(tzinfo=None)
                     - dt.replace(tzinfo=None)).total_seconds() / 60
                )
                if ago_min < 60:
                    when_str = f"il y a {ago_min}min"
                elif ago_min < 1440:
                    when_str = f"il y a {ago_min // 60}h"
                else:
                    when_str = f"il y a {ago_min // 1440}j"
                last_str = f"{dt.strftime('%d/%m %H:%M')} ({when_str})"
            except Exception:
                last_str = last_scrape
        else:
            last_str = "jamais"

        by_plat = stats.get("by_platform", {})
        # safe() sur les clés au cas où une plateforme aurait un nom exotique
        sources = " · ".join(
            f"{safe(k)}:{v}"
            for k, v in sorted(by_plat.items(), key=lambda x: -x[1])[:5]
        )

        unscored = stats.get("unscored", 0)
        scoring_state = "ok"
        scoring_msg = "actif"
        if unscored > 1000:
            scoring_state = "warn"
            scoring_msg = f"{unscored} en attente (clé valide ?)"

        st.markdown(
            status_line("BACKEND",     "ONLINE", "ok")
            + status_line("DERNIER RUN", last_str, "ok" if last_scrape else "warn")
            + status_line("SOURCES",   sources or "—", "ok")
            + status_line("SCORING",   scoring_msg, scoring_state)
            + status_line("WAL",       "actif (lectures concurrentes)", "ok"),
            unsafe_allow_html=True,
        )
        term_card_close()

    with c2:
        term_card_open("quick_actions.sh")
        st.markdown(
            '<p class="prompt">commandes immédiates · résultats en arrière-plan</p>',
            unsafe_allow_html=True,
        )

        # Sélecteur de profil pour le scrape manuel (sinon défaut = France)
        try:
            _profiles_for_scrape = [p["key"] for p in fetch_profiles()]
        except Exception:
            _profiles_for_scrape = ["France"]

        scrape_profile = st.selectbox(
            "profil à scraper",
            ["__ALL__"] + _profiles_for_scrape,
            format_func=lambda x: "▸ tous les profils (séquentiel)" if x == "__ALL__" else f"▸ {x}",
            key="qa_profile",
            label_visibility="collapsed",
        )

        a1, a2 = st.columns(2)
        if a1.button("▸ run scrape", use_container_width=True, key="qa_scrape",
                     type="primary"):
            if scrape_profile == "__ALL__":
                # Boucle séquentielle sur tous les profils
                profiles = _profiles_for_scrape
                progress = st.empty()
                results = []
                for i, prof in enumerate(profiles, 1):
                    progress.markdown(
                        f'<p class="prompt">scrape [{i}/{len(profiles)}] {prof}…</p>',
                        unsafe_allow_html=True,
                    )
                    try:
                        r = api_post("/search", {"profile": prof})
                        results.append((prof, r.get("scraped", 0), r.get("new", 0)))
                    except Exception as e:
                        results.append((prof, "ERR", str(e)[:40]))
                progress.empty()
                msg = " · ".join(
                    f"{p}: {n}/{s}" for p, s, n in results if s != "ERR"
                )
                errs = [f"{p}: {n}" for p, s, n in results if s == "ERR"]
                if msg:
                    alert(f"✓ {msg} (new/scraped)", "ok")
                if errs:
                    alert(f"✗ {' | '.join(errs)}", "err")
                clear_caches()
            else:
                with st.spinner(f"scrape {scrape_profile} en cours..."):
                    try:
                        result = api_post("/search", {"profile": scrape_profile})
                        alert(
                            f"[{scrape_profile}] {result.get('scraped', 0)} scrapées · "
                            f"{result.get('new', 0)} nouvelles · "
                            f"{result.get('duplicates', 0)} doublons · "
                            f"{result.get('blacklisted', 0)} filtrées",
                            "ok",
                        )
                        clear_caches()
                    except Exception as e:
                        alert(f"échec scrape : {e}", "err")

        if a2.button("▸ rescore manquants", use_container_width=True, key="qa_rescore"):
            try:
                result = api_post("/rescore", {})
                alert(f"{result.get('pending', 0)} jobs planifiés", "ok")
            except Exception as e:
                alert(f"échec rescore : {e}", "err")

        if a1.button("▸ purge cache", use_container_width=True, key="qa_cache",
                     help="Vide le cache UI Streamlit (re-fetch immédiat)"):
            clear_caches()
            st.rerun()

        term_card_close()

    # Top offres récentes
    st.markdown("<br>", unsafe_allow_html=True)
    term_card_open("top_5 — meilleurs scores récents")
    try:
        recent = fetch_jobs({"limit": 5, "order_by": "relevance", "min_score": 6})
        items = recent.get("items", [])
        if not items:
            st.markdown(
                '<p class="prompt">aucune offre scorée ≥6 récemment</p>',
                unsafe_allow_html=True,
            )
        else:
            for j in items:
                pill = score_pill(j.get("relevance_score"))
                wm = work_mode_tag(j.get("work_mode"))
                age = fmt_age(j.get("date_posted"))
                sal = fmt_salary(
                    j.get("min_salary"), j.get("max_salary"), j.get("currency")
                ) or ""
                st.markdown(
                    f'<div class="job-row">'
                    f'<div class="job-row__title">{safe((j.get("title") or "")[:120])}</div>'
                    f'<div class="job-row__meta">'
                    f'<span>🏢 {safe((j.get("company") or "?")[:40])}</span>'
                    f'<span>📍 {safe((j.get("location") or "?")[:40])}</span>'
                    f'<span>🕒 {age}</span>'
                    f'{wm}'
                    f'<span>💰 {safe(sal)}</span>'
                    f'<span style="margin-left:auto">{pill}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception as e:
        alert(f"erreur lecture top : {e}", "err")
    term_card_close()


# ============================================================================
# PAGE — OFFRES (split view : liste + détail)
# ============================================================================

def page_offres() -> None:
    page_header("ls -la /offres | sort -k score -r", "offres", "")

    try:
        profiles = fetch_profiles()
    except Exception:
        profiles = []

    # Filtres dans expander
    with st.expander("⚙️  filtres avancés", expanded=False):
        f1, f2, f3 = st.columns(3)
        keywords = f1.text_input("Mot-clé (titre/société/desc)", "", key="f_kw")
        min_score = f2.slider("Score min", 0.0, 10.0, 0.0, 0.5, key="f_score")
        order_by = f3.selectbox("Tri", ORDER_BY_OPTIONS, key="f_order")

        f4, f5, f6 = st.columns(3)
        platform = f4.multiselect("Plateforme", PLATFORM_OPTIONS, key="f_platform")
        regions_opts = sorted({p["region"] for p in profiles}) if profiles else []
        region = f5.multiselect("Région", regions_opts, key="f_region")
        work_mode = f6.multiselect("Mode", WORK_MODE_OPTIONS, key="f_mode")

        f7, f8, f9 = st.columns(3)
        remote_only = f7.checkbox("Remote uniquement", key="f_remote")
        include_archived = f8.checkbox("Inclure archivées", key="f_archived")
        scored_only = f9.checkbox(
            "🤖 Scorées par IA uniquement",
            key="f_scored",
            help="N'affiche que les offres ayant un score Claude (exclut les non scorées)",
        )

    params = build_jobs_params(
        keywords=keywords, min_score=min_score, scored_only=scored_only,
        platform=platform, region=region, work_mode=work_mode,
        remote_only=remote_only, include_archived=include_archived,
        order_by=order_by, limit=100,
    )

    try:
        result = fetch_jobs(params)
    except Exception as e:
        alert(f"erreur lecture jobs : {e}", "err")
        return

    items = result.get("items", [])
    total = result.get("total", 0)

    st.markdown(
        f'<p class="prompt">{len(items)} affichées sur {total} matchant les filtres</p>',
        unsafe_allow_html=True,
    )

    if not items:
        alert("aucune offre ne correspond aux filtres", "warn")
        return

    # Split view : liste à gauche, détail à droite
    left, right = st.columns([0.42, 0.58], gap="medium")

    sel_id = st.session_state.get("selected_job_id")
    item_ids = {j["id"] for j in items}
    if sel_id is None or sel_id not in item_ids:
        st.session_state["selected_job_id"] = items[0]["id"]

    with left:
        st.markdown(
            '<p class="prompt" style="margin-bottom:0.5rem;">click sur un titre pour voir le détail à droite</p>',
            unsafe_allow_html=True,
        )
        # On affiche chaque offre sous forme HTML stylé + un bouton compact
        # "ouvrir" en dessous (Streamlit ne permet pas de bouton avec HTML interne).
        for j in items[:50]:
            is_sel = j["id"] == st.session_state["selected_job_id"]
            pill = score_pill(j.get("relevance_score"))
            age = fmt_age(j.get("date_posted"))
            wm = work_mode_tag(j.get("work_mode"))
            row_class = "job-row job-row--active" if is_sel else "job-row"

            # Affichage HTML compact (titre + ligne meta)
            st.markdown(
                f'<div class="{row_class}">'
                f'<div class="job-row__title">{safe((j.get("title") or "")[:90])}</div>'
                f'<div class="job-row__meta">'
                f'<span class="tag">{safe((j.get("platform") or "?")[:10])}</span>'
                f'<span>🏢 {safe((j.get("company") or "?")[:25])}</span>'
                f'<span>🕒 {age}</span>'
                f'{wm}'
                f'<span style="margin-left:auto">{pill}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
            # Bouton "ouvrir détail" — minimaliste, juste pour la sélection
            label = "▸ ouvert ▸" if is_sel else "▸ ouvrir"
            if st.button(label, key=f"row_{j['id']}",
                         use_container_width=True,
                         type="primary" if is_sel else "secondary",
                         disabled=is_sel):
                st.session_state["selected_job_id"] = j["id"]
                st.rerun()

    with right:
        sel_id = st.session_state["selected_job_id"]
        sel = next((j for j in items if j["id"] == sel_id), items[0])
        render_job_detail(sel)


def render_job_detail(j: dict) -> None:
    """Panneau de droite : détail complet d'une offre sélectionnée.

    Toutes les chaînes provenant de l'offre (titre, société, lieu, description,
    raisonnement Claude, sources URL) passent par safe()/safe_url() pour éviter
    l'injection HTML/JS via une offre malicieusement formée.
    """
    pill = score_pill(j.get("relevance_score"))
    term_card_open(f"job/{j['id']}")

    st.markdown(
        f'<h2 style="margin:0 0 8px 0;">{safe(j.get("title", "Sans titre"))}</h2>'
        f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;'
        f'margin-bottom:12px;">'
        f'<span class="tag tag--green">{safe(j.get("platform", "?"))}</span>'
        f'<span style="color:var(--text-secondary);">🏢 {safe(j.get("company") or "—")}</span>'
        f'<span style="color:var(--text-secondary);">📍 {safe(j.get("location") or "—")}</span>'
        f'<span style="color:var(--text-secondary);">🕒 {fmt_age(j.get("date_posted"))}</span>'
        f'<span style="margin-left:auto">{pill}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Score breakdown 4 axes (Content/Géo/Salaire/Fraîcheur)
    if j.get("relevance_score") is not None:
        st.markdown(
            score_breakdown_html(
                j.get("base_score"),
                j.get("score_geo"),
                j.get("score_salary"),
                j.get("score_freshness"),
            ),
            unsafe_allow_html=True,
        )

    # Raisonnement libre de Claude (justification du score)
    if j.get("relevance_reasoning"):
        st.markdown(
            f'<div class="alert alert--info" style="margin:8px 0;">'
            f'<strong>🤖 ANALYSE</strong><br>{safe(j["relevance_reasoning"])}</div>',
            unsafe_allow_html=True,
        )

    # Métadonnées en tags (salaire, mode, langue, type contrat, région)
    sal = fmt_salary(j.get("min_salary"), j.get("max_salary"), j.get("currency"))
    sal_eff = j.get("salary_effective_eur")
    meta_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;">'
    if sal:
        meta_html += f'<span class="tag tag--yellow">💰 {safe(sal)}</span>'
    if sal_eff:
        meta_html += f'<span class="tag">≈ {sal_eff:.0f}€ effectif</span>'
    if j.get("work_mode"):
        meta_html += work_mode_tag(j["work_mode"])
    if j.get("language"):
        meta_html += f'<span class="tag">🌐 {safe(j["language"])}</span>'
    if j.get("job_type"):
        meta_html += f'<span class="tag">📋 {safe(j["job_type"])}</span>'
    if j.get("region"):
        meta_html += f'<span class="tag tag--cyan">{safe(j["region"])}</span>'
    meta_html += "</div>"
    st.markdown(meta_html, unsafe_allow_html=True)

    # Description (tronquée à 1500 chars pour éviter de saturer la vue)
    if j.get("description"):
        desc = j["description"][:1500]
        if len(j["description"]) > 1500:
            desc += "\n\n[…description tronquée — voir lien plateforme]"
        st.markdown(f'<div class="desc">{safe(desc)}</div>',
                    unsafe_allow_html=True)

    # Sources multi-platform (offre vue sur LinkedIn ET Indeed par exemple)
    sources = j.get("sources", [])
    if sources and len(sources) > 1:
        srcs_html = " · ".join(
            f'<a href="{safe_url(s.get("url"))}" target="_blank" rel="noopener">'
            f'{safe(s.get("platform"))}</a>'
            for s in sources
        )
        st.markdown(
            f'<p style="color:var(--text-secondary);font-size:0.75rem;margin:8px 0;">'
            f'Vue aussi sur : {srcs_html}</p>',
            unsafe_allow_html=True,
        )

    # Barre d'actions
    st.markdown('<br>', unsafe_allow_html=True)
    a1, a2, a3, a4 = st.columns(4)

    a1.link_button("🔗 source", safe_url(j.get("job_url")), use_container_width=True)

    current_status = j.get("application_status")
    status_options = ["—", "to_study", "interesting", "applied", "interview", "closed"]
    new_status = a2.selectbox(
        "statut",
        status_options,
        index=0 if current_status is None else status_options.index(current_status),
        key=f"st_{j['id']}",
        label_visibility="collapsed",
    )
    if new_status != (current_status or "—"):
        set_status(j["id"], None if new_status == "—" else new_status)
        st.rerun()

    if a3.button("🔄 rescore", key=f"rs_{j['id']}", use_container_width=True):
        with st.spinner("rescore en cours..."):
            try:
                api_post(f"/jobs/{j['id']}/rescore")
                clear_caches()
                st.rerun()
            except Exception as e:
                alert(f"échec : {e}", "err")

    if j.get("archived"):
        if a4.button("♻️ désarchiver", key=f"unarch_{j['id']}",
                     use_container_width=True):
            set_archived(j["id"], False)
            st.rerun()
    else:
        if a4.button("🗑 archiver", key=f"arch_{j['id']}",
                     use_container_width=True):
            set_archived(j["id"], True)
            st.rerun()

    # Notes utilisateur
    notes = st.text_area(
        "📝 notes",
        value=j.get("notes") or "",
        key=f"notes_{j['id']}",
        height=80,
    )
    if notes != (j.get("notes") or ""):
        if st.button("💾 sauver notes", key=f"sn_{j['id']}"):
            set_notes(j["id"], notes)
            st.rerun()

    term_card_close()


# ============================================================================
# PAGE — TRIAGE (tableau bulk avec pagination + filtres + actions de masse)
# ============================================================================

def page_triage() -> None:
    page_header(
        "batch processing · multi-selection + actions de masse",
        "triage",
        "",
    )

    try:
        profiles = fetch_profiles()
    except Exception:
        profiles = []

    # ---- Filtres serrés en haut ----
    with st.expander("⚙️  filtres", expanded=True):
        f1, f2, f3, f4 = st.columns(4)
        keywords = f1.text_input("Mot-clé", "", key="tri_kw")
        min_score = f2.slider("Score min", 0.0, 10.0, 0.0, 0.5, key="tri_score")
        order_by = f3.selectbox("Tri", ORDER_BY_OPTIONS, key="tri_order")
        page_size = f4.selectbox("Par page", [25, 50, 100, 200], index=1,
                                  key="tri_size")

        f5, f6, f7, f8 = st.columns(4)
        platform = f5.multiselect("Plateforme", PLATFORM_OPTIONS, key="tri_plat")
        regions_opts = sorted({p["region"] for p in profiles}) if profiles else []
        region = f6.multiselect("Région", regions_opts, key="tri_region")
        work_mode = f7.multiselect("Mode", WORK_MODE_OPTIONS, key="tri_mode")
        scope = f8.selectbox("Périmètre", ["actives", "archivées", "toutes"],
                              key="tri_scope")

        # Toggle scoring IA + reset pagination si filtres changent
        f9, f10 = st.columns(2)
        scored_only = f9.checkbox(
            "🤖 Scorées par IA uniquement",
            key="tri_scored",
            help="N'affiche que les offres ayant un score Claude",
        )
        unscored_only = f10.checkbox(
            "⏳ EN ATTENTE de scoring (sans note IA)",
            key="tri_unscored",
            help="Inverse : les offres jamais scorées par Claude — utile pour audit",
        )

    # ---- Pagination ----
    # On reset à 0 quand la signature des filtres change : sinon on resterait
    # bloqué sur "page 5 d'un autre dataset" après une modification de filtre.
    filter_sig = (
        keywords, min_score, order_by, page_size,
        tuple(platform), tuple(region), tuple(work_mode),
        scope, scored_only, unscored_only,
    )
    if st.session_state.get("_tri_filter_sig") != filter_sig:
        st.session_state["_tri_filter_sig"] = filter_sig
        st.session_state["tri_page"] = 0
    if "tri_page" not in st.session_state:
        st.session_state["tri_page"] = 0

    # Construit les params API via helper mutualisé (offres + triage)
    params = build_jobs_params(
        keywords=keywords, min_score=min_score, scored_only=scored_only,
        platform=platform, region=region, work_mode=work_mode,
        include_archived=scope in ("archivées", "toutes"),
        order_by=order_by, limit=page_size,
        offset=st.session_state["tri_page"] * page_size,
    )

    try:
        # Fetch léger (sans description ni sources) — payload réduit ~80%
        result = fetch_jobs_light(params)
    except Exception as e:
        alert(f"erreur lecture jobs : {e}", "err")
        return

    items = result.get("items", [])
    total = result.get("total", 0)

    if scope == "archivées":
        items = [j for j in items if j.get("archived")]
    if unscored_only:
        # Filtrage côté client (pas d'endpoint API dédié pour cette inversion)
        items = [j for j in items if j.get("relevance_score") is None]

    # ---- Barre de pagination ----
    n_pages = (total + page_size - 1) // page_size if page_size else 1
    cur_page = st.session_state["tri_page"]
    pcols = st.columns([1, 1, 4, 1, 1])
    if pcols[0].button("◀◀ début", disabled=cur_page == 0,
                       use_container_width=True, key="pg_first"):
        st.session_state["tri_page"] = 0
        st.rerun()
    if pcols[1].button("◀ préc", disabled=cur_page == 0,
                       use_container_width=True, key="pg_prev"):
        st.session_state["tri_page"] = max(0, cur_page - 1)
        st.rerun()
    pcols[2].markdown(
        f'<p class="prompt" style="text-align:center;margin:0.5rem 0;">'
        f'page {cur_page + 1} / {max(1, n_pages)} · '
        f'{len(items)} affichées sur {total}</p>',
        unsafe_allow_html=True,
    )
    if pcols[3].button("suiv ▶", disabled=cur_page >= n_pages - 1,
                       use_container_width=True, key="pg_next"):
        st.session_state["tri_page"] = min(n_pages - 1, cur_page + 1)
        st.rerun()
    if pcols[4].button("fin ▶▶", disabled=cur_page >= n_pages - 1,
                       use_container_width=True, key="pg_last"):
        st.session_state["tri_page"] = max(0, n_pages - 1)
        st.rerun()

    if not items:
        alert("aucune offre dans cette page", "warn")
        return

    # ---- Construction du DataFrame ----
    # On garde "_id" en interne (jamais affiché) pour récupérer les sélections.
    # Les colonnes kanban/arch ne sont PAS affichées par défaut (cf column_order)
    # mais restent dans le DataFrame pour permettre le filtrage côté UI si besoin.
    rows = []
    for j in items:
        rows.append({
            "✓": False,
            "_id": j["id"],
            "score": j.get("relevance_score"),
            "src": j.get("platform", ""),
            "titre": (j.get("title") or "")[:90],
            "société": (j.get("company") or "")[:35],
            "lieu": (j.get("location") or "")[:30],
            "mode": (j.get("work_mode") or "").replace("_", " "),
            "âge": fmt_age(j.get("date_posted")),
            "salaire": fmt_salary(
                j.get("min_salary"), j.get("max_salary"), j.get("currency")
            ) or "",
            "lien": j.get("job_url", ""),
        })
    df = pd.DataFrame(rows)

    # column_order = liste des colonnes affichées (et leur ordre).
    # _id est dans le DataFrame mais EXCLU d'affichage → on le récupère pour les actions.
    edited = st.data_editor(
        df,
        use_container_width=True,
        height=550,
        hide_index=True,
        disabled=[c for c in df.columns if c != "✓"],
        column_order=["✓", "score", "src", "titre", "société",
                      "lieu", "mode", "âge", "salaire", "lien"],
        column_config={
            "✓": st.column_config.CheckboxColumn(
                "", help="Cocher pour action de masse",
                default=False, width="small",
            ),
            "score": st.column_config.NumberColumn(
                "score", format="%.1f", width="small",
            ),
            "src": st.column_config.TextColumn("src", width="small"),
            "titre": st.column_config.TextColumn("titre", width="large"),
            "société": st.column_config.TextColumn("société"),
            "lieu": st.column_config.TextColumn("lieu"),
            "mode": st.column_config.TextColumn("mode", width="small"),
            "âge": st.column_config.TextColumn("âge", width="small"),
            "salaire": st.column_config.TextColumn("salaire"),
            "lien": st.column_config.LinkColumn("🔗", display_text="ouvrir"),
        },
        key="tri_editor",
    )

    selected_ids = edited[edited["✓"]]["_id"].tolist()
    n_sel = len(selected_ids)

    # ---- Barre d'actions de masse ----
    st.markdown(
        f'<p class="prompt" style="margin-top:1rem;">'
        f'{n_sel} offre(s) sélectionnée(s) · choisir une action :</p>',
        unsafe_allow_html=True,
    )

    acols = st.columns(5)

    if acols[0].button("📥 → pipeline", disabled=n_sel == 0,
                       use_container_width=True, key="bulk_pipeline"):
        try:
            r = api_post("/jobs/bulk", {"action": "pipeline_in", "ids": selected_ids})
            alert(f"{r.get('affected', 0)} ajoutées au pipeline (à étudier)", "ok")
            clear_caches()
            st.rerun()
        except Exception as e:
            alert(f"erreur : {e}", "err")

    if acols[1].button("🗑 archiver", disabled=n_sel == 0,
                       use_container_width=True, key="bulk_archive"):
        try:
            r = api_post("/jobs/bulk", {"action": "archive", "ids": selected_ids})
            alert(f"{r.get('affected', 0)} archivées", "ok")
            clear_caches()
            st.rerun()
        except Exception as e:
            alert(f"erreur : {e}", "err")

    if acols[2].button("♻️ désarchiver", disabled=n_sel == 0,
                       use_container_width=True, key="bulk_unarchive"):
        try:
            r = api_post("/jobs/bulk", {"action": "unarchive", "ids": selected_ids})
            alert(f"{r.get('affected', 0)} désarchivées", "ok")
            clear_caches()
            st.rerun()
        except Exception as e:
            alert(f"erreur : {e}", "err")

    # Suppression définitive — confirmation 2-étapes
    confirm_step = st.session_state.get("_tri_delete_step", 0)
    if confirm_step == 0:
        if acols[3].button("❌ supprimer DÉFINITIVEMENT", disabled=n_sel == 0,
                           use_container_width=True, key="bulk_del_btn"):
            st.session_state["_tri_delete_step"] = 1
            st.session_state["_tri_delete_ids"] = selected_ids
            st.rerun()
    else:
        ids_to_del = st.session_state.get("_tri_delete_ids", [])
        alert(
            f"⚠️ confirmation : suppression DÉFINITIVE de {len(ids_to_del)} offres "
            f"(irréversible — pas d'archive)",
            "err",
        )
        cc1, cc2 = st.columns(2)
        if cc1.button("✓ CONFIRMER SUPPRESSION", use_container_width=True,
                      key="bulk_del_confirm"):
            try:
                r = api_post("/jobs/bulk",
                             {"action": "delete", "ids": ids_to_del})
                alert(f"{r.get('affected', 0)} offres supprimées définitivement",
                      "ok")
                clear_caches()
                st.session_state["_tri_delete_step"] = 0
                st.session_state["_tri_delete_ids"] = []
                st.rerun()
            except Exception as e:
                alert(f"erreur : {e}", "err")
                st.session_state["_tri_delete_step"] = 0
        if cc2.button("✕ annuler", use_container_width=True,
                      key="bulk_del_cancel"):
            st.session_state["_tri_delete_step"] = 0
            st.session_state["_tri_delete_ids"] = []
            st.rerun()

    if acols[4].button("🔄 refresh", use_container_width=True, key="bulk_refresh"):
        clear_caches()
        st.rerun()


# ============================================================================
# PAGE — PIPELINE (Kanban 5 colonnes)
# ============================================================================

KANBAN_STAGES = [
    ("to_study",    "📥 À ÉTUDIER"),
    ("interesting", "⭐ INTÉRESSANTE"),
    ("applied",     "📤 POSTULÉE"),
    ("interview",   "💬 ENTRETIEN"),
    ("closed",      "🔒 FERMÉE"),
]


def page_pipeline() -> None:
    page_header("kanban /pipeline · move via boutons ◀ ▶", "pipeline", "")

    try:
        result = fetch_jobs({
            "in_pipeline": True, "limit": 500, "include_archived": True,
        })
    except Exception as e:
        alert(f"erreur : {e}", "err")
        return

    items = result.get("items", [])
    if not items:
        alert(
            "aucune offre dans le pipeline · va dans Offres pour ajouter une candidature",
            "info",
        )
        return

    # Group par statut
    by_stage: dict[str, list] = {s: [] for s, _ in KANBAN_STAGES}
    for j in items:
        s = j.get("application_status")
        if s in by_stage:
            by_stage[s].append(j)

    st.markdown(
        f'<p class="prompt">{len(items)} offres dans le pipeline</p>',
        unsafe_allow_html=True,
    )

    # 5 colonnes
    cols = st.columns(5, gap="small")
    for idx, (stage_key, stage_label) in enumerate(KANBAN_STAGES):
        col_jobs = by_stage[stage_key]
        with cols[idx]:
            st.markdown(
                f'<div class="kanban-col"><div class="kanban-col__header">'
                f'<p class="kanban-col__title">{stage_label}</p>'
                f'<span class="kanban-col__count">{len(col_jobs)}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

            for j in col_jobs:
                pill = score_pill(j.get("relevance_score"))
                applied_txt = ""
                if j.get("applied_date"):
                    applied_txt = f' · postulé {fmt_age(j["applied_date"])}'

                st.markdown(
                    f'<div class="kc">'
                    f'<div class="kc__title">{safe((j.get("title") or "")[:60])}</div>'
                    f'<div class="kc__meta">'
                    f'<span>{safe((j.get("company") or "?")[:18])}</span>'
                    f'<span style="margin-left:auto">{pill}</span>'
                    f'</div>'
                    f'<div class="kc__meta" style="margin-top:4px;">'
                    f'<span>{safe(j.get("region") or "")}{applied_txt}</span>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # Boutons de déplacement
                idx_now = [k for k, _ in KANBAN_STAGES].index(stage_key)
                bcols = st.columns([1, 1, 1])
                if idx_now > 0:
                    if bcols[0].button("◀", key=f"prev_{j['id']}", help="reculer",
                                       use_container_width=True):
                        set_status(j["id"], KANBAN_STAGES[idx_now - 1][0])
                        st.rerun()
                else:
                    bcols[0].markdown("&nbsp;", unsafe_allow_html=True)
                if idx_now < len(KANBAN_STAGES) - 1:
                    if bcols[1].button("▶", key=f"next_{j['id']}", help="avancer",
                                       use_container_width=True):
                        set_status(j["id"], KANBAN_STAGES[idx_now + 1][0])
                        st.rerun()
                else:
                    bcols[1].markdown("&nbsp;", unsafe_allow_html=True)
                if bcols[2].button("✕", key=f"out_{j['id']}",
                                   help="retirer du pipeline",
                                   use_container_width=True):
                    set_status(j["id"], None)
                    st.rerun()


# ============================================================================
# PAGE — INSIGHTS (4 dashboards Plotly)
# ============================================================================

def page_insights() -> None:
    page_header("analytics · post-mortem chiffré", "insights", "")

    try:
        stats = fetch_stats()
        # On utilise la version light (sans description ni sources) car les graphes
        # n'utilisent que les champs synthétiques. Économise ~80% de bande passante.
        all_jobs = fetch_jobs_light(
            {"limit": 1000, "include_archived": True}
        ).get("items", [])
    except Exception as e:
        alert(f"erreur : {e}", "err")
        return

    df = pd.DataFrame(all_jobs)
    if df.empty:
        alert("base vide", "warn")
        return

    t1, t2, t3, t4 = st.tabs([
        "▸ SOURCES & VOLUMES",
        "▸ SCORING",
        "▸ GÉOGRAPHIE",
        "▸ TEMPOREL",
    ])

    with t1:
        c1, c2 = st.columns(2)
        with c1:
            term_card_open("by_platform.svg")
            by_plat = pd.DataFrame(
                [{"platform": k, "count": v}
                 for k, v in stats.get("by_platform", {}).items()]
            ).sort_values("count", ascending=True)
            if not by_plat.empty:
                fig = px.bar(by_plat, x="count", y="platform", orientation="h",
                             color="count",
                             color_continuous_scale=["#1E3044", "#00FF41"])
                fig.update_traces(marker_line_width=0)
                fig.update_layout(coloraxis_showscale=False, showlegend=False,
                                  height=300)
                st.plotly_chart(theme_plot(fig), use_container_width=True)
            term_card_close()
        with c2:
            term_card_open("by_region.svg")
            by_reg = pd.DataFrame(
                [{"region": k, "count": v}
                 for k, v in stats.get("by_region", {}).items()]
            )
            if not by_reg.empty:
                fig = px.pie(by_reg, names="region", values="count", hole=0.5)
                fig.update_traces(
                    textfont_color="#FFFFFF",
                    marker=dict(line=dict(color="#000", width=2)),
                )
                fig.update_layout(height=300)
                st.plotly_chart(theme_plot(fig), use_container_width=True)
            else:
                st.markdown('<p class="prompt">pas de données géo</p>',
                            unsafe_allow_html=True)
            term_card_close()

    with t2:
        c1, c2 = st.columns(2)
        with c1:
            term_card_open("score_distribution.svg")
            scored = df[df["relevance_score"].notna()]
            if not scored.empty:
                fig = px.histogram(scored, x="relevance_score", nbins=20,
                                   color_discrete_sequence=["#00FF41"])
                fig.update_layout(height=300, showlegend=False,
                                  xaxis_title="score", yaxis_title="offres")
                st.plotly_chart(theme_plot(fig), use_container_width=True)
            else:
                alert("aucune offre scorée", "warn")
            term_card_close()
        with c2:
            term_card_open("score_axes.svg")
            scored = df[df["relevance_score"].notna()]
            if not scored.empty:
                # BUG corrigé : avant on testait `"base_score" in scored` ce qui
                # vérifiait la présence dans les VALEURS du DataFrame (jamais True),
                # donc les moyennes étaient toujours à 0. Le bon test est sur .columns.
                axes = pd.DataFrame({
                    "axe": ["Content (Claude)", "Géo", "Salaire", "Fraîcheur"],
                    "moyenne": [
                        scored["base_score"].mean() if "base_score" in scored.columns else 0,
                        scored["score_geo"].mean() if "score_geo" in scored.columns else 0,
                        scored["score_salary"].mean() if "score_salary" in scored.columns else 0,
                        scored["score_freshness"].mean() if "score_freshness" in scored.columns else 0,
                    ],
                })
                fig = px.bar(axes, x="axe", y="moyenne", color="moyenne",
                             color_continuous_scale=["#1E3044", "#00FF41"],
                             text_auto=".1f")
                fig.update_layout(height=300, showlegend=False,
                                  coloraxis_showscale=False, yaxis_range=[0, 10])
                fig.update_traces(textposition="outside",
                                  textfont_color="#FFFFFF")
                st.plotly_chart(theme_plot(fig), use_container_width=True)
            term_card_close()

    with t3:
        c1, c2 = st.columns(2)
        with c1:
            term_card_open("work_mode.svg")
            wm_data = df["work_mode"].fillna("(inconnu)").value_counts().reset_index()
            wm_data.columns = ["mode", "count"]
            fig = px.pie(wm_data, names="mode", values="count", hole=0.5,
                         color_discrete_sequence=["#00FF41", "#00D4FF",
                                                   "#FFD700", "#8899AA"])
            fig.update_traces(
                textfont_color="#FFFFFF",
                marker=dict(line=dict(color="#000", width=2)),
            )
            fig.update_layout(height=300)
            st.plotly_chart(theme_plot(fig), use_container_width=True)
            term_card_close()
        with c2:
            term_card_open("top_companies.svg")
            top_co = (
                df["company"].fillna("(inconnue)")
                .value_counts().head(15).reset_index()
            )
            top_co.columns = ["company", "count"]
            fig = px.bar(top_co, x="count", y="company", orientation="h",
                         color="count",
                         color_continuous_scale=["#1E3044", "#00FF41"])
            fig.update_layout(height=400, showlegend=False,
                              coloraxis_showscale=False,
                              yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(theme_plot(fig), use_container_width=True)
            term_card_close()

    with t4:
        term_card_open("scrapes_over_time.svg")
        try:
            logs = fetch_logs({"limit": 60}).get("items", [])
            if logs:
                ld = pd.DataFrame(logs)
                ld["started_at"] = pd.to_datetime(ld["started_at"])
                ld["date"] = ld["started_at"].dt.date
                daily = ld.groupby("date", as_index=False).agg(
                    new_jobs=("new_jobs", "sum"),
                    duplicates=("duplicates", "sum"),
                )
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=daily["date"], y=daily["new_jobs"],
                    name="Nouvelles", marker_color="#00FF41",
                ))
                fig.add_trace(go.Bar(
                    x=daily["date"], y=daily["duplicates"],
                    name="Doublons", marker_color="#1E3044",
                ))
                fig.update_layout(barmode="stack", height=350,
                                  xaxis_title="date", yaxis_title="offres")
                st.plotly_chart(theme_plot(fig), use_container_width=True)
            else:
                st.markdown('<p class="prompt">pas de logs disponibles</p>',
                            unsafe_allow_html=True)
        except Exception as e:
            alert(f"erreur logs : {e}", "err")
        term_card_close()


# ============================================================================
# PAGE — SYSTÈME (logs + ops)
# ============================================================================

def page_systeme() -> None:
    page_header("ops · logs · post-mortem", "système", "")

    try:
        hdb = fetch_health_db()
        h = api_get("/health")
    except Exception as e:
        alert(f"backend injoignable : {e}", "err")
        return

    c1, c2 = st.columns(2)
    with c1:
        term_card_open("health.json")
        st.markdown(
            status_line("STATUS",        h.get("status", "?").upper(), "ok")
            + status_line("BACKEND",     h.get("time", "?"), "ok")
            + status_line("DB SIZE",     f'{hdb.get("db_mb", 0)} MB', "ok")
            + status_line("DB WAL",      f'{hdb.get("wal_bytes", 0)} bytes', "ok")
            + status_line("JOBS TOTAL",  str(hdb.get("jobs_total", 0)), "ok")
            + status_line("ARCHIVED",    str(hdb.get("jobs_archived", 0)), "ok")
            + status_line("IN PIPELINE", str(hdb.get("jobs_applied", 0)), "ok")
            + status_line("LOGS KEPT",   str(hdb.get("scrape_logs_total", 0)), "ok"),
            unsafe_allow_html=True,
        )
        term_card_close()

    with c2:
        term_card_open("ops_actions.sh")
        st.markdown(
            '<p class="prompt">opérations sensibles · facture OpenRouter possible</p>',
            unsafe_allow_html=True,
        )

        if st.button("▸ rescore manquants seulement",
                     use_container_width=True, key="op_rescore"):
            try:
                r = api_post("/rescore")
                alert(f"{r.get('pending', 0)} jobs planifiés", "ok")
            except Exception as e:
                alert(f"erreur : {e}", "err")

        # Force rescore avec confirmation
        force_step = st.session_state.get("_force_step", 0)
        if force_step == 0:
            if st.button("▸ rescore TOUT (force) ⚠️",
                         use_container_width=True, key="op_force_btn"):
                st.session_state["_force_step"] = 1
                st.rerun()
        else:
            alert(
                "confirmation requise — coût estimé ~$2/1000 jobs avec Haiku 4.5",
                "warn",
            )
            cc1, cc2 = st.columns(2)
            if cc1.button("✓ CONFIRMER", use_container_width=True,
                          key="op_force_confirm"):
                try:
                    r = api_post("/rescore", params={"force": "true"})
                    alert(f"{r.get('pending', 0)} jobs planifiés (force=true)", "ok")
                    st.session_state["_force_step"] = 0
                except Exception as e:
                    alert(f"erreur : {e}", "err")
                    st.session_state["_force_step"] = 0
            if cc2.button("✕ annuler", use_container_width=True,
                          key="op_force_cancel"):
                st.session_state["_force_step"] = 0
                st.rerun()

        if st.button("▸ purger cache UI", use_container_width=True, key="op_cache"):
            clear_caches()
            st.rerun()

        term_card_close()

    st.markdown("<br>", unsafe_allow_html=True)

    # Logs scrape
    term_card_open("scrape_logs.tail")
    try:
        logs = fetch_logs({"limit": 50}).get("items", [])
        if not logs:
            st.markdown('<p class="prompt">aucun log</p>', unsafe_allow_html=True)
        else:
            ld = pd.DataFrame(logs)
            cols_to_show = ["started_at", "status", "profile", "triggered_by",
                            "scraped", "new_jobs", "duplicates", "merged_sources",
                            "blacklisted"]
            shown = ld[[c for c in cols_to_show if c in ld.columns]].copy()
            if "started_at" in shown.columns:
                shown["started_at"] = (
                    pd.to_datetime(shown["started_at"])
                    .dt.strftime("%Y-%m-%d %H:%M:%S")
                )
            st.dataframe(shown, use_container_width=True,
                         hide_index=True, height=400)

            with_errors = [l for l in logs if l.get("errors") or l.get("fatal_error")]
            if with_errors:
                st.markdown(
                    f'<p class="prompt">{len(with_errors)} run(s) avec erreurs</p>',
                    unsafe_allow_html=True,
                )
                for l in with_errors[:5]:
                    with st.expander(
                        f"⚠️  {l.get('started_at', '?')} · {l.get('profile', '?')}"
                    ):
                        if l.get("fatal_error"):
                            st.markdown(
                                f'<div class="alert alert--err">{safe(l["fatal_error"])}</div>',
                                unsafe_allow_html=True,
                            )
                        for err in (l.get("errors") or [])[:20]:
                            st.markdown(
                                f'<div class="alert alert--warn">{safe(err)}</div>',
                                unsafe_allow_html=True,
                            )
    except Exception as e:
        alert(f"erreur lecture logs : {e}", "err")
    term_card_close()


# ============================================================================
# Router (sélectionne la page courante)
# ============================================================================

PAGE_FUNCS = {
    "cockpit":  page_cockpit,
    "offres":   page_offres,
    "triage":   page_triage,
    "pipeline": page_pipeline,
    "insights": page_insights,
    "systeme":  page_systeme,
}

PAGE_FUNCS[st.session_state["current_page"]]()


# ============================================================================
# Footer
# ============================================================================

st.markdown(
    '<div class="footer">'
    'jobscout v2.0 · '
    f'<a href="{BACKEND_URL}/docs" target="_blank">api docs</a>'
    '</div>',
    unsafe_allow_html=True,
)
