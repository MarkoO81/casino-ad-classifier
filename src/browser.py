"""Shared Playwright headless browser utility.

Handles three use cases:
  1. render_url()          — fetch any JS-rendered page (Google Transparency Center, etc.)
  2. resolve_js_redirect() — follow JS/meta-refresh redirects to the final URL
  3. scrape_transparency() — search Google Ads Transparency Center for casino ads

Each call launches a fresh Chromium instance and closes it when done.
This is intentionally stateless — no shared browser instance — so it
works safely inside Flask threads and the APScheduler background job.
"""

from __future__ import annotations
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def _get_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        return None


def render_url(url: str, wait: str = "networkidle", timeout: int = 15000) -> dict:
    """Render a page with headless Chromium and return extracted content.

    Returns dict with: url (final), html, text, links, error.
    """
    sync_playwright = _get_playwright()
    if not sync_playwright:
        return {"url": url, "html": "", "text": "", "links": [], "error": "playwright not installed"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto(url, wait_until=wait, timeout=timeout)

            final_url = page.url
            text = page.inner_text("body") or ""
            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
            )
            html = page.content()
            browser.close()

        return {"url": final_url, "html": html, "text": text, "links": links, "error": None}

    except Exception as e:
        return {"url": url, "html": "", "text": "", "links": [], "error": str(e)}


def resolve_js_redirect(url: str, timeout: int = 12000) -> dict:
    """Follow JS/meta-refresh redirects and return the final landing URL.

    Falls back to requests-based resolution if Playwright isn't available.
    Returns dict with: original, final, final_domain, error.
    """
    from src.url_check import extract_domain

    sync_playwright = _get_playwright()
    if not sync_playwright:
        # Graceful fallback to the plain HTTP resolver
        from src.url_check import resolve_redirects
        return resolve_redirects(url)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(url, wait_until="commit", timeout=timeout)
            # Wait a moment for any JS redirects to fire
            page.wait_for_timeout(2000)
            final = page.url
            browser.close()

        return {
            "original": url,
            "final": final,
            "final_domain": extract_domain(final),
            "error": None,
        }
    except Exception as e:
        return {
            "original": url,
            "final": url,
            "final_domain": extract_domain(url),
            "error": str(e),
        }


# Casino-related search queries for the Transparency Center
_CASINO_QUERIES = [
    "online casino",
    "free spins",
    "casino bonus",
    "welcome bonus casino",
    "spletna igralnica",    # SL: online casino
    "brezplačna vrtenja",   # SL: free spins
    "kockarnica",           # HR: casino
    "besplatni spinovi",    # HR: free spins
]


def scrape_transparency(country: str = "SI") -> list[dict]:
    """Search Google Ads Transparency Center for casino ads.

    Returns a list of records, one per query:
      { query, search_url, country, ads: [{advertiser, text, url}], error, js_required }

    Uses a single browser + context for all queries — one launch, N pages.
    """
    sync_playwright = _get_playwright()

    if not sync_playwright:
        return [
            {"query": q, "search_url": f"https://adstransparency.google.com/?{urlencode({'query': q, 'region': country})}",
             "country": country, "ads": [], "error": "playwright not installed", "js_required": False}
            for q in _CASINO_QUERIES
        ]

    results = []
    logger.info("Transparency scrape start — country=%s, queries=%d", country, len(_CASINO_QUERIES))
    try:
        with sync_playwright() as p:
            logger.debug("Launching Chromium (anonymous context)")
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(locale="en-US")

            for query in _CASINO_QUERIES:
                params = urlencode({"query": query, "region": country})
                search_url = f"https://adstransparency.google.com/?{params}"
                record = {"query": query, "search_url": search_url,
                          "country": country, "ads": [], "error": None, "js_required": False}
                page = None
                try:
                    logger.debug("  fetching query=%r", query)
                    page = ctx.new_page()
                    page.goto(search_url, wait_until="networkidle", timeout=20000)
                    try:
                        page.wait_for_selector(
                            "[class*='creative'], [class*='ad-card'], "
                            "[class*='AdCard'], [class*='result']",
                            timeout=8000
                        )
                    except Exception:
                        pass

                    text_blocks = page.eval_on_selector_all(
                        "p, span, div[class*='text'], div[class*='body'], "
                        "div[class*='creative'] *",
                        "els => [...new Set(els.map(e => e.innerText.trim()).filter(t => t.length > 20 && t.length < 500))]"
                    )
                    ad_links = page.eval_on_selector_all(
                        "a[href*='google.com/aclk'], a[href*='adclick'], "
                        "a[href^='http']:not([href*='transparency'])",
                        "els => els.map(e => e.href)"
                    )
                    page_text = page.inner_text("body") or ""

                    if len(page_text.strip()) < 300:
                        record["js_required"] = True
                        logger.info("  query=%r → JS wall (page too short)", query)
                    else:
                        seen = set()
                        for block in text_blocks:
                            b = block.strip()
                            if b and b not in seen:
                                seen.add(b)
                                record["ads"].append({
                                    "advertiser": "",
                                    "text": b,
                                    "url": ad_links[len(seen) - 1] if len(seen) <= len(ad_links) else None,
                                })
                        logger.info("  query=%r → %d text blocks extracted", query, len(record["ads"]))
                except Exception as e:
                    record["error"] = str(e)
                    logger.warning("  query=%r → error: %s", query, e)
                finally:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass

                results.append(record)

            browser.close()
            logger.info("Transparency scrape done — %d queries completed", len(results))

    except Exception as e:
        logger.error("Transparency scrape failed (browser launch?): %s", e)
        results = [
            {"query": q, "search_url": f"https://adstransparency.google.com/?{urlencode({'query': q, 'region': country})}",
             "country": country, "ads": [], "error": str(e), "js_required": False}
            for q in _CASINO_QUERIES
        ]

    return results
