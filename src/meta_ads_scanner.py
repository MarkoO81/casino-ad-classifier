"""Meta Ads Collector integration for Facebook Ad Library.

Uses curl_cffi to impersonate Chrome's TLS fingerprint — bypasses bot
detection without Playwright or cookies.
"""
from __future__ import annotations
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

from src.facebook_scanner import _FB_QUERIES as _QUERIES


def scan_facebook_meta_collector(country: str = "SI", platform: str = "",
                                  proxy: str = "", stop_event=None,
                                  state_cb=None) -> list[dict]:
    """Scrape Facebook Ad Library via MetaAdsCollector (curl_cffi TLS impersonation).

    Returns the same record format as scan_facebook_library():
    [{ query, search_url, country, ads: [{advertiser, text, url, ...}], error, js_required }]
    """
    try:
        from meta_ads_collector import MetaAdsCollector
    except ImportError:
        logger.error("[meta_ads] meta-ads-collector not installed")
        return _empty(country, "meta-ads-collector not installed")

    kwargs: dict = {"rate_limit_delay": 1.5, "jitter": 0.5, "max_retries": 3}
    if proxy:
        kwargs["proxy"] = proxy
        logger.info("[meta_ads] using proxy: %s", proxy)

    collector = MetaAdsCollector(**kwargs)
    results = []
    total = len(_QUERIES)

    for i, query in enumerate(_QUERIES, 1):
        if stop_event and stop_event.is_set():
            logger.info("[meta_ads] stopped by user at query %d/%d", i, total)
            break

        if state_cb:
            state_cb(query, i, total, 0)

        search_url = ("https://www.facebook.com/ads/library/?" +
                      urlencode({"active_status": "active", "ad_type": "all",
                                 "country": country, "q": query,
                                 "search_type": "keyword_unordered", "media_type": "all"}))
        record: dict = {"query": query, "search_url": search_url, "country": country,
                        "ads": [], "error": None, "js_required": False}
        try:
            search_kwargs: dict = {
                "query":      query,
                "country":    country,
                "status":     "ACTIVE",
                "ad_type":    "ALL",
                "max_results": 100,
            }
            if platform:
                search_kwargs["search_type"] = "KEYWORD"

            ads_raw = collector.search(**search_kwargs)
            mapped = [_map(ad, country, platform) for ad in ads_raw]
            record["ads"] = [a for a in mapped if a]
            logger.info("[meta_ads] query=%r → %d ads", query, len(record["ads"]))
        except Exception as e:
            record["error"] = str(e)
            logger.error("[meta_ads] query=%r error: %s", query, e)

        results.append(record)

    return results


def _map(ad, country: str, platform: str = "") -> dict | None:
    # Skip Instagram-only ads when scanning Facebook (and vice versa)
    platforms = getattr(ad, "publisher_platforms", []) or []
    if platform == "INSTAGRAM" and "INSTAGRAM" not in [p.upper() for p in platforms]:
        return None

    # Extract text from first creative with a body
    body = ""
    for c in (getattr(ad, "creatives", []) or []):
        body = getattr(c, "body", "") or ""
        if body:
            break
    if not body:
        return None

    # Impressions
    impr = getattr(ad, "impressions", None)
    impr_str = ""
    if impr:
        lo = getattr(impr, "lower_bound", "")
        hi = getattr(impr, "upper_bound", "")
        impr_str = f"{lo}–{hi}" if lo and hi else (f">{lo}" if lo else "")

    # Spend
    spend = getattr(ad, "spend", None)
    spend_str = ""
    if spend:
        lo = getattr(spend, "lower_bound", "")
        hi = getattr(spend, "upper_bound", "")
        cur = getattr(spend, "currency", "") or ""
        spend_str = (f"{lo}–{hi}" if lo and hi else (f">{lo}" if lo else ""))
        if cur:
            spend_str = f"{spend_str} {cur}".strip()

    # Country delivery %
    country_pct = ""
    for r in (getattr(ad, "region_distribution", []) or []):
        region = getattr(r, "region", "") or ""
        if region.upper() == country.upper():
            pct = getattr(r, "percentage", "")
            country_pct = f"{pct}%" if pct else ""
            break

    # Landing URL from first creative
    link_url = None
    for c in (getattr(ad, "creatives", []) or []):
        link_url = getattr(c, "link_url", None)
        if link_url:
            break

    page = getattr(ad, "page", None)
    ad_id = str(getattr(ad, "id", "") or "")
    snap = getattr(ad, "snapshot_url", None) or getattr(ad, "ad_snapshot_url", None) or ""
    start = getattr(ad, "delivery_start_time", None)

    return {
        "advertiser":       str(getattr(page, "name", "") or ""),
        "paid_for_by":      str(getattr(ad, "funding_entity", "") or
                                ", ".join(getattr(ad, "bylines", []) or [])),
        "text":             body,
        "url":              link_url,
        "ad_id":            ad_id,
        "ad_permalink":     snap or (f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else ""),
        "impressions":      impr_str,
        "spend_range":      spend_str,
        "country_delivery": country_pct,
        "platforms":        ", ".join(platforms),
        "start_date":       start.isoformat()[:10] if start else "",
    }


def _empty(country: str, error: str) -> list[dict]:
    return [
        {"query": q,
         "search_url": ("https://www.facebook.com/ads/library/?" +
                        urlencode({"active_status": "active", "ad_type": "all",
                                   "country": country, "q": q})),
         "country": country, "ads": [], "error": error, "js_required": False}
        for q in _QUERIES
    ]
