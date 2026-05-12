"""Persistent settings loaded from config/settings.json."""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"

DEFAULTS = {
    "meta_access_token": "",
    "facebook_cookies": "",
    "apify_token": "",
    # legacy single-actor field — kept for backward compatibility
    "apify_actor_id": "apify~facebook-ads-library-scraper",
    "apify_enabled": False,
    # per-source Apify settings
    "apify_facebook_enabled":      False,
    "apify_facebook_actor_id":     "apify~facebook-ads-library-scraper",
    "apify_instagram_enabled":     False,
    "apify_instagram_actor_id":    "apify~facebook-ads-library-scraper",
    "apify_google_enabled":        False,
    "apify_google_actor_id":       "epctex~google-ads-transparency-center-scraper",
    "source_country": "SI",
    "excluded_operators": [],
    "scan_targets": [],
    "google_transparency_enabled": False,
    "facebook_library_enabled": False,
    "instagram_library_enabled": False,
    "scan_interval": "off",
}


def load() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return {**DEFAULTS, **data}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
