"""Scoring de pertinence — Groq (primary) avec fallback OpenRouter.

Architecture multi-provider :
    1. PRIMARY  — Groq, modèle llama-3.3-70b-versatile (gratuit, rapide)
    2. FALLBACK — OpenRouter, modèle meta-llama/llama-3.3-70b-instruct:free

Les deux exposent une API OpenAI-compatible et supportent le tool calling, donc
on garde la même primitive `chat.completions.create(..., tools=[...], tool_choice=...)`.

Configuration :
    Modèles, débits (RPM), concurrence, retries et température vivent dans
    settings (config/settings.json, section `llm`) — éditables via la page
    Paramètres et lus À CHAQUE APPEL (aucun restart requis). Les variables
    d'environnement historiques (GROQ_MODEL, GROQ_RPM, OPENROUTER_FALLBACK_*,
    SCORING_CONCURRENCY, SCORING_RETRIES) servent de défauts au premier boot.
    Les clés API sont résolues par settings.get_secret() : surcharge UI
    (config/secrets.json) puis .env.

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
import random
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

import settings
from constants import CONFIG_DIR
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
# Catalogue des providers — tous OpenAI-compatibles (un seul client, une seule
# primitive chat.completions + tool_choice forcé). Anthropic et Google passent
# par leurs endpoints de compatibilité OpenAI OFFICIELS — choix assumé pour
# garder l'abstraction multi-provider unique (pas de 2e SDK sur le NAS).
# L'activation, le modèle, le RPM et l'ORDRE viennent de settings.llm.providers.
# ============================================================================

# Métadonnées affichées dans le dashboard analytics OpenRouter (optionnel).
_OR_HEADERS = {
    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8502"),
    "X-Title": os.getenv("OPENROUTER_APP_NAME", "JobScout"),
}

PROVIDER_CATALOG: dict[str, dict] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_name": "GROQ_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_name": "OPENROUTER_API_KEY",
        "headers": _OR_HEADERS,
    },
    "anthropic": {
        # Endpoint de compatibilité OpenAI officiel d'Anthropic (tool calling OK).
        "base_url": "https://api.anthropic.com/v1/",
        "api_key_name": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_name": "OPENAI_API_KEY",
    },
    "google": {
        # Endpoint de compatibilité OpenAI officiel de Gemini.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_name": "GEMINI_API_KEY",
    },
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


# Cache de limiteurs par provider — reconstruit si le RPM change en cours de
# route (édition via la page Paramètres), conservé sinon pour garder l'état
# d'espacement entre deux batchs.
_limiter_cache: dict[str, tuple[int, RateLimiter]] = {}


def _limiter(name: str, rpm: int) -> RateLimiter:
    cached = _limiter_cache.get(name)
    if cached and cached[0] == rpm:
        return cached[1]
    lim = RateLimiter(rpm)
    _limiter_cache[name] = (rpm, lim)
    return lim


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
# System prompt par défaut — exemple pour un profil TAM cybersécurité senior.
#
# Config chaude : surchargeable par config/scoring_prompt.txt (volume monté)
# — rechargé via POST /config/reload, sans rebuild d'image. C'est le premier
# fichier à éditer pour adapter JobScout à TON profil.
# ============================================================================

DEFAULT_SYSTEM_PROMPT = """Tu es un expert en recrutement IT/cybersécurité senior.
Tu évalues la QUALITÉ DE CONTENU d'une offre pour LE PROFIL CIBLE défini ci-dessous.
Tu dois appeler submit_relevance_score avec un score entier 0-10 et une justification courte en français.

Important : ignore la localisation, le salaire et la fraîcheur. Ces axes sont calculés séparément en Python.

=== PROFIL CIBLE (EXEMPLE — remplace ce bloc par ton propre profil) ===
Candidat : Senior Technical Account Manager en cybersécurité réseau, 10+ ans réseau/sécurité.
Forces : relation technique partenaires/clients EMEA, escalades techniques, architecture sécurité,
firewalls / UTM, ZTNA / Zero Trust, Endpoint, MDR, SD-WAN, migrations firewall,
support N2/N3, conseil technique, adoption produit, satisfaction client.

Marché cible — 4 familles par priorité décroissante (refonte 2026-06-08). Quand le poste
est clairement IT / SI / cybersécurité / infrastructure / réseau / cloud, le score de contenu
doit refléter cet ordre :

  PRIORITÉ 1 — Technical Account Manager (coeur du profil)
    Senior / Technical Account Manager cyber/sécurité, Responsable Technique de Comptes,
    Partner Technical Account Manager, Responsable Technique Partenaires.
  PRIORITÉ 2 — Team Leader Sécurité Réseaux
    Team Leader / Lead / Responsable d'équipe Sécurité Réseaux, Network Security Team Lead,
    management technique d'une équipe sécurité réseau / cybersécurité.
  PRIORITÉ 3 — Responsable des Systèmes d'Information
    Responsable des Systèmes d'Information, Responsable Informatique, IT Manager,
    Information Systems Manager — direction / encadrement SI.
  PRIORITÉ 4 — Directeur Technique
    Directeur Technique, Directeur des Systèmes d'Information, CTO, IT Director — UNIQUEMENT si
    le contexte est clairement IT, SI, cybersécurité, infrastructure, réseau, cloud ou architecture.

Hiérarchie : à pertinence et qualité égales, 1 ≥ 2 ≥ 3 ≥ 4. Mais un poste parfaitement aligné
en priorité 2, 3 ou 4 reste un EXCELLENT score (7-9) : ne le plafonne PAS sous prétexte qu'il
n'est pas TAM. Ne dégrade un poste priorité 3/4 que si le lien IT/SI/cyber/infra est faible.

Marché secondaire (acceptable, à valoriser modérément, sans dépasser les 4 priorités) : rôles
seniors client-facing cyber proches du profil — Architecte Sécurité / Réseau / Zero Trust / SASE,
Customer Success Engineer Security, Consultant Cybersécurité senior, Security Escalation Engineer.

=== DISQUALIFICATION IMMÉDIATE (score 0-2, stop) ===
- Stage, alternance, junior pur.
- Développeur pur : software/fullstack/frontend/backend/data/AI engineer sans sécurité ni leadership.
- Commercial pur : Account Executive, SDR, BDR, Business Developer, Key Account Manager, responsable grands comptes.
- Sales Engineer / avant-vente / pre-sales si le poste porte surtout quota, closing, pipeline ou vente.
- Non-IT : BTP, médical, finance/compta, RH, industrie, maintenance, sécurité physique, HSE, sûreté, retail.
- Poste en allemand/néerlandais ou autre langue non FR/EN si c'est visible dans le titre/description.
→ Score ≤ 2, justification en 1 phrase, appel immédiat à submit_relevance_score.

=== FAUX AMIS À NE PAS CONFONDRE ===
Lis le contexte, pas seulement le titre :
- "Responsable Technique" / "Directeur Technique" : bon seulement si IT/SI/cyber/infra. Sinon souvent BTP,
  hôtellerie, maintenance, industrie, formation, labo, déchets : disqualification.
- "Solutions Architect" : bon si cyber/network/security/cloud security ; faible si ERP/SAP/ServiceNow/MES/finance.
- "Security" : peut être cybersécurité OU sécurité physique/safety/HSE/sûreté. Le second est disqualifiant.
- "SSI" : Système Sécurité Information = cible ; Sécurité Incendie = rejet.
- "Account Manager" sans Technical/TAM/security = commercial, rejet.
- "Customer Success" : bon si technique/security/cyber ; faible ou rejet si pure adoption SaaS commerciale.
- FPGA, ASIC, ADAS, RAMS, I&C, EIA, MES, AVEVA, SCADA industriel : généralement hors cible.

=== SCORING SI NON DISQUALIFIÉ ===
Calcule chaque axe sur 10, puis applique la formule.

A1 — FIT PROFIL CIBLE (×0.45)
  9-10 : Priorité 1 (TAM cyber/sécurité senior) ou Priorité 2 (Team Leader / Lead / Responsable
         d'équipe Sécurité Réseaux, Network Security Team Lead) clairement cyber/réseau.
  7-8  : Priorité 3 (Responsable des Systèmes d'Information, Responsable Informatique, IT/IS Manager)
         ou Priorité 4 (Directeur Technique / DSI / CTO) en contexte clairement IT/SI/cyber/infra ;
         ou rôle secondaire cyber très solide (Architecte Sécurité/ZT/SASE, Consultant cyber senior,
         CSM Security senior, Security Escalation Manager).
  5-6  : Rôle cyber/réseau/SI intéressant mais moins senior, moins client-facing, ou contexte tech
         un peu flou (ex. IT Manager généraliste sans dimension sécu nette).
  3-4  : Responsabilité/direction technique au lien IT/SI/cyber faible, consultant IT vague,
         solutions architect non cyber.
  0-2  : Hors profil.

A2 — TECHNICITÉ CYBER / RÉSEAU (×0.25)
  9-10 : Firewall, Zero Trust/ZTNA, SASE, endpoint, MDR, EDR, XDR, IAM, VPN, SD-WAN, architecture réseau sécurité,
         escalades techniques ou troubleshooting avancé explicitement présents.
  6-8  : Sécurité SI, cloud security, SOC, vulnérabilités, architecture infra avec sécurité réelle.
  3-5  : IT généraliste avec peu de sécurité concrète.
  0-2  : Pas de contenu cyber/réseau/infra pertinent.

A3 — POSTURE SENIOR, CLIENT OU RESPONSABILITÉ TECH (×0.15)
  9-10 : Ownership senior : comptes stratégiques, partenaires, EMEA, escalades critiques, architecture, référent,
         management d'équipe tech, budget/périmètre, direction technique IT/cyber/infra.
  6-8  : Leadership technique, coordination, projet senior, rôle transverse ou référent.
  3-5  : Exécution individuelle sans leadership clair.
  0-2  : Junior/exécutant.

A4 — QUALITÉ ENTREPRISE / ENVIRONNEMENT (×0.10)
  8-10 : Éditeur cyber/network/security, MSSP, intégrateur sécurité reconnu, grand groupe tech.
  5-7  : ESN/cabinet IT sérieux, PME tech, client final avec vraie DSI/cyber.
  2-4  : Secteur non-tech ou contexte peu stratégique.
  0-1  : Entreprise inconnue sans contexte.

A5 — DESCRIPTION EXPLOITABLE (×0.05)
  8-10 : Missions, stack, interlocuteurs, périmètre et séniorité clairs.
  5-7  : Description correcte.
  2-4  : Générique.
  0-1  : Quasi vide.

=== BONUS / MALUS CIBLÉS ===
Bonus cumulables, mais garde un total raisonnable :
+1.0 : Éditeur cyber/network/security reconnu ou MSSP très pertinent.
+1.0 : ZTNA / Zero Trust / SASE / firewall / endpoint / MDR explicitement au cœur du poste.
+0.5 : partenaires, channel, EMEA, escalade technique, adoption produit ou support premium.
+0.5 : IA appliquée à la cybersécurité ou à l'exploitation IT.
+0.5 : poste à responsabilité tech clairement IT/cyber/infra (Responsable, Manager, Directeur Technique).
       Peut monter à +1.0 uniquement si le périmètre responsabilité est très clair : équipe, budget,
       architecture, roadmap, exploitation ou sécurité SI. Ne jamais appliquer si le contexte est non-tech.

Malus :
-2 : objectifs commerciaux explicites, quota, closing, pipeline sales.
-2 : architecte/consultant mais ERP/SAP/ServiceNow/MES/finance sans sécurité.
-1 : description vague ou intitulé trop ambigu.
-1 : rôle trop junior/hands-on sans séniorité.

Score final contenu = round(clamp(A1×0.45 + A2×0.25 + A3×0.15 + A4×0.10 + A5×0.05 + bonus - malus, 0, 10))

=== CONSIGNES ===
- Base-toi uniquement sur titre, entreprise, type, plateforme et description fournis.
- Ne donne pas un bon score à un poste à responsabilité si la responsabilité n'est pas dans l'IT/SI/cyber/infra.
- À l'inverse, valorise PLEINEMENT les vrais postes Responsable SI / Team Leader sécu réseau /
  Directeur Technique quand ils sont clairement IT/SI/cyber/infra (priorités 2-4) : 7-9, pas un score timide.
- Justification : 1-2 phrases max, en français, avec les éléments décisifs et bonus/malus si important.
- Appelle submit_relevance_score avec score (int) et reasoning."""


PROMPT_FILE = "scoring_prompt.txt"

# Prompt effectif — initialisé par reload_prompt() en fin de module.
SYSTEM_PROMPT: str = DEFAULT_SYSTEM_PROMPT


def reload_prompt() -> dict:
    """(Re)charge le system prompt depuis config/scoring_prompt.txt, ou le défaut.

    Appelé au chargement du module et par POST /config/reload. Un fichier
    illisible ou quasi vide (< 200 chars) est ignoré — on ne score jamais
    avec un prompt tronqué par accident.
    """
    global SYSTEM_PROMPT
    cfg_file = CONFIG_DIR / PROMPT_FILE
    if cfg_file.is_file():
        try:
            text = cfg_file.read_text(encoding="utf-8").strip()
            if len(text) >= 200:
                SYSTEM_PROMPT = text
                logger.info("System prompt chargé depuis %s (%d chars)", cfg_file, len(text))
                return {"source": str(cfg_file), "chars": len(text), "error": None}
            logger.warning("scoring_prompt.txt trop court (%d chars) — défaut conservé", len(text))
            return {"source": "defaults", "chars": len(DEFAULT_SYSTEM_PROMPT),
                    "error": f"fichier trop court ({len(text)} chars)"}
        except OSError as e:
            logger.warning("scoring_prompt.txt illisible (%s) — défaut conservé", e)
            return {"source": "defaults", "chars": len(DEFAULT_SYSTEM_PROMPT),
                    "error": str(e)}
    SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
    return {"source": "defaults", "chars": len(DEFAULT_SYSTEM_PROMPT), "error": None}


def save_prompt(text: str) -> dict:
    """Écrit le system prompt vers config/scoring_prompt.txt puis le recharge.

    Utilisé par PUT /config/prompt (page Paramètres). Même garde-fou que
    reload_prompt : un prompt < 200 chars est refusé (troncature accidentelle).
    """
    text = (text or "").strip()
    if len(text) < 200:
        raise ValueError(f"prompt trop court ({len(text)} chars, minimum 200)")
    cfg_file = CONFIG_DIR / PROMPT_FILE
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(text, encoding="utf-8")
    return reload_prompt()


def get_prompt() -> dict:
    """Prompt effectif + provenance, pour affichage/édition dans l'UI."""
    cfg_file = CONFIG_DIR / PROMPT_FILE
    return {
        "text": SYSTEM_PROMPT,
        "source": str(cfg_file) if cfg_file.is_file() else "defaults",
        "chars": len(SYSTEM_PROMPT),
        "is_default": SYSTEM_PROMPT == DEFAULT_SYSTEM_PROMPT,
    }


def export_default_prompt(force: bool = False) -> Optional[str]:
    """Écrit le prompt codé vers config/scoring_prompt.txt (amorçage du volume).

    No-op si le fichier existe déjà (sauf force=True).
    """
    cfg_file = CONFIG_DIR / PROMPT_FILE
    if cfg_file.exists() and not force:
        return None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(DEFAULT_SYSTEM_PROMPT, encoding="utf-8")
    return str(cfg_file)


# Chargement initial (fichier de config si présent, sinon défaut).
reload_prompt()


def _build_user_prompt(job: Job) -> str:
    """Construit le user prompt à partir d'un job ORM.

    Omet volontairement la localisation, le salaire et la date — ces axes sont
    gérés en Python (compute_geo_score / compute_salary_score / compute_freshness_score)
    et ne doivent PAS influencer la note de qualité de contenu rendue par le modèle.
    """
    max_chars = settings.get().llm.max_description_chars
    desc = (job.description or "").strip()
    if len(desc) > max_chars:
        desc = desc[:max_chars] + "\n[…description tronquée]"

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
    api_key_name: str        # nom du secret (résolu via settings.get_secret)
    model: str
    limiter: RateLimiter
    headers: Optional[dict] = None

    def is_configured(self) -> bool:
        return bool(settings.get_secret(self.api_key_name))

    def make_client(self) -> Optional[AsyncOpenAI]:
        api_key = settings.get_secret(self.api_key_name)
        if not api_key:
            return None
        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key,
            default_headers=self.headers or {},
        )


def get_providers() -> list[Provider]:
    """Providers ACTIVÉS, dans l'ordre de priorité de settings.llm.providers —
    le 1er configuré est le primaire, les suivants des fallbacks. Reconstruits
    depuis la config courante à chaque appel : un changement via la page
    Paramètres s'applique au prochain scoring, sans restart."""
    llm = settings.get().llm
    providers: list[Provider] = []
    for cfg in llm.providers:
        if not cfg.enabled:
            continue
        cat = PROVIDER_CATALOG.get(cfg.name)
        if cat is None:  # provider inconnu dans un settings.json ancien — skip
            continue
        providers.append(Provider(
            name=cfg.name,
            base_url=cat["base_url"],
            api_key_name=cat["api_key_name"],
            model=cfg.model,
            limiter=_limiter(cfg.name, cfg.rpm),
            headers=cat.get("headers"),
        ))
    return providers


# ============================================================================
# Appel modèle + parsing de la réponse
# ============================================================================

async def _call_provider(
    provider: Provider, client: AsyncOpenAI, job: Job,
    system_prompt: Optional[str] = None,
) -> RelevanceScore:
    """Appel unique à un provider (avec rate-limiting). Retourne le score parsé.

    Lève sur erreur réseau, 4xx/5xx, ou réponse non-conforme — le caller décide
    s'il faut basculer sur le fallback. `system_prompt` permet de tester un
    prompt candidat sans l'enregistrer (banc d'essai de la page Paramètres).

    Note : pas de cache_control ni d'Anthropic-Beta (ces headers/champs sont
    Anthropic-only et causent une erreur sur Groq). System prompt envoyé en
    plain text via la structure OpenAI standard.
    """
    await provider.limiter.wait()

    resp = await client.chat.completions.create(
        model=provider.model,
        max_tokens=512,           # largement suffisant pour {score, reasoning}
        temperature=settings.get().llm.temperature,  # bas pour scores reproductibles
        messages=[
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
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


def _is_retryable(e: Exception) -> bool:
    """429, erreurs réseau, timeouts et 5xx méritent un retry ; le reste non
    (un 400/401/422 réessayé donnera le même résultat)."""
    if isinstance(e, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(e, APIStatusError) and e.status_code >= 500:
        return True
    return False


async def _call_with_retries(
    provider: Provider, client: AsyncOpenAI, job: Job,
    system_prompt: Optional[str] = None,
) -> RelevanceScore:
    """_call_provider + backoff exponentiel (5s, 15s, 45s + jitter).

    Sur les free tiers, un 429 ponctuel est la norme : basculer immédiatement
    sur le fallback gaspillait son quota. On ne bascule qu'après épuisement
    des retries, ou immédiatement sur une erreur non-récupérable.
    """
    retries = settings.get().llm.retries
    for attempt in range(retries):
        try:
            return await _call_provider(provider, client, job, system_prompt)
        except Exception as e:
            if not _is_retryable(e) or attempt == retries - 1:
                raise
            delay = 5.0 * (3 ** attempt) + random.uniform(0.0, 2.0)
            logger.info(
                "[%s] %s pour job %s — retry %d/%d dans %.0fs",
                provider.name, type(e).__name__, job.id,
                attempt + 1, retries - 1, delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")  # la boucle raise toujours avant


async def score_one_job(
    job: Job, system_prompt: Optional[str] = None
) -> tuple[RelevanceScore, str]:
    """Score un job via Groq → fallback OpenRouter si Groq échoue.

    Retourne (score, nom du provider utilisé). `system_prompt` permet un essai
    avec un prompt candidat (banc d'essai) sans toucher au prompt actif.

    Stratégie de fallback :
      - Si Groq répond mal (429, 5xx, parsing) → on tente OpenRouter
      - Si Groq pas configuré (clé absente) → on attaque direct OpenRouter
      - Si aucun provider dispo → RuntimeError
    """
    last_error: Optional[Exception] = None
    providers = get_providers()

    for provider in providers:
        if not provider.is_configured():
            continue

        client = provider.make_client()
        if client is None:
            continue

        try:
            result = await _call_with_retries(provider, client, job, system_prompt)
            # Première fois qu'on bascule sur fallback : on log explicitement
            # pour diagnostique des dépassements de quota.
            if provider.name != "groq":
                logger.info(
                    "Scoring job %s via fallback %s (provider primary indisponible)",
                    job.id, provider.name,
                )
            return result, provider.name
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
        f"(configured: {[p.name for p in providers if p.is_configured()]}; "
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
    llm = settings.get().llm
    available = [p.name for p in get_providers() if p.is_configured()]
    if not available:
        logger.warning(
            "Aucune clé API configurée (GROQ_API_KEY ou OPENROUTER_API_KEY) — "
            "skipping scoring for %d jobs", len(job_ids),
        )
        return

    semaphore = asyncio.Semaphore(llm.concurrency)

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
                result, _provider = await score_one_job(snapshot)
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
                    job.score_freshness = compute_freshness_score(
                        job.date_posted, fallback=job.scraped_at
                    )

                # relevance_score final = pondération des 4 axes (formule dans enrichment.py)
                job.relevance_score = compute_final_score(
                    content=base,
                    geo=job.score_geo,
                    salary=job.score_salary,
                    freshness=job.score_freshness,
                )
                job.relevance_reasoning = result.reasoning

    logger.info(
        "Scoring %d jobs (providers=%s, concurrency=%d)",
        len(job_ids), available, llm.concurrency,
    )
    await asyncio.gather(*(process(jid) for jid in job_ids))
    logger.info("Scoring pass complete")


# ============================================================================
# Banc d'essai du prompt — offres fictives calibrées + scoring ad hoc
# ============================================================================

# Fixtures couvrant les cas limites de la rubrique : un cas parfait (attendu 8-10),
# un piège "faux ami" (sécurité incendie, attendu 0-2), un commercial déguisé
# (attendu 0-2) et un cas moyen légitime (attendu 5-8). Si un prompt candidat
# inverse ces attentes, il est cassé.
SAMPLE_JOBS: list[dict] = [
    {
        "key": "tam_cyber",
        "label": "Cas parfait — TAM cybersécurité (attendu 8-10)",
        "expected": "8-10",
        "title": "Senior Technical Account Manager - Cybersecurity",
        "company": "Fortinet",
        "job_type": "fulltime",
        "platform": "linkedin",
        "description": (
            "Nous recherchons un Senior Technical Account Manager pour accompagner nos "
            "partenaires et clients stratégiques EMEA. Vous serez le référent technique "
            "post-vente : escalades critiques, architecture firewall/SD-WAN, déploiements "
            "ZTNA et Zero Trust, adoption produit (NGFW, EDR/XDR, SASE). Profil : 8+ ans en "
            "sécurité réseau, expérience firewalls, VPN, endpoint. Poste hybride, "
            "management d'escalade N2/N3, interlocuteur CISO/DSI."
        ),
    },
    {
        "key": "faux_ami_ssi",
        "label": "Piège faux ami — sécurité incendie (attendu 0-2)",
        "expected": "0-2",
        "title": "Chef de Service Sécurité Incendie SSIAP 3",
        "company": "Centre Commercial Grand Var",
        "job_type": "fulltime",
        "platform": "indeed",
        "description": (
            "Rattaché au directeur technique, vous encadrez l'équipe de sécurité incendie "
            "du site (SSIAP 1 et 2). Missions : gestion du PC sécurité, levées de doute, "
            "rondes de prévention, tenue du registre de sécurité, relations avec la "
            "commission de sécurité. Diplôme SSIAP 3 exigé, habilitation électrique "
            "appréciée. Poste en présentiel."
        ),
    },
    {
        "key": "commercial_deguise",
        "label": "Piège commercial — account manager IT (attendu 0-2)",
        "expected": "0-2",
        "title": "Account Manager - Solutions IT",
        "company": "ESN régionale",
        "job_type": "fulltime",
        "platform": "wttj",
        "description": (
            "Rejoignez notre équipe commerciale ! Vous développez un portefeuille de "
            "clients IT : prospection, qualification des besoins, réponse aux appels "
            "d'offres, négociation et closing. Objectifs trimestriels ambitieux, "
            "commissions non plafonnées, véhicule de fonction. Profil commercial "
            "chasseur, connaissance du secteur informatique appréciée."
        ),
    },
    {
        "key": "resp_si_pme",
        "label": "Cas moyen — Responsable SI PME (attendu 5-8)",
        "expected": "5-8",
        "title": "Responsable des Systèmes d'Information",
        "company": "PME industrielle (300 salariés)",
        "job_type": "fulltime",
        "platform": "apec",
        "description": (
            "Rattaché à la direction générale, vous pilotez le SI de l'entreprise : "
            "management d'une équipe de 4 personnes (infra, support, ERP), budget IT, "
            "roadmap de modernisation (migration cloud, renforcement cybersécurité, "
            "plan de reprise d'activité), relation avec les prestataires. Expérience "
            "requise : 8 ans en infrastructure/réseaux dont 3 en management. "
            "Télétravail partiel possible (2j/semaine)."
        ),
    },
]


async def score_adhoc(
    fields: dict, prompt_override: Optional[str] = None
) -> dict:
    """Note une offre FICTIVE (jamais persistée) — banc d'essai de la page Paramètres.

    `prompt_override` permet de tester un prompt candidat AVANT de l'enregistrer.
    Consomme un vrai appel LLM (~3 200 tokens sur le quota Groq).
    """
    job = Job(
        id=0,
        platform=str(fields.get("platform") or "test"),
        job_url="adhoc://prompt-test",
        title=str(fields.get("title") or "").strip() or "Sans titre",
        company=str(fields.get("company") or "").strip() or None,
        job_type=str(fields.get("job_type") or "").strip() or None,
        description=str(fields.get("description") or "").strip() or None,
    )
    t0 = time.monotonic()
    result, provider = await score_one_job(job, system_prompt=prompt_override)
    return {
        "score": result.score,
        "reasoning": result.reasoning,
        "provider": provider,
        "prompt_used": "candidat (non sauvegardé)" if prompt_override else "actif",
        "duration_s": round(time.monotonic() - t0, 1),
    }


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
