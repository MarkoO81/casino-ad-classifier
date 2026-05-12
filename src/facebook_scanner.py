"""Scrape Facebook Ad Library for casino-related active ads."""

from __future__ import annotations
import logging
import random
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


def _random_fingerprint() -> dict:
    """Return a randomised but realistic browser fingerprint for each session."""
    chrome_major = random.randint(130, 136)
    chrome_build  = random.randint(6000, 6999)
    chrome_patch  = random.randint(0, 200)
    chrome_ver    = f"{chrome_major}.0.{chrome_build}.{chrome_patch}"

    os_templates = [
        # Windows 10 / 11 variants
        f"Windows NT 10.0; Win64; x64",
        f"Windows NT 10.0; Win64; x64",   # weight Windows higher (most common)
        # macOS — randomise minor versions 10.15.x and 11-14
        f"Macintosh; Intel Mac OS X 10_15_{random.randint(6, 7)}",
        f"Macintosh; Intel Mac OS X {random.randint(11, 14)}_0",
        # Linux desktop
        f"X11; Linux x86_64",
    ]
    os_str = random.choice(os_templates)

    ua = (
        f"Mozilla/5.0 ({os_str}) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_ver} Safari/537.36"
    )

    # Common desktop resolutions
    viewports = [
        (1920, 1080), (1920, 1080),  # most common
        (1440, 900), (1536, 864),
        (1366, 768), (1280, 800),
        (2560, 1440),
    ]
    w, h = random.choice(viewports)

    # Vary Accept-Language slightly
    languages = [
        "en-US,en;q=0.9",
        "en-US,en;q=0.9,sl;q=0.8",
        "en-GB,en;q=0.9",
        "en-US,en;q=0.9,hr;q=0.7",
    ]

    return {
        "user_agent": ua,
        "viewport":   {"width": w, "height": h},
        "screen":     {"width": w, "height": h},
        "language":   random.choice(languages),
    }


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


def _parse_cookies(raw: str) -> list[dict]:
    """Parse cookies from cURL command, raw Cookie header, or JSON array."""
    import re, json as _json

    raw = raw.strip()
    logger.info("  Cookie input: %d chars, starts with: %r", len(raw), raw[:80])

    # ── JSON array ──────────────────────────────────────────────────────────
    if raw.startswith("["):
        try:
            items = _json.loads(raw)
            result = [
                {"name": c["name"], "value": c["value"],
                 "domain": c.get("domain", ".facebook.com"),
                 "path": c.get("path", "/"), "secure": c.get("secure", True),
                 "httpOnly": c.get("httpOnly", False), "sameSite": "None"}
                for c in items if c.get("name") and c.get("value")
            ]
            logger.info("  Cookie parse: JSON → %d cookies", len(result))
            return result
        except Exception as e:
            logger.warning("  Cookie parse: JSON failed: %s", e)
            return []

    # ── cURL command ─────────────────────────────────────────────────────────
    cookie_str = ""
    if raw.lower().startswith("curl"):
        # Log every -H header name so we can see what's present
        header_names = re.findall(r"-H\s+[\$]?['\"]([^:]+):", raw, re.IGNORECASE)
        logger.info("  Cookie parse: found -H headers: %s", header_names)

        # Match -b / --cookie (short and long form) and -H cookie: header
        m = (re.search(r"-b\s+'([^']+)'", raw) or                    # -b '...' short form
             re.search(r'-b\s+"([^"]+)"', raw) or
             re.search(r"--cookie\s+'([^']+)'", raw) or
             re.search(r'--cookie\s+"([^"]+)"', raw) or
             re.search(r"-H\s+\$'[Cc]ookie:\s*([^']+)'", raw) or    # $'...' ANSI quoting
             re.search(r"-H\s+'[Cc]ookie:\s*([^']+)'", raw) or
             re.search(r'-H\s+"[Cc]ookie:\s*([^"]+)"', raw))
        if m:
            cookie_str = m.group(1).strip()
            logger.info("  Cookie parse: extracted from cURL, %d chars", len(cookie_str))
        else:
            logger.warning("  Cookie parse: cURL detected but no Cookie header found")
            logger.warning("  Cookie input snippet: %r", raw[:200])
            return []
    elif raw.lower().startswith("cookie:"):
        cookie_str = raw[7:].strip()
        logger.info("  Cookie parse: raw Cookie header")
    else:
        cookie_str = raw
        logger.info("  Cookie parse: bare name=value string")

    if not cookie_str:
        return []

    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if name and value:
            cookies.append({
                "name": name, "value": value,
                "domain": ".facebook.com", "path": "/",
                "secure": True, "httpOnly": False, "sameSite": "None",
            })
    logger.info("  Cookie parse: %d cookies found", len(cookies))
    return cookies


def scan_facebook_library(country: str = "SI", platform: str = "",
                          cookies_json: str = "", stop_event=None,
                          state_cb=None, proxy: str = "") -> list[dict]:
    """Scrape Facebook Ad Library for casino-related active ads.

    Pass platform="INSTAGRAM" to restrict to Instagram placements.
    Pass cookies_json (JSON array from Cookie-Editor export) to authenticate
    and bypass the login wall.
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
            fp = _random_fingerprint()
            logger.info("  FB fingerprint: UA=%s  viewport=%sx%s",
                        fp["user_agent"], fp["viewport"]["width"], fp["viewport"]["height"])

            launch_kwargs: dict = {
                "headless": True,
                "args": [
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    f"--window-size={fp['viewport']['width']},{fp['viewport']['height']}",
                ],
            }
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}
                logger.info("  FB using proxy: %s", proxy)
            browser = p.chromium.launch(**launch_kwargs)
            ctx = browser.new_context(
                user_agent=fp["user_agent"],
                locale="en-US",
                viewport=fp["viewport"],
                screen=fp["screen"],
                extra_http_headers={
                    "Accept-Language": fp["language"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-User": "?1",
                    "Sec-Fetch-Dest": "document",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            ctx.add_init_script("""
                // navigator.webdriver
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

                // Realistic plugin list
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const arr = [
                            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                        ];
                        arr.__proto__ = PluginArray.prototype;
                        return arr;
                    }
                });

                // Realistic mimeTypes
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: () => { const a = []; a.__proto__ = MimeTypeArray.prototype; return a; }
                });

                // Non-zero hardware concurrency
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

                // Languages
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

                // Chrome runtime object
                window.chrome = {
                    app: { isInstalled: false, InstallState: { DISABLED:'a',INSTALLED:'b',NOT_INSTALLED:'c' }, RunningState: { CANNOT_RUN:'a',READY_TO_RUN:'b',RUNNING:'c' } },
                    runtime: { OnInstalledReason: {}, OnRestartRequiredReason: {}, PlatformArch: {}, PlatformNaclArch: {}, PlatformOs: {}, RequestUpdateCheckStatus: {} },
                    loadTimes: function() { return { commitLoadTime: Date.now()/1000-2, connectionInfo: 'h2', finishDocumentLoadTime: Date.now()/1000-1, finishLoadTime: Date.now()/1000, firstPaintAfterLoadTime: 0, firstPaintTime: Date.now()/1000-1.5, navigationType: 'Other', npnNegotiatedProtocol: 'h2', requestTime: Date.now()/1000-3, startLoadTime: Date.now()/1000-3, wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true, wasNpnNegotiated: true }; },
                    csi: function() { return { onloadT: Date.now(), pageT: Date.now()-2000, startE: Date.now()-3000, tran: 15 }; },
                };

                // Permission query — real Chrome resolves 'notifications'
                const origQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : origQuery(params);

                // Spoof WebGL vendor/renderer
                const origGetParam = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {
                    if (param === 37445) return 'Intel Inc.';
                    if (param === 37446) return 'Intel Iris OpenGL Engine';
                    return origGetParam.call(this, param);
                };
            """)

            # Inject session cookies if provided — bypasses the login wall entirely
            if cookies_json and cookies_json.strip():
                playwright_cookies = _parse_cookies(cookies_json)
                if playwright_cookies:
                    ctx.add_cookies(playwright_cookies)
                    logger.info("  FB session cookies injected (%d cookies)", len(playwright_cookies))
                else:
                    logger.warning("  FB cookies: could not parse any cookies from input")

            # Accept cookie consent once so it persists across pages
            consent_page = ctx.new_page()
            try:
                consent_page.goto(_LIBRARY_URL, wait_until="domcontentloaded", timeout=20000)
                consent_page.wait_for_timeout(3000)
                final_url = consent_page.url
                logger.info("  Consent page: %s", final_url)
                if not _try_accept_consent(consent_page):
                    logger.info("  No consent popup found — continuing")
                else:
                    consent_page.wait_for_timeout(1500)
            except Exception as e:
                logger.warning("  Consent page failed: %s", e)
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
                if stop_event and stop_event.is_set():
                    logger.info("  Scan stopped by user after query %d", len(results))
                    break

                try:
                    q_num = _FB_QUERIES.index(query) + 1 if query in _FB_QUERIES else "?"
                    logger.info("  [%s/%s] query=%r", q_num, len(_FB_QUERIES), query)
                    if state_cb:
                        state_cb(query, q_num, len(_FB_QUERIES), 0)
                    page = ctx.new_page()
                    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(4000)

                    # Re-check consent mid-session
                    _try_accept_consent(page)

                    # Detect redirect away from facebook.com (e.g. to login.facebook.com)
                    landed_url = page.url
                    if "facebook.com/ads/library" not in landed_url:
                        record["js_required"] = True
                        logger.info("  FB query=%r → redirected away from Ad Library: %s", query, landed_url)

                    page_text = page.inner_text("body") or ""
                    body_len = len(page_text.strip())

                    login_wall = (
                        "log in" in page_text.lower() and
                        "ad library" not in page_text.lower() and
                        body_len < 4000
                    )

                    if body_len < 500 or login_wall:
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
