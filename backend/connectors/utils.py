"""Helpers partagés entre connecteurs — calcul des dates de coupure `hours_old`.

Avant 2026-06-10, chaque connecteur recopiait sa propre ligne de cutoff
(8 duplications, 3 variantes). Centralisé ici : un fix de sémantique ne se
fait plus qu'à un seul endroit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def cutoff_date(hours_old: int) -> date:
    """Date de coupure pour les sources qui n'exposent qu'une date de publication.

    `hours_old` est arrondi au jour inférieur (minimum 1 jour) — même sémantique
    que l'implémentation historique de apec/cadremploi/freework/wttj.
    """
    return date.today() - timedelta(days=max(1, hours_old // 24))


def cutoff_ts(hours_old: int) -> float:
    """Timestamp Unix de coupure (sources à timestamp : greenhouse, himalayas, remotive)."""
    return datetime.now(timezone.utc).timestamp() - hours_old * 3600


def cutoff_dt(hours_old: int) -> datetime:
    """Datetime UTC aware de coupure (workday)."""
    return datetime.now(timezone.utc) - timedelta(hours=hours_old)
