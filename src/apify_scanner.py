"""Apify Facebook Ad Library actor integration.

Runs the Apify Facebook Ads Library actor, waits for completion, and
returns results in the same format as scan_facebook_library() so the
existing _classify_raw_ads() pipeline works unchanged.

Actor used: apify/facebook-ads-library-scraper
Docs: https://apify.com/apify/facebook-ads-library-scraper
"""

from __future__ import annotations
import logging
import time

logger = logging.getLogger(__name__)

_BASE_URL    = "https://api.apify.com/v2"
_DEFAULT_ACTOR = "apify~facebook-ads-library-scraper"
_POLL_INTERVAL = 5   # seconds between status checks
_TIMEOUT       = 600  # max seconds to wait for run to finish


def fetch_ads(queries: list[str], country: str, token: str,
              actor_id: str = _DEFAULT_ACTOR) -> list[dict]:
    """Run Apify actor for each query and return results in scan_facebook_library() format."""
    try:
        import requests as req
    except ImportError:
        logger.error("requests not installed — cannot call Apify API")
        return _empty_results(queries, country, "requests not installed")

    results = []
    logger.info("[apify] Starting — actor=%s  country=%s  queries=%d", actor_id, country, len(queries))

    # Batch all queries into a single actor run to save credits
    actor_input = {
        "searchTerms":  queries,
        "countryCode":  country,
        "adType":       "ALL",
        "activeStatus": "ACTIVE",
        "maxResults":   100,
    }

    try:
        run_id = _start_run(req, actor_id, token, actor_input)
        logger.info("[apify] Run started: %s", run_id)

        status = _wait_for_run(req, run_id, token)
        if status != "SUCCEEDED":
            logger.error("[apify] Run ended with status=%s", status)
            return _empty_results(queries, country, f"run status: {status}")

        items = _get_items(req, run_id, token)
        logger.info("[apify] Run succeeded — %d items fetched", len(items))

        results = _map_items(items, queries, country)

    except Exception as e:
        logger.error("[apify] Error: %s", e)
        return _empty_results(queries, country, str(e))

    return results


def _start_run(req, actor_id: str, token: str, input_data: dict) -> str:
    resp = req.post(
        f"{_BASE_URL}/acts/{actor_id}/runs",
        params={"token": token},
        json=input_data,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "data" not in data:
        raise RuntimeError(f"Unexpected response: {data}")
    return data["data"]["id"]


def _wait_for_run(req, run_id: str, token: str) -> str:
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        resp = req.get(
            f"{_BASE_URL}/actor-runs/{run_id}",
            params={"token": token},
            timeout=15,
        )
        resp.raise_for_status()
        status = resp.json()["data"]["status"]
        logger.info("[apify] Run %s status: %s", run_id[:8], status)
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return status
        time.sleep(_POLL_INTERVAL)
    return "TIMED-OUT"


def _get_items(req, run_id: str, token: str) -> list[dict]:
    resp = req.get(
        f"{_BASE_URL}/actor-runs/{run_id}/dataset/items",
        params={"token": token, "format": "json", "clean": "true"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _map_items(items: list[dict], queries: list[str], country: str) -> list[dict]:
    """Map Apify actor output to our scan_facebook_library() record format.

    The apify/facebook-ads-library-scraper actor returns items with fields like:
    adArchiveID, pageID, pageName, startDate, endDate, adCreativeBody,
    ctaText, ctaType, impressionsWithIndex, spendingWithIndex, etc.
    We map these to our internal ad record format grouped by query.
    """
    # Group items back by search term if actor provides it, else use first query
    from collections import defaultdict
    by_query: dict[str, list] = defaultdict(list)

    for item in items:
        # Different actor versions use different field names
        term = (item.get("searchTerm") or item.get("query") or
                item.get("searchQuery") or queries[0] if queries else "")
        by_query[term].append(item)

    # Build result records in scan_facebook_library() format
    results = []
    for query in queries:
        ads = []
        for item in by_query.get(query, []) or by_query.get("", []):
            # Ad body text — try multiple field names used by different actor versions
            body = (item.get("adCreativeBody") or
                    item.get("ad_creative_body") or
                    item.get("body") or
                    item.get("text") or "")
            if isinstance(body, list):
                body = " | ".join(b for b in body if b)
            body = str(body).strip()
            if not body:
                continue

            # Impressions
            impr = item.get("impressionsWithIndex") or item.get("impressions") or {}
            if isinstance(impr, dict):
                lo = impr.get("lowerBound") or impr.get("lower_bound", "")
                hi = impr.get("upperBound") or impr.get("upper_bound", "")
                impr_str = f"{lo}–{hi}" if lo and hi else (f">{lo}" if lo else "")
            else:
                impr_str = str(impr) if impr else ""

            # Spend
            spend = item.get("spendingWithIndex") or item.get("spend") or {}
            if isinstance(spend, dict):
                lo = spend.get("lowerBound") or spend.get("lower_bound", "")
                hi = spend.get("upperBound") or spend.get("upper_bound", "")
                cur = spend.get("currency", "")
                spend_str = (f"{lo}–{hi}" if lo and hi else (f">{lo}" if lo else ""))
                if cur:
                    spend_str += f" {cur}"
            else:
                spend_str = ""

            # Country delivery %
            delivery = item.get("deliveryByRegion") or item.get("delivery_by_region") or []
            country_pct = ""
            if isinstance(delivery, list):
                for r in delivery:
                    if isinstance(r, dict) and r.get("region", "").upper() == country.upper():
                        country_pct = f"{r.get('percentage', '')}%"
                        break

            # Ad permalink
            ad_id = str(item.get("adArchiveID") or item.get("id") or "")
            permalink = (item.get("snapshot_url") or
                         (f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else ""))

            ads.append({
                "advertiser":       str(item.get("pageName") or item.get("page_name") or ""),
                "paid_for_by":      str(item.get("byline") or item.get("bylines") or ""),
                "text":             body,
                "url":              item.get("ctaLink") or item.get("cta_link") or None,
                "ad_id":            ad_id,
                "ad_permalink":     permalink,
                "impressions":      impr_str,
                "spend_range":      spend_str.strip(),
                "country_delivery": country_pct,
                "platforms":        ", ".join(item.get("publisherPlatform") or
                                              item.get("publisher_platforms") or []),
                "start_date":       str(item.get("startDate") or item.get("ad_delivery_start_time") or "")[:10],
            })

        from urllib.parse import urlencode
        search_url = ("https://www.facebook.com/ads/library/?" +
                      urlencode({"active_status": "active", "ad_type": "all",
                                 "country": country, "q": query,
                                 "search_type": "keyword_unordered", "media_type": "all"}))
        results.append({
            "query":      query,
            "search_url": search_url,
            "country":    country,
            "ads":        ads,
            "error":      None,
            "js_required": False,
        })
        logger.info("[apify] query=%r → %d ads", query, len(ads))

    return results


def _empty_results(queries: list[str], country: str, error: str) -> list[dict]:
    from urllib.parse import urlencode
    return [
        {"query": q,
         "search_url": ("https://www.facebook.com/ads/library/?" +
                        urlencode({"active_status": "active", "ad_type": "all",
                                   "country": country, "q": q})),
         "country": country, "ads": [], "error": error, "js_required": False}
        for q in queries
    ]
