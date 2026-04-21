"""APEC connector — https://www.apec.fr/candidat/recherche-emploi.html

APEC is a JS-heavy SPA that renders its listings through a GraphQL-like internal
endpoint. Rather than reverse-engineer their private API (which changes), we use
Playwright: load the search page, wait for the listings, scrape the DOM.

Requires PLAYWRIGHT_ENABLED=true in .env.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

from .base import BaseConnector, ConnectorResult, JobRecord
from .playwright_base import PlaywrightSession, is_playwright_enabled

logger = logging.getLogger(__name__)

BASE = "https://www.apec.fr"
SEARCH_URL = f"{BASE}/candidat/recherche-emploi.html"


class ApecConnector(BaseConnector):
    platform_name = "apec"

    def is_enabled(self) -> bool:
        return is_playwright_enabled()

    async def scrape(
        self,
        *,
        search_term: str,
        location: str,
        country: str,
        hours_old: int,
        results_wanted: int,
    ) -> ConnectorResult:
        result = ConnectorResult()

        if not is_playwright_enabled():
            result.errors.append("apec: PLAYWRIGHT_ENABLED not set — connector disabled")
            return result

        # APEC is FR-only.
        if country and country.lower() not in ("france", "fr"):
            return result

        qs = urlencode({"motsCles": search_term})
        url = f"{SEARCH_URL}?{qs}"
        cutoff = date.today() - timedelta(days=max(1, hours_old // 24))

        try:
            async with PlaywrightSession() as page:
                await page.goto(url, timeout=45_000, wait_until="domcontentloaded")

                # Cookie consent — APEC's button says "Accepter tous les cookies".
                # Try a few selectors; swallow failures.
                for sel in [
                    'button:has-text("Accepter tous les cookies")',
                    'button:has-text("Accepter")',
                    '#onetrust-accept-btn-handler',
                    'button[aria-label*="Accept"]',
                    'button[aria-label*="Accepter"]',
                ]:
                    try:
                        await page.click(sel, timeout=2500)
                        await page.wait_for_timeout(500)
                        break
                    except Exception:
                        continue

                # APEC's SPA posts search results after consent. Give it up to 20s.
                # Selector: links to /detail-offre/<id>
                try:
                    await page.wait_for_selector(
                        'a[href*="detail-offre"]',
                        timeout=20_000,
                    )
                except Exception:
                    result.errors.append(
                        "apec: no results selector appeared (listings may require "
                        "JS interaction or APEC changed its markup)"
                    )
                    return result

                # Scroll to trigger lazy-loading of more cards
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(600)

                # Extract links + surrounding card info
                cards_data = await page.evaluate("""
                    () => {
                        const anchors = Array.from(document.querySelectorAll(
                            'a[href*="detail-offre"]'
                        ));
                        const seen = new Set();
                        const out = [];
                        for (const a of anchors) {
                            const href = a.getAttribute('href');
                            if (!href || seen.has(href)) continue;
                            seen.add(href);
                            const card = a.closest('div[class*="card"], article, li, [class*="result"]') || a.parentElement;
                            const text = card ? card.innerText : a.innerText;
                            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                            out.push({
                                href,
                                title: lines[0] || '',
                                rawLines: lines.slice(0, 10),
                            });
                        }
                        return out;
                    }
                """)
        except Exception as e:
            result.errors.append(f"apec playwright: {type(e).__name__}: {e}")
            return result

        for c in cards_data[: results_wanted * 2]:
            try:
                href = c.get("href", "")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = BASE + href

                title = c.get("title") or ""
                if not title or len(title) < 4:
                    continue

                lines = c.get("rawLines") or []
                company = lines[1] if len(lines) > 1 else None
                loc = lines[2] if len(lines) > 2 else "France"

                # Detect "Il y a X j." pattern
                posted = None
                for line in lines:
                    m = re.search(r"il\s*y\s*a\s*(\d+)\s*j", line, re.IGNORECASE)
                    if m:
                        posted = date.today() - timedelta(days=int(m.group(1)))
                        break
                    if re.search(r"aujourd", line, re.IGNORECASE):
                        posted = date.today()
                        break
                if posted and posted < cutoff:
                    continue

                rec: JobRecord = {
                    "site": self.platform_name,
                    "id": href.rsplit("/", 1)[-1][:100] if "/" in href else None,
                    "title": title[:400],
                    "company": company,
                    "location": loc,
                    "description": None,
                    "job_url": href,
                    "date_posted": posted.isoformat() if posted else None,
                    "is_remote": None,
                    "currency": None,
                    "min_amount": None,
                    "max_amount": None,
                    "interval": None,
                    "job_type": None,
                }
                result.records.append(rec)
            except Exception as e:
                result.errors.append(f"apec parse: {e}")

        if len(result.records) > results_wanted:
            result.records = result.records[:results_wanted]
        return result
