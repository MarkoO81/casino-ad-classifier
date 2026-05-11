"""Meta Ad Library API client.

Uses the official Graph API to retrieve structured ad data including
impressions, spend ranges, and per-country delivery — information not
reliably available through Playwright scraping.

Requires an access token with ads_read permission (configured in Settings).
API reference: https://developers.facebook.com/docs/marketing-api/reference/ads-archive
"""

from __future__ import annotations
import logging
import time
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_API_VERSION = "v19.0"
_ARCHIVE_URL = f"https://graph.facebook.com/{_API_VERSION}/ads_archive"

_FIELDS = ",".join([
    "id",
    "page_name",
    "bylines",           # "Paid for by" entity (may differ from page_name)
    "impressions",       # {"lower_bound": "1000", "upper_bound": "4999"}
    "spend",             # {"lower_bound": "100", "upper_bound": "499", "currency": "EUR"}
    "delivery_by_region",# [{"region": "SI", "percentage": "82.4"}]
    "publisher_platforms",
    "ad_creative_bodies",
    "ad_delivery_start_time",
    "ad_delivery_stop_time",
])


def fetch_ads(queries: list[str], country: str, access_token: str,
              platform: str = "") -> list[dict]:
    """Fetch active casino-related ads via the Meta Ad Library API.

    Returns records in the same format as scan_facebook_library() so they
    can be processed by the same _classify_raw_ads() pipeline, but each
    ad dict carries extra fields: paid_for_by, impressions, spend_range,
    country_delivery, platforms, ad_id.
    """
    try:
        import requests as req
    except ImportError:
        logger.error("requests not installed — cannot call Meta API")
        return []

    results = []

    for query in queries:
        search_url = (
            "https://www.facebook.com/ads/library/?"
            + urlencode({"active_status": "active", "ad_type": "all",
                         "country": country, "q": query,
                         "search_type": "keyword_unordered", "media_type": "all"})
        )
        record: dict = {
            "query": query, "search_url": search_url,
            "country": country, "ads": [], "error": None, "js_required": False,
        }

        params: dict = {
            "access_token":       access_token,
            "fields":             _FIELDS,
            "search_terms":       query,
            "ad_reached_countries": country,
            "ad_type":            "ALL",
            "active_status":      "ACTIVE",
            "limit":              50,
        }
        if platform:
            params["publisher_platforms"] = platform

        try:
            resp = req.get(_ARCHIVE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                record["error"] = data["error"].get("message", str(data["error"]))
                logger.warning("Meta API error query=%r: %s", query, record["error"])
            else:
                for ad in data.get("data", []):
                    bodies = ad.get("ad_creative_bodies") or []
                    text = " | ".join(b for b in bodies if b).strip()
                    if not text:
                        continue

                    impr       = ad.get("impressions") or {}
                    spend      = ad.get("spend") or {}
                    bylines    = ad.get("bylines") or []
                    platforms  = ad.get("publisher_platforms") or []
                    regions    = ad.get("delivery_by_region") or []

                    impr_str = _fmt_range(impr.get("lower_bound"), impr.get("upper_bound"))
                    spend_str = (
                        _fmt_range(spend.get("lower_bound"), spend.get("upper_bound"))
                        + (" " + spend["currency"] if spend.get("currency") else "")
                    ).strip()

                    country_pct = next(
                        (r.get("percentage", "") for r in regions
                         if r.get("region", "").upper() == country.upper()),
                        ""
                    )

                    record["ads"].append({
                        "advertiser":       ad.get("page_name", ""),
                        "paid_for_by":      ", ".join(bylines),
                        "text":             text[:500],
                        "url":              None,
                        "ad_id":            str(ad.get("id", "")),
                        "impressions":      impr_str,
                        "spend_range":      spend_str,
                        "country_delivery": f"{country_pct}%" if country_pct else "",
                        "platforms":        ", ".join(platforms),
                        "start_date":       (ad.get("ad_delivery_start_time") or "")[:10],
                        "stop_date":        (ad.get("ad_delivery_stop_time") or "")[:10],
                    })

                logger.info("Meta API query=%r → %d ads", query, len(record["ads"]))

            time.sleep(0.25)

        except Exception as e:
            record["error"] = str(e)
            logger.warning("Meta API query=%r → %s", query, e)

        results.append(record)

    return results


def _fmt_range(lo: str | None, hi: str | None) -> str:
    if not lo:
        return ""
    return f"{lo}–{hi}" if hi else f">{lo}"
