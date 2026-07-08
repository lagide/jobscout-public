"""Sélection des URL canoniques et alternatives d'une ligne de scraper."""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional


def clean_http_url(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and value.lower().strip() in {"", "nan", "none"}:
        return None
    url = str(value).strip()
    return url if url.startswith(("http://", "https://")) else None


def select_job_urls(row: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Retourne (canonique, alternatives), lien employeur prioritaire pour Indeed."""
    platform = str(row.get("site", "unknown"))
    listing = clean_http_url(row.get("job_url"))
    direct = clean_http_url(row.get("job_url_direct"))
    canonical = direct if platform == "indeed" and direct else (listing or direct or "")
    alternatives = []
    for url in (canonical, listing, direct):
        if url and url not in alternatives:
            alternatives.append(url)
    return canonical, alternatives
