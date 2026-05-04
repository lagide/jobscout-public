"""Scoring de pertinence — Groq (primary) avec fallback OpenRouter.

Architecture multi-provider :
    1. PRIMARY  — Groq, modèle llama-3.3-70b-versatile (gratuit, rapide)
    2. FALLBACK — OpenRouter, modèle meta-llama/llama-3.3-70b-instruct:free

Les deux exposent une API OpenAI-compatible et supportent le tool calling, donc
on garde la même primitive `chat.completions.create(..., tools=[...], tool_choice=...)`.

Variables d'environnement :
    GROQ_API_KEY                    — clé Groq (https://console.groq.com/keys)
    GROQ_MODEL                      — défaut: llama-3.3-70b-versatile
    GROQ_RPM                        — défaut: 6 (free tier TPM-bound, 6000 TPM / 1800 tokens/call)
    OPENROUTER_API_KEY              — clé OpenRouter (fallback)
    OPENROUTER_FALLBACK_MODEL       — défaut: meta-llama/llama-3.3-70b-instruct:free
    OPENROUTER_FALLBACK_RPM         — défaut: 18 (free tier ~20 RPM, marge de sécurité)
    SCORING_CONCURRENCY             — défaut: 1 (free tiers = sequential plus sûr)

Limites du free tier Groq llama-3.3-70b-versatile (Q1 2026) :
    • 30 RPM (req/min) — non-binding
    • 6000 TPM (tokens/min) — BINDING : ~3.3 calls/min sustainable à 1800 tk/call
    • 1000 RPD (req/jour) / 200000 TPD
    On vise 6 RPM (1 toutes les 10s) pour rester à l'aise sur la TPM.

Pas de prompt caching (Anthropic-only), donc le system prompt est envoyé
intégralement à chaque call. Compensation : Groq est gratuit pour ce modèle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from openai import AsyncOpenAI

from database import get_session
from enrichment import (
    compute_final_score,
    compute_freshness_score,
    compute_geo_score,
    compute_salary_score,
)
from models import Job
from schemas import RelevanceScore

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration des providers
# ============================================================================

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_RPM = int(os.getenv("GROQ_RPM", "6"))

OR_BASE_URL = "https://openrouter.ai/api/v1"
OR_FALLBACK_MODEL = os.getenv(
    "OPENROUTER_FALLBACK_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
)
OR_FALLBACK_RPM = int(os.getenv("OPENROUTER_FALLBACK_RPM", "18"))

# Free tiers : on reste séquentiel par défaut. Augmenter prudemment si payant.
MAX_CONCURRENCY = int(os.getenv("SCORING_CONCURRENCY", "1"))

# Limite la description envoyée au modèle pour contenir les tokens.
MAX_DESCRIPTION_CHARS = 2000  # ~500 tokens

# Métadonnées affichées dans le dashboard analytics OpenRouter (optionnel).
_OR_HEADERS = {
    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8501"),
    "X-Title": os.getenv("OPENROUTER_APP_NAME", "JobScout"),
}


# ============================================================================
# Rate limiter — token bucket simple, espacement uniforme entre appels
# ============================================================================

class RateLimiter:
    """Limiteur de débit par espacement minimum entre 2 calls.

    Stratégie : on calcule l'intervalle minimum (60/RPM secondes) et on
    s'assure que `wait()` ne retourne pas avant que cet intervalle soit écoulé
    depuis le dernier appel. Asyncio-safe via lock.

    Plus prévisible qu'un token bucket à seau pour les free tiers, qui
    pénalisent les bursts (429 immédiat).
    """

    def __init__(self, rpm: int):
        self._interval = 60.0 / max(1, rpm)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Bloque jusqu'à ce qu'un nouveau call soit autorisé par le quota."""
        async with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._last_call + self._interval - now)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_call = time.monotonic()


_groq_limiter = RateLimiter(GROQ_RPM)
_or_limiter = RateLimiter(OR_FALLBACK_RPM)


# ============================================================================
# Tool de sortie structurée — schéma Pydantic forcé via tool_choice
# ============================================================================

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


# ============================================================================
# System prompt (v2 — disqualification immédiate + bonus IA)
# ============================================================================

SYSTEM_PROMPT = """Tu es un expert en recrutement IT senior.
Tu évalues la QUALITÉ D'UNE OFFRE D'EMPLOI pour le profil ci-dessous.
Tu dois appeler submit_relevance_score avec un score entier 0-10 et une justification.

═══ PROFIL CIBLE ═══
Rôles visés : Technical Account Manager (TAM, orientation technique/conseil),
Responsable / Directeur IT, RSSI/CISO, DSI, Responsable Infrastructure,
Architecte SI, Manager IT.
Séniorité : 5-15 ans, avec management d'équipe ou leadership technique.
Domaines : infrastructure, cybersécurité, gouvernance SI, projets stratégiques.

═══ DISQUALIFICATION IMMÉDIATE (score 0-2, stop) ═══
- Stage, alternance
- Développeur pur sans management
- Commercial / Sales (AE, SDR, BDR, ingénieur d'affaires, chargé d'affaires)
- Avant-vente / Pre-sales / Solution Engineer / Sales Engineer
- Non-IT
→ Score ≤ 2, justification en 1 phrase, appel immédiat à submit_relevance_score.

═══ SCORING (uniquement si non disqualifié) ═══
Calcule chaque axe sur 10, puis applique la formule finale ci-dessous.

A1 — PERTINENCE DU RÔLE (×0.40)
  9-10 : Intitulé et missions exactement dans le profil cible, niveau senior confirmé.
  6-8  : Rôle très proche (Lead Infra, Architecte, Chef de projet IT senior,
          Resp. cybersécurité avec périmètre réel).
  3-5  : Adjacent ou séniorité insuffisante (ingénieur sécurité sans management,
          TAM avec dimension commerciale notable, DevOps Lead).
  0-2  : Non pertinent.
  ⚠ Le titre seul pèse peu — lis les missions pour juger le niveau réel.
  ⚠ Un TAM avec KPIs commerciaux ou objectifs de vente doit scorer 3-5 max en A1.

A2 — QUALITÉ DE L'ENTREPRISE (×0.25)
  8-10 : Éditeur logiciel, scale-up tech, grand groupe tech, spécialiste cyber/infra reconnu.
  5-7  : ESN/SSII de réputation établie, cabinet de conseil IT, PME tech.
  2-4  : PME non-tech, secteur peu stratégique.
  0-1  : Entreprise cliente non identifiée (annonce cabinet seul, aucun indice).

A3 — RICHESSE DE LA DESCRIPTION (×0.25)
  8-10 : Stack précis, missions détaillées, taille d'équipe, contexte projet clair.
  5-7  : Description correcte, quelques infos concrètes.
  2-4  : Vague ou générique.
  0-1  : Quasi vide.

A4 — CULTURE & AVANTAGES (×0.10)
  7-10 : Infos équipe, télétravail, avantages concrets (RTT, équipement, stock-options).
  4-6  : Quelques mentions.
  0-3  : Aucune info.

B1 — BONUS DIMENSION IA
  +1   : Entreprise IA/ML core (éditeur, lab, start-up IA) OU missions impliquant
         explicitement l'IA (déploiement, gouvernance, intégration d'outils IA,
         stratégie IA d'entreprise).
  +0.5 : Mention de l'IA comme contexte ou outil sans en faire le cœur du poste.
  +0   : Aucune mention.

Score final = min(10, round(A1×0.40 + A2×0.25 + A3×0.25 + A4×0.10 + B1))

═══ CONSIGNES ═══
- Base-toi UNIQUEMENT sur les informations fournies.
- Ignore la localisation et le salaire (gérés séparément).
- Justification : 1-2 phrases max, citant les éléments décisifs (inclure le bonus IA si appliqué).
- Appelle submit_relevance_score avec score (int) et justification."""


def _build_user_prompt(job: Job) -> str:
    """Construit le user prompt à partir d'un job ORM.

    Omet volontairement la localisation, le salaire et la date — ces axes sont
    gérés en Python (compute_geo_score / compute_salary_score / compute_freshness_score)
    et ne doivent PAS influencer la note de qualité de contenu rendue par le modèle.
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


# ============================================================================
# Providers — wrappers AsyncOpenAI + métadonnées
# ============================================================================

@dataclass
class Provider:
    """Décrit un provider LLM utilisable par le scoring."""
    name: str                # "groq" / "openrouter"
    base_url: str
    api_key_env: str         # nom de la var d'env qui contient la clé
    model: str
    limiter: RateLimiter
    headers: Optional[dict] = None

    def is_configured(self) -> bool:
        return bool(os.getenv(self.api_key_env))

    def make_client(self) -> Optional[AsyncOpenAI]:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            return None
        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key,
            default_headers=self.headers or {},
        )


PROVIDERS: list[Provider] = [
    Provider(
        name="groq",
        base_url=GROQ_BASE_URL,
        api_key_env="GROQ_API_KEY",
        model=GROQ_MODEL,
        limiter=_groq_limiter,
    ),
    Provider(
        name="openrouter",
        base_url=OR_BASE_URL,
        api_key_env="OPENROUTER_API_KEY",
        model=OR_FALLBACK_MODEL,
        limiter=_or_limiter,
        headers=_OR_HEADERS,
    ),
]


# ============================================================================
# Appel modèle + parsing de la réponse
# ============================================================================

async def _call_provider(
    provider: Provider, client: AsyncOpenAI, job: Job
) -> RelevanceScore:
    """Appel unique à un provider (avec rate-limiting). Retourne le score parsé.

    Lève sur erreur réseau, 4xx/5xx, ou réponse non-conforme — le caller décide
    s'il faut basculer sur le fallback.

    Note : pas de cache_control ni d'Anthropic-Beta (ces headers/champs sont
    Anthropic-only et causent une erreur sur Groq). System prompt envoyé en
    plain text via la structure OpenAI standard.
    """
    await provider.limiter.wait()

    resp = await client.chat.completions.create(
        model=provider.model,
        max_tokens=512,           # largement suffisant pour {score, reasoning}
        temperature=0.2,          # bas pour scores reproductibles
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
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
            f"[{provider.name}] no tool_call for job {job.id} "
            f"(finish_reason={choice.finish_reason!r})"
        )

    args_json = tool_calls[0].function.arguments
    try:
        parsed = json.loads(args_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"[{provider.name}] tool_call args not valid JSON for job {job.id}: {e}"
        ) from e

    # Tolérance : Llama dépasse parfois la limite de 400 chars du reasoning.
    # On tronque plutôt que de faire échouer la validation Pydantic.
    reasoning = parsed.get("reasoning", "")
    if isinstance(reasoning, str) and len(reasoning) > 400:
        parsed["reasoning"] = reasoning[:397].rstrip() + "…"

    return RelevanceScore.model_validate(parsed)


async def score_one_job(job: Job) -> RelevanceScore:
    """Score un job via Groq → fallback OpenRouter si Groq échoue.

    Stratégie de fallback :
      - Si Groq répond mal (429, 5xx, parsing) → on tente OpenRouter
      - Si Groq pas configuré (clé absente) → on attaque direct OpenRouter
      - Si aucun provider dispo → RuntimeError
    """
    last_error: Optional[Exception] = None

    for provider in PROVIDERS:
        if not provider.is_configured():
            continue

        client = provider.make_client()
        if client is None:
            continue

        try:
            result = await _call_provider(provider, client, job)
            # Première fois qu'on bascule sur fallback : on log explicitement
            # pour diagnostique des dépassements de quota.
            if provider.name != "groq":
                logger.info(
                    "Scoring job %s via fallback %s (provider primary indisponible)",
                    job.id, provider.name,
                )
            return result
        except Exception as e:
            last_error = e
            logger.warning(
                "Provider %s failed for job %s: %s%s",
                provider.name, job.id, type(e).__name__,
                f" — {e}" if str(e) else "",
            )
            # On continue vers le provider suivant (fallback)

    # Aucun provider n'a fonctionné
    raise RuntimeError(
        f"All providers failed for job {job.id} "
        f"(configured: {[p.name for p in PROVIDERS if p.is_configured()]}; "
        f"last error: {last_error})"
    )


# ============================================================================
# Scoring batch — fire-and-forget depuis /search ou /rescore
# ============================================================================

async def score_jobs_background(job_ids: Iterable[int]) -> None:
    """Score N offres avec concurrence bornée + rate limiting.

    Appelé en fire-and-forget depuis le scraper et les endpoints /rescore.
    Les erreurs sont loguées mais jamais relevées — best-effort.
    """
    job_ids = list(job_ids)
    if not job_ids:
        return

    # Au moins un provider doit être configuré
    available = [p.name for p in PROVIDERS if p.is_configured()]
    if not available:
        logger.warning(
            "Aucune clé API configurée (GROQ_API_KEY ou OPENROUTER_API_KEY) — "
            "skipping scoring for %d jobs", len(job_ids),
        )
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def process(job_id: int) -> None:
        async with semaphore:
            # Session courte n°1 : snapshot du job sans tenir de lock pendant l'API call.
            with get_session() as s:
                job = s.get(Job, job_id)
                if job is None or job.relevance_score is not None:
                    return
                snapshot = Job(**{
                    c.name: getattr(job, c.name) for c in Job.__table__.columns
                })

            try:
                result = await score_one_job(snapshot)
            except Exception as e:
                logger.warning("Scoring failed for job %s: %s", job_id, e)
                return

            # Session courte n°2 : on persiste le résultat.
            with get_session() as s:
                job = s.get(Job, job_id)
                if job is None:
                    return
                base = float(result.score)
                job.base_score = base

                # Backfill des composantes déterministes pour les anciennes lignes.
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

                # relevance_score final = pondération des 4 axes (formule dans enrichment.py)
                job.relevance_score = compute_final_score(
                    content=base,
                    geo=job.score_geo,
                    salary=job.score_salary,
                    freshness=job.score_freshness,
                )
                job.relevance_reasoning = result.reasoning

    logger.info(
        "Scoring %d jobs (providers=%s, primary_model=%s, concurrency=%d, RPM groq=%d)",
        len(job_ids), available, GROQ_MODEL, MAX_CONCURRENCY, GROQ_RPM,
    )
    await asyncio.gather(*(process(jid) for jid in job_ids))
    logger.info("Scoring pass complete")


async def rescore_all_missing() -> int:
    """Score toutes les offres sans relevance_score. Retourne le nombre planifié."""
    with get_session() as s:
        ids = [
            jid for (jid,) in
            s.query(Job.id).filter(Job.relevance_score.is_(None)).all()
        ]
    if ids:
        await score_jobs_background(ids)
    return len(ids)


async def rescore_all_force() -> int:
    """Score TOUTES les offres, indépendamment de leur état actuel.

    Appelé par POST /rescore?force=true après changement de formule/prompt.
    Le caller doit avoir vidé les scores existants au préalable.
    """
    with get_session() as s:
        ids = [jid for (jid,) in s.query(Job.id).all()]
    if ids:
        await score_jobs_background(ids)
    return len(ids)
