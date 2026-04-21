"""FX conversion via frankfurter.app (free, no API key).

Rates are cached in-process for 24h (TTL). Frankfurter's latest endpoint returns
ECB reference rates, which are published once per business day.

A small set of hard-coded fallback rates is used if the API is unreachable.
"""
from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
CACHE_TTL_SECONDS = 24 * 3600

# Conservative fallback rates to EUR. Only used if the API is down at boot + cold cache.
# (Order-of-magnitude accuracy is fine — this is a display-side estimate.)
_FALLBACK_TO_EUR: dict[str, float] = {
    "EUR": 1.00,
    "USD": 0.92,
    "CHF": 1.05,
    "GBP": 1.17,
    "CAD": 0.67,
}


class _RatesCache:
    """In-memory, thread-safe FX cache."""

    def __init__(self) -> None:
        self._rates_to_eur: dict[str, float] = {}
        self._fetched_at: float = 0.0
        self._lock = Lock()

    def _fresh(self) -> bool:
        return (time.time() - self._fetched_at) < CACHE_TTL_SECONDS

    def _load_from_api(self) -> None:
        """Populate the cache with ECB rates. Safe to call repeatedly."""
        try:
            # Frankfurter default base is EUR → rates[X] = EUR→X.
            # We flip to X→EUR = 1/rates[X].
            r = httpx.get(FRANKFURTER_URL, timeout=10, follow_redirects=True)
            r.raise_for_status()
            payload = r.json()
            rates = payload.get("rates", {})
            base = payload.get("base", "EUR")
            if base == "EUR":
                new_rates = {cur: (1.0 / rate) for cur, rate in rates.items() if rate}
                new_rates["EUR"] = 1.0
            else:
                new_rates = {cur: rate for cur, rate in rates.items()}
                new_rates[base] = 1.0
            self._rates_to_eur = new_rates
            self._fetched_at = time.time()
            logger.info(
                "FX cache refreshed from frankfurter — %d currencies, date=%s",
                len(new_rates), payload.get("date"),
            )
        except Exception as e:
            logger.warning("Frankfurter fetch failed (%s) — using fallback rates", e)
            if not self._rates_to_eur:
                # Only set fallback if we have nothing at all
                self._rates_to_eur = dict(_FALLBACK_TO_EUR)
                self._fetched_at = time.time()

    def to_eur(self, amount: Optional[float], currency: Optional[str]) -> Optional[float]:
        if amount is None or currency is None:
            return None
        cur = currency.strip().upper()
        with self._lock:
            if not self._fresh() or cur not in self._rates_to_eur:
                self._load_from_api()
        rate = self._rates_to_eur.get(cur) or _FALLBACK_TO_EUR.get(cur)
        if rate is None:
            return None
        return round(float(amount) * rate, 2)


_cache = _RatesCache()


def to_eur(amount: Optional[float], currency: Optional[str]) -> Optional[float]:
    """Convert `amount` (in `currency`) to EUR using live ECB rates. None-safe."""
    return _cache.to_eur(amount, currency)


def compute_effective_eur(eur_amount: Optional[float], cost_coef: float) -> Optional[float]:
    """Apply a purchasing-power coefficient (<1 = lower real PP than FR baseline)."""
    if eur_amount is None:
        return None
    return round(eur_amount * cost_coef, 2)
