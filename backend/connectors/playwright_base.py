"""Shared Playwright infrastructure for JS-heavy scrapers.

Plays well with the async scrape pipeline: every connector borrows a Page from a
shared Browser (one per event loop). Gated by PLAYWRIGHT_ENABLED — so the image
can ship with Chromium installed but unused, zero boot-time cost.

Usage inside a connector:

    from .playwright_base import PlaywrightSession, is_playwright_enabled

    async def scrape(self, ...):
        if not is_playwright_enabled():
            return ConnectorResult(errors=["playwright disabled"])
        async with PlaywrightSession() as page:
            await page.goto(url, timeout=30_000)
            ...
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)


def is_playwright_enabled() -> bool:
    return os.getenv("PLAYWRIGHT_ENABLED", "false").lower() in ("true", "1", "yes")


_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class PlaywrightSession:
    """Async context manager that yields a Page bound to a fresh ephemeral context.

    We launch Chromium per session (instead of sharing a browser across scrapes)
    because Playwright browsers don't play well with long-lived async loops across
    multiple scrape cycles — the overhead of launching Chromium per scrape cycle
    (~1.5s) is acceptable given each cycle scrapes multiple terms.
    """

    def __init__(
        self,
        *,
        viewport: tuple[int, int] = (1280, 900),
        extra_headers: Optional[dict[str, str]] = None,
        locale: str = "fr-FR",
    ):
        self._viewport = viewport
        self._extra_headers = extra_headers or {}
        self._locale = locale
        self._playwright = None
        self._browser = None
        self._ctx = None
        self._page = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._ctx = await self._browser.new_context(
            viewport={"width": self._viewport[0], "height": self._viewport[1]},
            user_agent=_USER_AGENT,
            locale=self._locale,
            extra_http_headers=self._extra_headers,
        )
        # Hide the "webdriver" flag — modest anti-automation countermeasure
        await self._ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = await self._ctx.new_page()
        return self._page

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._page is not None:
                await self._page.close()
        except Exception:
            pass
        try:
            if self._ctx is not None:
                await self._ctx.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
