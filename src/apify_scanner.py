"""Apify cloud-scraper integration for multiple ad sources.

Each source has a dedicated actor and a field mapper. The engine is generic:
start run → poll → fetch items → map to our internal format.

Known working actors
--------------------
Facebook Ad Library : apify~facebook-ads-library-scraper
Instagram (same lib): apify~facebook-ads-library-scraper  (platform filter in input)
Google Transparency : epctex~google-ads-transparency-center-scraper
"""

from __future__ import annotations
import logging
import time
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_BASE_URL      = "https://api.apify.com/v2"
_POLL_INTERVAL = 5    # seconds between status checks
_TIMEOUT       = 600  # max seconds to wait for a run

# Default actor IDs — overridable in Settings
ACTOR_DEFAULTS = {
    "facebook":  "apify~facebook-ads-library-scraper",
    "instagram": "apify~facebook-ads-library-scraper",
    "google":    "epctex~google-ads-transparency-center-scraper",
}


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_facebook(queries: list[str], country: str, token: str,
                   actor_id: str = "", cookies: str = "", proxy: str = "") -> list[dict]:
    return _run(queries, country, token,
                actor_id or ACTOR_DEFAULTS["facebook"],
                _build_fb_input, _map_fb_item, cookies=cookies, proxy=proxy)


def fetch_instagram(queries: list[str], country: str, token: str,
                    actor_id: str = "", cookies: str = "", proxy: str = "") -> list[dict]:
    return _run(queries, country, token,
                actor_id or ACTOR_DEFAULTS["instagram"],
                _build_ig_input, _map_fb_item, cookies=cookies, proxy=proxy)


def fetch_google(queries: list[str], country: str, token: str,
                 actor_id: str = "", proxy: str = "") -> list[dict]:
    return _run(queries, country, token,
                actor_id or ACTOR_DEFAULTS["google"],
                _build_google_input, _map_google_item, proxy=proxy)


# ── Input builders ────────────────────────────────────────────────────────────

def _build_fb_input(queries: list[str], country: str, cookies: str = "", proxy: str = "") -> dict:
    urls = [
        {
            "url": (
                "https://www.facebook.com/ads/library/?"
                + urlencode({
                    "active_status": "active",
                    "ad_type": "all",
                    "country": country,
                    "q": q,
                    "search_type": "keyword_unordered",
                    "media_type": "all",
                })
            )
        }
        for q in queries
    ]
    inp: dict = {"urls": urls, "maxResults": 100}
    parsed = _parse_cookie_str(cookies)
    if parsed:
        inp["cookies"] = parsed
        logger.info("[apify] injecting %d cookies into actor input", len(parsed))
    if proxy:
        inp["proxyConfiguration"] = {"useApifyProxy": False, "proxyUrls": [proxy]}
        logger.info("[apify] using proxy: %s", proxy)
    return inp


def _build_ig_input(queries: list[str], country: str, cookies: str = "", proxy: str = "") -> dict:
    urls = [
        {
            "url": (
                "https://www.facebook.com/ads/library/?"
                + urlencode({
                    "active_status": "active",
                    "ad_type": "all",
                    "country": country,
                    "q": q,
                    "search_type": "keyword_unordered",
                    "publisher_platforms[]": "INSTAGRAM",
                    "media_type": "all",
                })
            )
        }
        for q in queries
    ]
    inp: dict = {"urls": urls, "maxResults": 100}
    parsed = _parse_cookie_str(cookies)
    if parsed:
        inp["cookies"] = parsed
    if proxy:
        inp["proxyConfiguration"] = {"useApifyProxy": False, "proxyUrls": [proxy]}
    return inp


def _build_google_input(queries: list[str], country: str) -> dict:
    return {
        "searchQueries": queries,
        "countryCode":   country,
        "maxResults":    100,
    }


# ── Field mappers ─────────────────────────────────────────────────────────────

def _map_fb_item(item: dict, country: str) -> dict | None:
    """Map facebook-ads-library-scraper output to our ad dict."""
    # Skip error records (e.g. ADS_NOT_FOUND)
    if item.get("errorCode") or item.get("error"):
        return None

    body = (item.get("adBody") or                  # curious_coder actor
            item.get("adCreativeBody") or item.get("ad_creative_body") or
            item.get("body") or item.get("text") or "")
    if isinstance(body, list):
        body = " | ".join(b for b in body if b)
    body = str(body).strip()
    if not body:
        return None

    impr = item.get("impressionsWithIndex") or item.get("impressions") or {}
    impr_str = _fmt_range_dict(impr)

    spend = item.get("spendingWithIndex") or item.get("spend") or {}
    spend_str = _fmt_range_dict(spend)
    if isinstance(spend, dict) and spend.get("currency"):
        spend_str = (spend_str + " " + spend["currency"]).strip()

    country_pct = _delivery_pct(
        item.get("deliveryByRegion") or item.get("delivery_by_region") or [], country)

    ad_id   = str(item.get("adArchiveID") or item.get("id") or "")
    perma   = (item.get("snapshot_url") or
               (f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else ""))
    platforms = item.get("publisherPlatform") or item.get("publisher_platforms") or []

    return {
        "advertiser":       str(item.get("pageName") or item.get("page_name") or ""),
        "paid_for_by":      str(item.get("byline") or item.get("bylines") or ""),
        "text":             body,
        "url":              item.get("ctaLink") or item.get("cta_link") or None,
        "ad_id":            ad_id,
        "ad_permalink":     perma,
        "impressions":      impr_str,
        "spend_range":      spend_str,
        "country_delivery": country_pct,
        "platforms":        ", ".join(platforms) if isinstance(platforms, list) else str(platforms),
        "start_date":       str(item.get("startDate") or item.get("ad_delivery_start_time") or "")[:10],
    }


def _map_google_item(item: dict, country: str) -> dict | None:
    """Map epctex/google-ads-transparency-center-scraper output to our ad dict."""
    body = (item.get("adText") or item.get("description") or
            item.get("headline") or item.get("text") or "")
    if isinstance(body, list):
        body = " | ".join(b for b in body if b)
    body = str(body).strip()
    if not body:
        return None

    return {
        "advertiser":       str(item.get("advertiserName") or item.get("advertiser") or ""),
        "paid_for_by":      "",
        "text":             body,
        "url":              item.get("destinationUrl") or item.get("url") or None,
        "ad_id":            str(item.get("adId") or item.get("id") or ""),
        "ad_permalink":     str(item.get("adUrl") or item.get("permalink") or ""),
        "impressions":      "",
        "spend_range":      "",
        "country_delivery": "",
        "platforms":        "google",
        "start_date":       str(item.get("firstShownDate") or item.get("start_date") or "")[:10],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_cookie_str(raw: str) -> list[dict]:
    """Convert a raw cookie string or cURL command into Apify's cookie array format."""
    import re as _re
    if not raw or not raw.strip():
        return []
    raw = raw.strip()
    # If it's a cURL command, extract the Cookie header value first
    if raw.lower().startswith("curl"):
        m = (_re.search(r"-b\s+'([^']+)'", raw) or
             _re.search(r'-b\s+"([^"]+)"', raw) or
             _re.search(r"--cookie\s+'([^']+)'", raw) or
             _re.search(r'--cookie\s+"([^"]+)"', raw) or
             _re.search(r"-H\s+'[Cc]ookie:\s*([^']+)'", raw) or
             _re.search(r'-H\s+"[Cc]ookie:\s*([^"]+)"', raw))
        if m:
            raw = m.group(1).strip()
        else:
            return []
    cookies = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name:
            cookies.append({
                "name":   name,
                "value":  value,
                "domain": ".facebook.com",
                "path":   "/",
            })
    return cookies


# ── Generic engine ────────────────────────────────────────────────────────────

def _run(queries: list[str], country: str, token: str, actor_id: str,
         input_builder, item_mapper, cookies: str = "", proxy: str = "") -> list[dict]:
    try:
        import requests as req
    except ImportError:
        logger.error("[apify] requests not installed")
        return _empty_results(queries, country, "requests not installed")

    logger.info("[apify] actor=%s  country=%s  queries=%d", actor_id, country, len(queries))

    try:
        import inspect
        sig = inspect.signature(input_builder).parameters
        builder_kwargs = {}
        if "cookies" in sig:
            builder_kwargs["cookies"] = cookies
        if "proxy" in sig:
            builder_kwargs["proxy"] = proxy
        run_id = _start_run(req, actor_id, token, input_builder(queries, country, **builder_kwargs))
        logger.info("[apify] run started: %s", run_id)
        status = _wait_for_run(req, run_id, token)
        if status != "SUCCEEDED":
            logger.error("[apify] run ended with status=%s", status)
            return _empty_results(queries, country, f"run status: {status}")
        items = _get_items(req, run_id, token)
        logger.info("[apify] %d raw items fetched", len(items))
    except Exception as e:
        logger.error("[apify] error: %s", e)
        return _empty_results(queries, country, str(e))

    return _build_results(items, queries, country, item_mapper)


def _start_run(req, actor_id: str, token: str, input_data: dict) -> str:
    # Apify website shows username/actor-name; the REST API uses username~actor-name
    actor_id = actor_id.replace("/", "~", 1)
    resp = req.post(f"{_BASE_URL}/acts/{actor_id}/runs",
                    params={"token": token}, json=input_data, timeout=30)
    if resp.status_code == 404:
        raise RuntimeError(
            f"Actor not found: {actor_id!r}. "
            "Check the actor ID in Settings — copy it from apify.com/store "
            "(accepts username/actor-name or username~actor-name)."
        )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:400]
        raise RuntimeError(f"Apify {resp.status_code} starting actor {actor_id!r}: {detail}")
    resp.raise_for_status()
    data = resp.json()
    if "data" not in data:
        raise RuntimeError(f"Unexpected response: {data}")
    return data["data"]["id"]


def _wait_for_run(req, run_id: str, token: str) -> str:
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        resp = req.get(f"{_BASE_URL}/actor-runs/{run_id}",
                       params={"token": token}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        status = data["status"]
        logger.info("[apify] run %s … %s", run_id[:8], status)
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            if status != "SUCCEEDED":
                msg = data.get("statusMessage") or ""
                _fetch_run_log(req, run_id, token)
                if msg:
                    logger.error("[apify] run status message: %s", msg)
            return status
        time.sleep(_POLL_INTERVAL)
    return "TIMED-OUT"


def _fetch_run_log(req, run_id: str, token: str):
    """Fetch and log the last 30 lines of the actor run log."""
    try:
        resp = req.get(f"{_BASE_URL}/actor-runs/{run_id}/log",
                       params={"token": token}, timeout=20)
        if resp.ok:
            lines = resp.text.strip().splitlines()
            for line in lines[-30:]:
                logger.error("[apify-log] %s", line)
    except Exception as e:
        logger.warning("[apify] could not fetch run log: %s", e)


def _get_items(req, run_id: str, token: str) -> list[dict]:
    resp = req.get(f"{_BASE_URL}/actor-runs/{run_id}/dataset/items",
                   params={"token": token, "format": "json", "clean": "true"},
                   timeout=60)
    resp.raise_for_status()
    return resp.json()


def _build_results(items: list[dict], queries: list[str], country: str,
                   item_mapper) -> list[dict]:
    """Group items by searchTerm (or extract from URL) and build per-query result records."""
    from collections import defaultdict
    from urllib.parse import urlparse, parse_qs

    # Log first non-error item to help diagnose field mapping
    for _s in items:
        if not _s.get("errorCode"):
            logger.info("[apify] sample ad keys: %s", list(_s.keys()))
            logger.info("[apify] sample ad (first 500 chars): %.500s", str(_s))
            break

    by_query: dict[str, list] = defaultdict(list)
    ungrouped = []
    for item in items:
        term = (item.get("searchTerm") or item.get("query") or
                item.get("searchQuery") or "")
        if not term:
            # Fallback: extract q= from the item's own url field
            item_url = item.get("url") or item.get("pageUrl") or ""
            if item_url:
                qs = parse_qs(urlparse(item_url).query)
                term = (qs.get("q") or [""])[0]
        if term:
            by_query[term].append(item)
        else:
            ungrouped.append(item)

    results = []
    for query in queries:
        bucket = by_query.get(query, []) or (ungrouped if not by_query else [])
        ads = []
        dropped = 0
        for item in bucket:
            mapped = item_mapper(item, country)
            if mapped:
                ads.append(mapped)
            else:
                dropped += 1
        if dropped:
            logger.warning("[apify] query=%r — dropped %d items (no text body)", query, dropped)
        search_url = ("https://www.facebook.com/ads/library/?" +
                      urlencode({"active_status": "active", "ad_type": "all",
                                 "country": country, "q": query,
                                 "search_type": "keyword_unordered", "media_type": "all"}))
        results.append({"query": query, "search_url": search_url, "country": country,
                        "ads": ads, "error": None, "js_required": False})
        logger.info("[apify] query=%r → %d ads", query, len(ads))
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_range_dict(d) -> str:
    if not isinstance(d, dict):
        return ""
    lo = d.get("lowerBound") or d.get("lower_bound", "")
    hi = d.get("upperBound") or d.get("upper_bound", "")
    return f"{lo}–{hi}" if lo and hi else (f">{lo}" if lo else "")


def _delivery_pct(regions, country: str) -> str:
    for r in regions:
        if isinstance(r, dict) and r.get("region", "").upper() == country.upper():
            pct = r.get("percentage", "")
            return f"{pct}%" if pct else ""
    return ""


def _empty_results(queries: list[str], country: str, error: str) -> list[dict]:
    return [
        {"query": q,
         "search_url": ("https://www.facebook.com/ads/library/?" +
                        urlencode({"active_status": "active", "ad_type": "all",
                                   "country": country, "q": q})),
         "country": country, "ads": [], "error": error, "js_required": False}
        for q in queries
    ]
