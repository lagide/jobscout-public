"""Custom scrapers / API connectors registered alongside JobSpy.

Each connector implements the BaseConnector interface and is registered in the
CONNECTOR_REGISTRY. The scraper picks them up when their platform name appears
in SearchRequest.sites.
"""
from __future__ import annotations

from .base import BaseConnector, ConnectorResult, JobRecord
from .registry import CONNECTOR_REGISTRY, get_connector, registered_platforms

__all__ = [
    "BaseConnector",
    "ConnectorResult",
    "JobRecord",
    "CONNECTOR_REGISTRY",
    "get_connector",
    "registered_platforms",
]
