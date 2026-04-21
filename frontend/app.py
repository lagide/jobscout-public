"""Streamlit UI for JobScout.

Tabs: Offres | Statistiques | Actions | Logs
Talks to the FastAPI backend over HTTP; BACKEND_URL defaults to the Compose service name.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
REQUEST_TIMEOUT = 180  # /search can take over a minute per profile

st.set_page_config(
    page_title="JobScout — Senior IT Jobs",
    page_icon="🎯",
    layout="wide",
)


# ---------- Custom CSS ----------

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 4rem; }
    .score-badge {
        display: flex; align-items: center; justify-content: center;
        width: 64px; height: 64px; border-radius: 50%;
        font-size: 1.6rem; font-weight: 700; color: white; margin: 0 auto;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    }
    .score-label {
        text-align: center; font-size: 0.72rem; color: #6b7280;
        margin-top: 4px; letter-spacing: 0.04em; text-transform: uppercase;
    }
    .pill {
        display: inline-block; padding: 2px 10px; border-radius: 999px;
        font-size: 0.78rem; font-weight: 500; margin: 2px 6px 2px 0;
        border: 1px solid transparent; line-height: 1.4;
    }
    .pill-platform  { background: #eef2ff; color: #3730a3; border-color: #c7d2fe; }
    .pill-remote    { background: #ecfdf5; color: #065f46; border-color: #a7f3d0; }
    .pill-salary    { background: #fffbeb; color: #92400e; border-color: #fde68a; }
    .pill-location  { background: #f3f4f6; color: #374151; border-color: #e5e7eb; }
    .pill-date      { background: #f5f3ff; color: #5b21b6; border-color: #ddd6fe; }
    .pill-new       { background: #fee2e2; color: #991b1b; border-color: #fca5a5; font-weight: 700; }
    .pill-region    { background: #e0f2fe; color: #075985; border-color: #7dd3fc; }
    .pill-source    { background: #f1f5f9; color: #334155; border-color: #cbd5e1; font-weight: 600; }
    .pill-full-remote { background: #dcfce7; color: #166534; border-color: #86efac; }
    .pill-hybrid    { background: #fef9c3; color: #854d0e; border-color: #fde047; }
    .pill-onsite    { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
    .pill-lang      { background: #ede9fe; color: #5b21b6; border-color: #c4b5fd; }
    .pill-effective { background: #fae8ff; color: #86198f; border-color: #f0abfc; font-weight: 600; }
    .job-title {
        font-size: 1.1rem; font-weight: 650; color: #111827;
        margin: 0 0 2px 0; line-height: 1.35;
    }
    .job-company { font-size: 0.92rem; color: #4b5563; margin: 0 0 8px 0; }
    .job-reason {
        font-size: 0.88rem; color: #374151; background: #f9fafb;
        border-left: 3px solid #6366f1; padding: 8px 12px; margin-top: 10px;
        border-radius: 4px; line-height: 1.5;
    }
    .job-reason-empty {
        font-size: 0.85rem; color: #9ca3af; font-style: italic; margin-top: 10px;
    }
    .sources-row { margin-top: 6px; }
    .sources-label {
        font-size: 0.75rem; color: #6b7280; margin-right: 6px; text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .score-breakdown {
        margin-top: 8px; padding: 6px 10px; background: #f8fafc;
        border-radius: 6px; border: 1px solid #e2e8f0;
        display: flex; flex-wrap: wrap; gap: 6px 16px;
        font-size: 0.76rem; color: #64748b;
    }

    /* ---------- Split view: compact list (left) ---------- */
    .list-panel {
        max-height: calc(100vh - 280px); overflow-y: auto;
        padding-right: 4px;
    }
    .list-panel::-webkit-scrollbar { width: 6px; }
    .list-panel::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }

    /* Style the Streamlit buttons used as list rows */
    div[data-testid="stVerticalBlock"] .row-btn button,
    .row-btn button {
        text-align: left !important;
        padding: 8px 12px !important;
        font-weight: 500 !important;
        font-size: 0.88rem !important;
        line-height: 1.35 !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        border-left: 3px solid transparent !important;
        border-radius: 6px !important;
        color: #1f2937 !important;
        min-height: 56px !important;
        transition: background .15s, border-color .15s;
    }
    .row-btn button:hover {
        background: #f1f5f9 !important;
        border-color: #e2e8f0 !important;
    }
    .row-btn-selected button {
        background: #eff6ff !important;
        border-color: #bfdbfe !important;
        border-left: 3px solid #3b82f6 !important;
    }

    /* ---------- Detail panel (right) ---------- */
    .detail-panel {
        position: sticky; top: 16px;
        padding: 18px 20px; background: #ffffff;
        border: 1px solid #e5e7eb; border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .detail-header {
        display: flex; align-items: center; gap: 14px;
        padding-bottom: 14px; border-bottom: 1px solid #f1f5f9;
    }
    .detail-score {
        width: 56px; height: 56px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 1.3rem; font-weight: 700; color: white; flex-shrink: 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .detail-title {
        font-size: 1.2rem; font-weight: 650; color: #0f172a;
        margin: 0 0 4px 0; line-height: 1.3;
    }
    .detail-company { font-size: 0.95rem; color: #475569; margin: 0; }
    .detail-section-label {
        font-size: 0.72rem; font-weight: 600; color: #64748b;
        text-transform: uppercase; letter-spacing: 0.06em;
        margin: 16px 0 6px 0;
    }
    .detail-description {
        font-size: 0.88rem; color: #374151; line-height: 1.55;
        max-height: 360px; overflow-y: auto;
        padding: 10px 12px; background: #f8fafc; border-radius: 6px;
    }
    .empty-detail {
        padding: 60px 20px; text-align: center; color: #94a3b8;
        font-size: 0.95rem; font-style: italic;
    }

    /* Dark mode tweaks */
    @media (prefers-color-scheme: dark) {
        .row-btn button { color: #e5e7eb !important; }
        .row-btn button:hover { background: #1e293b !important; border-color: #334155 !important; }
        .row-btn-selected button {
            background: #0f172a !important; border-color: #1e40af !important;
            border-left: 3px solid #60a5fa !important;
        }
        .detail-panel { background: #0f172a; border-color: #1e293b; }
        .detail-title { color: #f1f5f9; }
        .detail-company { color: #cbd5e1; }
        .detail-description { background: #1e293b; color: #e2e8f0; }
    }

    /* ---------- Kanban cards (P1) ---------- */
    .kanban-card-btn button {
        text-align: left !important;
        padding: 8px 10px !important;
        font-size: 0.82rem !important;
        line-height: 1.3 !important;
        background: transparent !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 8px !important;
        color: #1f2937 !important;
        min-height: 50px !important;
        margin-bottom: 6px !important;
        white-space: pre-wrap !important;
    }
    .kanban-card-btn button:hover { background: #f8fafc !important; }
    .kanban-card-selected button {
        background: #eff6ff !important;
        border: 2px solid #3b82f6 !important;
    }
    .kanban-col-header {
        font-size: 0.88rem; font-weight: 600; color: #475569;
        padding: 6px 10px; margin-bottom: 8px;
        background: #f8fafc; border-radius: 6px;
        border-top: 3px solid #cbd5e1;
        display: flex; justify-content: space-between; align-items: center;
    }
    .kanban-col-count {
        font-size: 0.72rem; font-weight: 500; color: #64748b;
        background: white; padding: 2px 8px; border-radius: 12px;
        border: 1px solid #e2e8f0;
    }
    .reminder-badge {
        display: inline-block; padding: 2px 8px; border-radius: 10px;
        font-size: 0.72rem; font-weight: 600; margin-left: 4px;
    }
    .reminder-urgent   { background: #fee2e2; color: #991b1b; }
    .reminder-warning  { background: #fef3c7; color: #92400e; }
    .reminder-info     { background: #dbeafe; color: #1e40af; }
    .reminder-ok       { background: #dcfce7; color: #166534; }

    /* ---------- Dashboards (S1) ---------- */
    .dash-section {
        margin: 18px 0 8px 0;
        padding: 4px 0 2px 0;
        border-bottom: 2px solid #e5e7eb;
    }
    .dash-section-title {
        font-size: 1.1rem; font-weight: 650; color: #0f172a;
        margin: 0 0 4px 0;
    }
    .dash-section-sub {
        font-size: 0.82rem; color: #64748b; margin: 0;
    }
    .insight-card {
        padding: 12px 16px; background: #f0f9ff;
        border-left: 4px solid #3b82f6; border-radius: 6px;
        margin: 10px 0; font-size: 0.9rem; color: #1e3a8a;
    }
    .anomaly-card {
        padding: 12px 16px; background: #fef3c7;
        border-left: 4px solid #f59e0b; border-radius: 6px;
        margin: 10px 0; font-size: 0.9rem; color: #78350f;
    }

    /* ---------- Logs dashboard (L1+L2) ---------- */
    .health-gauge {
        padding: 16px 20px; border-radius: 10px;
        display: flex; align-items: center; gap: 16px;
    }
    .health-ok    { background: linear-gradient(135deg, #dcfce7 0%, #bbf7d0 100%); border-left: 5px solid #16a34a; }
    .health-warn  { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border-left: 5px solid #f59e0b; }
    .health-error { background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%); border-left: 5px solid #dc2626; }
    .health-icon { font-size: 2.2rem; flex-shrink: 0; }
    .health-title { font-size: 1.05rem; font-weight: 700; margin: 0; }
    .health-subtitle { font-size: 0.85rem; opacity: 0.85; margin: 2px 0 0 0; }
    .timeline-dot {
        display: inline-block; width: 14px; height: 14px; border-radius: 50%;
        margin: 2px; vertical-align: middle;
    }
    .connector-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 8px 12px; margin: 4px 0;
        background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0;
        font-size: 0.88rem;
    }
    .connector-status {
        display: inline-block; width: 8px; height: 8px; border-radius: 50%;
        margin-right: 8px;
    }

    @media (prefers-color-scheme: dark) {
        .kanban-card-btn button { background: #0f172a !important; color: #e5e7eb !important; border-color: #1e293b !important; }
        .kanban-card-btn button:hover { background: #1e293b !important; }
        .kanban-card-selected button { background: #0c1628 !important; border-color: #60a5fa !important; }
        .kanban-col-header { background: #0f172a; color: #cbd5e1; border-top-color: #334155; }
        .kanban-col-count  { background: #1e293b; border-color: #334155; color: #cbd5e1; }
        .dash-section-title { color: #f1f5f9; }
        .dash-section-sub   { color: #94a3b8; }
        .dash-section       { border-bottom-color: #1e293b; }
        .insight-card  { background: #0c1628; color: #bfdbfe; }
        .anomaly-card  { background: #2b2415; color: #fde68a; }
        .connector-row { background: #0f172a; border-color: #1e293b; color: #e2e8f0; }
        .health-ok, .health-warn, .health-error { color: #1f2937; }
    }
    .score-comp { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
    .comp-label { color: #94a3b8; }
    .comp-val   { font-weight: 700; min-width: 2.2ch; }
    .comp-bar   {
        display: inline-block; height: 5px; border-radius: 3px;
        vertical-align: middle; min-width: 2px;
    }
    @media (prefers-color-scheme: dark) {
        .job-title   { color: #f3f4f6; }
        .job-company { color: #d1d5db; }
        .job-reason  { background: #1f2937; color: #e5e7eb; }
        .score-breakdown { background: #1e293b; border-color: #334155; color: #94a3b8; }
        .comp-label  { color: #64748b; }
        .pill-platform { background: #312e81; color: #e0e7ff; border-color: #4338ca; }
        .pill-remote   { background: #064e3b; color: #d1fae5; border-color: #047857; }
        .pill-salary   { background: #78350f; color: #fef3c7; border-color: #b45309; }
        .pill-location { background: #374151; color: #f3f4f6; border-color: #4b5563; }
        .pill-date     { background: #4c1d95; color: #ede9fe; border-color: #6d28d9; }
        .pill-new      { background: #7f1d1d; color: #fee2e2; border-color: #b91c1c; }
        .pill-region   { background: #0c4a6e; color: #e0f2fe; border-color: #0369a1; }
        .pill-source   { background: #1e293b; color: #e2e8f0; border-color: #475569; }
        .pill-full-remote { background: #14532d; color: #dcfce7; border-color: #16a34a; }
        .pill-hybrid      { background: #713f12; color: #fef9c3; border-color: #a16207; }
        .pill-onsite      { background: #7f1d1d; color: #fee2e2; border-color: #b91c1c; }
        .pill-lang        { background: #4c1d95; color: #ede9fe; border-color: #7c3aed; }
        .pill-effective   { background: #581c87; color: #fae8ff; border-color: #a21caf; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- HTTP helpers ----------

def api_get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json: dict | None = None) -> dict:
    r = requests.post(f"{BACKEND_URL}{path}", json=json, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=30)
def fetch_jobs(params: dict) -> dict:
    return api_get("/jobs", params=params)


@st.cache_data(ttl=30)
def fetch_stats() -> dict:
    return api_get("/stats")


@st.cache_data(ttl=300)
def fetch_profiles() -> list[dict]:
    return api_get("/profiles")


@st.cache_data(ttl=15)
def fetch_logs(params: dict) -> dict:
    return api_get("/logs", params=params)


@st.cache_data(ttl=10)
def fetch_pipeline() -> dict:
    """All offers currently in the pipeline (non-null application_status)."""
    return api_get("/jobs", params={"limit": 500, "in_pipeline": "true", "include_archived": "false"})


def clear_caches() -> None:
    fetch_jobs.clear()
    fetch_stats.clear()
    fetch_logs.clear()
    fetch_pipeline.clear()


# ---------- Pipeline state mutations ----------

PIPELINE_STATUSES = [
    ("to_study",     "🔍 À étudier"),
    ("interesting",  "⭐ Intéressant"),
    ("applied",      "📮 Postulé"),
    ("interview",    "🎤 Entretien"),
    ("closed",       "✔️ Clôturé"),
]
PIPELINE_LABEL = dict(PIPELINE_STATUSES)
PIPELINE_KEYS = [k for k, _ in PIPELINE_STATUSES]


def set_status(job_id: int, status: Optional[str]) -> None:
    """Move an offer between pipeline columns. status=None removes it from the pipeline."""
    api_post(f"/jobs/{job_id}/status", json={"status": status})
    clear_caches()


def set_notes(job_id: int, notes: str) -> None:
    api_post(f"/jobs/{job_id}/notes", json={"notes": notes})
    clear_caches()


def set_archived(job_id: int, archived: bool) -> None:
    api_post(f"/jobs/{job_id}/archive", json={"archived": archived})
    clear_caches()


def age_color(date_str) -> str:
    """Traffic-light background color based on job posting age."""
    if not date_str or pd.isna(date_str):
        return "#9ca3af"  # gray — unknown
    try:
        d = pd.to_datetime(date_str, errors="coerce")
        if pd.isna(d):
            return "#9ca3af"
        days = (datetime.now() - d.to_pydatetime().replace(tzinfo=None)).days
        if days <= 3:
            return "#10b981"  # green — fresh
        if days <= 7:
            return "#f59e0b"  # amber
        if days <= 14:
            return "#ef4444"  # red
        return "#6b7280"  # dark gray — stale
    except Exception:
        return "#9ca3af"


# ---------- Formatting helpers ----------

def score_color(score) -> str:
    if score is None or pd.isna(score):
        return "#9ca3af"
    s = float(score)
    if s >= 8:
        return "#10b981"
    if s >= 6:
        return "#3b82f6"
    if s >= 4:
        return "#f59e0b"
    return "#ef4444"


def fmt_k(v) -> Optional[str]:
    if v is None or pd.isna(v):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v >= 1000:
        return f"{v/1000:.0f}k"
    return f"{v:.0f}"


def fmt_salary(lo, hi, cur) -> Optional[str]:
    a, b = fmt_k(lo), fmt_k(hi)
    cur = cur if isinstance(cur, str) and cur else "€"
    if a and b and a != b:
        return f"{a}–{b} {cur}"
    return f"{a or b} {cur}" if (a or b) else None


def fmt_date_pill(date_str) -> tuple[str, bool]:
    if not date_str or pd.isna(date_str):
        return ("—", False)
    try:
        d = pd.to_datetime(date_str, errors="coerce")
        if pd.isna(d):
            return ("—", False)
        now = datetime.now(tz=d.tzinfo) if d.tzinfo else datetime.now()
        delta = now - d.to_pydatetime().replace(tzinfo=None) if not d.tzinfo else now - d.to_pydatetime()
        hours = delta.total_seconds() / 3600
        is_new = hours <= 48
        if hours < 24:
            return (f"il y a {int(hours)}h", is_new)
        days = int(hours // 24)
        return (f"il y a {days}j", is_new)
    except Exception:
        return (str(date_str), False)


PLATFORM_EMOJI = {
    "linkedin": "💼",
    "indeed": "🔎",
    "glassdoor": "🏢",
    "zip_recruiter": "📮",
    "google": "🔍",
    "remotive": "🌐",
    "francetravail": "🇫🇷",
    "freework": "🛠️",
    "himalayas": "🏔️",
    "greenhouse": "🌱",
    "workday": "🏢",
    "apec": "🎓",
}


# ---------- Load metadata ----------

try:
    PROFILES = fetch_profiles()
except requests.RequestException:
    PROFILES = []
PROFILE_BY_KEY = {p["key"]: p for p in PROFILES}
PROFILE_KEYS = [p["key"] for p in PROFILES]
REGIONS = sorted({p["region"] for p in PROFILES})


# ---------- Sidebar: filters ----------

st.sidebar.title("🎯 JobScout")
st.sidebar.caption("Offres seniors IT — Cyber / Réseau / Leadership")

with st.sidebar:
    st.subheader("Filtres")
    keywords = st.text_input("Mots-clés", placeholder="ex: SIEM, EDR, cloud")
    location_filter = st.text_input("Localisation (libre)", placeholder="ex: Paris, Lyon")

    platforms = st.multiselect(
        "Plateformes",
        options=["linkedin", "indeed", "glassdoor", "zip_recruiter", "google",
                 "remotive", "francetravail", "freework", "himalayas",
                 "greenhouse", "workday", "apec"],
        default=["linkedin", "indeed", "glassdoor", "francetravail", "freework"],
    )

    if PROFILE_KEYS:
        profile_filter = st.multiselect(
            "Profils géographiques",
            options=PROFILE_KEYS,
            default=[],
            format_func=lambda k: f"{PROFILE_BY_KEY[k]['flag']} {k}",
            help="Filtrer sur le(s) profil(s) ayant capté les offres.",
        )
    else:
        profile_filter = []

    work_mode_filter = st.multiselect(
        "Mode de travail",
        options=["full_remote", "hybrid", "onsite"],
        default=[],
        format_func={
            "full_remote": "🟢 Full remote",
            "hybrid": "🟡 Hybride",
            "onsite": "🔴 Présentiel",
        }.get,
    )

    language_filter = st.multiselect(
        "Langue",
        options=["fr", "en", "de"],
        default=[],
        format_func={"fr": "🇫🇷 Français", "en": "🇬🇧 English", "de": "🇩🇪 Deutsch"}.get,
    )

    min_salary = st.number_input("Salaire minimum (€)", min_value=0, value=0, step=5000)
    min_score = st.slider("Score de pertinence minimum", 0.0, 10.0, 0.0, 0.5)
    remote_only = st.checkbox("Télétravail uniquement (legacy flag)")
    order_by = st.selectbox(
        "Tri",
        ["relevance", "date", "scraped"],
        format_func={
            "relevance": "Pertinence",
            "date": "Date de publication",
            "scraped": "Date d'ajout",
        }.get,
    )

    if st.button("🔄 Rafraîchir", use_container_width=True):
        clear_caches()
        st.rerun()


# ---------- Main area ----------

tab_jobs, tab_pipeline, tab_stats, tab_actions, tab_logs = st.tabs(
    ["📋 Offres", "📌 Pipeline", "📊 Statistiques", "⚙️ Actions", "📜 Logs"]
)

query_params: dict = {
    "limit": 500,
    "order_by": order_by,
    "remote_only": str(remote_only).lower(),
}
if keywords:
    query_params["keywords"] = keywords
if location_filter:
    query_params["location"] = location_filter
if platforms:
    query_params["platform"] = platforms
if profile_filter:
    query_params["profile"] = profile_filter
if work_mode_filter:
    query_params["work_mode"] = work_mode_filter
if language_filter:
    query_params["language"] = language_filter
if min_salary > 0:
    query_params["min_salary"] = min_salary
if min_score > 0:
    query_params["min_score"] = min_score


# ---------- Score breakdown helper ----------

def _score_bar_html(label: str, icon: str, score: Optional[float], tooltip: str = "") -> str:
    """Compact mini-bar for one scoring component (geo / salary / freshness / content)."""
    if score is None:
        return (
            f'<span class="score-comp" title="{tooltip}">'
            f'<span class="comp-label">{icon} {label}</span>'
            f'<span class="comp-val" style="color:#94a3b8;">—</span>'
            f'</span>'
        )
    pct = max(0.0, min(100.0, score * 10.0))
    # Color: red < 4, orange 4-6, blue 6-8, green ≥ 8
    if score >= 8:
        color = "#16a34a"
    elif score >= 6:
        color = "#2563eb"
    elif score >= 4:
        color = "#d97706"
    else:
        color = "#dc2626"
    bar_w = int(pct * 0.5)  # max 50px
    return (
        f'<span class="score-comp" title="{tooltip}">'
        f'<span class="comp-label">{icon} {label}</span>'
        f'<span class="comp-val" style="color:{color};">{score:.1f}</span>'
        f'<span class="comp-bar" style="width:{bar_w}px;background:{color};"></span>'
        f'</span>'
    )


def _score_breakdown_html(row: pd.Series) -> str:
    """Build the full 4-component score breakdown row for a job card."""
    geo      = row.get("score_geo")
    salary   = row.get("score_salary")
    freshness = row.get("score_freshness")
    content  = row.get("base_score")

    # Tooltip hints
    wm = row.get("work_mode") or ""
    wm_hint = {"full_remote": "Full remote", "hybrid": "Hybride", "onsite": "Présentiel"}.get(wm, "Mode inconnu")
    geo_tip = f"Accessibilité géo ({wm_hint})"

    sal_min = row.get("salary_eur_min")
    sal_max = row.get("salary_eur_max")
    if sal_min or sal_max:
        sal_tip = f"Salaire: {int(sal_min or 0)/1000:.0f}–{int(sal_max or 0)/1000:.0f}k€/an"
    else:
        sal_tip = "Salaire non communiqué"

    dp = row.get("date_posted")
    fresh_tip = f"Publié le {dp}" if dp else "Date inconnue"

    parts = [
        _score_bar_html("Contenu",   "🧠", content,  "Pertinence rôle + qualité entreprise + description"),
        _score_bar_html("Géo",       "🗺",  geo,      geo_tip),
        _score_bar_html("Salaire",   "💶", salary,   sal_tip),
        _score_bar_html("Fraîcheur", "📅", freshness, fresh_tip),
    ]
    return f'<div class="score-breakdown">{"".join(parts)}</div>'


# ---------- Split view helpers ----------

def _score_badge_emoji(score) -> str:
    """Colored dot prefix for a score (consistent with score_color thresholds)."""
    if score is None or pd.isna(score):
        return "⚪"
    s = float(score)
    if s >= 8:
        return "🟢"
    if s >= 6:
        return "🔵"
    if s >= 4:
        return "🟠"
    return "🔴"


def _work_mode_icon(mode) -> str:
    return {"full_remote": "🏠", "hybrid": "🔀", "onsite": "🏢"}.get(mode, "")


def _render_list_row(row: pd.Series, is_selected: bool) -> None:
    """Compact clickable row for the left list panel (~56px tall)."""
    rid = int(row["id"])
    score = row.get("relevance_score")
    score_text = f"{float(score):.1f}" if (score is not None and not pd.isna(score)) else "—"
    badge = _score_badge_emoji(score)

    title = (str(row.get("title") or "(sans titre)"))[:58]
    company = (str(row.get("company") or ""))[:32] or "—"
    loc = (str(row.get("location") or ""))[:28] or ""
    wm_icon = _work_mode_icon(row.get("work_mode"))

    sal = fmt_salary(row.get("min_salary"), row.get("max_salary"), row.get("currency"))
    sal_part = f" · 💶 {sal}" if sal else ""

    _, is_new = fmt_date_pill(row.get("date_posted"))
    new_part = " 🆕" if is_new else ""

    # Multi-source flag
    sources = row.get("sources") or []
    multi = f" · ×{len(sources)}" if isinstance(sources, list) and len(sources) > 1 else ""

    # Pipeline marker
    status = row.get("application_status")
    pipeline_mark = " 📌" if status else ""

    # Streamlit buttons support newlines + basic emojis as labels
    label = (
        f"{badge} {score_text}  ·  {title}{new_part}{pipeline_mark}\n"
        f"{company} · 📍 {loc} {wm_icon}{sal_part}{multi}"
    )

    # Wrap in a div so our CSS can target it specifically
    css_class = "row-btn row-btn-selected" if is_selected else "row-btn"
    st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
    if st.button(label, key=f"rowsel_{rid}", use_container_width=True):
        st.session_state["selected_job_id"] = rid
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _render_detail_panel(row: pd.Series) -> None:
    """Detail panel on the right — header, scores, reasoning, description, actions."""
    rid = int(row["id"])
    score = row.get("relevance_score")
    color = score_color(score)
    score_text = f"{float(score):.1f}" if (score is not None and not pd.isna(score)) else "—"
    title = str(row.get("title") or "(sans titre)")
    company = str(row.get("company") or "—")
    loc = row.get("location") or ""
    platform = str(row.get("platform") or "")
    emoji = PLATFORM_EMOJI.get(platform, "•")

    # --- Header: big score + title + company ---
    st.markdown(
        f"""
        <div class="detail-header">
          <div class="detail-score" style="background:{color};">{score_text}</div>
          <div style="flex:1;min-width:0;">
            <div class="detail-title">{title}</div>
            <div class="detail-company">
              <strong>{company}</strong>
              {f" · 📍 {loc}" if loc else ""}
              &nbsp;·&nbsp; {emoji} {platform}
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Quick actions row (CTA + pipeline selector + archive) ---
    ca1, ca2, ca3 = st.columns([2, 2, 1])
    with ca1:
        url = row.get("job_url")
        if url:
            st.link_button("Voir l'offre ↗", url, use_container_width=True, type="primary")
    with ca2:
        current_status = row.get("application_status")
        current_idx = PIPELINE_KEYS.index(current_status) + 1 if current_status in PIPELINE_KEYS else 0
        # Options: "(non suivi)" + PIPELINE_KEYS
        opts = ["__none__"] + PIPELINE_KEYS
        labels = {"__none__": "— (non suivi)", **PIPELINE_LABEL}
        new_status = st.selectbox(
            "Pipeline",
            options=opts,
            index=current_idx,
            format_func=lambda k: labels.get(k, k),
            key=f"dp_status_{rid}",
            label_visibility="collapsed",
        )
        target = None if new_status == "__none__" else new_status
        if target != current_status:
            set_status(rid, target)
            st.toast(
                f"Offre → {labels.get(new_status, new_status)}" if target
                else "Offre retirée du pipeline",
                icon="✅",
            )
            st.rerun()
    with ca3:
        archived = bool(row.get("archived"))
        if st.button(
            "📦" if not archived else "↩️",
            key=f"dp_arch_{rid}",
            help="Archiver" if not archived else "Désarchiver",
            use_container_width=True,
        ):
            set_archived(rid, not archived)
            st.rerun()

    # --- Chips row: region, mode, lang, salary, date ---
    pills: list[str] = []
    region = row.get("region")
    geo = row.get("geo_profile")
    if region and isinstance(region, str):
        flag = PROFILE_BY_KEY.get(geo, {}).get("flag", "")
        pills.append(f'<span class="pill pill-region">{flag} {geo or region}</span>')

    wm = row.get("work_mode")
    wm_map = {
        "full_remote": ('<span class="pill pill-full-remote">🟢 Full remote</span>'),
        "hybrid":      ('<span class="pill pill-hybrid">🟡 Hybride</span>'),
        "onsite":      ('<span class="pill pill-onsite">🔴 Présentiel</span>'),
    }
    if wm in wm_map:
        pills.append(wm_map[wm])

    lang = row.get("language")
    if lang and lang != "fr":
        pills.append(f'<span class="pill pill-lang">{ {"en":"🇬🇧 EN","de":"🇩🇪 DE"}.get(lang, lang.upper()) }</span>')

    sal = fmt_salary(row.get("min_salary"), row.get("max_salary"), row.get("currency"))
    if sal:
        pills.append(f'<span class="pill pill-salary">💰 {sal}</span>')

    date_label, is_new = fmt_date_pill(row.get("date_posted"))
    if date_label != "—":
        pills.append(f'<span class="pill pill-date">🗓️ {date_label}</span>')
    if is_new:
        pills.append('<span class="pill pill-new">✨ NOUVEAU</span>')

    if pills:
        st.markdown(
            '<div style="margin-top:14px;">' + " ".join(pills) + "</div>",
            unsafe_allow_html=True,
        )

    # --- Score breakdown ---
    has_any = any(
        row.get(f) is not None and not (isinstance(row.get(f), float) and pd.isna(row.get(f)))
        for f in ("score_geo", "score_salary", "score_freshness", "base_score")
    )
    if has_any:
        st.markdown('<div class="detail-section-label">📊 Détail du score</div>', unsafe_allow_html=True)
        st.markdown(_score_breakdown_html(row), unsafe_allow_html=True)

    # --- Claude reasoning ---
    reasoning = row.get("relevance_reasoning")
    if reasoning and not pd.isna(reasoning) and str(reasoning).strip():
        st.markdown('<div class="detail-section-label">💡 Justification Claude</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="job-reason">{reasoning}</div>', unsafe_allow_html=True)

    # --- Multi-source ---
    sources = row.get("sources") or []
    if isinstance(sources, list) and len(sources) > 1:
        st.markdown('<div class="detail-section-label">🔗 Aussi visible sur</div>', unsafe_allow_html=True)
        src_pills = []
        seen = set()
        for s in sources:
            pf = s.get("platform") if isinstance(s, dict) else None
            if not pf or pf in seen:
                continue
            seen.add(pf)
            em = PLATFORM_EMOJI.get(pf, "•")
            url2 = s.get("url", "")
            if url2:
                src_pills.append(
                    f'<a href="{url2}" target="_blank" class="pill pill-source" '
                    f'style="text-decoration:none;">{em} {pf}</a>'
                )
            else:
                src_pills.append(f'<span class="pill pill-source">{em} {pf}</span>')
        st.markdown(" ".join(src_pills), unsafe_allow_html=True)

    # --- Full description (collapsible) ---
    desc = row.get("description")
    if desc and not pd.isna(desc) and str(desc).strip():
        st.markdown('<div class="detail-section-label">📄 Description complète</div>', unsafe_allow_html=True)
        # Escape HTML to avoid injection; keep line breaks
        import html as _html
        safe = _html.escape(str(desc)).replace("\n", "<br>")
        st.markdown(f'<div class="detail-description">{safe}</div>', unsafe_allow_html=True)

    # --- Notes ---
    st.markdown('<div class="detail-section-label">📝 Mes notes</div>', unsafe_allow_html=True)
    current_notes = row.get("notes") or ""
    new_notes = st.text_area(
        "Notes",
        value=current_notes,
        key=f"dp_notes_{rid}",
        label_visibility="collapsed",
        height=90,
        placeholder="Contacts, points clés, préparation entretien…",
    )
    if new_notes != current_notes:
        if st.button("💾 Enregistrer les notes", key=f"dp_notes_save_{rid}"):
            set_notes(rid, new_notes)
            st.toast("Notes sauvegardées", icon="✅")
            st.rerun()


# ---------- Tab 1: Jobs ----------

def render_card(row: pd.Series) -> None:
    score = row.get("relevance_score")
    color = score_color(score)
    score_text = f"{float(score):.1f}" if (score is not None and not pd.isna(score)) else "—"

    with st.container(border=True):
        col_score, col_main, col_cta = st.columns([1, 7, 2], vertical_alignment="center")

        with col_score:
            st.markdown(
                f"""
                <div class="score-badge" style="background:{color};">{score_text}</div>
                <div class="score-label">Pertinence</div>
                """,
                unsafe_allow_html=True,
            )

        with col_main:
            title = str(row.get("title") or "(sans titre)")
            company = str(row.get("company") or "—")
            st.markdown(
                f"""
                <div class="job-title">{title}</div>
                <div class="job-company"><strong>{company}</strong></div>
                """,
                unsafe_allow_html=True,
            )

            pills: list[str] = []

            # Region / profile badge first (geo context)
            region = row.get("region")
            geo = row.get("geo_profile")
            if region and isinstance(region, str):
                flag = PROFILE_BY_KEY.get(geo, {}).get("flag", "")
                label = f"{flag} {geo}" if geo else region
                pills.append(f'<span class="pill pill-region">{label}</span>')

            platform = str(row.get("platform") or "")
            if platform:
                emoji = PLATFORM_EMOJI.get(platform, "•")
                pills.append(f'<span class="pill pill-platform">{emoji} {platform}</span>')

            loc = row.get("location")
            if loc and not pd.isna(loc):
                pills.append(f'<span class="pill pill-location">📍 {loc}</span>')

            # Work mode pill — prefer the enriched value, fall back to is_remote flag.
            work_mode = row.get("work_mode")
            if work_mode == "full_remote":
                pills.append('<span class="pill pill-full-remote">🟢 Full remote</span>')
            elif work_mode == "hybrid":
                pills.append('<span class="pill pill-hybrid">🟡 Hybride</span>')
            elif work_mode == "onsite":
                pills.append('<span class="pill pill-onsite">🔴 Présentiel</span>')
            elif row.get("is_remote") is True:
                pills.append('<span class="pill pill-remote">🏠 Télétravail</span>')

            # Language pill — flag non-French offers.
            lang = row.get("language")
            if lang and lang != "fr":
                lang_labels = {"en": "🇬🇧 English", "de": "🇩🇪 Deutsch"}
                pills.append(
                    f'<span class="pill pill-lang">{lang_labels.get(lang, lang.upper())}</span>'
                )

            sal = fmt_salary(row.get("min_salary"), row.get("max_salary"), row.get("currency"))
            if sal:
                pills.append(f'<span class="pill pill-salary">💰 {sal}</span>')

            # EUR conversion pill (only shown when currency isn't already EUR).
            currency = str(row.get("currency") or "").upper()
            sal_eur_max = row.get("salary_eur_max")
            sal_eur_min = row.get("salary_eur_min")
            if currency and currency != "EUR" and (sal_eur_max or sal_eur_min):
                eur_label = fmt_salary(sal_eur_min, sal_eur_max, "€")
                if eur_label:
                    pills.append(f'<span class="pill pill-salary">≈ {eur_label}</span>')

            # Effective salary — purchasing-power-adjusted (only if coef != 1.0).
            sal_eff = row.get("salary_effective_eur")
            geo = row.get("geo_profile")
            if sal_eff and geo and geo in ("Suisse", "Luxembourg", "Belgique", "Canada (QC)"):
                eff_label = fmt_k(sal_eff)
                if eff_label:
                    pills.append(
                        f'<span class="pill pill-effective">🛒 ≈ {eff_label} € PPA</span>'
                    )

            date_label, is_new = fmt_date_pill(row.get("date_posted"))
            if date_label != "—":
                pills.append(f'<span class="pill pill-date">🗓️ {date_label}</span>')
            if is_new:
                pills.append('<span class="pill pill-new">✨ NOUVEAU</span>')

            if pills:
                st.markdown(" ".join(pills), unsafe_allow_html=True)

            # Multi-source row — only render if offer was seen on >1 platform
            sources = row.get("sources") or []
            if isinstance(sources, list) and len(sources) > 1:
                src_pills = ['<span class="sources-label">Aussi sur :</span>']
                seen = set()
                for s in sources:
                    pf = s.get("platform") if isinstance(s, dict) else None
                    if not pf or pf in seen:
                        continue
                    seen.add(pf)
                    em = PLATFORM_EMOJI.get(pf, "•")
                    src_pills.append(f'<span class="pill pill-source">{em} {pf}</span>')
                if len(src_pills) > 1:
                    st.markdown(
                        f'<div class="sources-row">{" ".join(src_pills)}</div>',
                        unsafe_allow_html=True,
                    )

            reasoning = row.get("relevance_reasoning")
            if reasoning and not pd.isna(reasoning) and str(reasoning).strip():
                st.markdown(
                    f'<div class="job-reason">💡 <strong>Claude :</strong> {reasoning}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="job-reason-empty">⏳ En attente du scoring Claude…</div>',
                    unsafe_allow_html=True,
                )

            # Score component breakdown (only shown when at least one component is available)
            has_any = any(
                row.get(f) is not None and not (
                    isinstance(row.get(f), float) and pd.isna(row.get(f))
                )
                for f in ("score_geo", "score_salary", "score_freshness", "base_score")
            )
            if has_any:
                st.markdown(_score_breakdown_html(row), unsafe_allow_html=True)

        with col_cta:
            url = row.get("job_url")
            if url:
                st.link_button("Voir l'offre ↗", url, use_container_width=True)
            job_id = row.get("id")
            current_status = row.get("application_status")
            if job_id is not None:
                if current_status:
                    st.caption(f"📌 {PIPELINE_LABEL.get(current_status, current_status)}")
                else:
                    if st.button("📌 Suivre", key=f"follow_{job_id}", use_container_width=True):
                        set_status(int(job_id), "to_study")
                        st.rerun()


with tab_jobs:
    try:
        payload = fetch_jobs(query_params)
    except requests.RequestException as e:
        st.error(f"Impossible de contacter le backend à {BACKEND_URL} : {e}")
        st.stop()

    total = payload["total"]
    items = payload["items"]

    if not items:
        st.info("Aucune offre ne correspond aux filtres. Lance un scrape depuis l'onglet **Actions**.")
    else:
        df = pd.DataFrame(items)

        scored_series = pd.to_numeric(df.get("relevance_score"), errors="coerce")
        avg_score = scored_series.mean() if scored_series.notna().any() else None

        salary_series = pd.concat(
            [pd.to_numeric(df.get("min_salary"), errors="coerce"),
             pd.to_numeric(df.get("max_salary"), errors="coerce")],
            axis=1,
        ).mean(axis=1)
        median_salary = salary_series.median() if salary_series.notna().any() else None

        remote_pct = (df.get("is_remote") == True).mean() * 100 if "is_remote" in df else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Offres", f"{total}")
        k2.metric("Score moyen", f"{avg_score:.1f} / 10" if avg_score is not None else "—")
        k3.metric("Salaire médian", f"{median_salary/1000:.0f}k €" if median_salary else "—")
        k4.metric("Télétravail", f"{remote_pct:.0f}%")

        st.markdown("")

        left_ctrl, mid_ctrl, right_ctrl = st.columns([3, 3, 2])
        with left_ctrl:
            view_mode = st.radio(
                "Affichage",
                ["Liste (Split)", "Cartes détaillées", "Tableau"],
                horizontal=True,
                label_visibility="collapsed",
                key="view_mode_radio",
            )
        with right_ctrl:
            page_size = st.selectbox(
                "Par page", [25, 50, 100, 200], index=1, label_visibility="collapsed",
            )

        total_pages = max(1, (len(df) + page_size - 1) // page_size)
        with mid_ctrl:
            page = st.number_input(
                "Page", min_value=1, max_value=total_pages, value=1,
                label_visibility="collapsed",
            )

        start = (page - 1) * page_size
        end = start + page_size
        page_df = df.iloc[start:end]

        st.caption(
            f"Affichage **{start+1}–{min(end, len(df))}** sur **{len(df)}** offres · "
            f"page {page}/{total_pages}"
        )

        if view_mode == "Liste (Split)":
            # --- Split view: left list + right detail panel ---
            # Maintain selection across reruns
            if "selected_job_id" not in st.session_state:
                st.session_state["selected_job_id"] = int(page_df.iloc[0]["id"])

            # If current selection isn't in the filtered page, fall back to first
            visible_ids = set(page_df["id"].astype(int).tolist())
            if st.session_state["selected_job_id"] not in visible_ids:
                st.session_state["selected_job_id"] = int(page_df.iloc[0]["id"])

            selected_id = st.session_state["selected_job_id"]
            # Find the selected row (across the full df in case it's on another page)
            selected_match = df[df["id"].astype(int) == selected_id]
            selected_row = selected_match.iloc[0] if not selected_match.empty else page_df.iloc[0]

            col_list, col_detail = st.columns([2, 3], gap="medium")
            with col_list:
                st.markdown('<div class="list-panel">', unsafe_allow_html=True)
                for _, row in page_df.iterrows():
                    _render_list_row(row, is_selected=(int(row["id"]) == selected_id))
                st.markdown("</div>", unsafe_allow_html=True)
            with col_detail:
                with st.container():
                    _render_detail_panel(selected_row)

        elif view_mode == "Cartes détaillées":
            for _, row in page_df.iterrows():
                render_card(row)
        else:
            display = pd.DataFrame({
                "Score": page_df["relevance_score"],
                "Titre": page_df["title"],
                "Entreprise": page_df["company"].fillna(""),
                "Profil": page_df.get("geo_profile", pd.Series([None] * len(page_df))).fillna(""),
                "Lieu": page_df["location"].fillna(""),
                "Remote": page_df["is_remote"].map({True: "✓", False: "—"}).fillna("—"),
                "Salaire": [
                    fmt_salary(lo, hi, cur) or "—"
                    for lo, hi, cur in zip(
                        page_df["min_salary"], page_df["max_salary"], page_df["currency"]
                    )
                ],
                "Plateforme": page_df["platform"],
                "Date": pd.to_datetime(page_df["date_posted"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "Lien": page_df["job_url"],
                "Justification": page_df["relevance_reasoning"].fillna("(non scoré)"),
            })

            st.dataframe(
                display,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Score": st.column_config.ProgressColumn(
                        "Score", format="%.1f", min_value=0, max_value=10,
                    ),
                    "Titre": st.column_config.TextColumn(width="large"),
                    "Justification": st.column_config.TextColumn(width="large"),
                    "Lien": st.column_config.LinkColumn("Lien", display_text="Voir ↗"),
                },
                height=min(800, 50 + 40 * len(display)),
            )

        st.divider()
        export_df = pd.DataFrame({
            "Score": df["relevance_score"],
            "Titre": df["title"],
            "Entreprise": df["company"].fillna(""),
            "Profil": df.get("geo_profile", pd.Series([None] * len(df))).fillna(""),
            "Lieu": df["location"].fillna(""),
            "Remote": df["is_remote"].map({True: "oui", False: "non"}).fillna(""),
            "Salaire min": df["min_salary"],
            "Salaire max": df["max_salary"],
            "Devise": df["currency"].fillna(""),
            "Plateforme": df["platform"],
            "Date": pd.to_datetime(df["date_posted"], errors="coerce").dt.strftime("%Y-%m-%d"),
            "Lien": df["job_url"],
            "Justification": df["relevance_reasoning"].fillna(""),
        })
        buf = io.StringIO()
        export_df.to_csv(buf, index=False)
        st.download_button(
            "📥 Exporter en CSV (toutes les offres filtrées)",
            data=buf.getvalue().encode("utf-8"),
            file_name=f"jobscout-{datetime.now().strftime('%Y%m%d-%H%M')}.csv",
            mime="text/csv",
        )


# ---------- Tab 2: Pipeline (Kanban) — P1 refonte ----------

def _pipeline_reminder(row: dict) -> Optional[tuple[str, str]]:
    """Return (css_class, label) for a temporal reminder, or None.

    Rules:
      - status=applied and no applied_date → '—'
      - status=applied and applied_date > 21 days → 'À relancer' (urgent)
      - status=applied and applied_date > 14 days → 'Relance possible' (warning)
      - status=applied and applied_date > 7 days → 'J+N' (info)
      - status=interview → 'Entretien' (info)
      - status=to_study since scraped > 14 days → 'Oublié ?' (warning)
    """
    status = row.get("application_status")
    if status == "applied":
        ad = row.get("applied_date")
        if ad:
            try:
                if isinstance(ad, str):
                    ad = datetime.fromisoformat(ad).date()
                days = (datetime.now().date() - ad).days
                if days >= 21:
                    return ("reminder-urgent", f"⏰ À relancer ({days}j)")
                if days >= 14:
                    return ("reminder-warning", f"📬 Relance possible (J+{days})")
                if days >= 7:
                    return ("reminder-info", f"📮 J+{days}")
                return ("reminder-ok", f"📮 J+{days}")
            except (ValueError, TypeError):
                pass
    elif status == "interview":
        return ("reminder-info", "🎤 En entretien")
    elif status == "to_study":
        sd = row.get("scraped_at")
        if sd:
            try:
                d = pd.to_datetime(sd, errors="coerce")
                if pd.notna(d):
                    days = (datetime.now() - d.to_pydatetime().replace(tzinfo=None)).days
                    if days >= 21:
                        return ("reminder-warning", f"💤 Dort depuis {days}j")
            except Exception:
                pass
    return None


def _render_kanban_compact_card(row: dict, is_selected: bool) -> None:
    """Compact clickable Kanban card — score + title + age dot + reminder."""
    rid = int(row["id"])
    score = row.get("relevance_score")
    score_text = f"{float(score):.1f}" if (score is not None and score != 0) else "—"
    badge = _score_badge_emoji(score)

    title_full = str(row.get("title") or "(sans titre)")
    title = title_full[:52]
    company = (str(row.get("company") or ""))[:28] or "—"

    # Button label (2 lines)
    label = f"{badge} {score_text}  {title}\n{company}"

    css_class = "kanban-card-btn kanban-card-selected" if is_selected else "kanban-card-btn"
    st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
    if st.button(
        label,
        key=f"kb_card_{rid}",
        use_container_width=True,
    ):
        st.session_state["pipeline_selected_id"] = rid
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # Reminder badge below the card (if applicable)
    rem = _pipeline_reminder(row)
    if rem:
        cls, txt = rem
        st.markdown(
            f'<div style="margin:-4px 0 8px 4px;"><span class="reminder-badge {cls}">{txt}</span></div>',
            unsafe_allow_html=True,
        )


def _render_pipeline_detail_panel(row: dict) -> None:
    """Detail panel shown below Kanban when a card is selected."""
    rid = int(row["id"])
    score = row.get("relevance_score")
    color = score_color(score)
    score_text = f"{float(score):.1f}" if (score is not None and score != 0) else "—"
    title = str(row.get("title") or "(sans titre)")
    company = str(row.get("company") or "—")
    loc = row.get("location") or ""
    platform = str(row.get("platform") or "")
    emoji = PLATFORM_EMOJI.get(platform, "•")

    # Header
    st.markdown(
        f"""
        <div class="detail-header">
          <div class="detail-score" style="background:{color};">{score_text}</div>
          <div style="flex:1;min-width:0;">
            <div class="detail-title">{title}</div>
            <div class="detail-company">
              <strong>{company}</strong>
              {f" · 📍 {loc}" if loc else ""}
              &nbsp;·&nbsp; {emoji} {platform}
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Action row: CTA + status selector + archive
    ca1, ca2, ca3 = st.columns([2, 2, 1])
    with ca1:
        if row.get("job_url"):
            st.link_button("Voir l'offre ↗", row["job_url"], use_container_width=True, type="primary")
    with ca2:
        current_status = row.get("application_status") or "to_study"
        current_idx = PIPELINE_KEYS.index(current_status) if current_status in PIPELINE_KEYS else 0
        opts = PIPELINE_KEYS + ["__remove__"]
        labels = {**PIPELINE_LABEL, "__remove__": "❌ Retirer du pipeline"}
        new_status = st.selectbox(
            "Statut",
            options=opts,
            index=current_idx,
            format_func=lambda k: labels.get(k, k),
            key=f"pdp_status_{rid}",
            label_visibility="collapsed",
        )
        if new_status != current_status:
            target = None if new_status == "__remove__" else new_status
            set_status(rid, target)
            st.rerun()
    with ca3:
        archived = bool(row.get("archived"))
        if st.button(
            "📦" if not archived else "↩️",
            key=f"pdp_arch_{rid}",
            help="Archiver" if not archived else "Désarchiver",
            use_container_width=True,
        ):
            set_archived(rid, not archived)
            st.rerun()

    # Reminder
    rem = _pipeline_reminder(row)
    if rem:
        cls, txt = rem
        st.markdown(
            f'<div style="margin:8px 0;"><span class="reminder-badge {cls}">{txt}</span></div>',
            unsafe_allow_html=True,
        )

    # Applied date display + edit
    if row.get("application_status") in ("applied", "interview", "closed"):
        applied = row.get("applied_date")
        if applied:
            st.caption(f"📮 Postulé le {applied}")

    # Claude reasoning
    reasoning = row.get("relevance_reasoning")
    if reasoning and str(reasoning).strip():
        st.markdown('<div class="detail-section-label">💡 Justification Claude</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="job-reason">{reasoning}</div>', unsafe_allow_html=True)

    # Notes
    st.markdown('<div class="detail-section-label">📝 Mes notes</div>', unsafe_allow_html=True)
    current_notes = row.get("notes") or ""
    new_notes = st.text_area(
        "Notes",
        value=current_notes,
        key=f"pdp_notes_{rid}",
        label_visibility="collapsed",
        height=110,
        placeholder="Contact recruteur, points clés, préparation entretien, questions à poser…",
    )
    if new_notes != current_notes:
        if st.button("💾 Enregistrer les notes", key=f"pdp_notes_save_{rid}"):
            set_notes(rid, new_notes)
            st.toast("Notes sauvegardées", icon="✅")
            st.rerun()

    # Description excerpt
    desc = row.get("description")
    if desc and str(desc).strip():
        with st.expander("📄 Description complète"):
            import html as _html
            safe = _html.escape(str(desc)).replace("\n", "<br>")
            st.markdown(f'<div class="detail-description">{safe}</div>', unsafe_allow_html=True)


def render_kanban_card(row: dict) -> None:
    """Compact card rendered inside a Kanban column."""
    job_id = row["id"]
    score = row.get("relevance_score")
    color = score_color(score)
    score_text = f"{float(score):.1f}" if (score is not None and score != 0) else "—"
    age_hex = age_color(row.get("date_posted"))

    with st.container(border=True):
        # Header: score badge + title + age dot
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <div style="background:{color};color:white;width:32px;height:32px;
                    border-radius:50%;display:flex;align-items:center;
                    justify-content:center;font-weight:700;font-size:0.85rem;flex-shrink:0;">
                    {score_text}
                </div>
                <div style="flex:1;min-width:0;">
                    <div style="font-weight:600;font-size:0.88rem;line-height:1.25;
                        overflow:hidden;text-overflow:ellipsis;display:-webkit-box;
                        -webkit-line-clamp:2;-webkit-box-orient:vertical;">
                        {row.get('title') or '(sans titre)'}
                    </div>
                </div>
                <div title="Ancienneté" style="width:10px;height:10px;border-radius:50%;
                    background:{age_hex};flex-shrink:0;"></div>
            </div>
            <div style="font-size:0.78rem;color:#6b7280;margin-bottom:6px;
                overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                🏢 {row.get('company') or '—'} · 📍 {row.get('location') or '—'}
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Status selector (drives transitions between columns)
        current_status = row.get("application_status") or "to_study"
        idx = PIPELINE_KEYS.index(current_status) if current_status in PIPELINE_KEYS else 0
        new_status = st.selectbox(
            "Déplacer",
            options=PIPELINE_KEYS + ["__remove__"],
            index=idx,
            format_func=lambda k: PIPELINE_LABEL.get(k, "❌ Retirer du pipeline"),
            key=f"kb_status_{job_id}",
            label_visibility="collapsed",
        )
        if new_status != current_status:
            target = None if new_status == "__remove__" else new_status
            set_status(job_id, target)
            st.rerun()

        # Quick actions row
        ac1, ac2 = st.columns([3, 1])
        with ac1:
            if row.get("job_url"):
                st.link_button("↗ Ouvrir", row["job_url"], use_container_width=True)
        with ac2:
            if st.button("📦", key=f"kb_arch_{job_id}", help="Archiver cette offre"):
                set_archived(job_id, True)
                st.rerun()

        # Applied date display
        if row.get("applied_date"):
            st.caption(f"📮 Postulé le {row['applied_date']}")

        # Notes expander
        with st.expander("📝 Notes", expanded=bool(row.get("notes"))):
            current_notes = row.get("notes") or ""
            new_notes = st.text_area(
                "Notes",
                value=current_notes,
                key=f"kb_notes_{job_id}",
                label_visibility="collapsed",
                height=80,
                placeholder="Contact, points clés, préparation entretien…",
            )
            if new_notes != current_notes:
                if st.button("💾 Enregistrer", key=f"kb_notes_save_{job_id}", use_container_width=True):
                    set_notes(job_id, new_notes)
                    st.toast("Notes sauvegardées", icon="✅")
                    st.rerun()


with tab_pipeline:
    st.subheader("Pipeline de candidature")

    # Top control row: filter + archived toggle
    pc1, pc2 = st.columns([3, 2])
    with pc1:
        pipe_search = st.text_input(
            "Rechercher dans le pipeline",
            placeholder="Titre, entreprise…",
            label_visibility="collapsed",
            key="pipe_search",
        )
    with pc2:
        show_archived = st.checkbox(
            "Afficher les archivées", value=False, key="show_archived",
        )

    try:
        pipe_payload = fetch_pipeline()
    except requests.RequestException as e:
        st.error(f"Impossible de charger le pipeline : {e}")
        st.stop()

    pipe_items = list(pipe_payload.get("items", []))

    if show_archived:
        try:
            arch_payload = api_get(
                "/jobs",
                params={"limit": 500, "include_archived": "true", "application_status": ["closed"]},
            )
            existing_ids = {it["id"] for it in pipe_items}
            for it in arch_payload.get("items", []):
                if it["id"] not in existing_ids:
                    pipe_items.append(it)
        except requests.RequestException:
            pass

    # Apply search filter
    if pipe_search:
        q = pipe_search.lower()
        pipe_items = [
            it for it in pipe_items
            if q in (it.get("title") or "").lower()
            or q in (it.get("company") or "").lower()
        ]

    if not pipe_items:
        st.info(
            "Pipeline vide — clique **📌 Suivre** sur une offre pour l'ajouter. "
            "Les offres commencent en « 🔍 À étudier »."
        )
    else:
        # ---- KPI row with reminders breakdown ----
        counts = {k: 0 for k, _ in PIPELINE_STATUSES}
        to_relaunch = 0   # applied > 21 days
        to_follow_up = 0  # applied 14-21 days
        forgotten = 0     # to_study > 21 days (scraped_at-based)
        for it in pipe_items:
            status = it.get("application_status")
            if status in counts:
                counts[status] += 1
            rem = _pipeline_reminder(it)
            if rem:
                cls = rem[0]
                if cls == "reminder-urgent":
                    to_relaunch += 1
                elif cls == "reminder-warning":
                    if "Relance" in rem[1]:
                        to_follow_up += 1
                    elif "Dort" in rem[1]:
                        forgotten += 1

        k_cols = st.columns(len(PIPELINE_STATUSES) + 1)
        for (status_key, label), col in zip(PIPELINE_STATUSES, k_cols[:-1]):
            col.metric(label, counts[status_key])
        with k_cols[-1]:
            st.metric(
                "⚠ Actions",
                to_relaunch + to_follow_up + forgotten,
                help=(
                    f"À relancer : {to_relaunch}\n"
                    f"Relance possible : {to_follow_up}\n"
                    f"Dort (non étudié) : {forgotten}"
                ),
            )

        # ---- Alert banners for pressing items ----
        if to_relaunch > 0:
            st.markdown(
                f'<div class="anomaly-card">⏰ <strong>{to_relaunch} offre(s) à relancer</strong> '
                f"— tu as postulé il y a plus de 21 jours sans progression.</div>",
                unsafe_allow_html=True,
            )
        if forgotten > 0:
            st.markdown(
                f'<div class="insight-card">💤 <strong>{forgotten} offre(s) dorment en « À étudier »</strong> '
                f"depuis plus de 21 jours — décide (postuler ou retirer).</div>",
                unsafe_allow_html=True,
            )

        st.divider()

        # ---- 5-column Kanban (compact cards) ----
        # Sort each column by reminder urgency then score desc
        def _sort_key(it: dict) -> tuple:
            rem = _pipeline_reminder(it)
            urgency = {
                "reminder-urgent": 0,
                "reminder-warning": 1,
                "reminder-info": 2,
                "reminder-ok": 3,
            }.get(rem[0] if rem else None, 4)
            return (urgency, -(it.get("relevance_score") or 0))

        selected_id = st.session_state.get("pipeline_selected_id")
        cols = st.columns(len(PIPELINE_STATUSES))
        for (status_key, label), col in zip(PIPELINE_STATUSES, cols):
            with col:
                col_items = [it for it in pipe_items if it.get("application_status") == status_key]
                col_items.sort(key=_sort_key)
                st.markdown(
                    f'<div class="kanban-col-header">'
                    f'<span>{label}</span>'
                    f'<span class="kanban-col-count">{len(col_items)}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                for it in col_items:
                    _render_kanban_compact_card(it, is_selected=(int(it["id"]) == selected_id))

        # ---- Detail panel (appears below Kanban when a card is selected) ----
        if selected_id is not None:
            matching = [it for it in pipe_items if int(it["id"]) == selected_id]
            if matching:
                st.divider()
                with st.container():
                    _render_pipeline_detail_panel(matching[0])


# ---------- Tab 3: Stats — S1 refonte (4 dashboards thématiques) ----------

def _dash_section(title: str, sub: str = "") -> None:
    """Visual section header for dashboards."""
    st.markdown(
        f'<div class="dash-section">'
        f'<div class="dash-section-title">{title}</div>'
        f'{f"<div class=dash-section-sub>{sub}</div>" if sub else ""}'
        f"</div>",
        unsafe_allow_html=True,
    )


with tab_stats:
    # Load data defensively — never call st.stop() so subsequent tabs still render
    stats: Optional[dict] = None
    df: pd.DataFrame = pd.DataFrame()
    try:
        stats = fetch_stats()
        payload = fetch_jobs({"limit": 1000})
        df = pd.DataFrame(payload.get("items", []))
    except Exception as e:
        st.error(f"Stats indisponibles : {type(e).__name__}: {e}")

    # ==== Top KPI strip ====
    if stats is not None:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total offres", stats.get("total_jobs", "—"))
        c2.metric("Notées", stats.get("scored", "—"))
        c3.metric("En attente", stats.get("unscored", "—"))
        c4.metric(
            "Dernier scrape",
            (datetime.fromisoformat(stats["last_scrape"]).strftime("%d/%m %H:%M")
             if stats.get("last_scrape") else "jamais"),
        )

    if df.empty:
        st.info("Aucune donnée à tracer.")
    else:
        score_s = pd.to_numeric(df.get("relevance_score"), errors="coerce")
        df["_score"] = score_s

        # ============================================================
        # 1. OÙ SONT LES BONNES OFFRES ?
        # ============================================================
        _dash_section(
            "🎯 Où sont les bonnes offres ?",
            "Distribution des scores, top entreprises, répartition géo.",
        )

        # Score histogram + region quality
        dash1_c1, dash1_c2 = st.columns(2)

        with dash1_c1:
            if score_s.notna().any():
                hist = px.histogram(
                    df.dropna(subset=["_score"]),
                    x="_score", nbins=20, range_x=[0, 10],
                    color_discrete_sequence=["#3b82f6"],
                    title="Distribution des scores de pertinence",
                )
                hist.update_layout(
                    xaxis_title="Score", yaxis_title="Nb offres",
                    bargap=0.08, height=320, margin=dict(l=10, r=10, t=50, b=10),
                )
                st.plotly_chart(hist, use_container_width=True)
            else:
                st.caption("Pas encore de scores disponibles.")

        with dash1_c2:
            # Avg score per region
            if "region" in df.columns and score_s.notna().any():
                reg_avg = (
                    df.dropna(subset=["_score"])
                    .groupby("region")["_score"]
                    .agg(["mean", "count"])
                    .reset_index()
                )
                reg_avg = reg_avg[reg_avg["count"] >= 3].sort_values("mean", ascending=True)
                if not reg_avg.empty:
                    fig_reg = px.bar(
                        reg_avg, y="region", x="mean", orientation="h",
                        color="mean", color_continuous_scale="RdYlGn",
                        range_color=[0, 10],
                        title="Score moyen par région",
                        text=reg_avg["mean"].round(1).astype(str) + " (" + reg_avg["count"].astype(str) + ")",
                    )
                    fig_reg.update_layout(
                        xaxis_title="Score moyen (0-10)", yaxis_title="",
                        coloraxis_showscale=False, height=320,
                        margin=dict(l=10, r=10, t=50, b=10),
                    )
                    st.plotly_chart(fig_reg, use_container_width=True)
                else:
                    st.caption("Pas assez de données par région (min. 3 offres).")

        # Top companies with high score
        if score_s.notna().any() and "company" in df.columns:
            hi = df.dropna(subset=["_score", "company"])
            hi = hi[hi["_score"] >= 7]
            if not hi.empty:
                top_co = (
                    hi.groupby("company")
                    .agg(offres=("id", "count"), score_moyen=("_score", "mean"))
                    .reset_index()
                )
                top_co = top_co[top_co["offres"] >= 2].sort_values(
                    ["score_moyen", "offres"], ascending=False
                ).head(15)
                if not top_co.empty:
                    top_co["score_moyen"] = top_co["score_moyen"].round(2)
                    st.markdown("**Top entreprises avec ≥ 2 offres et score ≥ 7**")
                    st.dataframe(
                        top_co.rename(columns={
                            "company": "Entreprise",
                            "offres": "Offres ≥ 7",
                            "score_moyen": "Score moyen",
                        }),
                        hide_index=True, use_container_width=True, height=260,
                        column_config={
                            "Score moyen": st.column_config.ProgressColumn(
                                "Score moyen", format="%.2f", min_value=0, max_value=10,
                            ),
                        },
                    )

        # ============================================================
        # 2. SALAIRES
        # ============================================================
        _dash_section(
            "💰 Salaires",
            "Par mode de travail, par région, et taux de transparence par plateforme.",
        )

        sal_df = df.dropna(subset=["salary_eur_min", "salary_eur_max"], how="all").copy()
        if not sal_df.empty:
            sal_df["_sal"] = sal_df[["salary_eur_min", "salary_eur_max"]].mean(axis=1)
            sal_df = sal_df[sal_df["_sal"] > 0]

        s_c1, s_c2 = st.columns(2)
        with s_c1:
            if not sal_df.empty and "work_mode" in sal_df.columns:
                wm_df = sal_df.dropna(subset=["work_mode"])
                if not wm_df.empty:
                    fig_wm = px.box(
                        wm_df, x="work_mode", y="_sal", color="work_mode",
                        category_orders={"work_mode": ["full_remote", "hybrid", "onsite"]},
                        title="Salaire (EUR) par mode de travail",
                        points=False,
                    )
                    fig_wm.update_layout(
                        yaxis_title="Salaire annuel €", xaxis_title="",
                        showlegend=False, height=320,
                        margin=dict(l=10, r=10, t=50, b=10),
                    )
                    st.plotly_chart(fig_wm, use_container_width=True)
            else:
                st.caption("Pas assez de salaires renseignés.")

        with s_c2:
            # % offers with salary per platform
            if "platform" in df.columns:
                trans = df.groupby("platform").apply(
                    lambda g: pd.Series({
                        "Total": len(g),
                        "Avec salaire": int(g[["min_salary", "max_salary"]].notna().any(axis=1).sum()),
                    })
                ).reset_index()
                trans["%"] = (trans["Avec salaire"] / trans["Total"] * 100).round(1)
                trans = trans.sort_values("%", ascending=True)
                fig_tp = px.bar(
                    trans, y="platform", x="%", orientation="h",
                    color="%", color_continuous_scale="Teal",
                    title="Transparence salariale (% offres avec salaire)",
                    text="%",
                )
                fig_tp.update_traces(texttemplate="%{x:.0f}%")
                fig_tp.update_layout(
                    xaxis_title="% offres", yaxis_title="",
                    coloraxis_showscale=False, height=320,
                    margin=dict(l=10, r=10, t=50, b=10),
                )
                st.plotly_chart(fig_tp, use_container_width=True)

        # ============================================================
        # 3. MA PROGRESSION (pipeline funnel)
        # ============================================================
        _dash_section(
            "📌 Ma progression — funnel pipeline",
            "Parcours du 'À étudier' à l'entretien. Les flèches montrent le taux de conversion.",
        )

        try:
            pipe_data = fetch_pipeline()
            pipe_list = pipe_data.get("items", [])
        except requests.RequestException:
            pipe_list = []

        if not pipe_list:
            st.caption(
                "Pipeline vide — ajoute des offres au pipeline depuis l'onglet Offres "
                "(bouton 📌 Suivre) pour voir la progression ici."
            )
        else:
            pipe_counts = {k: 0 for k, _ in PIPELINE_STATUSES}
            for it in pipe_list:
                s = it.get("application_status")
                if s in pipe_counts:
                    pipe_counts[s] += 1

            # Cumulative funnel: to_study includes downstream stages
            funnel_order = ["to_study", "interesting", "applied", "interview", "closed"]
            funnel_labels = [PIPELINE_LABEL[k] for k in funnel_order]
            cumulative = []
            for i, k in enumerate(funnel_order):
                cumulative.append(sum(pipe_counts[x] for x in funnel_order[i:]))

            fig_funnel = px.funnel(
                x=cumulative, y=funnel_labels,
                title="Funnel pipeline (offres ayant atteint chaque étape)",
            )
            fig_funnel.update_layout(
                height=300, margin=dict(l=10, r=10, t=50, b=10),
            )
            st.plotly_chart(fig_funnel, use_container_width=True)

            # Conversion rates
            if cumulative[0] > 0:
                conv = []
                for i in range(len(funnel_order) - 1):
                    if cumulative[i] > 0:
                        rate = cumulative[i + 1] / cumulative[i] * 100
                        conv.append((funnel_labels[i], funnel_labels[i + 1], rate))
                if conv:
                    conv_html = '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;">'
                    for src, dst, rate in conv:
                        rate_color = "#16a34a" if rate >= 50 else ("#f59e0b" if rate >= 25 else "#dc2626")
                        conv_html += (
                            f'<div style="padding:6px 12px;background:#f8fafc;border-radius:6px;'
                            f'border:1px solid #e2e8f0;font-size:0.85rem;">'
                            f'{src} → {dst}: <strong style="color:{rate_color};">{rate:.0f}%</strong></div>'
                        )
                    conv_html += "</div>"
                    st.markdown(conv_html, unsafe_allow_html=True)

        # ============================================================
        # 4. SANTÉ DU SCRAPING (aperçu — détail dans Logs)
        # ============================================================
        _dash_section(
            "🔍 Santé du scraping",
            "Offres/jour par plateforme (30 derniers jours). Détails complets dans l'onglet Logs.",
        )

        df["_date_posted"] = pd.to_datetime(df["date_posted"], errors="coerce")
        recent = df.dropna(subset=["_date_posted"]).copy()
        cutoff = datetime.now() - pd.Timedelta(days=30)
        recent = recent[recent["_date_posted"] >= cutoff]

        if recent.empty:
            st.caption("Pas de données récentes avec dates de publication.")
        else:
            timeline = (
                recent.groupby([recent["_date_posted"].dt.date, "platform"])
                .size().reset_index(name="count")
                .rename(columns={"_date_posted": "Date"})
            )
            fig_tl = px.area(
                timeline, x="Date", y="count", color="platform",
                title="Offres publiées par jour (30j)",
            )
            fig_tl.update_layout(
                height=280, margin=dict(l=10, r=10, t=50, b=10),
                yaxis_title="Nb offres", xaxis_title="",
            )
            st.plotly_chart(fig_tl, use_container_width=True)

        # ============================================================
        # Insights / Anomalies auto
        # ============================================================
        insights: list[str] = []
        anomalies: list[str] = []

        # Insight: % full remote
        if "work_mode" in df.columns:
            fr_pct = (df["work_mode"] == "full_remote").mean() * 100
            if fr_pct > 20:
                insights.append(
                    f"💡 <strong>{fr_pct:.0f}% des offres</strong> sont en full remote — "
                    f"excellente couverture géographique."
                )

        # Anomaly: salary missing rate
        missing_sal = df[["min_salary", "max_salary"]].isna().all(axis=1).mean() * 100
        if missing_sal > 70:
            anomalies.append(
                f"⚠️ <strong>{missing_sal:.0f}% des offres n'ont pas de salaire</strong> — "
                f"Claude ne peut pas scorer cet axe (pénalité neutre 4/10)."
            )

        # Anomaly: scoring coverage
        if stats is not None and stats.get("unscored", 0) > 50:
            anomalies.append(
                f"⚠️ <strong>{stats['unscored']} offres non notées</strong> — "
                f"lance un POST /rescore pour les traiter."
            )

        if insights or anomalies:
            _dash_section("🔬 Insights & anomalies détectées")
            for msg in insights:
                st.markdown(f'<div class="insight-card">{msg}</div>', unsafe_allow_html=True)
            for msg in anomalies:
                st.markdown(f'<div class="anomaly-card">{msg}</div>', unsafe_allow_html=True)


# ---------- Tab 3: Actions ----------

DEFAULT_TERMS_HINT = (
    "Laisse vide pour utiliser la liste complète par défaut (48 termes cyber/réseau/leadership)."
)

with tab_actions:
    st.subheader("Déclencher un scrape")
    st.caption(
        "Les termes par défaut couvrent les profils cyber, réseau, architecture et leadership IT. "
        "Choisis un profil géographique (ou « Tous » pour scraper toutes les zones séquentiellement)."
    )

    with st.form("scrape_form"):
        profile_options = ["Tous les profils"] + PROFILE_KEYS
        profile_labels = {k: f"{PROFILE_BY_KEY[k]['flag']} {k}" for k in PROFILE_KEYS}
        profile_labels["Tous les profils"] = "🌍 Tous les profils"

        col_a, col_b = st.columns([2, 1])
        with col_a:
            selected_profile = st.selectbox(
                "Profil géographique",
                options=profile_options,
                format_func=lambda k: profile_labels.get(k, k),
            )
        with col_b:
            hours_old = st.number_input("Ancienneté max (h)", 1, 720, 168)

        col_c, col_d = st.columns(2)
        results_per_term = col_c.number_input("Résultats / terme", 1, 100, 20)
        sites = col_d.multiselect(
            "Plateformes",
            options=["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"],
            default=["linkedin", "indeed", "glassdoor"],
        )

        terms_override = st.text_area(
            "Termes personnalisés (optionnel, un par ligne)",
            value="",
            height=120,
            placeholder=DEFAULT_TERMS_HINT,
            help=DEFAULT_TERMS_HINT,
        )

        submitted = st.form_submit_button("🚀 Lancer le scrape")

        if submitted:
            terms = [t.strip() for t in terms_override.splitlines() if t.strip()] or None
            profiles_to_run = PROFILE_KEYS if selected_profile == "Tous les profils" else [selected_profile]

            total_scraped = total_new = total_dup = total_merged = 0
            run_errors: list[str] = []

            progress = st.progress(0.0, text=f"Démarrage — {len(profiles_to_run)} profil(s) à scraper")
            for i, prof in enumerate(profiles_to_run, start=1):
                progress.progress(
                    (i - 0.5) / len(profiles_to_run),
                    text=f"Scraping {PROFILE_BY_KEY[prof]['flag']} {prof} ({i}/{len(profiles_to_run)})…",
                )
                try:
                    body = {
                        "profile": prof,
                        "sites": sites,
                        "results_per_term": int(results_per_term),
                        "hours_old": int(hours_old),
                        "score_new_jobs": True,
                    }
                    if terms:
                        body["search_terms"] = terms
                    result = api_post("/search", json=body)
                    total_scraped += result["scraped"]
                    total_new += result["new"]
                    total_dup += result["duplicates"]
                    total_merged += result.get("merged_sources", 0)
                    if result.get("errors"):
                        run_errors.extend([f"[{prof}] {e}" for e in result["errors"]])
                except requests.RequestException as e:
                    run_errors.append(f"[{prof}] {e}")
                progress.progress(i / len(profiles_to_run))

            progress.empty()
            st.success(
                f"✅ {total_scraped} offres vues · {total_new} nouvelles · "
                f"{total_dup} doublons URL · {total_merged} sources fusionnées. "
                f"Scoring Claude lancé en arrière-plan."
            )
            if run_errors:
                with st.expander(f"⚠️ {len(run_errors)} erreur(s) non bloquante(s)"):
                    for err in run_errors[:30]:
                        st.text(err)
            clear_caches()

    st.divider()
    st.subheader("Scoring Claude")
    try:
        _stats = fetch_stats()
        unscored = _stats.get("unscored", 0)
    except requests.RequestException:
        unscored = "?"
    st.caption(f"Il reste **{unscored}** offres à noter.")

    if st.button("🎯 Noter toutes les offres en attente"):
        try:
            resp = api_post("/rescore")
            st.success(f"✅ {resp['pending']} offres passent en file de scoring (tâche de fond).")
            clear_caches()
        except requests.RequestException as e:
            st.error(f"Échec du rescore : {e}")

    st.divider()
    st.subheader("Notifications Telegram")
    st.caption(
        "Si `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` sont configurés dans `.env`, "
        "une notif est envoyée à chaque scrape avec les offres qui dépassent le score "
        "`TELEGRAM_MIN_SCORE` (défaut 7/10)."
    )
    if st.button("📲 Tester l'envoi Telegram"):
        try:
            resp = api_post("/telegram/test")
            if resp.get("sent"):
                st.success(f"✅ Message envoyé — seuil configuré : {resp['min_score']}/10")
            else:
                st.warning(f"⚠️ Tentative effectuée mais échec d'envoi. Vérifie bot token / chat id.")
        except requests.HTTPError as e:
            if e.response.status_code == 400:
                st.error("❌ Telegram n'est pas configuré. Ajoute TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID dans le `.env` et redémarre le backend.")
            else:
                st.error(f"Erreur API : {e}")
        except requests.RequestException as e:
            st.error(f"Erreur réseau : {e}")

    st.divider()
    st.subheader("Santé du backend")
    try:
        h = api_get("/health")
        st.json(h)
    except requests.RequestException as e:
        st.error(f"Backend injoignable : {e}")


# ---------- Tab 4: Logs ----------

_STATUS_EMOJI = {"running": "⏳", "success": "✅", "failed": "❌"}

with tab_logs:
    all_logs: list = []
    try:
        all_logs = fetch_logs({"limit": 100})["items"]
    except Exception as e:
        st.error(f"Impossible de charger les logs : {type(e).__name__}: {e}")

    if not all_logs:
        st.info("Aucun scrape enregistré pour l'instant. Lance un scrape depuis l'onglet Actions.")

    # ============================================================
    # 1. HEALTH GAUGE — last 3 runs + overall success rate (7d)
    # ============================================================
    now = datetime.now()
    last3 = all_logs[:3]
    last_run = all_logs[0]
    last_started = datetime.fromisoformat(last_run["started_at"])

    # Success rate over last 7 days
    week_cutoff = now - pd.Timedelta(days=7)
    week_runs = [
        l for l in all_logs
        if datetime.fromisoformat(l["started_at"]) >= week_cutoff
        and l["status"] != "running"
    ]
    if week_runs:
        week_ok = sum(1 for l in week_runs if l["status"] == "success")
        week_rate = week_ok / len(week_runs) * 100
    else:
        week_rate = None

    # Determine health level
    last_ok = last_run["status"] == "success"
    fatal_count = sum(1 for l in last3 if l.get("fatal_error"))
    if not last_ok or fatal_count >= 2:
        health_class, icon, title = "health-error", "🔴", "Scraping en échec"
        subtitle = f"Dernier run : {last_run['status']} — vérifier les erreurs ci-dessous."
    elif week_rate is not None and week_rate < 80:
        health_class, icon, title = "health-warn", "🟡", "Scraping instable"
        subtitle = f"Taux de succès 7j : {week_rate:.0f}% (seuil sain : 80%)."
    else:
        health_class, icon, title = "health-ok", "🟢", "Scraping en bonne santé"
        if week_rate is not None:
            subtitle = f"Taux de succès 7j : {week_rate:.0f}% · dernier run : {last_started.strftime('%d/%m %H:%M')}"
        else:
            subtitle = f"Dernier run : {last_started.strftime('%d/%m %H:%M')}"

    st.markdown(
        f"""
        <div class="health-gauge {health_class}">
            <div class="health-icon">{icon}</div>
            <div style="flex:1;">
                <div class="health-title">{title}</div>
                <div class="health-subtitle">{subtitle}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Last 3 runs summary
    r1, r2, r3 = st.columns(3)
    for col, run in zip([r1, r2, r3], last3 + [None] * (3 - len(last3))):
        with col:
            if run is None:
                st.metric("—", "—")
                continue
            st.metric(
                label=f"{_STATUS_EMOJI.get(run['status'], '?')} "
                      f"{datetime.fromisoformat(run['started_at']).strftime('%d/%m %H:%M')}",
                value=f"{run['new_jobs']} nouveaux",
                delta=f"{run['scraped']} scraped · {len(run.get('errors') or [])} err",
                delta_color="off",
            )

    # ============================================================
    # 2. TIMELINE 30 last days (dots)
    # ============================================================
    _dash_section("📅 Timeline 30 derniers jours")

    month_cutoff = now - pd.Timedelta(days=30)
    month_runs = [
        l for l in all_logs
        if datetime.fromisoformat(l["started_at"]) >= month_cutoff
    ]
    month_runs.reverse()  # chronological left→right

    if not month_runs:
        st.caption("Aucun scrape dans les 30 derniers jours.")
    else:
        dots_html = ""
        for run in month_runs:
            dt = datetime.fromisoformat(run["started_at"])
            status = run["status"]
            err_count = len(run.get("errors") or [])
            if status == "success" and err_count == 0:
                color = "#16a34a"
            elif status == "success":
                color = "#f59e0b"
            elif status == "failed":
                color = "#dc2626"
            else:
                color = "#94a3b8"
            tip = (
                f"{dt.strftime('%d/%m %H:%M')} · {status} · "
                f"{run['new_jobs']} new, {err_count} err"
            )
            dots_html += (
                f'<span class="timeline-dot" '
                f'style="background:{color};" title="{tip}"></span>'
            )
        st.markdown(
            f'<div style="padding:10px;background:#f8fafc;border-radius:8px;'
            f'border:1px solid #e2e8f0;">{dots_html}</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"{len(month_runs)} run(s) · 🟢 succès sans erreur · 🟠 succès avec erreurs · "
            f"🔴 échec · ⚪ en cours. Survol d'un point pour voir le détail."
        )

    # ============================================================
    # 3. HEALTH PER CONNECTOR
    # ============================================================
    _dash_section(
        "🔌 Santé par connecteur / plateforme",
        "Analyse des erreurs des 20 derniers runs pour identifier les sources défaillantes.",
    )

    # Parse errors to classify per connector
    import re as _re
    connector_stats: dict[str, dict] = {}
    # Pattern typical: "[JobSpy/search_term] Error" or "[connector_name/term] Error" or "[connector_name] Error"
    err_re = _re.compile(r"^\[([^/\]]+)(?:/[^\]]+)?\]", _re.IGNORECASE)

    # Normalize connector name
    def _norm(n: str) -> str:
        n = n.strip().lower()
        if n.startswith("jobspy"):
            # JobSpy is an umbrella — but logs may have separate entries
            return "jobspy"
        return n

    recent_runs = all_logs[:20]
    known_connectors = [
        "jobspy", "linkedin", "indeed", "glassdoor", "zip_recruiter", "google",
        "remotive", "francetravail", "freework", "himalayas",
        "greenhouse", "workday", "apec",
    ]
    for run in recent_runs:
        for err in run.get("errors") or []:
            m = err_re.match(err)
            if m:
                key = _norm(m.group(1))
                st_bucket = connector_stats.setdefault(key, {"errors": 0, "runs_with_err": set()})
                st_bucket["errors"] += 1
                st_bucket["runs_with_err"].add(run["id"])

    # Build per-connector summary (include 0-error ones too for context)
    # Approximate jobs-per-run from latest run's scraped count
    platform_jobs: dict[str, int] = {}
    for run in recent_runs[:5]:  # avg over last 5
        # We can't split by platform from ScrapeLog directly, so skip this detail.
        pass

    summary_rows = []
    for conn in known_connectors:
        err_info = connector_stats.get(conn)
        err_count = err_info["errors"] if err_info else 0
        affected_runs = len(err_info["runs_with_err"]) if err_info else 0
        if err_count == 0:
            dot_color = "#16a34a"
            status_label = "OK"
        elif affected_runs >= 5:
            dot_color = "#dc2626"
            status_label = f"{err_count} erreurs sur {affected_runs} runs"
        elif affected_runs >= 2:
            dot_color = "#f59e0b"
            status_label = f"{err_count} erreurs sur {affected_runs} runs"
        else:
            dot_color = "#f59e0b"
            status_label = f"{err_count} erreur{'s' if err_count>1 else ''} sporadique"

        # Don't show connectors with 0 errors and no mention if not in known list
        # (We always show known connectors)
        summary_rows.append({
            "conn": conn,
            "dot_color": dot_color,
            "status_label": status_label,
            "err_count": err_count,
            "emoji": PLATFORM_EMOJI.get(conn, "•") if conn != "jobspy" else "🕷️",
        })

    # Sort: errored first (desc), then OK
    summary_rows.sort(key=lambda r: (-r["err_count"], r["conn"]))

    cols_per_row = 3
    for i in range(0, len(summary_rows), cols_per_row):
        row_cols = st.columns(cols_per_row)
        for col, entry in zip(row_cols, summary_rows[i:i + cols_per_row]):
            with col:
                st.markdown(
                    f'<div class="connector-row">'
                    f'<span><span class="connector-status" style="background:{entry["dot_color"]};"></span>'
                    f'{entry["emoji"]} <strong>{entry["conn"]}</strong></span>'
                    f'<span style="color:#64748b;font-size:0.78rem;">{entry["status_label"]}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ============================================================
    # 4. RUN TABLE + ERRORS GROUPED BY CONNECTOR
    # ============================================================
    _dash_section(
        "📋 Détail des runs",
        "Filtre sur le statut et ouvre les runs avec erreurs pour inspecter la cause.",
    )

    f1, f2 = st.columns([2, 1])
    with f1:
        status_filter = st.selectbox(
            "Statut",
            ["Tous", "success", "failed", "running"],
            format_func=lambda s: {
                "Tous": "Tous", "success": "✅ Succès",
                "failed": "❌ Échec", "running": "⏳ En cours",
            }.get(s, s),
            key="logs_status_filter",
        )
    with f2:
        log_limit = st.number_input("Nb max affiché", 10, 500, 50, key="logs_limit")

    filtered_logs = all_logs[: int(log_limit)]
    if status_filter != "Tous":
        filtered_logs = [l for l in filtered_logs if l["status"] == status_filter]

    if not filtered_logs:
        st.caption("Aucun run ne correspond.")
    else:
        table = []
        for l in filtered_logs:
            started = datetime.fromisoformat(l["started_at"]).strftime("%d/%m %H:%M:%S")
            duration = ""
            if l.get("ended_at"):
                d = datetime.fromisoformat(l["ended_at"]) - datetime.fromisoformat(l["started_at"])
                duration = f"{d.total_seconds():.0f}s"
            prof = l.get("profile") or "—"
            flag = PROFILE_BY_KEY.get(prof, {}).get("flag", "")
            table.append({
                "": _STATUS_EMOJI.get(l["status"], "?"),
                "Démarré": started,
                "Durée": duration,
                "Profil": f"{flag} {prof}",
                "Déclencheur": l["triggered_by"],
                "Vus": l["scraped"],
                "Nouveaux": l["new_jobs"],
                "Doublons": l["duplicates"],
                "Fusionnés": l["merged_sources"],
                "# erreurs": len(l.get("errors") or []),
            })
        st.dataframe(
            pd.DataFrame(table), hide_index=True, use_container_width=True,
            height=min(400, 40 + 35 * len(table)),
        )

        # Errors grouped by connector for each run with issues
        runs_with_errors = [l for l in filtered_logs if l.get("errors") or l.get("fatal_error")]
        if runs_with_errors:
            st.markdown("**Erreurs regroupées par connecteur**")
            for l in runs_with_errors:
                started = datetime.fromisoformat(l["started_at"]).strftime("%d/%m %H:%M:%S")
                flag = PROFILE_BY_KEY.get(l.get("profile") or "", {}).get("flag", "")
                err_list = l.get("errors") or []
                n = len(err_list) + (1 if l.get("fatal_error") else 0)
                label = (
                    f"{_STATUS_EMOJI.get(l['status'], '?')} {started} — "
                    f"{flag} {l.get('profile') or '—'} · {n} erreur(s)"
                )
                with st.expander(label):
                    if l.get("fatal_error"):
                        st.error(f"Erreur fatale : {l['fatal_error']}")

                    # Group by connector prefix
                    grouped: dict[str, list[str]] = {}
                    for err in err_list:
                        m = err_re.match(err)
                        key = _norm(m.group(1)) if m else "autres"
                        grouped.setdefault(key, []).append(err)

                    for conn, errs in sorted(grouped.items(), key=lambda x: -len(x[1])):
                        emoji = PLATFORM_EMOJI.get(conn, "•") if conn != "jobspy" else "🕷️"
                        st.markdown(f"**{emoji} {conn}** — {len(errs)} erreur(s)")
                        for err in errs[:10]:
                            st.text(err)
                        if len(errs) > 10:
                            st.caption(f"… + {len(errs) - 10} autres")
