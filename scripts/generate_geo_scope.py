#!/usr/bin/env python3
"""Génère config/geo_scope.json depuis geo.api.gouv.fr.

Calcule toutes les communes françaises situées à moins de `--radius-km` de
`--lat`/`--lon`, et les écrit dans le format attendu par `backend/geo_scope.py`
(allowed_names, allowed_postcodes, aliases, origin, radius_km).

Usage :
    python3 scripts/generate_geo_scope.py \\
        --name "Paris" --lat 48.8566 --lon 2.3522 --radius-km 90 \\
        --alias "ile de france" --alias idf --alias paris \\
        -o config/geo_scope.json

Aucune donnée personnelle n'est codée en dur ici : origine, rayon et alias
sont des arguments. Adapte-les à ta propre localisation.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request

API_URL = (
    "https://geo.api.gouv.fr/communes"
    "?fields=nom,code,codeDepartement,codesPostaux,centre&format=json&geometry=centre"
)

DEFAULT_ALIASES = [
    "ile de france", "idf", "hauts de seine", "seine saint denis",
    "val de marne", "val d'oise", "yvelines", "essonne",
    "seine et marne", "paris", "la defense",
]
DEFAULT_IDF_DEPARTMENTS = ["75", "77", "78", "91", "92", "93", "94", "95"]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.lower().replace("’", "'").replace("-", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="Nom de l'origine (ex: 'Paris')")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius-km", type=float, default=90.0)
    parser.add_argument("--alias", action="append", default=[], help="Alias texte libre (répétable)")
    parser.add_argument("--idf-department", action="append", default=[], dest="idf_departments")
    parser.add_argument("-o", "--output", default="config/geo_scope.json")
    args = parser.parse_args()

    aliases = args.alias or DEFAULT_ALIASES
    idf_departments = args.idf_departments or DEFAULT_IDF_DEPARTMENTS

    print(f"Fetching commune list from {API_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(API_URL, timeout=60) as resp:
        communes = json.loads(resp.read())
    print(f"{len(communes)} communes total, filtering within {args.radius_km} km of {args.name}...", file=sys.stderr)

    allowed_names: set[str] = set()
    allowed_postcodes: set[str] = set()
    kept = []
    for c in communes:
        centre = c.get("centre")
        if not centre or "coordinates" not in centre:
            continue
        lon, lat = centre["coordinates"]
        dist = haversine_km(args.lat, args.lon, lat, lon)
        if dist <= args.radius_km:
            allowed_names.add(normalize(c["nom"]))
            for cp in c.get("codesPostaux", []):
                allowed_postcodes.add(cp)
            kept.append({
                "name": c["nom"],
                "normalized_name": normalize(c["nom"]),
                "department": c.get("codeDepartement"),
                "postcodes": c.get("codesPostaux", []),
                "distance_km": round(dist, 1),
                "reason": f"radius_{int(args.radius_km)}km",
            })

    out = {
        "source": API_URL,
        "origin": {"name": args.name, "lat": args.lat, "lon": args.lon},
        "radius_km": args.radius_km,
        "idf_departments": idf_departments,
        "aliases": aliases,
        "allowed_names": sorted(allowed_names),
        "allowed_postcodes": sorted(allowed_postcodes),
        "communes": sorted(kept, key=lambda c: c["distance_km"]),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(kept)} communes ({len(allowed_postcodes)} postcodes) to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
