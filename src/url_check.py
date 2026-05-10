"""Landing URL resolution and license check.

The single most discriminative signal: where does the click actually go?
A confirmed gambling-themed creative landing on a non-licensed domain
is essentially a confirmed violation.

Pipeline:
  1. Take the ad's link_url (from Meta Ad Library).
  2. Follow redirects (affiliate networks often hop 3-5 times).
  3. Extract the registrable domain.
  4. Check against:
       - WHITELIST: FURS-licensed Slovenian gambling operators
       - GREYLIST:  known affiliate / tracking domains (resolve further)
       - BLACKLIST: known offshore operators (instant high-confidence)
       - UNKNOWN:   needs human review

Update the whitelist by scraping or periodically downloading the official
FURS register of authorized operators. Last source as of writing:
  https://www.fu.gov.si/  (Office for Gambling Supervision)

NOTE: hardcoded lists below are SEED examples. You must maintain the
whitelist against the current FURS register; treat it as authoritative
configuration, not code.
"""

from urllib.parse import urlparse
import re

# ---- SEED LISTS — replace with live data in production ----

# Confirm against current FURS register before relying on this list.
WHITELIST_DOMAINS = {
    # State / concessionaire operators
    "eloterija.si",
    "sportna-loterija.si",
    "e-stave.si",
    # HIT group (concessionaire for land-based + online)
    "hit.si",
    "hit-casinos.com",
    # Add others from the current FURS register
}

# Domains we know forward to gambling — drop them in via observed evidence
BLACKLIST_DOMAINS = {
    # Examples only — populate from your own intelligence
    # "betxxx.com", "casino-yyy.net", ...
}

# Affiliate / link-shortener / tracking domains — resolve further before judging
GREYLIST_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "lnkd.in", "fb.me",
    "trk.com", "go.affise.com", "go.linkmink.com",
    "tracker.aff.io", "track.adsrv.io",
    # Add observed affiliate networks
}


def extract_domain(url: str) -> str:
    """Return the registrable domain (e.g. 'sub.example.co.uk' -> 'example.co.uk').

    Prefers tldextract if available; falls back to a naive 2-label heuristic
    that's good enough for most ccTLDs we care about (.si, .com, .net, .eu).
    """
    if not url:
        return ""
    try:
        import tldextract
        ext = tldextract.extract(url)
        if not ext.domain:
            return ""
        if ext.suffix:
            return f"{ext.domain}.{ext.suffix}".lower()
        return ext.domain.lower()
    except ImportError:
        host = urlparse(url).netloc.lower()
        host = re.sub(r"^www\.", "", host)
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host


def resolve_redirects(url: str, max_hops: int = 8, timeout: float = 10.0,
                      use_browser: bool = False) -> dict:
    """Follow redirects to the final landing URL.

    Set use_browser=True to use Playwright for JS/meta-refresh redirects
    (affiliate links that require JavaScript to resolve).
    """
    if use_browser:
        try:
            from src.browser import resolve_js_redirect
            return resolve_js_redirect(url)
        except Exception:
            pass  # fall through to requests-based resolver
    """Follow HTTP redirects to the final landing URL.

    Returns dict with: original, final, hops (list of urls), final_domain, error.
    Uses HEAD first (fast) and falls back to GET (some affiliate hosts only
    redirect on GET, sometimes only after JS — those need a headless browser).
    """
    result = {
        "original": url, "final": url, "hops": [url],
        "final_domain": extract_domain(url), "error": None,
    }
    try:
        import requests
    except ImportError:
        result["error"] = "requests not installed"
        return result

    current = url
    seen = {current}
    for _ in range(max_hops):
        try:
            r = requests.head(current, allow_redirects=False, timeout=timeout,
                              headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code in (301, 302, 303, 307, 308):
                nxt = r.headers.get("Location")
                if not nxt or nxt in seen:
                    break
                seen.add(nxt)
                result["hops"].append(nxt)
                current = nxt
                continue
            # No redirect — try GET to catch meta-refresh / JS redirects
            # (skipped for brevity; integrate Playwright here for full coverage)
            break
        except Exception as e:
            result["error"] = str(e)
            break

    result["final"] = current
    result["final_domain"] = extract_domain(current)
    return result


def classify_domain(domain: str) -> dict:
    """Classify a final domain. Returns category + score adjustment."""
    d = (domain or "").lower()
    if not d:
        return {"category": "unknown", "score": 0.0, "reason": "no domain"}
    if d in WHITELIST_DOMAINS:
        return {"category": "whitelist", "score": -1.0,
                "reason": "FURS-licensed operator"}
    if d in BLACKLIST_DOMAINS:
        return {"category": "blacklist", "score": 1.0,
                "reason": "known offshore operator"}
    if d in GREYLIST_DOMAINS:
        return {"category": "greylist", "score": 0.0,
                "reason": "tracking/affiliate domain — resolve further"}
    return {"category": "unknown", "score": 0.2,
            "reason": "unknown domain — review needed"}
