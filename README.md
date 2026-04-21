# JobScout

> **Self-hosted senior IT job aggregator with LLM-based relevance scoring.**
> FastAPI + Streamlit + SQLite + Docker Compose. Built to replace 2h/day of
> manual scrolling on LinkedIn/Indeed/FranceTravail/etc. with a single
> ranked feed.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.40-FF4B4B?logo=streamlit&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

---

## What it does

Scrapes **11 job platforms** every morning, deduplicates postings across
sources, enriches each offer (work mode, salary in EUR, freshness, language)
and computes a **multi-criteria relevance score (0–10)** combining:

| Axis | Source | Weight |
|---|---|---|
| Content quality (role fit, company, description richness) | Claude Haiku 4.5 via OpenRouter | 30% |
| Geographic accessibility | Deterministic Python (home-location calibrated) | 30% |
| Salary competitiveness (annualised EUR) | Deterministic Python | 20% |
| Posting freshness | Deterministic Python | 15% |
| Competition level (stub) | Neutral default | 5% |

Results are served in a Streamlit UI with **split list/detail view**, a
**Kanban application pipeline** (to_study / interesting / applied /
interview / closed) with temporal reminders, **4 thematic dashboards**
(scoring, salary, pipeline, temporal), and a **connector health panel**.

## Connector coverage

| Connector | Type | Region | Notes |
|---|---|---|---|
| LinkedIn, Indeed, Glassdoor, ZipRecruiter | [JobSpy](https://github.com/cullenwatson/JobSpy) | Global | Parallel scraping |
| FranceTravail | Official API (OAuth) | France | Optional, silent if no creds |
| Greenhouse | Public ATS API | Global | Comma-separated board slugs |
| Workday | Public careers REST | Global | `Name\|URL` entries |
| Remotive | Public API | Global remote | Free tier |
| Himalayas | Public API | Global remote | Free tier |
| FreeWork | HTML (requests) | France (freelance) | — |
| APEC | Playwright (headless Chromium) | France (cadres) | Optional |

New connectors subclass `BaseConnector` and register themselves in
`backend/connectors/registry.py`. Connectors without credentials
self-disable silently.

## Architecture

```
┌─────────────┐    ┌─────────────────────────────┐    ┌──────────────┐
│  Streamlit  │───▶│     FastAPI (8000)          │───▶│  SQLite      │
│   (8501)    │    │  ─ /search /jobs /rescore   │    │  (volume)    │
│             │    │  ─ APScheduler (daily)      │    └──────────────┘
└─────────────┘    │  ─ 11 pluggable connectors  │
                   │  ─ OpenRouter (Claude 4.5)  │
                   └─────────────────────────────┘
```

**Key design choices:**
- **Prompt caching** on the Claude system prompt (~90% cost reduction on repeat scoring).
- **Python-side deterministic scoring** for geo/salary/freshness — Claude only handles content quality, so a calibration change doesn't require a re-call.
- **Content-hash deduplication** across platforms — the same role on LinkedIn and FranceTravail collapses into one row with a `sources` array.
- **SSE-free UI**: Streamlit polls `/jobs` with filters; heavy lifting (scraping, scoring) runs in `BackgroundTasks`.

## Quickstart

```bash
# 1) Clone and configure
git clone https://github.com/lagide/jobscout-public.git
cd jobscout-public
cp .env.example .env
# Edit .env — minimum required: OPENROUTER_API_KEY

# 2) Launch
docker compose up -d --build

# 3) Access
# Frontend:  http://localhost:8501
# API docs:  http://localhost:8000/docs
```

At first boot:
1. SQLite schema is created via SQLAlchemy.
2. 30s after startup, the scheduler runs an initial scrape (configurable via `RUN_ON_STARTUP`).
3. Each new offer is scored by Claude in the background.
4. The scheduler re-scrapes every `REFRESH_INTERVAL_HOURS` (default 24).

## Configuration

See `.env.example` for the full list. Minimum required:

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | Get one at https://openrouter.ai/keys |

Optional:

| Variable | Default | Purpose |
|---|---|---|
| `SCORING_MODEL` | `anthropic/claude-haiku-4.5` | Any OpenRouter-supported model |
| `SCHEDULED_PROFILES` | `France` | Comma-separated geo profiles |
| `REFRESH_INTERVAL_HOURS` | `24` | Scheduler cadence |
| `SCORING_CONCURRENCY` | `4` | Parallel Claude requests |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | Push notifications on high-scoring offers |

## Adapting the scoring to your context

The default geographic scoring assumes the user lives outside Paris but
within commute range (Northern France). To adapt:

1. **`backend/enrichment.py` → `_PARIS_RE`**: swap the regex for your own
   target hub (e.g. Lyon, Geneva, Luxembourg departments/cities).
2. **`backend/enrichment.py` → `compute_geo_score`**: tune the score ladder
   (remote-days thresholds, onsite penalties).
3. **`backend/scoring.py` → `SYSTEM_PROMPT`**: edit the target roles, seniority,
   and skill focus. Currently tuned for senior IT management (TAM, CIO, CISO,
   CTO, IT Director, Infrastructure Lead).
4. **`backend/enrichment.py` → `_SALARY_BRACKETS`**: adjust the EUR thresholds
   for your seniority level.

## Cost reference

With Claude Haiku 4.5 via OpenRouter and prompt caching enabled:
- **~$2 per 1,000 scored offers** (system prompt cache hits ~90%).
- A typical scrape brings 50–150 new offers/day → **~$0.10–0.30/day**.
- Switching to Opus 4.7 is ~20× more expensive but marginally better on ambiguous roles.

## Stack

- **Backend**: FastAPI, SQLAlchemy 2.0, APScheduler, httpx, OpenAI SDK (pointed at OpenRouter), Playwright, JobSpy, Pydantic v2.
- **Frontend**: Streamlit 1.40, Plotly, Pandas.
- **Infra**: Docker Compose (2 services, 1 SQLite volume). Designed to run on low-power hardware (tested on Synology DS220+, Celeron J4025).

## Project layout

```
jobscout/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── main.py              # FastAPI + lifespan scheduler
│   ├── scraper.py           # Connector orchestration + dedup
│   ├── scoring.py           # OpenRouter client, prompt caching
│   ├── enrichment.py        # Work mode / language / geo / salary / freshness
│   ├── scheduler.py         # APScheduler daily job
│   ├── notifier.py          # Telegram push (optional)
│   ├── database.py models.py schemas.py
│   ├── connectors/          # BaseConnector + 9 implementations
│   │   ├── base.py registry.py
│   │   ├── francetravail.py greenhouse.py workday.py
│   │   ├── remotive.py himalayas.py freework.py
│   │   ├── apec.py playwright_base.py
│   │   └── ...
│   └── Dockerfile requirements.txt
└── frontend/
    ├── app.py               # Streamlit: offers, pipeline, stats, logs, actions
    └── Dockerfile requirements.txt
```

## License

MIT — see `LICENSE`.

## Disclaimer

Scoring thresholds (salary, geography, role vocabulary) are tuned for a
specific personal profile. **Fork and adapt** the relevant files in
`backend/` before using in anger. The UI copy is mostly in French; PRs
welcome for i18n.
