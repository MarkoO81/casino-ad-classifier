"""Google Ads Transparency Center scanner.

Delegates to src/browser.py for Playwright rendering.
Falls back to a plain requests attempt if Playwright is unavailable.
"""

from __future__ import annotations
from urllib.parse import urlencode

TRANSPARENCY_BASE = "https://adstransparency.google.com"


def scan_transparency_center(country: str = "SI") -> list[dict]:
    """Scan Google Ads Transparency Center for casino ads in a given country."""
    from src.browser import scrape_transparency
    return scrape_transparency(country)


def build_search_url(query: str, country: str) -> str:
    return f"{TRANSPARENCY_BASE}/?{urlencode({'query': query, 'region': country})}"
