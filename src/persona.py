"""Persona simulation for casino ad targeting research.

Maintains Playwright persistent browser profiles that accumulate gambling-related
browsing history so Google's ad targeting shows more casino ads to those profiles.
Each persona lives in config/personas/{name}/browser/ (Playwright user-data-dir).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

_PERSONAS_BASE = Path(__file__).parent.parent / "config" / "personas"

# Google searches that build up a gambling-interest signal
_WARM_SEARCHES = [
    "https://www.google.com/search?q=online+casino+bonus&hl=sl",
    "https://www.google.com/search?q=free+spins+casino&hl=sl",
    "https://www.google.com/search?q=spletna+igralnica+brezpla%C4%8Dna+vrtenja&hl=sl",
    "https://www.google.com/search?q=casino+online+signup+bonus&hl=sl",
    "https://www.google.com/search?q=kockarnica+online+bonus&hl=sl",
]


@dataclass
class PersonaStatus:
    name: str
    last_warmed: str | None = None
    cookie_count: int = 0
    domains_visited: list = field(default_factory=list)
    warm_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_warm(self) -> bool:
        return self.last_warmed is not None


def _ensure_base() -> Path:
    _PERSONAS_BASE.mkdir(parents=True, exist_ok=True)
    return _PERSONAS_BASE


def _status_path(name: str) -> Path:
    return _PERSONAS_BASE / name / "status.json"


def _data_dir(name: str) -> Path:
    d = _PERSONAS_BASE / name / "browser"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_status(name: str) -> PersonaStatus:
    p = _status_path(name)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            return PersonaStatus(
                name=d.get("name", name),
                last_warmed=d.get("last_warmed"),
                cookie_count=d.get("cookie_count", 0),
                domains_visited=d.get("domains_visited", []),
                warm_count=d.get("warm_count", 0),
            )
        except Exception:
            pass
    return PersonaStatus(name=name)


def _save_status(status: PersonaStatus) -> None:
    p = _status_path(status.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(status.to_dict(), indent=2))


def list_personas() -> list[PersonaStatus]:
    base = _ensure_base()
    result = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            result.append(_load_status(d.name))
    return result


def create_persona(name: str) -> PersonaStatus:
    name = name.strip().lower().replace(" ", "_").replace("-", "_")
    if not name:
        raise ValueError("Persona name cannot be empty")
    _data_dir(name)
    status = PersonaStatus(name=name)
    _save_status(status)
    return status


def delete_persona(name: str) -> None:
    d = _PERSONAS_BASE / name
    if d.exists():
        shutil.rmtree(d)


def warm_persona(name: str) -> PersonaStatus:
    """Visit casino-related Google searches to build up this persona's ad profile.

    Uses a persistent Chromium context (stored in config/personas/{name}/browser/)
    so cookies and history accumulate across warm-up runs.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright not installed")

    data_dir = str(_data_dir(name))
    new_domains: set[str] = set()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=data_dir,
            headless=True,
            args=["--no-sandbox"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="sl-SI",
            timezone_id="Europe/Ljubljana",
            geolocation={"latitude": 46.0569, "longitude": 14.5058},
            permissions=["geolocation"],
        )
        page = ctx.new_page()

        for search_url in _WARM_SEARCHES:
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                new_domains.add("google.com")
                # Follow first non-Google result to deepen the signal
                try:
                    links = page.query_selector_all("a[href^='http']:not([href*='google'])")
                    for link in links[:1]:
                        href = link.get_attribute("href") or ""
                        if href.startswith("http"):
                            new_domains.add(href.split("/")[2])
                            page.goto(href, wait_until="domcontentloaded", timeout=15000)
                            page.wait_for_timeout(1500)
                            break
                except Exception:
                    pass
            except Exception:
                pass

        cookie_count = len(ctx.cookies())
        ctx.close()

    status = _load_status(name)
    status.last_warmed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    status.cookie_count = cookie_count
    status.domains_visited = sorted(set(status.domains_visited) | new_domains)
    status.warm_count += 1
    _save_status(status)
    return status


def scrape_as_persona(name: str, country: str = "SI") -> list[dict]:
    """Scrape Google Ads Transparency Center using persona's persistent browser profile.

    Reuses the same persistent context for all queries so cookies carry over.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        from src.browser import scrape_transparency
        return scrape_transparency(country)

    from urllib.parse import urlencode
    from src.browser import _CASINO_QUERIES

    data_dir = str(_data_dir(name))
    results = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=data_dir,
            headless=True,
            args=["--no-sandbox"],
            locale="sl-SI",
            timezone_id="Europe/Ljubljana",
        )

        for query in _CASINO_QUERIES:
            params = urlencode({"query": query, "region": country})
            search_url = f"https://adstransparency.google.com/?{params}"
            record = {
                "query": query,
                "search_url": search_url,
                "country": country,
                "ads": [],
                "error": None,
                "js_required": False,
            }
            page = None
            try:
                page = ctx.new_page()
                page.goto(search_url, wait_until="networkidle", timeout=20000)
                try:
                    page.wait_for_selector(
                        "[class*='creative'], [class*='ad-card'], [class*='AdCard'], [class*='result']",
                        timeout=8000,
                    )
                except Exception:
                    pass

                text_blocks = page.eval_on_selector_all(
                    "p, span, div[class*='text'], div[class*='body'], div[class*='creative'] *",
                    "els => [...new Set(els.map(e => e.innerText.trim()).filter(t => t.length > 20 && t.length < 500))]",
                )
                ad_links = page.eval_on_selector_all(
                    "a[href*='google.com/aclk'], a[href*='adclick'], a[href^='http']:not([href*='transparency'])",
                    "els => els.map(e => e.href)",
                )
                page_text = page.inner_text("body") or ""

                if len(page_text.strip()) < 300:
                    record["js_required"] = True
                else:
                    seen: set[str] = set()
                    for block in text_blocks:
                        b = block.strip()
                        if b and b not in seen:
                            seen.add(b)
                            record["ads"].append({
                                "advertiser": "",
                                "text": b,
                                "url": ad_links[len(seen) - 1] if len(seen) <= len(ad_links) else None,
                            })

            except Exception as e:
                record["error"] = str(e)
            finally:
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass

            results.append(record)

        ctx.close()

    return results
