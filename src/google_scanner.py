"""Scrape Google Ads Transparency Center for casino ads.

The Transparency Center is a JavaScript-rendered React app, so plain
requests only gets the shell HTML. We extract what we can from the
initial page load and construct direct search URLs for the user to
follow manually when the JS wall blocks automated access.

Best-effort: works when Google returns static content, degrades
gracefully when JS rendering is required.
"""

from __future__ import annotations
from urllib.parse import urlencode


TRANSPARENCY_BASE = "https://adstransparency.google.com"

# Casino-related search terms to query
CASINO_QUERIES = [
    "online casino",
    "free spins",
    "casino bonus",
    "spletna igralnica",   # SL
    "kockarnica",          # HR
    "casino bonus dobrodošlice",
]


def build_search_url(query: str, country: str) -> str:
    params = urlencode({"query": query, "region": country})
    return f"{TRANSPARENCY_BASE}/?{params}"


def scan_transparency_center(country: str = "SI") -> list[dict]:
    """Attempt to fetch results from Google Ads Transparency Center.

    Returns a list of result records. Each record includes a direct
    search URL for manual follow-up when automated scraping is blocked.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        return [_error_record(f"Missing dependency: {e}", country)]

    results = []

    for query in CASINO_QUERIES:
        url = build_search_url(query, country)
        record = {
            "query": query,
            "search_url": url,
            "country": country,
            "ads": [],
            "error": None,
            "js_required": False,
        }

        try:
            r = requests.get(
                url, timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                allow_redirects=True,
            )
            r.raise_for_status()
        except Exception as e:
            record["error"] = str(e)
            results.append(record)
            continue

        soup = BeautifulSoup(r.text, "html.parser")

        # Check if we got real content or just the JS shell
        # The Transparency Center shell has very little visible text
        visible = soup.get_text(" ", strip=True)
        if len(visible) < 200 or "noscript" in r.text.lower():
            record["js_required"] = True
            results.append(record)
            continue

        # If we do get content, try to extract ad cards
        # (structure varies; this is best-effort)
        for card in soup.select("[class*='ad-card'], [class*='creative'], article"):
            text = card.get_text(" ", strip=True)
            link = card.find("a", href=True)
            if text:
                record["ads"].append({
                    "text": text[:300],
                    "url": link["href"] if link else None,
                })

        results.append(record)

    return results


def _error_record(msg: str, country: str) -> dict:
    return {
        "query": "all",
        "search_url": f"{TRANSPARENCY_BASE}/?region={country}",
        "country": country,
        "ads": [],
        "error": msg,
        "js_required": False,
    }
