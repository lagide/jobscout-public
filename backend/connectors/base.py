"""Common connector interface.

Each connector returns a list of JobRecord dicts with the same keys JobSpy produces,
so the downstream normalization in scraper._row_to_job_kwargs keeps working.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, TypedDict


class JobRecord(TypedDict, total=False):
    """Shape-compatible with JobSpy rows. All fields optional except title + url + site."""
    site: str                # platform name (required)
    title: str               # (required)
    job_url: str             # (required)
    id: Optional[str]        # platform-native id
    company: Optional[str]
    location: Optional[str]
    description: Optional[str]
    min_amount: Optional[float]
    max_amount: Optional[float]
    currency: Optional[str]
    interval: Optional[str]      # yearly/monthly/hourly
    is_remote: Optional[bool]
    job_type: Optional[str]
    date_posted: Optional[str]   # ISO string or yyyy-mm-dd


@dataclass
class ConnectorResult:
    """What a connector returns for a single (term, location) pair."""
    records: list[JobRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BaseConnector(ABC):
    """Abstract connector — one per external data source."""

    platform_name: str  # must match the key used in SearchRequest.sites

    @abstractmethod
    async def scrape(
        self,
        *,
        search_term: str,
        location: str,
        country: str,
        hours_old: int,
        results_wanted: int,
    ) -> ConnectorResult:
        """Run one search. Must not raise — return errors in the result instead."""

    def is_enabled(self) -> bool:
        """Override to gate on env-var configuration (e.g. missing API key)."""
        return True
