"""Scrape Facebook Ad Library filtered to Instagram placements."""

from __future__ import annotations
from src.facebook_scanner import scan_facebook_library


def scan_instagram_library(country: str = "SI", cookies_json: str = "") -> list[dict]:
    """Same as scan_facebook_library but restricted to Instagram placements."""
    return scan_facebook_library(country=country, platform="INSTAGRAM", cookies_json=cookies_json)
