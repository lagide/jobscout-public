"""Claude-based relevance scoring via OpenRouter.

Uses the OpenAI-compatible chat completions API against OpenRouter, with *forced
tool calling* to guarantee the model returns a JSON payload matching our Pydantic
RelevanceScore schema. Tool calling is the most reliable cross-provider mechanism
for structured output on OpenRouter (works for Anthropic, OpenAI, Gemini, etc.).

Env vars:
    OPENROUTER_API_KEY  — required.
    SCORING_MODEL       — default: anthropic/claude-haiku-4.5
    SCORING_CONCURRENCY — default: 4

Cost reference (Haiku 4.5 via OpenRouter, realistic — measured):
    • Without prompt caching: ~$6 per 1000 jobs (3500 in + 150 out tokens/job).
    • With ephemeral prompt caching on the system block: ~$2 per 1000 jobs.
    System prompt is ~1800 tokens and reused verbatim → caching the prefix via
    ``cache_control`` gives a ~90% discount on that portion after the first call
    (5 min TTL). The user prompt is NOT cached (changes per job).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Iterable

from openai import AsyncOpenAI

from database import get_session
from enrichment import compute_final_score, compute_freshness_score, compute_geo_score, compute_salary_score
from models import Job
from schemas import RelevanceScore

logger = logging.getLogger(__name__)

# ---------- Config ----------

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = os.getenv("SCORING_MODEL", "anthropic/claude-haiku-4.5")
MAX_CONCURRENCY = int(os.getenv("SCORING_CONCURRENCY", "4"))
MAX_DESCRIPTION_CHARS = 2000  # ~500 tokens — enough for role + company context

# Optional OpenRouter metadata — shows up in your OpenRouter dashboard analytics.
_DEFAULT_HEADERS = {
    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8501"),
    "X-Title": os.getenv("OPENROUTER_APP_NAME", "JobScout"),
}

# Tool forcing a strict schema on the response.
# Using a tool call instead of response_format is more reliable with Anthropic
# models on OpenRouter: the provider *always* honors tool_choice.
_SCORING_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_relevance_score",
        "description": (
            "Submit the final relevance score (0-10) and a short French "
            "justification for the evaluated job offer."
        ),
        "parameters": RelevanceScore.model_json_schema(),
    },
}

# System prompt — evaluates CONTENT QUALITY only.
# Geography, salary and freshness are handled by Python-side deterministic scoring.
SYSTEM_PROMPT = """Tu es un expert en recrutement spécialisé dans les métiers IT seniors.

Tu évalues la QUALITÉ DU CONTENU d'une offre d'emploi sur 4 axes combinés en un score 0-10.
Tu ne dois PAS tenir compte de la localisation, du salaire ni de la date de l'annonce — \
ces critères sont gérés par un système distinct.

═══════════════════════════════════════════════
PROFIL CIBLE
═══════════════════════════════════════════════
Postes : Technical Account Manager (TAM), Responsable Informatique / Technique, \
Directeur Technique (CTO), RSSI / CISO, DSI, Responsable Infrastructure, Architecte SI, \
Manager IT.
Séniorité : management d'équipe ou leadership technique (5-15+ ans).
Compétences clés : infrastructure IT, cybersécurité, gouvernance SI, management \
d'équipes techniques, projets stratégiques.

═══════════════════════════════════════════════
TES 4 AXES D'ÉVALUATION
═══════════════════════════════════════════════

1. PERTINENCE DU RÔLE — poids ~40 %
   • 9-10 : Intitulé exact ou équivalent (RSSI, CTO, DSI, TAM, Directeur IT) OU \
missions clairement managériales/stratégiques dans la description.
   • 6-8  : Rôle très proche (Lead Infra Senior, Architecte SI, Chef de projet IT \
confirmé, Responsable cybersécurité).
   • 3-5  : Rôle adjacent (DevOps Lead, Ingénieur sécurité sans management, TAM \
junior) — potentiel limité.
   • 0-2  : Rôle non pertinent (développeur pur, stage, alternance, commerce pur, \
non-IT, poste hors sujet).
   ⚠ Le titre seul pèse très peu — lis la description pour juger le niveau réel.

2. QUALITÉ DE L'ENTREPRISE — poids ~25 %
   • 8-10 : Éditeur logiciel / scale-up tech / grand groupe tech / entreprise \
reconnue en cybersécurité ou infrastructure.
   • 5-7  : ESN/SSII de bonne réputation, cabinet de conseil IT, PME tech sérieuse.
   • 2-4  : PME non-tech, secteur peu stratégique, entreprise inconnue.
   • 0-1  : Cabinet de recrutement anonyme seulement (zéro info sur l'entreprise finale).

3. RICHESSE DE LA DESCRIPTION — poids ~25 %
   • 8-10 : Stack technique précis, missions détaillées, taille d'équipe mentionnée, \
profil attendu clair, contexte projet/entreprise fourni.
   • 5-7  : Description correcte avec quelques infos techniques et profil esquissé.
   • 2-4  : Description vague ou générique — peu d'infos concrètes.
   • 0-1  : Annonce très courte, copier-coller visible ou aucun contenu utile.

4. CULTURE & AVANTAGES — poids ~10 %
   • 7-10 : Infos sur l'équipe, mode de travail mentionné, avantages listés (RTT, \
équipement fourni, stock-options, mutuelle premium).
   • 4-6  : Quelques mentions de culture ou d'avantages.
   • 0-3  : Aucune info sur l'environnement de travail ou les avantages.

═══════════════════════════════════════════════
CONSIGNES
═══════════════════════════════════════════════
- Base-toi UNIQUEMENT sur les informations fournies.
- NE tiens PAS compte de la localisation (géré séparément).
- NE tiens PAS compte du salaire (géré séparément).
- Donne un score entier 0-10 et une justification courte en français (1-2 phrases max) \
citant les éléments décisifs.
- Tu DOIS répondre en appelant la fonction submit_relevance_score."""


def _build_user_prompt(job: Job) -> str:
    """Build the user-facing prompt for Claude content scoring.

    Intentionally omits location, salary and posting date — those axes are
    handled by deterministic Python scoring (compute_geo_score, etc.) and
    should NOT influence Claude's content quality evaluation.
    """
    desc = (job.description or "").strip()
    if len(desc) > MAX_DESCRIPTION_CHARS:
        desc = desc[:MAX_DESCRIPTION_CHARS] + "\n[…description tronquée]"

    return f"""Titre : {job.title}
Entreprise : {job.company or 'Inconnue'}
Type de contrat : {job.job_type or 'Non précisé'}
Plateforme source : {job.platform}

Description :
{desc or '(aucune description)'}
"""


def _make_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers=_DEFAULT_HEADERS,
    )


async def score_one_job(client: AsyncOpenAI, job: Job) -> RelevanceScore:
    """Score a single job. Returns a validated Pydantic model or raises."""
    # System prompt is identical across all jobs — mark it as cacheable so
    # Anthropic (via OpenRouter) returns a ~90% discount on cache hits.
    # Requires a ~1024-token minimum cacheable prefix; our prompt is ~1800.
    resp = await client.chat.completions.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": _build_user_prompt(job)},
        ],
        tools=[_SCORING_TOOL],
        tool_choice={
            "type": "function",
            "function": {"name": "submit_relevance_score"},
        },
    )
    choice = resp.choices[0]
    tool_calls = choice.message.tool_calls or []
    if not tool_calls:
        raise RuntimeError(
            f"Model did not emit a tool call for job {job.id}. "
            f"Finish reason: {choice.finish_reason!r}"
        )
    args_json = tool_calls[0].function.arguments
    try:
        parsed = json.loads(args_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Tool call args not valid JSON for job {job.id}: {e}") from e

    # Haiku sometimes blows the 400-char reasoning cap; truncate gracefully
    # instead of failing the whole scoring (we'd rather have a score than nothing).
    reasoning = parsed.get("reasoning", "")
    if isinstance(reasoning, str) and len(reasoning) > 400:
        parsed["reasoning"] = reasoning[:397].rstrip() + "…"

    return RelevanceScore.model_validate(parsed)


async def score_jobs_background(job_ids: Iterable[int]) -> None:
    """Score the given jobs concurrently (bounded) and persist results.

    Called as a fire-and-forget task from /search and from the scheduler.
    Errors are logged but never raised — scoring is best-effort.
    """
    job_ids = list(job_ids)
    if not job_ids:
        return

    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning(
            "OPENROUTER_API_KEY not set — skipping scoring for %d jobs", len(job_ids)
        )
        return

    client = _make_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def process(job_id: int) -> None:
        async with semaphore:
            # Re-fetch each job in its own short session to avoid holding a row lock
            with get_session() as s:
                job = s.get(Job, job_id)
                if job is None or job.relevance_score is not None:
                    return
                # Detach a snapshot for the API call
                snapshot = Job(**{c.name: getattr(job, c.name) for c in Job.__table__.columns})

            try:
                result = await score_one_job(client, snapshot)
            except Exception as e:
                logger.warning("Scoring failed for job %s: %s", job_id, e)
                return

            with get_session() as s:
                job = s.get(Job, job_id)
                if job is None:
                    return
                base = float(result.score)
                # base_score = Claude content quality (role relevance + company + description)
                job.base_score = base

                # Recompute deterministic components for old rows that lack them
                if job.score_geo is None:
                    job.score_geo = compute_geo_score(
                        job.work_mode, job.location, job.description
                    )
                if job.score_salary is None:
                    job.score_salary = compute_salary_score(
                        job.salary_eur_min, job.salary_eur_max, job.salary_interval
                    )
                if job.score_freshness is None:
                    job.score_freshness = compute_freshness_score(job.date_posted)

                # Final relevance_score = weighted combination of all four axes
                job.relevance_score = compute_final_score(
                    content=base,
                    geo=job.score_geo,
                    salary=job.score_salary,
                    freshness=job.score_freshness,
                )
                job.relevance_reasoning = result.reasoning

    logger.info(
        "Scoring %d jobs via OpenRouter (model=%s, concurrency=%d)",
        len(job_ids), MODEL, MAX_CONCURRENCY,
    )
    await asyncio.gather(*(process(jid) for jid in job_ids))
    logger.info("Scoring pass complete")

    # After scoring, ping Telegram if any of the freshly-scored jobs cleared the threshold.
    try:
        import notifier
        if notifier.is_configured():
            threshold = notifier.get_min_score()
            with get_session() as s:
                hits = (
                    s.query(Job)
                    .filter(Job.id.in_(job_ids))
                    .filter(Job.relevance_score.isnot(None))
                    .filter(Job.relevance_score >= threshold)
                    .all()
                )
                # Detach for use outside the session
                profile_hint = hits[0].geo_profile if hits else None
                payload = [
                    Job(
                        id=h.id,
                        title=h.title,
                        company=h.company,
                        location=h.location,
                        job_url=h.job_url,
                        relevance_score=h.relevance_score,
                        geo_profile=h.geo_profile,
                    )
                    for h in hits
                ]
            if payload:
                await notifier.notify_new_jobs(payload, profile=profile_hint)
    except Exception:
        logger.exception("Telegram notification failed (non-fatal)")


async def rescore_all_missing() -> int:
    """Score every job that has no relevance_score yet. Returns count scheduled."""
    with get_session() as s:
        ids = [row[0] for row in s.query(Job.id).filter(Job.relevance_score.is_(None)).all()]
    if ids:
        await score_jobs_background(ids)
    return len(ids)


async def rescore_all_force() -> int:
    """Score ALL jobs, regardless of whether they already have a score.

    Called by POST /rescore?force=true after the scoring formula changes.
    The caller is expected to have already cleared scores in the DB.
    """
    with get_session() as s:
        ids = [row[0] for row in s.query(Job.id).all()]
    if ids:
        await score_jobs_background(ids)
    return len(ids)
