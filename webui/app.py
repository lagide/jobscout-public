"""JobScout webui — frontend HTMX + Jinja2 + ECharts + anime.js (port 8502).

Remplaçant progressif de Streamlit (qui reste sur 8501) : même API backend,
rendu serveur, fragments HTMX pour l'interactivité, animations côté client.
Aucune écriture directe en DB — tout passe par l'API FastAPI du backend.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "180"))

app = FastAPI(title="JobScout webui", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ============================================================================
# Contrôle d'accès — read-only par DÉFAUT, écritures sur jeton secret
# ============================================================================
# Modèle « default-deny » robuste, indépendant de la topologie réseau :
#   - le webui est en lecture seule SAUF si la requête porte l'en-tête
#     `X-JobScout-Admin` égal à JOBSCOUT_ADMIN_TOKEN ;
#   - SEUL Traefik injecte ce jeton, et UNIQUEMENT sur le domaine admin après
#     authentification Authelia 2FA (il le retire sur le domaine public et de
#     toute requête cliente) → le jeton n'est jamais visible du client ;
#   - un conteneur du VPS qui taperait le tunnel en direct n'a pas le jeton
#     → read-only. Une écriture forgée échoue.
# En read-only : écritures → 403 ; pages privées (pipeline, système) → cockpit ;
# les templates masquent actions, notes perso et nav privée.
#
# JOBSCOUT_ADMIN_TOKEN absent (dev local) → plein accès (comportement legacy).
# JOBSCOUT_READONLY=1 force le mode public en local (tests de la vue publique).

_ADMIN_TOKEN = os.getenv("JOBSCOUT_ADMIN_TOKEN", "").strip()
_FORCE_READONLY = os.getenv("JOBSCOUT_READONLY", "").strip() in ("1", "true", "yes")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_ACTION_PREFIXES = ("/actions", "/ops", "/pipeline/move")
_PRIVATE_PREFIXES = ("/pipeline", "/systeme", "/parametres")  # masquées en public


def _is_readonly(request: Request) -> bool:
    if _FORCE_READONLY:
        return True
    if not _ADMIN_TOKEN:
        return False  # jeton non configuré (dev local) → plein accès
    supplied = request.headers.get("X-JobScout-Admin", "")
    return not secrets.compare_digest(supplied, _ADMIN_TOKEN)


@app.middleware("http")
async def readonly_guard(request: Request, call_next):
    readonly = _is_readonly(request)
    request.state.readonly = readonly
    if readonly:
        path = request.url.path
        if request.method in _WRITE_METHODS or path.startswith(_ACTION_PREFIXES):
            return HTMLResponse(
                '<div class="alert alert--err">action désactivée — version publique '
                'en lecture seule</div>',
                status_code=403,
            )
        if path.startswith(_PRIVATE_PREFIXES):
            return RedirectResponse("/cockpit", status_code=302)
    return await call_next(request)


# ============================================================================
# Client API backend
# ============================================================================

_client = httpx.AsyncClient(base_url=BACKEND_URL, timeout=REQUEST_TIMEOUT)


async def api_get(path: str, params: Optional[dict] = None) -> Any:
    r = await _client.get(path, params=params)
    r.raise_for_status()
    return r.json()


async def api_post(path: str, json: Optional[dict] = None,
                   params: Optional[dict] = None) -> Any:
    r = await _client.post(path, json=json, params=params)
    r.raise_for_status()
    return r.json()


async def api_put(path: str, json: Optional[dict] = None) -> Any:
    r = await _client.put(path, json=json)
    r.raise_for_status()
    return r.json()


def _api_error_detail(e: Exception) -> str:
    """Message d'erreur lisible — remonte le `detail` FastAPI (422) si présent."""
    if isinstance(e, httpx.HTTPStatusError):
        try:
            return str(e.response.json().get("detail", e))
        except Exception:
            return str(e)
    return str(e)


# ============================================================================
# Familles cibles (portées de frontend/jobscout_refonte.py — même logique)
# ============================================================================

FAMILIES = {
    "tam":   dict(label="TAM", full="Technical Account Manager", hue="var(--cyan)", order=0),
    "tl":    dict(label="TEAM-LEAD SÉCU", full="Team-Lead Sécurité Réseaux", hue="var(--green-dim)", order=1),
    "rsi":   dict(label="RESPONSABLE SI", full="Responsable SI", hue="var(--orange)", order=2),
    "sup":   dict(label="MANAGER SUPPORT", full="Manager Support Informatique", hue="var(--yellow)", order=3),
    "sdm":   dict(label="SDM / DELIVERY", full="Service Delivery Manager", hue="var(--green)", order=4),
    "autre": dict(label="AUTRES", full="Non classé", hue="var(--dim)", order=5),
}

_FAM_RULES = [
    ("tam", r"\b(technical account manager|responsable technique de comptes|"
            r"responsable technique partenaires|partner technical account|"
            r"technical partner manager|\btam\b)\b"),
    ("tl",  r"(team[ -]?lead(er)?|\blead\b|responsable d.[eé]quipe|chef d.[eé]quipe|"
            r"responsable).{0,30}(s[eé]curit[eé].{0,14}(r[eé]seau|inf|si\b)|"
            r"r[eé]seau.{0,14}s[eé]curit[eé]|network security|cybers[eé]curit|\bsoc\b)|"
            r"network security (team )?lead|team leader cybers[eé]curit"),
    ("rsi", r"\b(responsable s\.?i\b|responsable (des |du )?syst[eè]mes? d.information|"
            r"responsable informatique|responsable infrastructure|"
            r"information systems manager|\bit manager\b)\b"),
    # Famille "SDM / Delivery" (ajoutée 2026-06-25) : pilotage de la livraison de
    # services IT. Placée avant "sup" pour capturer "service delivery" avant "service desk".
    ("sdm", r"\b(service delivery manager|delivery manager|service delivery lead|"
            r"responsable (de la )?(livraison|delivery)|\bsdm\b)\b"),
    # Famille "Manager Support" (remplace l'ancienne "Directeur Technique" le 2026-06-22) :
    # encadrement/coordination du support IT, pas technicien N1-N2 (déjà blacklisté au scrape).
    ("sup", r"(manager|responsable|chef|lead|head|coordinateur|coordinator|pilote|p[oô]le|directeur)"
            r".{0,30}(support|service[\s-]?desk|help[\s-]?desk|centre de services?)"
            r"|(support|service[\s-]?desk|help[\s-]?desk).{0,20}(manager|responsable|lead)"
            r"|it support manager|service desk manager|head of (it )?support"),
]
_FAM_COMPILED = [(k, re.compile(p, re.IGNORECASE)) for k, p in _FAM_RULES]


def detect_family(title: Optional[str]) -> str:
    t = (title or "").lower()
    for key, rx in _FAM_COMPILED:
        if rx.search(t):
            return key
    return "autre"


# ============================================================================
# Filtres Jinja (formatage)
# ============================================================================

def fmt_score(s: Any) -> str:
    try:
        f = float(s)
    except (TypeError, ValueError):
        return "—"
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def score_class(s: Any) -> str:
    try:
        f = float(s)
    except (TypeError, ValueError):
        return ""
    if f >= 8:
        return "s-top"
    if f >= 7:
        return "s-hot"
    if f >= 6:
        return "s-mid"
    return ""


def fmt_age(val: Any) -> str:
    """Âge d'une offre. Accepte une date/string ISO, ou un dict d'offre.

    Quand on passe un dict sans `date_posted` (connecteurs linkedin / cadremploi
    / hellowork / freework qui ne la fournissent pas), repli sur `scraped_at`
    (date de découverte) préfixé par `~` pour signaler que c'est une date
    approximative — évite le « — » sur les offres mises en avant.
    """
    approx = False
    if isinstance(val, dict):
        date_str = val.get("date_posted")
        if not date_str:
            date_str = val.get("scraped_at")
            approx = True
    else:
        date_str = val
    if not date_str:
        return "—"
    try:
        d = datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).date()
        days = (datetime.now(timezone.utc).date() - d).days
        if days < 0:
            s = d.strftime("%d/%m")
        elif days == 0:
            s = "AUJ"
        else:
            s = f"{days}j"
        return f"~{s}" if approx else s
    except (ValueError, AttributeError):
        return "—"


def fmt_salary(j: dict) -> str:
    def k(v):
        try:
            v = float(v)
            return f"{v / 1000:.0f}k" if v >= 1000 else f"{v:.0f}"
        except (TypeError, ValueError):
            return None
    a, b = k(j.get("min_salary")), k(j.get("max_salary"))
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get((j.get("currency") or "").upper(),
                                                   j.get("currency") or "")
    if a and b and a != b:
        return f"{a}–{b} {sym}".strip()
    if a or b:
        return f"{a or b} {sym}".strip()
    return ""


templates.env.filters["fmt_score"] = fmt_score
templates.env.filters["score_class"] = score_class
templates.env.filters["fmt_age"] = fmt_age
templates.env.filters["fmt_salary"] = fmt_salary
templates.env.globals["FAMILIES"] = FAMILIES
templates.env.globals["detect_family"] = detect_family

WORK_MODE_LABEL = {"full_remote": "REMOTE", "hybrid": "HYBRID", "onsite": "ONSITE"}
templates.env.globals["WORK_MODE_LABEL"] = WORK_MODE_LABEL

STATUS = {
    "to_study":    dict(label="à étudier", glyph="·"),
    "interesting": dict(label="intéressante", glyph="★"),
    "applied":     dict(label="postulée", glyph="↗"),
    "interview":   dict(label="entretien", glyph="◆"),
    "in_process":  dict(label="processus en cours", glyph="»"),
    "closed":      dict(label="fermée", glyph="×"),
}
KANBAN_ORDER = ["to_study", "interesting", "applied", "interview", "in_process", "closed"]
templates.env.globals["STATUS"] = STATUS
templates.env.globals["KANBAN_ORDER"] = KANBAN_ORDER

PLATFORMS = ["linkedin", "indeed", "apec", "hellowork", "cadremploi",
             "wttj", "francetravail", "freework", "choisirservicepublic"]
templates.env.globals["PLATFORMS"] = PLATFORMS


async def base_ctx(request: Request) -> dict:
    """Contexte commun (sidebar) : stats système, tolérant aux pannes backend."""
    try:
        stats = await api_get("/stats")
    except Exception:
        stats = None
    return {"request": request, "sys_stats": stats, "path": request.url.path}


# ============================================================================
# Pages
# ============================================================================

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/cockpit")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/cockpit", response_class=HTMLResponse)
async def cockpit(request: Request):
    ctx = await base_ctx(request)
    stats = ctx["sys_stats"] or {}
    try:
        hdb = await api_get("/health/db-size")
    except Exception:
        hdb = {}
    try:
        items = (await api_get("/jobs", {"limit": 600, "include_archived": True,
                                         "order_by": "content", "light": True}))["items"]
    except Exception:
        items = []

    # Évolution 14 jours (scrapées / scorées par date de publication)
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    per_day = {d: [0, 0] for d in days}
    for j in items:
        dp = j.get("date_posted")
        if not dp:
            continue
        try:
            d = datetime.fromisoformat(str(dp).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d in per_day:
            per_day[d][0] += 1
            if j.get("base_score") is not None:
                per_day[d][1] += 1
    tot = [per_day[d][0] for d in days]
    sco = [per_day[d][1] for d in days]
    mx = max(tot + [1])
    W, H = 260.0, 70.0

    def pts(series):
        n = max(1, len(series) - 1)
        return " ".join(f"{i / n * W:.1f},{H - (v / mx) * H:.1f}"
                        for i, v in enumerate(series))

    # Distribution scores contenu
    bands = [("8-10", 8, 10.01, True), ("6-7", 6, 8, False),
             ("4-5", 4, 6, False), ("0-3", 0, 4, False)]
    bcnt = []
    for _, lo, hi, hot in bands:
        bcnt.append(sum(1 for j in items
                        if j.get("base_score") is not None
                        and lo <= j["base_score"] < hi))
    bmx = max(bcnt + [1])
    dist = [{"band": b[0], "count": c, "pct": int(c / bmx * 100), "hot": b[3]}
            for b, c in zip(bands, bcnt)]

    # Familles
    fam_counts: dict[str, int] = {}
    for j in items:
        fam_counts[detect_family(j.get("title"))] = \
            fam_counts.get(detect_family(j.get("title")), 0) + 1
    fmx = max([v for k, v in fam_counts.items() if k != "autre"] + [1])
    fams = [{"key": k, "count": fam_counts.get(k, 0),
             "pct": int(fam_counts.get(k, 0) / fmx * 100), **FAMILIES[k]}
            for k in sorted(FAMILIES, key=lambda x: FAMILIES[x]["order"])
            if k != "autre"]

    # Pipeline
    pipe_counts = {k: sum(1 for j in items if j.get("application_status") == k)
                   for k in KANBAN_ORDER}

    top = sorted([j for j in items if (j.get("base_score") or 0) >= 6],
                 key=lambda j: (-(j.get("base_score") or 0), str(j.get("date_posted"))),
                 reverse=False)[:8]

    ctx.update({
        "hdb": hdb, "evo_total": pts(tot), "evo_scored": pts(sco),
        "dist": dist, "fams": fams, "pipe_counts": pipe_counts,
        "pipe_total": sum(v for k, v in pipe_counts.items() if k != "closed"),
        "top": top, "now": datetime.now(),
        "last_scrape": stats.get("last_scrape"),
        "nsrc": len(stats.get("by_platform", {}) or {}),
    })
    return templates.TemplateResponse(request, "cockpit.html", ctx)


@app.get("/offres", response_class=HTMLResponse)
async def offres(request: Request, job: Optional[int] = None):
    ctx = await base_ctx(request)
    ctx["selected_job"] = job
    return templates.TemplateResponse(request, "offres.html", ctx)


@app.get("/offres/list", response_class=HTMLResponse)
async def offres_list(request: Request,
                      kw: str = "", platform: str = "", mode: str = "",
                      min_base: float = 0.0):
    params: dict[str, Any] = {"limit": 400, "order_by": "content", "light": True}
    if kw:
        params["keywords"] = kw
    if platform:
        params["platform"] = platform
    if mode:
        params["work_mode"] = mode
    if min_base > 0:
        params["min_base_score"] = min_base
    try:
        items = (await api_get("/jobs", params))["items"]
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert--err">erreur backend : {e}</div>')

    groups = []
    for fam_key in sorted(FAMILIES, key=lambda k: FAMILIES[k]["order"]):
        rows = [j for j in items if detect_family(j.get("title")) == fam_key]
        rows.sort(key=lambda j: -(j.get("base_score") or -1))
        if not rows:
            continue
        scores = [j["base_score"] for j in rows if j.get("base_score") is not None]
        avg = sum(scores) / len(scores) if scores else None
        groups.append({"key": fam_key, **FAMILIES[fam_key],
                       "rows": rows, "avg": avg})
    return templates.TemplateResponse(request, "partials/offres_list.html",
        {"request": request, "groups": groups, "total": len(items)})


@app.get("/offres/detail/{job_id}", response_class=HTMLResponse)
async def offres_detail(request: Request, job_id: int):
    try:
        j = await api_get(f"/jobs/{job_id}")
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert--err">offre introuvable : {e}</div>')
    return templates.TemplateResponse(request, "partials/job_detail.html",
        {"request": request, "j": j, "fam": FAMILIES[detect_family(j.get("title"))]})


@app.get("/triage", response_class=HTMLResponse)
async def triage(request: Request):
    ctx = await base_ctx(request)
    return templates.TemplateResponse(request, "triage.html", ctx)


@app.get("/triage/table", response_class=HTMLResponse)
async def triage_table(request: Request,
                       kw: str = "", platform: str = "", mode: str = "",
                       min_score: float = 0.0, scope: str = "actives",
                       order_by: str = "relevance",
                       page: int = Query(0, ge=0), size: int = 50):
    params: dict[str, Any] = {
        "limit": size, "offset": page * size, "order_by": order_by,
        "include_archived": scope in ("archivées", "toutes"), "light": True,
    }
    if kw:
        params["keywords"] = kw
    if platform:
        params["platform"] = platform
    if mode:
        params["work_mode"] = mode
    if min_score > 0:
        params["min_score"] = min_score
    try:
        data = await api_get("/jobs", params)
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert--err">erreur backend : {e}</div>')
    items = data["items"]
    if scope == "archivées":
        items = [j for j in items if j.get("archived")]
    total = data["total"]
    n_pages = max(1, (total + size - 1) // size)
    return templates.TemplateResponse(request, "partials/triage_table.html",
        {"request": request, "items": items, "total": total,
         "page": page, "n_pages": n_pages, "size": size})


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline(request: Request):
    ctx = await base_ctx(request)
    return templates.TemplateResponse(request, "pipeline.html", ctx)


@app.get("/pipeline/board", response_class=HTMLResponse)
async def pipeline_board(request: Request):
    try:
        items = (await api_get("/jobs", {"in_pipeline": True, "limit": 500,
                                         "include_archived": True}))["items"]
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert--err">erreur backend : {e}</div>')
    cols = {k: [j for j in items if j.get("application_status") == k]
            for k in KANBAN_ORDER}
    return templates.TemplateResponse(request, "partials/kanban.html",
        {"request": request, "cols": cols, "total": len(items)})


@app.get("/insights", response_class=HTMLResponse)
async def insights(request: Request):
    ctx = await base_ctx(request)
    stats = ctx["sys_stats"] or {}
    try:
        items = (await api_get("/jobs", {"limit": 1000, "include_archived": True,
                                         "light": True}))["items"]
    except Exception:
        items = []
    try:
        logs = (await api_get("/logs", {"limit": 60}))["items"]
    except Exception:
        logs = []

    src = sorted(stats.get("by_platform", {}).items(), key=lambda kv: kv[1])

    buckets = {"0-1": 0, "2-3": 0, "4-5": 0, "6-7": 0, "8-10": 0}
    for j in items:
        s = j.get("relevance_score")
        if s is None:
            continue
        if s >= 8:
            buckets["8-10"] += 1
        elif s >= 6:
            buckets["6-7"] += 1
        elif s >= 4:
            buckets["4-5"] += 1
        elif s >= 2:
            buckets["2-3"] += 1
        else:
            buckets["0-1"] += 1

    modes: dict[str, int] = {}
    for j in items:
        m = j.get("work_mode") or "(inconnu)"
        modes[m] = modes.get(m, 0) + 1

    def avg(key):
        vals = [j[key] for j in items if j.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else 0

    daily: dict[str, list[int]] = {}
    for log in logs:
        d = str(log.get("started_at", ""))[:10]
        if not d:
            continue
        daily.setdefault(d, [0, 0])
        daily[d][0] += log.get("new_jobs") or 0
        daily[d][1] += log.get("duplicates") or 0
    days = sorted(daily)[-14:]

    companies: dict[str, int] = {}
    for j in items:
        c = j.get("company") or "(inconnue)"
        companies[c] = companies.get(c, 0) + 1
    top_co = sorted(companies.items(), key=lambda kv: -kv[1])[:12][::-1]

    ctx["chart_data"] = {
        "src": {"labels": [k for k, _ in src], "values": [v for _, v in src]},
        "dist": {"labels": list(buckets.keys()), "values": list(buckets.values())},
        "modes": [{"name": k, "value": v}
                  for k, v in sorted(modes.items(), key=lambda kv: -kv[1])],
        "axes": [avg("base_score"), avg("score_geo"),
                 avg("score_salary"), avg("score_freshness")],
        "time": {"days": [d[5:] for d in days],
                 "new": [daily[d][0] for d in days],
                 "dup": [daily[d][1] for d in days]},
        "top_co": {"labels": [k for k, _ in top_co],
                   "values": [v for _, v in top_co]},
    }
    return templates.TemplateResponse(request, "insights.html", ctx)


@app.get("/systeme", response_class=HTMLResponse)
async def systeme(request: Request):
    ctx = await base_ctx(request)
    try:
        ctx["h"] = await api_get("/health")
        ctx["hdb"] = await api_get("/health/db-size")
    except Exception as e:
        ctx["h"], ctx["hdb"] = None, {}
        ctx["backend_error"] = str(e)
    try:
        logs = (await api_get("/logs", {"limit": 50}))["items"]
    except Exception:
        logs = []
    ctx["logs"] = logs
    ctx["logs_errors"] = [l for l in logs if l.get("errors") or l.get("fatal_error")][:5]
    return templates.TemplateResponse(request, "systeme.html", ctx)


# ============================================================================
# Actions (proxys POST vers le backend) — réponses = fragments HTML
# ============================================================================

def _alert(msg: str, kind: str = "ok") -> HTMLResponse:
    return HTMLResponse(f'<div class="alert alert--{kind} anim-pop">{msg}</div>')


@app.post("/actions/status/{job_id}", response_class=HTMLResponse)
async def action_status(request: Request, job_id: int, status: str = Form("")):
    await api_post(f"/jobs/{job_id}/status",
                   {"status": status or None})
    return await offres_detail(request, job_id)


@app.post("/actions/archive/{job_id}", response_class=HTMLResponse)
async def action_archive(request: Request, job_id: int, archived: str = Form("true")):
    await api_post(f"/jobs/{job_id}/archive", {"archived": archived == "true"})
    return await offres_detail(request, job_id)


@app.post("/actions/notes/{job_id}", response_class=HTMLResponse)
async def action_notes(job_id: int, notes: str = Form("")):
    await api_post(f"/jobs/{job_id}/notes", {"notes": notes})
    return _alert("notes enregistrées")


@app.post("/actions/bulk", response_class=HTMLResponse)
async def action_bulk(action: str = Form(...), ids: str = Form("")):
    id_list = [int(i) for i in ids.split(",") if i.strip().isdigit()]
    if not id_list:
        return _alert("aucune offre sélectionnée", "warn")
    r = await api_post("/jobs/bulk", {"action": action, "ids": id_list})
    labels = {"pipeline_in": "ajoutée(s) au pipeline", "archive": "archivée(s)",
              "unarchive": "désarchivée(s)", "delete": "supprimée(s) définitivement"}
    return _alert(f"{r.get('affected', 0)} offre(s) {labels.get(action, action)}")


@app.post("/pipeline/move", response_class=HTMLResponse)
async def pipeline_move(request: Request, job_id: int = Form(...),
                        status: str = Form("")):
    await api_post(f"/jobs/{job_id}/status", {"status": status or None})
    return await pipeline_board(request)


@app.post("/ops/scrape", response_class=HTMLResponse)
async def ops_scrape():
    try:
        r = await api_post("/search", {"profile": "France"})
        return _alert(f"scrape terminé — {r.get('new', 0)} nouvelles, "
                      f"{r.get('duplicates', 0)} doublons, "
                      f"{r.get('blacklisted', 0)} blacklistées")
    except Exception as e:
        return _alert(f"échec scrape : {e}", "err")


@app.post("/ops/rescore", response_class=HTMLResponse)
async def ops_rescore(force: bool = False):
    try:
        r = await api_post("/rescore", params={"force": str(force).lower()})
        return _alert(f"{r.get('pending', 0)} offre(s) planifiée(s) au scoring"
                      + (" (force)" if force else ""))
    except Exception as e:
        return _alert(f"échec rescore : {e}", "err")


@app.post("/ops/config-reload", response_class=HTMLResponse)
async def ops_config_reload():
    try:
        r = await api_post("/config/reload")
        bl, pr = r.get("blacklist", {}), r.get("prompt", {})
        return _alert(f"config rechargée — {bl.get('title_patterns', '?')} patterns, "
                      f"prompt {pr.get('chars', '?')} chars")
    except Exception as e:
        return _alert(f"échec reload : {e}", "err")


# ============================================================================
# Paramètres — page d'administration de la config centralisée
# ============================================================================

@app.get("/parametres", response_class=HTMLResponse)
async def parametres(request: Request):
    ctx = await base_ctx(request)
    try:
        ctx["cfg"] = await api_get("/settings")
        ctx["prompt"] = await api_get("/config/prompt")
        ctx["blacklist"] = await api_get("/config/blacklist")
        ctx["samples"] = (await api_get("/scoring/samples"))["samples"]
    except Exception as e:
        ctx["backend_error"] = _api_error_detail(e)
        ctx.setdefault("cfg", None)
        ctx.setdefault("prompt", None)
        ctx.setdefault("blacklist", None)
        ctx.setdefault("samples", [])
    return templates.TemplateResponse(request, "parametres.html", ctx)


@app.post("/actions/settings/weights", response_class=HTMLResponse)
async def settings_weights(request: Request):
    form = await request.form()
    try:
        patch = {"weights": {
            k: float(form.get(k, 0)) for k in
            ("content", "geo", "salary", "freshness", "competition")
        }}
        await api_put("/settings", patch)
        return _alert("poids enregistrés — pense à « recalculer les scores » "
                      "pour les appliquer aux offres déjà notées")
    except ValueError:
        return _alert("valeurs invalides (nombres attendus)", "err")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/llm", response_class=HTMLResponse)
async def settings_llm(request: Request):
    """Formulaire providers : une ligne par provider (ordre, activé, modèle, RPM)
    + paramètres transverses. L'ordre saisi devient l'ordre de fallback."""
    form = await request.form()
    try:
        providers = []
        for name in form.getlist("provider_name"):
            providers.append({
                "name": name,
                "enabled": form.get(f"enabled_{name}") is not None,
                "model": str(form.get(f"model_{name}", "")).strip(),
                "rpm": int(form.get(f"rpm_{name}", 10)),
                "_order": int(form.get(f"order_{name}", 99)),
            })
        providers.sort(key=lambda p: p.pop("_order"))
        patch = {"llm": {
            "providers": providers,
            "concurrency": int(form.get("concurrency", 1)),
            "retries": int(form.get("retries", 3)),
            "max_description_chars": int(form.get("max_description_chars", 2000)),
            "temperature": float(form.get("temperature", 0.2)),
        }}
        await api_put("/settings", patch)
        actifs = [p["name"] for p in providers if p["enabled"]]
        return _alert(f"providers LLM enregistrés — ordre actif : {' → '.join(actifs) or 'AUCUN'} "
                      "(appliqué au prochain scoring)")
    except ValueError:
        return _alert("valeurs invalides", "err")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/search", response_class=HTMLResponse)
async def settings_search(request: Request):
    form = await request.form()
    try:
        patch = {"search": {
            "sites": [str(s) for s in form.getlist("sites")],
            "results_per_term": int(form.get("results_per_term", 10)),
            "hours_old": int(form.get("hours_old", 28)),
            "location": str(form.get("location", "France")).strip(),
            "country": str(form.get("country", "France")).strip(),
            "geo_filter_enabled": form.get("geo_filter_enabled") is not None,
        }}
        await api_put("/settings", patch)
        geo = "activé" if patch["search"]["geo_filter_enabled"] else "DÉSACTIVÉ"
        return _alert(f"sources enregistrées — {len(patch['search']['sites'])} active(s), "
                      f"zone « {patch['search']['location']} », filtre géo {geo}")
    except ValueError:
        return _alert("valeurs invalides", "err")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/terms", response_class=HTMLResponse)
async def settings_terms(request: Request, terms: str = Form("")):
    lines = [t.strip() for t in terms.splitlines() if t.strip()]
    try:
        await api_put("/settings", {"search": {"search_terms": lines}})
        return _alert(f"{len(lines)} terme(s) de recherche enregistrés")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/scheduler", response_class=HTMLResponse)
async def settings_scheduler(request: Request):
    form = await request.form()
    try:
        patch = {"scheduler": {
            # checkbox HTML : absente quand décochée → présence = true
            "scrape_enabled": form.get("scrape_enabled") is not None,
            "run_on_startup": form.get("run_on_startup") is not None,
            "refresh_interval_hours": int(form.get("refresh_interval_hours", 24)),
            "job_retention_days": int(form.get("job_retention_days", 90)),
            "job_not_seen_days": int(form.get("job_not_seen_days", 14)),
            "scrape_log_keep": int(form.get("scrape_log_keep", 100)),
        }}
        r = await api_put("/settings", patch)
        nxt = (r.get("next_scrape") or "—")[:16].replace("T", " ")
        return _alert(f"scheduler enregistré — prochain scrape : {nxt}")
    except ValueError:
        return _alert("valeurs invalides", "err")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/secret", response_class=HTMLResponse)
async def settings_secret(name: str = Form(...), value: str = Form("")):
    """Enregistre une clé PUIS la teste automatiquement (double vérification :
    confirmation UI + test de connexion réel après écriture)."""
    try:
        await api_post("/settings/secrets", {"name": name, "value": value})
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")
    if not value.strip():
        return _alert(f"clé {name} : surcharge retirée (retour au .env)")
    try:
        t = await api_post("/settings/secrets/test", {"name": name})
        kind = "ok" if t.get("ok") else "warn"
        verdict = "test OK" if t.get("ok") else "⚠ test en échec"
        return _alert(f"clé {name} enregistrée — {verdict} : {t.get('detail')} "
                      f"({t.get('latency_ms')} ms)", kind)
    except Exception as e:
        return _alert(f"clé {name} enregistrée, mais test impossible : "
                      f"{_api_error_detail(e)}", "warn")


@app.post("/actions/settings/secret-test", response_class=HTMLResponse)
async def settings_secret_test(name: str = Form(...), value: str = Form("")):
    """Teste une clé SANS l'enregistrer (valeur saisie, ou configurée si vide)."""
    try:
        t = await api_post("/settings/secrets/test",
                           {"name": name, "value": value or None})
        kind = "ok" if t.get("ok") else "err"
        return _alert(f"{name} ({t.get('tested')}) : {t.get('detail')} — "
                      f"{t.get('latency_ms')} ms", kind)
    except Exception as e:
        return _alert(f"test impossible : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/source-test/{name}", response_class=HTMLResponse)
async def settings_source_test(name: str):
    """Sonde une source (mini-recherche réelle, 3 résultats max, ~10-90 s)."""
    import html as _html
    try:
        r = await api_post(f"/sources/{name}/test")
    except Exception as e:
        return _alert(f"{name} : test impossible — {_api_error_detail(e)}", "err")
    kind = "ok" if r.get("ok") else "err"
    errs = " · ".join(r.get("errors") or [])
    msg = f"{name} — {r.get('detail')} ({r.get('duration_s')}s)"
    if errs:
        msg += f" — {errs}"
    return _alert(_html.escape(msg), kind)


@app.post("/actions/settings/connectors", response_class=HTMLResponse)
async def settings_connectors(request: Request):
    form = await request.form()

    def _csv(field: str) -> list[str]:
        return [x.strip() for x in str(form.get(field, "")).split(",") if x.strip()]

    def _lines(field: str) -> list[str]:
        return [l.strip() for l in str(form.get(field, "")).splitlines() if l.strip()]

    try:
        patch = {"connectors": {
            "playwright_enabled": form.get("playwright_enabled") is not None,
            "ft_rome_codes": _csv("ft_rome_codes"),
            "ft_qualification": str(form.get("ft_qualification", "")).strip(),
            "idf_departments": _csv("idf_departments"),
            "greenhouse_boards": _lines("greenhouse_boards"),
            "workday_sites": _lines("workday_sites"),
        }}
        await api_put("/settings", patch)
        gh, wd = len(patch["connectors"]["greenhouse_boards"]), len(patch["connectors"]["workday_sites"])
        return _alert(f"connecteurs enregistrés — {len(patch['connectors']['ft_rome_codes'])} codes ROME, "
                      f"{gh} board(s) Greenhouse, {wd} tenant(s) Workday")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/prompt-test", response_class=HTMLResponse)
async def settings_prompt_test(request: Request):
    """Banc d'essai : note une offre fictive avec le prompt du textarea (non
    sauvegardé) ou le prompt actif. Rend un fragment résultat détaillé."""
    import html as _html
    form = await request.form()
    body = {
        "title": form.get("title", ""),
        "company": form.get("company", ""),
        "job_type": form.get("job_type", ""),
        "platform": form.get("platform", "test"),
        "description": form.get("description", ""),
        # Le champ "text" vient de l'éditeur de prompt inclus via hx-include.
        # use_active coché → prompt vide → le backend utilise le prompt actif.
        "prompt": "" if form.get("use_active") is not None else form.get("text", ""),
    }
    expected = str(form.get("expected", "")).strip()
    try:
        r = await api_post("/scoring/test", body)
    except Exception as e:
        return _alert(f"échec du scoring d'essai : {_api_error_detail(e)}", "err")
    score = r.get("score")
    exp_html = ""
    if expected:
        lo, _, hi = expected.partition("-")
        try:
            in_range = float(lo) <= float(score) <= float(hi)
            exp_html = (f' · attendu {expected} → '
                        f'<b style="color:var(--{ "green" if in_range else "red" })">'
                        f'{"conforme ✓" if in_range else "HORS ATTENDU ✗"}</b>')
        except ValueError:
            pass
    return HTMLResponse(
        f'<div class="reason anim-pop">'
        f'<b>score contenu : {score}/10</b>{exp_html}<br>'
        f'{_html.escape(str(r.get("reasoning", "")))}<br>'
        f'<span style="color:var(--dim);font-size:.7rem">prompt {r.get("prompt_used")} · '
        f'provider {r.get("provider")} · {r.get("duration_s")}s · ~3 200 tokens consommés</span>'
        f'</div>'
    )


@app.post("/actions/settings/prompt", response_class=HTMLResponse)
async def settings_prompt(text: str = Form("")):
    try:
        r = await api_put("/config/prompt", {"text": text})
        return _alert(f"prompt enregistré ({r.get('chars', '?')} chars) — "
                      "un rescore force est nécessaire pour re-noter le stock")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/prompt-reset", response_class=HTMLResponse)
async def settings_prompt_reset():
    try:
        r = await api_post("/config/prompt/reset")
        return _alert(f"prompt par défaut restauré ({r.get('chars', '?')} chars) — recharge la page")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/blacklist", response_class=HTMLResponse)
async def settings_blacklist(request: Request,
                             title_patterns: str = Form(""),
                             title_abbr: str = Form(""),
                             companies: str = Form("")):
    body = {
        "title_patterns": [l.strip() for l in title_patterns.splitlines() if l.strip()],
        "title_abbr": [a.strip() for a in title_abbr.split(",") if a.strip()],
        "companies": [l.strip() for l in companies.splitlines() if l.strip()],
    }
    try:
        r = await api_put("/config/blacklist", body)
        return _alert(f"blacklist enregistrée — {r.get('title_patterns', '?')} patterns, "
                      f"{r.get('companies', '?')} entreprises")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/recompute", response_class=HTMLResponse)
async def settings_recompute():
    try:
        r = await api_post("/rescore/recompute")
        return _alert(f"scores recalculés sans LLM — {r.get('updated', 0)} offre(s) "
                      f"mises à jour sur {r.get('scored', 0)} notées")
    except Exception as e:
        return _alert(f"échec : {_api_error_detail(e)}", "err")


@app.post("/actions/settings/reset", response_class=HTMLResponse)
async def settings_reset(section: str = Form("")):
    try:
        await api_post("/settings/reset", params={"section": section} if section else None)
        return _alert(f"section {section or 'complète'} réinitialisée — recharge la page")
    except Exception as e:
        return _alert(f"refusé : {_api_error_detail(e)}", "err")
