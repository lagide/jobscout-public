"""Connector registry — maps platform_name → BaseConnector instance."""
from __future__ import annotations

from typing import Optional

from .apec import ApecConnector
from .base import BaseConnector
from .francetravail import FranceTravailConnector
from .cadremploi import CadremploiConnector
from .choisirservicepublic import ChoisirServicePublicConnector
from .freework import FreeWorkConnector
from .hellowork import HelloWorkConnector
from .greenhouse import GreenhouseConnector
from .himalayas import HimalayasConnector
from .remotive import RemotiveConnector
from .welcometothejungle import WelcomeToTheJungleConnector
from .workday import WorkdayConnector

CONNECTOR_REGISTRY: dict[str, BaseConnector] = {
    "remotive": RemotiveConnector(),
    "francetravail": FranceTravailConnector(),
    "freework": FreeWorkConnector(),
    "himalayas": HimalayasConnector(),
    "greenhouse": GreenhouseConnector(),
    "workday": WorkdayConnector(),
    "apec": ApecConnector(),
    "wttj": WelcomeToTheJungleConnector(),
    "hellowork": HelloWorkConnector(),
    "cadremploi": CadremploiConnector(),
    "choisirservicepublic": ChoisirServicePublicConnector(),
}


def get_connector(name: str) -> Optional[BaseConnector]:
    return CONNECTOR_REGISTRY.get(name)


def registered_platforms() -> list[str]:
    return list(CONNECTOR_REGISTRY.keys())
