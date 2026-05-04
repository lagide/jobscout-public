"""Constantes partagées — catalogue de termes de recherche et profils géographiques."""
from __future__ import annotations

import re
from typing import Optional

# ============================================================================
# Blacklist de titres — offres skippées AVANT stockage et scoring
# ============================================================================
# Les patterns ci-dessous matchent des intitulés de poste qui ne correspondent
# PAS au profil senior IT/cyber/management ciblé par cette app. Tout titre
# matché est ignoré : aucune insertion DB, aucun appel OpenRouter.
# Économise base + facture API + bruit dans le pipeline.
#
# Modifie librement la liste — un rebuild backend n'est PAS requis (les regex
# sont compilées au boot, mais comme constants.py est importé une fois, il faut
# tout de même redémarrer le container backend après modification).

BLACKLIST_TITLE_PATTERNS: list[str] = [
    # --- Sales / business ---
    r"commercial(e|es)?",
    r"account\s+executive",
    r"\bsales\b",
    r"business\s+(developer|development|dev)",
    # Variantes "X d'affaires" : capte chargé / ingénieur / responsable / directeur / délégué
    r"(charg[ée]|ing[ée]nieur|responsable|directeur|directrice|d[ée]l[ée]gu[ée]|attach[ée])\s+d['’]?\s*affaires?",
    # Conseil / gestion d'entreprise (consultants business pas IT)
    # Patterns volontairement précis pour ne PAS matcher "Expert en Gestion de crise" (cyber)
    r"gestion\s+d['’]?\s*entreprise",
    r"strat[ée]gie\s+d['’]?\s*entreprise",
    r"expert\s+en\s+gestion\s+d['’]?\s*entreprise",
    r"conseil(ler|lere|ler[èe]re)?\s+(commercial|client[èe]le)",
    # --- Apprentissage / alternance / stage ---
    r"alternan(t|te|ce)",
    r"apprenti(e)?",
    r"stagiaire",
    r"\bstage\b",
    # --- Postes non-cadre / techniciens / support N1-N2 ---
    r"technicien(ne)?(s)?",
    r"help[\s\-]*desk",
    r"hotliner?",
    r"op[ée]rateur(rice)?",
    r"support\s+(technique|informatique|utilisateur|niveau\s+[12])",
    r"niveau\s+[12]",
    r"first[\s\-]*line",
    r"second[\s\-]*line",
]

# Abréviations matchées en case-sensitive (éviter faux positifs sur 2-3 lettres)
BLACKLIST_TITLE_ABBR: list[str] = ["AE", "SDR", "BDR", "N1", "N2"]

_BL_FULL_RE = re.compile(
    r"\b(?:" + "|".join(BLACKLIST_TITLE_PATTERNS) + r")\b",
    re.IGNORECASE,
)
_BL_ABBR_RE = re.compile(r"\b(?:" + "|".join(BLACKLIST_TITLE_ABBR) + r")\b")


def is_title_blacklisted(title: Optional[str]) -> bool:
    """True si le titre matche un pattern blacklist (rôle non pertinent).

    Utilisé par scraper.scrape_and_store pour skip l'offre AVANT tout traitement
    (pas d'insertion DB, pas de scoring). Permet aussi la purge rétroactive des
    offres déjà en base via cleanup_database.
    """
    if not title:
        return False
    return bool(_BL_FULL_RE.search(title) or _BL_ABBR_RE.search(title))


# Termes de recherche canoniques ciblant les rôles seniors cybersécurité / infrastructure /
# leadership IT. Utilisés comme valeur par défaut de SearchRequest.search_terms et dans
# le textarea du frontend.
SEARCH_TERMS: list[str] = [
    "Ingénieur Réseau Sécurité",
    "Ingénieur Cybersécurité",
    "Ingénieur Sécurité Informatique",
    "Ingénieur Infrastructure Sécurité",
    "Network Security Engineer",
    "Cybersecurity Engineer",
    "Architecte Réseau Sécurité",
    "Consultant Réseau Sécurité",
    "Expert Cybersécurité",
    "Ingénieur SOC",
    "Analyste SOC Senior",
    "Ingénieur Sécurité Cloud",
    "Cloud Security Engineer",
    "Ingénieur SIEM",
    "Ingénieur EDR",
    "Ingénieur Zero Trust",
    "Référent Sécurité",
    "Lead Sécurité",
    "Technical Account Manager",
    "TAM Cybersécurité",
    "Responsable Sécurité Informatique",
    "Responsable Infrastructure",
    "Responsable Technique",
    "Responsable Système Réseau",
    "Directeur Technique",
    "Directeur des Systèmes d'Information",
    "DSI",
    "RSSI",
    "RSSI Adjoint",
    "CISO",
    "Head of Security",
    "Security Manager",
    "IT Manager",
    "Infrastructure Manager",
    "Chef de Projet Sécurité",
    "Chef de Projet Infrastructure",
    "Responsable Pôle Sécurité",
    "Team Lead Réseau Sécurité",
    "Lead Technique Sécurité",
    "Architecte Sécurité",
    "Architecte Technique",
    "Solutions Architect Security",
    "Pre-Sales Sécurité",
    "Ingénieur Avant-Vente Sécurité",
    "Security Consultant Senior",
    "Consultant SSI Senior",
]


# Profils géographiques de scrape.
#   location   — chaîne libre envoyée à JobSpy / connecteurs comme valeur de localisation.
#   country    — utilisé pour le paramètre country_indeed de JobSpy.
#   region     — code court persisté sur la ligne Job (utilisé pour filtres / affichage).
#   flag       — emoji affiché dans l'UI.
#   cost_coef  — coefficient simplifié de coût de la vie pour le "salaire effectif".
#                <1 = pouvoir d'achat inférieur à la France. Base : France = 1.00.
GEO_PROFILES: dict[str, dict] = {
    "France": {
        "location": "France",
        "country": "France",
        "region": "FR",
        "flag": "🇫🇷",
        "cost_coef": 1.00,
    },
    "Suisse": {
        "location": "Switzerland",
        "country": "Switzerland",
        "region": "CH",
        "flag": "🇨🇭",
        "cost_coef": 0.75,
    },
    "Luxembourg": {
        "location": "Luxembourg",
        "country": "Luxembourg",
        "region": "LU",
        "flag": "🇱🇺",
        "cost_coef": 0.85,
    },
    "Belgique": {
        "location": "Belgium",
        "country": "Belgium",
        "region": "BE",
        "flag": "🇧🇪",
        "cost_coef": 0.95,
    },
    "Canada (QC)": {
        "location": "Montreal",
        "country": "Canada",
        "region": "CA-QC",
        "flag": "🇨🇦",
        "cost_coef": 0.80,
    },
    "La Réunion": {
        "location": "La Réunion",
        "country": "France",
        "region": "RE",
        "flag": "🇷🇪",
        "cost_coef": 1.00,
    },
    "Martinique": {
        "location": "Martinique",
        "country": "France",
        "region": "MQ",
        "flag": "🇲🇶",
        "cost_coef": 1.00,
    },
}

# Default profile for single-profile scrapes.
DEFAULT_PROFILE: str = "France"
