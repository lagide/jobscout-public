"""Périmètre géographique des offres conservées par JobScout.

Règle métier :
  - full remote : toute la France ;
  - hybride / présentiel / mode inconnu : Île-de-France, Reims ou commune
    située à moins du rayon configuré (config/geo_scope.json, généré par
    scripts/generate_geo_scope.py) de l'origine choisie.

Le fichier JSON est généré depuis geo.api.gouv.fr et monté via ./config.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SCOPE_PATH = Path(os.getenv("GEO_SCOPE_PATH", "/app/config/geo_scope.json"))
IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}
IDF_LABELS = (
    "ile de france", "idf", "hauts de seine", "seine saint denis",
    "val de marne", "val d'oise", "yvelines", "essonne",
    "seine et marne", "paris", "la defense",
)


def normalize(value: Optional[str]) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'").replace("-", " ")
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return " ".join(value.split())


class GeoScope:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.names = set(raw["allowed_names"])
        self.postcodes = set(raw["allowed_postcodes"])
        self.aliases = {normalize(value) for value in raw.get("aliases", [])}
        self.long_names = sorted(
            (name for name in self.names if len(name) >= 5), key=len, reverse=True
        )

    def allows(
        self,
        location: Optional[str],
        work_mode: Optional[str],
        is_remote: Optional[bool] = None,
    ) -> tuple[bool, str]:
        if work_mode == "full_remote" or is_remote is True:
            return True, "full_remote"

        raw_location = str(location or "")
        normalized = normalize(raw_location)
        if not normalized:
            return False, "location_absente"

        padded = f" {normalized} "
        if any(f" {label} " in padded for label in IDF_LABELS):
            return True, "ile_de_france_label"
        if any(f" {alias} " in padded for alias in self.aliases):
            return True, "alias"

        postcodes = set(re.findall(r"(?<!\d)(\d{5})(?!\d)", raw_location))
        if postcodes & self.postcodes:
            return True, "postcode"

        departments = set(
            re.findall(
                r"(?:^|[\s,(\-/])(75|77|78|91|92|93|94|95)(?:$|[\s,)\-/])",
                raw_location,
            )
        )
        if departments & IDF_DEPARTMENTS:
            return True, "departement_idf"

        segments = {
            normalize(part)
            for part in re.split(r",|\s+-\s+|\(|\)|/", raw_location)
            if normalize(part)
        }
        cleaned = {
            re.sub(r"\b(cedex|arrondissement|france|fr|a8)\b.*$", "", part).strip()
            for part in segments
        }
        if (segments | cleaned) & self.names:
            return True, "commune_exacte"
        if any(f" {name} " in padded for name in self.long_names):
            return True, "commune_dans_libelle"
        return False, "hors_perimetre"


_scope: Optional[GeoScope] = None


def reload_geo_scope() -> dict:
    """Invalide le cache — le prochain accès relira config/geo_scope.json.

    Appelé par POST /config/reload pour que le périmètre géo soit rechargeable
    à chaud comme la blacklist et le prompt.
    """
    global _scope
    _scope = None
    try:
        scope = get_geo_scope()
        return {"source": str(DEFAULT_SCOPE_PATH), "names": len(scope.names),
                "postcodes": len(scope.postcodes), "error": None}
    except (OSError, ValueError, KeyError) as e:
        return {"source": str(DEFAULT_SCOPE_PATH), "error": f"{type(e).__name__}: {e}"}


def get_geo_scope(path: Optional[Path] = None) -> GeoScope:
    global _scope
    if path is not None:
        return GeoScope(path)
    if _scope is None:
        _scope = GeoScope(DEFAULT_SCOPE_PATH)
        logger.info("Geo scope loaded from %s", DEFAULT_SCOPE_PATH)
    return _scope


def is_location_in_scope(
    location: Optional[str],
    work_mode: Optional[str],
    is_remote: Optional[bool] = None,
) -> tuple[bool, str]:
    return get_geo_scope().allows(location, work_mode, is_remote)
