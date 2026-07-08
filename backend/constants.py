"""Constantes partagées — catalogue de termes de recherche, profils géographiques, blacklists.

Config chaude (2026-06-10) : la blacklist peut être surchargée par un fichier
`config/blacklist.json` monté en volume (CONFIG_DIR, défaut /app/config) :

    {
      "title_patterns": ["regex1", "regex2", ...],
      "title_abbr":     ["AE", "SDR", ...],
      "companies":      ["symrise", ...]
    }

S'il est présent, il REMPLACE les listes codées ci-dessous (pas de fusion, pour
pouvoir aussi retirer un pattern). Rechargeable à chaud via POST /config/reload —
fini le rebuild d'image pour ajouter un pattern.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))

# ============================================================================
# Blacklist de titres — offres skippées AVANT stockage et scoring
# ============================================================================
# Les patterns ci-dessous matchent des intitulés de poste qui ne correspondent
# PAS au profil senior IT/cyber/management ciblé par cette app. Tout titre
# matché est ignoré : aucune insertion DB, aucun appel OpenRouter.
# Économise base + facture API + bruit dans le pipeline.
#
# Note : redémarrer le container backend après modification — les regex sont
# compilées au chargement du module.

BLACKLIST_TITLE_PATTERNS: list[str] = [
    # ---------------------------------------------------------------- Sales / commercial
    r"commercial(e|es)?",
    r"account\s+executive",
    r"\bsales\b",
    r"business\s+(developer|development|dev)",
    r"(charg[ée]|ing[ée]nieur|responsable|directeur|directrice|d[ée]l[ée]gu[ée]|attach[ée])\s+d['’]?\s*affaires?",
    r"gestion\s+d['’]?\s*entreprise",
    r"strat[ée]gie\s+d['’]?\s*entreprise",
    r"expert\s+en\s+gestion\s+d['’]?\s*entreprise",
    r"conseil(ler|lere|ler[èe]re)?\s+(commercial|client[èe]le)",
    r"key\s+account\s+manager",
    r"relationship\s+manager",
    r"customer\s+(support|success)\s+(specialist|manager|representative|engineer)(?!.*(?:security|cyber|technical|technique|s[ée]curit[ée]|ztna|sase|firewall))",
    r"portfolio\s+marketing",
    r"marketing\s+manager",
    r"go[\s\-]?to[\s\-]?market",
    r"\bGTM\b\s+engineer",
    r"market\s+development",
    r"inside\s+sales",
    r"field\s+operations(?!\s+engineer)",
    r"pricing\s+(manager|analyst|engineer|specialist)",
    r"strategic\s+(partner|consultant)\s+manager",
    r"global\s+partner\s+strategy",

    # ---------------------------------------------------------------- Apprentissage / alternance / stage / junior
    r"alternan(t|te|ce)",
    r"apprenti(e)?",
    r"\bapprentice\b",
    r"stagiaire",
    r"\bstage\b",
    r"\bintern\b",
    r"\bpost[\s\-]?doc(?:torat|toral)?\b",
    r"\bPh\.?D\b",
    r"\bth[èe]se\b",
    r"doctorant(e)?",
    r"junior\s+(?:specialist|engineer|developer|analyst|project\s+engineer)",
    # \bjunior\b standalone is risky for legit "Junior to mid" — limit to anchored variants above.

    # ---------------------------------------------------------------- Techniciens / support N1-N2 / non-cadre
    r"technicien(ne)?(s)?",
    r"^(?!.*\b(?:manager|responsable|lead|chef|head)\b).*help[\s\-]*desk",
    r"hotliner?",
    r"op[ée]rateur(rice)?",
    r"^(?!.*\b(?:manager|responsable|lead|chef|head)\b).*support\s+(technique|informatique|utilisateur|niveau\s+[12])",
    r"niveau\s+[12]",
    r"first[\s\-]*line",
    r"second[\s\-]*line",
    r"install(ation)?\s+tech",

    # ---------------------------------------------------------------- HSE / Qualité / Sécurité physique / incendie / sûreté indus
    r"\bH(?:Q)?SE\b",
    r"\bQ(?:H)?SE\b",
    r"\bSSE\b",
    r"hygi[èe]ne.{0,8}s[ée]curit[ée]",
    r"s[ée]curit[ée].{0,8}incendie",
    r"pr[ée]vention(?:niste)?\s+incendie",
    r"\bcoordinateur\s+SSI\b",
    r"agent\s+de\s+s[ée]curit[ée]",
    r"lieutenant\s+s[ée]curit[ée]",
    r"chef\s+de\s+(?:service\s+(?:de\s+)?)?s[ée]curit[ée]\s+incendie",
    r"chef\s+de\s+poste",
    r"chef\s+de\s+s[ée]curit[ée](?!\s+(?:syst|info|num|du\s+SI|SI\b))",
    r"s[uû]ret[ée]\s+(?:nucl[ée]aire|d['’]?exploitation|de\s+fonctionnement|incendie|machines?|et\s+s[ée]curit[ée])",
    r"surveillance\s+s[uû]ret[ée]",
    r"safety\s+(engineer|manager|assurance|coordinator|specialist|risk)",
    r"\bRAMS\b",
    r"automaticien(?:ne)?",
    r"safety[\s\-]+critical",
    r"responsable\s+s[uû]ret[ée](?!\s+(?:syst|info|num|SI\b|du\s+SI))",
    # "Responsable Sécurité Opérationnelle" SEULE peut être cyber (cf. Filhet-Allard) — on garde uniquement les contextes clairement non-IT.
    r"responsable\s+s[ée]curit[ée]\s+et\s+(?:performance|technique|incendie|sant[ée])(?!.*(?:SI|cyber|info))",

    # ---------------------------------------------------------------- Embedded / électronique / industriel automation
    r"\bFPGA\b",
    r"\bASIC\b",
    r"\bUVM\b",
    r"\bADAS\b",
    r"\bMES\b\s*/?\s*AVEVA",
    r"\bAVEVA\b",
    r"\bSCADA\b",
    r"\bIVVQ?\b",
    r"\bI\s*&\s*C\b",
    r"\bEIA\b",
    r"\bHWIL\b",
    r"\bSATCOM\b",
    r"micro.?[ée]lectronique",
    r"d[ée]mant[èe]lement",
    r"conception\s+(?:RTL|VHDL|Verilog|FPGA|ASIC|[ée]lectronique)",
    r"placement\s+(?:et\s+)?routage",
    r"routage\s+(?:[ée]lectronique|de\s+cartes?)",
    r"hardware\s+(?:engineer|design)",
    r"firmware\s+engineer",
    r"linux\s+embarqu[ée]",
    r"d[ée]veloppeur(?:se)?\s+(?:logiciel|embarqu[ée])(?!\s+(?:cloud|backend|API))",
    r"\bIngenieur\s+Radio\b",
    r"radio[\s\-]+propagation",
    r"d[ée]fectivit[ée]",
    r"qualification\s+(?:logiciel|sous[\s\-]?syst[èe]me|process|processus)",
    r"\bAIT\b\s+\(",   # AIT = Assembly Integration Test (spatial), pattern with paren to avoid AIT false-pos
    r"system(s)?\s+autonomes?",
    r"systeme\s+guidage",
    r"infra[\s\-]?rouge",

    # ---------------------------------------------------------------- Maintenance industrielle / facility management
    r"reliability\s+maintenance",
    r"\bRME\b",
    r"\bDCEO\b",
    r"\bMCO\b\s+\(?(?!.*(?:SI|cloud|info|app|logiciel))",  # MCO unless IT context
    r"maintenance\s+industrielle",
    r"maintenance\s+a[ée]ronautique",
    r"maintenance\s+multi[\s\-]?technique",
    r"facility\s+management",
    r"\bFM\b\s+multi[\s\-]?technique",
    r"superviseur\s+(?:de\s+)?maintenance",
    r"chef\s+d['’]?\s*[ée]quipe\s+poseur",
    r"ing[ée]nieur\s+m[ée]thodes?\s+maintenance",
    r"travaux\s+neufs",
    r"chef\s+de\s+carri[èe]res?",
    r"responsable\s+fabrication",
    r"ing[ée]nieur\s+travaux",
    r"ing[ée]nieur\s+maintenance(?!\s+(?:logicielle|applicative|informatique|SI|IT))",
    r"chef\s+d['’]?\s*atelier",
    r"responsable\s+ateliers?",
    r"responsable\s+(?:m[ée]canique|production|industriel)",
    r"responsable\s+(?:de\s+)?d[ée]partement\s+syst[èe]mes?\s+m[ée]caniques?",
    r"adjoint\s+directeur\s+industriel",
    r"directeur\s+technique\s+/?\s*maintenance\s+a[ée]ronautique",
    r"directeur\s+de\s+programme\s+(?:industriel|a[ée]ronautique)",

    # ---------------------------------------------------------------- Pharma / agro / chimie / industrie non-IT
    r"assurance\s+qualit[ée](?:\s+(?:produit|agro|aliment))?",
    r"qualification\s+(?:processus|process|usine)",
    r"pet\s*food",
    r"g[ée]n[ée]tique\s+(?:porcine|animale|v[ée]g[ée]tale)",
    r"ing[ée]nieur\s+(?:assurance\s+)?qualit[ée]",
    r"animateur\s+qualit[ée]",
    r"r[ée]f[ée]rent\s+qualit[ée]",
    r"laboratoires?\s+du\s+centre\s+technique",
    r"d[ée]chets?\s+dangereux",

    # ---------------------------------------------------------------- BTP / immobilier / génie civil / construction
    r"g[ée]nie\s+(?:civil|climatique|[ée]lectrique|m[ée]canique)",
    r"\bBTP\b",
    r"travaux\s+ferroviaires?",
    r"cat[ée]naire",
    r"infrastructure\s+ferroviaire",
    r"chef\s+de\s+chantier",
    r"travaux\s+publics",
    r"ing[ée]nieur\s+(?:b[âa]timent|construction|immobilier)",
    r"responsable\s+d['’]?\s*unit[ée]\s+technique",
    r"contr[ôo]le\s+technique\s+construction",
    r"directeur\s+d['’]?\s*agence\s+contr[ôo]le",
    r"chef\s+de\s+projet\s+[ée]lectricit[ée]",
    r"responsable\s+d['’]?\s*[ée]quipe\s+[ée]lectricit[ée]",
    r"placement\s+routage",
    r"ing[ée]nieur\s+pricing",
    r"chef\s+de\s+produit\s+p[ée]age",

    # ---------------------------------------------------------------- RH / juridique / compliance non-cyber
    r"\bHR\s+(?:project\s+manager|business\s+partner|generalist|operations|specialist|director)",
    r"\bHRBP\b",
    r"charg[ée](?:e)?\s+(?:de\s+)?recrutement",
    r"talent\s+acquisition",
    r"compliance\s+officer",
    r"sanctions\s+compliance",
    r"export\s+control",
    r"\bjuriste\b",
    r"conseiller(?:[èe]re)?\s+(?:patrimonial|patrimoine|[ée]conomie\s+sociale)",
    r"ing[ée]nieur\s+patrimonial",
    r"assistant(?:e)?\s+(?:de\s+)?direction",
    r"\bHRBP\b",
    r"adjoint\s+au?\s+responsable\s+technique\s+-?\s*centre\s+de\s+simulation",
    r"r[ée]f[ée]rent\s+logement",
    r"r[ée]f[ée]rent(?:e)?\s+sant[ée]",
    r"r[ée]f[ée]rent(?:e)?\s+d['’]?\s*endoscopie",

    # ---------------------------------------------------------------- Langues/secteurs non cibles observés BE/CH/LU 2026-06
    r"beveiligingsconsultant",
    r"preventieadviseur",
    r"aantrekkelijk\s+salaris",
    r"bedrijfswagen",
    r"technisch\s+directeur",

    # ---------------------------------------------------------------- Dev / ERP / account management / faux positifs JobSpy 2026-06
    r"full[\s\-]?stack",
    r"software\s+(engineer|developer)",
    r"senior\s+software",
    r"d[ée]veloppeur(?:se)?(?!.*(?:s[ée]curit[ée]|cyber|firewall|ztna|sase))",
    r"developer(?!.*(?:security|cyber|firewall|ztna|sase))",
    r"accounting\s+associate",
    r"(?<!technical\s)account[\s\-]?manager",
    r"key[\s\-]?account[\s\-]?manager",
    r"responsable\s+grands?\s+comptes?",
    r"responsable\s+bureau\s+d['’]?[ée]tudes?",
    r"bid\s+manager",
    r"responsable\s+d['’]?offres?",
    r"m[ée]decin\s+conseil",
    r"manager\s+relation\s+assur[ée]s",
    r"[ée]lectricien(?:ne)?",
    r"architecte\s+(?:technique\s+)?solution\s+(?:ifs|erp|service\s*now|servicenow)",
    r"architecte\s+solutions?\s+mes",
    r"banque\s+priv[ée]e",
    r"sap\s+(?:pm|ps|consultant)",
    r"non[\s\-]?financial\s+risk",
    r"treasury\s+advisory",
    r"data\s+quality\s+consultant",
    r"supply\s+chain\s+consultant",
    r"consulting\s+actuary",
    r"manufacturing\s+consultant",
    r"partnership\s+development",
    r"erp\s+finance",
    r"data\s+engineer",
    r"ai\s+architect(?!.*(?:security|cyber|s[ée]curit[ée]))",

    # ---------------------------------------------------------------- Comptable / finance entreprise
    r"expert[\s\-]?comptable",
    r"expertise\s+comptable",
    r"analyste\s+financier",
    r"\bFP\s*&\s*A\b",
    r"contr[ôo]leur(?:se)?\s+(?:de\s+)?gestion",
    r"audit(?:eur|rice)\s+(?:financier|comptable)",
    r"equity\s+research",
    r"trade\s*(?:&|and)\s*cash\s+bank",
    r"responsable\s+(?:de\s+)?portefeuille\s+expertise\s+comptable",

    # ---------------------------------------------------------------- Médical / social / éducation pédagogie
    r"psychomotricien(ne)?",
    r"endoscopie",
    r"chirurgie",
    r"infirmier(?:e|i[èe]re)?",
    r"travailleur\s+social",
    r"r[ée]f[ée]rent\s+logement",
    r"ing[ée]nieur(?:e)?\s+p[ée]dagogique",
    r"directeur(?:trice)?\s+(?:du\s+)?plateau\s+technique\s+-?\s*bloc",

    # ---------------------------------------------------------------- Restauration / retail / hôtellerie / loisirs
    r"chauffeur[\s\-]?livreur(?:se)?",
    r"chauffeur(?:[\s\-]?livreur)?",
    r"vendeur(?:se)?",
    r"camping",
    r"h[ôo]tellerie\s+de\s+plein\s+air",
    r"superviseur\s+(?:de\s+)?camping",
    r"chef\s+de\s+cuisine",
    r"directeur(?:trice)?\s+d['’]?\s*usine",
    r"directeur(?:trice)?\s+d['’]?\s*agence(?!\s+(?:IT|informatique|num[ée]rique|digital))",
    r"directeur(?:trice)?\s+(?:de\s+)?restaurant",
    r"directeur\s+de\s+programme(?!\s+(?:IT|informatique|num[ée]rique|digital))",
    r"responsable\s+(?:technique\s+)?(?:et\s+)?(?:de\s+)?maintenance\s+(?:en\s+)?h[ôo]tellerie",
    r"agent\s+(?:qualifi[ée]\s+)?de\s+maintenance(?:\s+en\s+h[ôo]tellerie)?",
    r"responsable\s+laboratoires?",
    r"directeur\s+technique\s+d[ée]chets",

    # ---------------------------------------------------------------- Spontaneous / divers
    r"candidature\s+spontan[ée]e",
    r"spontaneous\s+application",
    r"\#nextstep",

    # ---------------------------------------------------------------- Bénévolat
    r"m[ée]c[ée]nat\s+de\s+comp[ée]tences?",
    r"b[ée]n[ée]voles?",

    # ---------------------------------------------------------------- Renfort 2026-06-08 (bruit observé après refonte priorités)
    # Finance / compta (trous EN + FR)
    r"financial\s+analyst",
    r"tr[ée]sorerie",
    r"fond[ée]?[\s\-]+de[\s\-]+pouvoir",
    r"administratif\s+et\s+financier",
    r"charg[ée]e?\s+d['’]?\s*investissement",
    r"comptes?\s+fournisseurs?",
    r"business\s+director",
    # Achats / sourcing
    r"sourcing\s+specialist",
    # Immobilier / logement social
    r"copropri[ée]t[ée]s?",
    r"bailleur\s+social",
    # RH / juridique (trous)
    r"\bHR\s+assistant\b",
    r"service\s+juridique",
    r"droit\s+de\s+la\s+protection\s+sociale",
    # Logistique / opérations non-IT
    r"responsable\s+(?:des\s+)?op[ée]rations(?!.*(?:\bIT\b|\bSI\b|cyber|informatique|num[ée]rique|cloud|data\s*cent|s[ée]curit))",
    r"(?:responsable|gestionnaire|chef\s+d['’]?\s*[ée]quipe|[ée]quipe)\s+(?:de\s+)?(?:la\s+)?logistique",
    # Bâtiment / agence non-IT (trous)
    r"responsable\s+technique\s+b[âa]timent",
    r"responsable\s+d['’]?\s*agence(?!\s+(?:IT|informatique|num[ée]rique|digital|cyber|SI\b))",
    # Dev pur / management ingénierie software (hors sécu/réseau/infra)
    r"software\s+(?:modeling|design|test|quality|validation|integration|simulation)\s+(?:engineer|developer)",
    r"engineering\s+manager(?!.*(?:security|cyber|network|s[ée]curit|firewall|ztna|sase|infrastructure|r[ée]seau))",
    # Commercial / consulting déguisé
    r"client\s+(?:engagement|value)\s+partner",
    r"\bengagement\s+manager\b(?!.*(?:security|cyber|s[ée]curit))",
]

# Abréviations matchées en case-sensitive (éviter faux positifs sur 2-3 lettres)
BLACKLIST_TITLE_ABBR: list[str] = ["AE", "SDR", "BDR", "N1", "N2", "KAM", "HSE", "QHSE", "QSE", "HRBP"]

# Regex compilées par reload_blacklist() (appelé en fin de module) — depuis
# config/blacklist.json si présent, sinon depuis les défauts ci-dessus.
_BL_FULL_RE: re.Pattern
_BL_ABBR_RE: re.Pattern


def is_title_blacklisted(title: Optional[str]) -> bool:
    """True si le titre matche un pattern blacklist (rôle non pertinent).

    Utilisé par scraper.scrape_and_store pour skip l'offre AVANT tout traitement
    (pas d'insertion DB, pas de scoring). Permet aussi la purge rétroactive des
    offres déjà en base via scheduler.cleanup_database et purge_blacklisted.py.
    """
    if not title:
        return False
    return bool(_BL_FULL_RE.search(title) or _BL_ABBR_RE.search(title))


# ============================================================================
# Blacklist d'entreprises — offres skippées même si le titre passe la blacklist
# ============================================================================
# Cas d'usage : entreprises qui ne postent essentiellement que des rôles non-IT
# (industriel, BTP, facility, agro), même si certains titres ambigus pourraient
# faussement matcher le profil. Liste configurable via EXCLUDED_COMPANIES dans .env
# (séparée par virgule), fusionnée avec quelques constantes hardcodées.

# Entreprises observées sur ~480 offres scorées comme NEVER-IT (raisonnable à hardcoder).
_HARDCODED_COMPANY_BLACKLIST: set[str] = {
    # Industriel / agro / chimie (jamais ou quasi jamais d'IT senior visible)
    "symrise", "adm", "axiom la génétique française", "tereos", "latécoère",
    "compagnie des alpes", "euroapi", "ecolab", "lynred", "synthron",
    # Facility / maintenance (les filiales, pas la maison-mère)
    "rentokil initial", "rentokil initial france", "colisee", "best western",
    "flower campings", "magora", "eqiom, a crh company",
    "vinci facilities",  # NB: PAS "vinci" tout court (la maison-mère a une vraie DSI)
    "panorama", "ortec group",
    # BTP / immobilier
    "icade", "eiffage génie civil", "eiffage énergie systèmes", "cbre",
    "btp consultants",
    # Restauration / retail (cas isolés)
    "mcdonald's", "e.leclerc varennes sur seine", "leclerc le poire sur vie",
    "le petit souk",
    # NB: PAS "Naval Group" (ratio 80% IT sur les offres vues),
    #     PAS "MBDA" / "Airbus" / "Thales" (vraies DSI),
    #     PAS cabinets recrutement IT (Manpower, Crit, Antal, Davidson…) — postent parfois de vrais rôles cibles
}


def _load_env_companies() -> set[str]:
    """Charge EXCLUDED_COMPANIES depuis l'env (séparées par virgule)."""
    raw = os.getenv("EXCLUDED_COMPANIES", "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


# Peuplé par reload_blacklist() (fin de module).
_COMPANY_BLACKLIST: set[str] = set()


def is_company_blacklisted(company: Optional[str]) -> bool:
    """True si l'entreprise est dans la blacklist (case-insensitive, exact match)."""
    if not company:
        return False
    return company.strip().lower() in _COMPANY_BLACKLIST


# ============================================================================
# Config chaude — chargement / rechargement de la blacklist depuis CONFIG_DIR
# ============================================================================

BLACKLIST_FILE = "blacklist.json"


def reload_blacklist() -> dict:
    """(Re)compile les blacklists depuis config/blacklist.json, ou les défauts.

    Appelé au chargement du module et par POST /config/reload. Si le fichier
    existe mais est invalide (JSON cassé, regex incompilable), on GARDE la
    config courante et on remonte l'erreur dans la réponse — jamais de
    blacklist vide par accident.
    """
    global _BL_FULL_RE, _BL_ABBR_RE, _COMPANY_BLACKLIST

    patterns = list(BLACKLIST_TITLE_PATTERNS)
    abbr = list(BLACKLIST_TITLE_ABBR)
    companies = set(_HARDCODED_COMPANY_BLACKLIST)
    source = "defaults"
    error: Optional[str] = None

    cfg_file = CONFIG_DIR / BLACKLIST_FILE
    if cfg_file.is_file():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            patterns = [str(p) for p in data.get("title_patterns", patterns)]
            abbr = [str(a) for a in data.get("title_abbr", abbr)]
            companies = {str(c).strip().lower() for c in data.get("companies", companies)}
            source = str(cfg_file)
        except (ValueError, OSError) as e:
            error = f"{type(e).__name__}: {e}"
            logger.warning("blacklist.json illisible (%s) — défauts conservés", error)
            patterns, abbr = list(BLACKLIST_TITLE_PATTERNS), list(BLACKLIST_TITLE_ABBR)
            companies = set(_HARDCODED_COMPANY_BLACKLIST)

    # Liste vide → regex qui ne matche JAMAIS. Sans ce garde, "|".join([]) donne
    # \b(?:)\b qui matche n'importe quel mot → blacklist totale par accident.
    _NEVER = r"(?!x)x"

    def _union(parts: list[str], flags: int = 0) -> re.Pattern:
        if not parts:
            return re.compile(_NEVER)
        return re.compile(r"\b(?:" + "|".join(parts) + r")\b", flags)

    try:
        full_re = _union(patterns, re.IGNORECASE)
        abbr_re = _union(abbr)
    except re.error as e:
        error = f"regex invalide: {e}"
        logger.warning("Blacklist non rechargée (%s) — config courante conservée", error)
        return {"source": source, "error": error,
                "title_patterns": None, "companies": None}

    _BL_FULL_RE = full_re
    _BL_ABBR_RE = abbr_re
    _COMPANY_BLACKLIST = companies | _load_env_companies()
    logger.info(
        "Blacklist chargée depuis %s — %d patterns, %d abréviations, %d entreprises",
        source, len(patterns), len(abbr), len(_COMPANY_BLACKLIST),
    )
    return {
        "source": source,
        "error": error,
        "title_patterns": len(patterns),
        "title_abbr": len(abbr),
        "companies": len(_COMPANY_BLACKLIST),
    }


def get_blacklist() -> dict:
    """Contenu effectif de la blacklist (fichier si présent, sinon défauts).

    Pour affichage/édition dans la page Paramètres.
    """
    cfg_file = CONFIG_DIR / BLACKLIST_FILE
    if cfg_file.is_file():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            return {
                "title_patterns": [str(p) for p in data.get("title_patterns", [])],
                "title_abbr": [str(a) for a in data.get("title_abbr", [])],
                "companies": sorted(str(c) for c in data.get("companies", [])),
                "source": str(cfg_file),
            }
        except (ValueError, OSError):
            pass
    return {
        "title_patterns": list(BLACKLIST_TITLE_PATTERNS),
        "title_abbr": list(BLACKLIST_TITLE_ABBR),
        "companies": sorted(_HARDCODED_COMPANY_BLACKLIST),
        "source": "defaults",
    }


def save_blacklist(
    title_patterns: list[str], title_abbr: list[str], companies: list[str]
) -> dict:
    """Valide (chaque regex doit compiler), écrit config/blacklist.json, recharge.

    Utilisé par PUT /config/blacklist (page Paramètres). Lève ValueError sur la
    première regex invalide — rien n'est écrit dans ce cas.
    """
    for pattern in title_patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"regex invalide {pattern!r} : {e}") from e
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / BLACKLIST_FILE).write_text(
        json.dumps(
            {
                "title_patterns": title_patterns,
                "title_abbr": title_abbr,
                "companies": sorted({c.strip().lower() for c in companies if c.strip()}),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return reload_blacklist()


def export_default_blacklist(force: bool = False) -> Optional[str]:
    """Écrit la blacklist codée vers config/blacklist.json (amorçage du volume).

    No-op si le fichier existe déjà (sauf force=True) — on n'écrase jamais une
    config éditée à la main.
    """
    cfg_file = CONFIG_DIR / BLACKLIST_FILE
    if cfg_file.exists() and not force:
        return None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(
        json.dumps(
            {
                "title_patterns": BLACKLIST_TITLE_PATTERNS,
                "title_abbr": BLACKLIST_TITLE_ABBR,
                "companies": sorted(_HARDCODED_COMPANY_BLACKLIST),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(cfg_file)


# Compilation initiale (défauts ou fichier de config si déjà présent).
reload_blacklist()


# ============================================================================
# Termes de recherche par défaut
# ============================================================================
# Exemple : termes alignés sur un profil Senior Technical Account Manager
# cybersécurité réseau (firewall/UTM, ZTNA, Endpoint, SD-WAN, escalades
# techniques, partenaires EMEA). Adapte cette liste à ton propre profil.
#
# Objectif : scraper moins, mais mieux. Le quotidien cible les rôles senior
# client-facing cyber / network security / Zero Trust, en français ET anglais.
# Les rôles stretch sont conservés pour recherche manuelle/hebdo, mais exclus du
# scheduler quotidien pour éviter de regonfler le volume.

# Refonte priorités 2026-06-08 : périmètre resserré à 4 familles cibles, classées
# par priorité décroissante. CORE = priorités 1 et 2 (scrape quotidien prioritaire),
# ADJACENT = priorités 3 et 4. Anciennes familles (Customer Success, Architecte,
# Consultant, Escalade) déclassées dans REMOVED_FROM_DAILY_SEARCH_TERMS.
DAILY_CORE_SEARCH_TERMS: list[str] = [
    # === Priorité 1 — Technical Account Manager (coeur du profil exemple) ===
    "Senior Technical Account Manager",
    "Technical Account Manager",
    "Technical Account Manager Cybersecurity",
    "Technical Account Manager Security",
    "Responsable Technique de Comptes",
    "Responsable Technique de Comptes Senior",
    "Partner Technical Account Manager",
    "Technical Partner Manager",
    "Responsable Technique Partenaires",

    # === Priorité 2 — Team Leader Sécurité Réseaux (management technique d'équipe sécu) ===
    "Team Leader Sécurité Réseaux",
    "Team Lead Sécurité Réseau",
    "Network Security Team Lead",
    "Network Security Team Leader",
    "Responsable d'Équipe Sécurité Réseau",
    "Chef d'Équipe Sécurité Réseaux",
    "Lead Sécurité Réseau",
    "Network Security Lead",
    "Team Leader Cybersécurité",
]

DAILY_ADJACENT_SEARCH_TERMS: list[str] = [
    # === Priorité 3 — Responsable des Systèmes d'Information (direction/encadrement SI) ===
    "Responsable des Systèmes d'Information",
    "Responsable du Système d'Information",
    "Responsable Systèmes d'Information",
    "Responsable Informatique",
    "Information Systems Manager",
    "IT Manager",

    # === Priorité 4 — Management du support informatique ===
    # Cible : responsabilité d'équipe/service support IT, pas technicien/helpdesk N1-N2.
    "Manager Support Informatique",
    "Responsable Support Informatique",
    "Responsable Support IT",
    "IT Support Manager",
    "Service Desk Manager",
    "Responsable Service Desk",
    "Head of IT Support",

    # === Priorité 5 — Service Delivery Management (ajouté 2026-06-25) ===
    # Cible : pilotage de la livraison de services IT (SLA, gouvernance de service),
    # pas chef de projet. Acronyme "SDM" volontairement exclu (trop bruyant en plein texte).
    "Service Delivery Manager",
    "Senior Service Delivery Manager",
    "IT Service Delivery Manager",
    "Responsable Delivery",
    "Responsable de la Livraison de Services",
]

STRETCH_SEARCH_TERMS: list[str] = [
    # À utiliser en manuel/hebdo : crédible comme évolution, mais plus bruyant
    # et souvent plus gouvernance/management pur que le profil actuel.
    # Déclassés du quotidien le 2026-06-22 au profit du management support IT.
    "Directeur Technique SI",
    "Directeur Technique Informatique",
    "Directeur des Systèmes d'Information",
    "Chief Technology Officer",
    "CTO",
    "IT Director",
    "Responsable Cybersécurité",
    "Responsable Sécurité Informatique",
    "Security Manager",
    "Infrastructure Security Manager",
    "Responsable Sécurité Infrastructure",
    "RSSI Adjoint",
    "Deputy CISO",
    "CISO",
    "RSSI",
    "Responsable Infrastructure",
    "Infrastructure Manager",
    "IT Manager",
    "Responsable Informatique",
]

REMOVED_FROM_DAILY_SEARCH_TERMS: list[str] = [
    # Trop larges / trop bruyants en plein-texte LinkedIn/Indeed.
    "Responsable Technique",
    "Architecte Technique",
    "Chef de Projet Infrastructure",
    "Chef de Projet Sécurité",
    "Ingénieur Réseau",
    "Network Engineer",
    "System Engineer",
    "Pre-Sales Sécurité",
    "Ingénieur Avant-Vente Sécurité",
    "Sales Engineer",
    "Solutions Consultant",
    # Déclassées le 2026-06-08 (refonte priorités → focus TAM / Team Lead sécu / RSI /
    # Directeur Technique). Restent valorisées en "secondaire" par le scoring si elles
    # apparaissent via d'autres sources (ex. codes ROME), mais plus scrapées en direct.
    "Customer Success Engineer Security",
    "Customer Success Engineer Cybersecurity",
    "Technical Customer Success Manager",
    "Customer Success Architect Security",
    "Customer Success Manager Sécurité",
    "Ingénieur Customer Success Cybersécurité",
    "Security Solutions Architect",
    "Cybersecurity Solutions Architect",
    "Architecte Solutions Sécurité",
    "Architecte Solutions Cybersécurité",
    "Network Security Architect",
    "Architecte Sécurité Réseau",
    "Zero Trust Architect",
    "Architecte Zero Trust",
    "SASE Architect",
    "Architecte SASE",
    "Professional Services Consultant Cybersecurity",
    "Consultant Services Professionnels Cybersécurité",
    "Senior Security Consultant",
    "Consultant Sécurité Senior",
    "Consultant Sécurité Réseau Senior",
    "Consultant Cybersécurité Senior",
    "Consultant SSI Senior",
    "Security Escalation Engineer",
    "Technical Escalation Manager",
    "Ingénieur Escalade Sécurité",
    "Responsable Escalade Technique",
]

# Utilisé par défaut par SearchRequest et donc par le scheduler quotidien.
SEARCH_TERMS: list[str] = DAILY_CORE_SEARCH_TERMS + DAILY_ADJACENT_SEARCH_TERMS


# ============================================================================
# Profils géographiques
# ============================================================================
# Refonte géo 2026-06-03 : périmètre réduit à la France uniquement.
# Suisse / Belgique / Luxembourg (et Canada QC / Réunion / Martinique, déjà
# inutilisés) retirés — rendement faible (CH 11 %, BE 9 %, LU 6 % de hits ≥6)
# et hors zone de mobilité réelle (voir config/geo_scope.json). Les annonces
# de ces régions ont été purgées de la base au même moment.
GEO_PROFILES: dict[str, dict] = {
    "France": {
        "location": "France",
        "country": "France",
        "region": "FR",
        "flag": "🇫🇷",
        "cost_coef": 1.00,
    },
}

DEFAULT_PROFILE: str = "France"


# ============================================================================
# Acquisition structurée (refonte source-aware 2026-05-29)
# ============================================================================
# Filtres structurés > recherche plein-texte. Utilisés par les connectors
# "structurés" (uses_search_terms=False), ex. France Travail interrogé par
# code ROME + qualification cadre + départements, plutôt que par mots-clés.

# Codes ROME ciblant les métiers SI/cyber seniors. Donnent des résultats
# chirurgicaux vs la recherche plein-texte (fini vendeurs/éducateurs/BTP).
#   M1802 — Expertise et support en systèmes d'information (admin, cyber, réseau)
#   M1803 — Direction des systèmes d'information (DSI, RSSI, resp. infra)
#   M1806 — Conseil et maîtrise d'ouvrage en systèmes d'information (archi, conseil)
#   M1810 — Production et exploitation de systèmes d'information (infra, ops)
# M1805 (Études et dev info) volontairement EXCLU : trop de bruit dev/junior.
# Édités via la page Paramètres (settings.connectors) ; env = défauts au premier boot.
# Imports paresseux de settings : constants est importé PAR settings au chargement.

def get_ft_rome_codes() -> list[str]:
    """Codes ROME à interroger sur France Travail (page Paramètres > connecteurs)."""
    from settings import get
    return list(get().connectors.ft_rome_codes)


def get_idf_departments() -> list[str]:
    """Départements IDF pour le filtrage géo à la source (page Paramètres > connecteurs)."""
    from settings import get
    return list(get().connectors.idf_departments)


def get_ft_qualification() -> str:
    """Niveau de qualification France Travail. "9" = cadre (élimine techniciens N1-N2),
    "" = pas de filtre. Page Paramètres > connecteurs."""
    from settings import get
    return get().connectors.ft_qualification
