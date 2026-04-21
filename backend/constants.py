"""Shared constants — search terms catalog and geographic profiles."""
from __future__ import annotations

# Canonical search terms targeting senior cybersecurity / infrastructure / IT leadership roles.
# Used as the default search_terms on SearchRequest and as the default in the UI textarea.
SEARCH_TERMS: list[str] = [
    "Ingénieur Réseau Sécurité",
    "Ingénieur Cybersécurité",
    "Ingénieur Sécurité Informatique",
    "Ingénieur Infrastructure Sécurité",
    "Network Security Engineer",
    "Cybersecurity Engineer",
    "Administrateur Réseau Sécurité",
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
    "Auditeur Sécurité Senior",
]


# Geographic scrape profiles.
#   location   — free-form location string sent to JobSpy / most aggregators.
#   country    — used for JobSpy's country_indeed parameter.
#   region     — short code persisted on the Job row (used for filtering / display).
#   flag       — emoji shown in the UI.
#   cost_coef  — simplified cost-of-living coefficient used to compute "effective salary".
#                A value <1 means purchasing power is lower than France baseline.
#                (Base = France = 1.00.)
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
