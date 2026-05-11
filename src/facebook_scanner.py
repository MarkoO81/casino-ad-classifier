"""Scrape Facebook Ad Library for casino-related active ads."""

from __future__ import annotations
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_FB_QUERIES = [
    "casino",
    "online casino",
    "casino bonus",
    "free spins",
    "spletna igralnica",           # SL: online casino
    "online igralnica",            # SL: variant
    "igre na srečo",               # SL: games of chance
    "brezplačna vrtenja",          # SL: free spins
    "bonus dobrodošlice",          # SL: welcome bonus
    "casino brez depozita",        # SL: no-deposit casino
    "igralni avtomati",            # SL: slot machines
    "kockarnica",                  # HR: casino
    "besplatni spinovi",           # HR: free spins
]

_LIBRARY_URL = "https://www.facebook.com/ads/library/"

_CONSENT_SELECTORS = [
    "[data-testid='cookie-policy-manage-dialog-accept-button']",
    "button:has-text('Allow all cookies')",
    "button:has-text('Decline optional cookies')",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "[data-cookiebannerbutton]",
    "div[role='dialog'] button:last-of-type",
]


def _build_url(query: str, country: str, platform: str = "") -> str:
    base = _LIBRARY_URL + "?" + urlencode({
        "active_status": "active",
        "ad_type":        "all",
        "country":        country,
        "q":              query,
        "search_type":    "keyword_unordered",
        "media_type":     "all",
    })
    if platform:
        base += f"&publisher_platforms[]={platform}"
    return base


def _try_accept_consent(page) -> bool:
    for selector in _CONSENT_SELECTORS:
        try:
            btn = page.query_selector(selector)
            if btn:
                btn.click()
                page.wait_for_timeout(1500)
                logger.info("  FB consent accepted via: %s", selector)
                return True
        except Exception:
            pass
    return False


def scan_facebook_library(country: str = "SI", platform: str = "") -> list[dict]:
    """Scrape Facebook Ad Library for casino-related active ads.

    Pass platform="INSTAGRAM" to restrict to Instagram placements.
    Returns same record format as browser.scrape_transparency():
    [{ query, search_url, country, ads: [{advertiser, text, url}], error, js_required }]
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [
            {"query": q, "search_url": _build_url(q, country, platform),
             "country": country, "ads": [], "error": "playwright not installed", "js_required": False}
            for q in _FB_QUERIES
        ]

    results = []
    plat_label = f" platform={platform}" if platform else ""
    logger.info("Facebook Ad Library scrape — country=%s%s, queries=%d", country, plat_label, len(_FB_QUERIES))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )

            # Accept cookie consent once so it persists across pages
            consent_page = ctx.new_page()
            try:
                consent_page.goto(_LIBRARY_URL, wait_until="domcontentloaded", timeout=15000)
                consent_page.wait_for_timeout(2000)
                if not _try_accept_consent(consent_page):
                    logger.debug("  no FB consent popup found")
            except Exception as e:
                logger.debug("  FB consent page failed: %s", e)
            finally:
                consent_page.close()

            for query in _FB_QUERIES:
                search_url = _build_url(query, country, platform)
                record = {
                    "query":      query,
                    "search_url": search_url,
                    "country":    country,
                    "ads":        [],
                    "error":      None,
                    "js_required": False,
                }
                page = None
                try:
                    logger.debug("  FB query=%r", query)
                    page = ctx.new_page()
                    page.goto(search_url, wait_until="networkidle", timeout=25000)
                    page.wait_for_timeout(3000)

                    # Re-check consent mid-session
                    _try_accept_consent(page)

                    page_text = page.inner_text("body") or ""
                    body_len = len(page_text.strip())

                    login_wall = (
                        body_len < 500 or
                        ("log in" in page_text.lower() and body_len < 3000 and "ad library" not in page_text.lower())
                    )

                    if body_len < 300 or login_wall:
                        record["js_required"] = True
                        logger.info("  FB query=%r → login/consent wall (body=%d chars)", query, body_len)
                    else:
                        # Scroll to trigger lazy-loading of ad cards
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                        page.wait_for_timeout(1500)

                        # Extract structured ad data: text blocks + advertiser names + landing URLs
                        page_data = page.evaluate("""() => {
                            // Ad copy: leaf text nodes of meaningful length
                            const texts = [...new Set(
                                [...document.querySelectorAll('div,span,p')]
                                .filter(e => !e.children.length)
                                .map(e => e.innerText.trim())
                                .filter(t => t.length > 20 && t.length < 500)
                            )];

                            // Advertiser names: links to Facebook pages inside ads
                            // (exclude nav/utility/library links)
                            const advertisers = [...new Set(
                                [...document.querySelectorAll('a[href*="facebook.com/"]')]
                                .filter(a => !a.href.match(/\\/(ads\\/library|help|policies|privacy|login|reg|photo|video|groups|events|marketplace|sharer)/))
                                .map(a => a.innerText.trim())
                                .filter(t => t.length > 1 && t.length < 80)
                            )];

                            // Landing URLs: external links (not social platforms)
                            const landingUrls = [...new Set(
                                [...document.querySelectorAll('a[href^="https://"]')]
                                .map(a => a.href)
                                .filter(h => !['facebook.com','instagram.com','messenger.com',
                                               'whatsapp.com','google.com','apple.com'].some(d => h.includes(d)))
                            )];

                            // Ad Library permalinks (facebook.com/ads/library/?id=...)
                            const adPermalinks = [...new Set(
                                [...document.querySelectorAll('a[href*="ads/library/?id="]')]
                                .map(a => a.href)
                            )];

                            // "Paid for by" disclaimer visible at the bottom of each ad card
                            const paidForBy = [...document.querySelectorAll('div,span')]
                                .filter(e => !e.children.length && e.innerText)
                                .map(e => e.innerText.trim())
                                .filter(t => t.toLowerCase().startsWith('paid for by'))
                                .map(t => t.replace(/^paid for by:?\\s*/i, '').trim())
                                .filter(t => t.length > 0 && t.length < 120);

                            // Ad IDs from permalink hrefs
                            const adIds = [...document.querySelectorAll('a[href*="ads/library/?id="]')]
                                .map(a => { const m = a.href.match(/[?&]id=(\\d+)/); return m ? m[1] : null; })
                                .filter(Boolean);

                            return {texts, advertisers, landingUrls, adPermalinks, paidForBy, adIds};
                        }""")

                        texts         = page_data.get("texts", [])
                        advertisers   = page_data.get("advertisers", [])
                        landing_urls  = page_data.get("landingUrls", [])
                        ad_permalinks = page_data.get("adPermalinks", [])
                        paid_for_by   = page_data.get("paidForBy", [])
                        ad_ids        = page_data.get("adIds", [])

                        seen: set[str] = set()
                        for i, block in enumerate(texts):
                            b = block.strip()
                            if b and b not in seen:
                                seen.add(b)
                                record["ads"].append({
                                    "advertiser":   advertisers[i] if i < len(advertisers) else "",
                                    "paid_for_by":  paid_for_by[i] if i < len(paid_for_by) else "",
                                    "text":         b,
                                    "url":          landing_urls[i] if i < len(landing_urls) else None,
                                    "ad_permalink": ad_permalinks[i] if i < len(ad_permalinks) else "",
                                    "ad_id":        ad_ids[i] if i < len(ad_ids) else "",
                                })
                        logger.info("  FB query=%r → %d text blocks, %d advertisers, %d landing urls",
                                    query, len(record["ads"]), len(advertisers), len(landing_urls))

                except Exception as e:
                    record["error"] = str(e)
                    logger.warning("  FB query=%r → error: %s", query, e)
                finally:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass

                results.append(record)

            browser.close()
            logger.info("Facebook Ad Library scrape done — %d queries", len(results))

    except Exception as e:
        logger.error("Facebook Ad Library scrape failed: %s", e)
        results = [
            {"query": q, "search_url": _build_url(q, country, platform),
             "country": country, "ads": [], "error": str(e), "js_required": False}
            for q in _FB_QUERIES
        ]

    return results
