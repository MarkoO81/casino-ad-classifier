"""Background scheduler for periodic ad scanning."""

from __future__ import annotations
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_PATH      = Path(__file__).parent.parent / "config" / "scan_history.json"
LAST_RESULTS_PATH = Path(__file__).parent.parent / "config" / "last_scan_results.json"
MAX_HISTORY = 48  # keep 2 days at 1h intervals

INTERVALS = {"off": 0, "1h": 1, "4h": 4, "8h": 8, "24h": 24}

_scheduler  = None
_stop_event = threading.Event()
_state_lock = threading.Lock()
_scan_state: dict = {
    "running":     False,
    "source":      "",
    "query":       "",
    "query_num":   0,
    "query_total": 0,
    "ads_found":   0,
    "elapsed":     0.0,
    "start_ts":    "",
    "_start_mono": 0.0,
    "log_lines":   deque(maxlen=40),
}


def _set_state(**kwargs):
    with _state_lock:
        _scan_state.update(kwargs)


def _log_state(msg: str):
    logger.info(msg)
    with _state_lock:
        _scan_state["log_lines"].append(msg)


def get_status() -> dict:
    with _state_lock:
        s = {k: v for k, v in _scan_state.items() if k != "_start_mono"}
        s["log_lines"] = list(_scan_state["log_lines"])
        if _scan_state["running"] and _scan_state["_start_mono"]:
            s["elapsed"] = round(time.monotonic() - _scan_state["_start_mono"], 1)
        s["stopped_requested"] = _stop_event.is_set()
        return s


def stop_scan():
    _stop_event.set()
    logger.info("Stop requested by user")


def _fb_state_cb(query: str, num: int, total: int, ads: int):
    """Callback from facebook_scanner to update per-query progress."""
    _set_state(query=query, query_num=num, query_total=total, ads_found=ads)


def _classify_raw_ads(raw: list, source: str, ts: str) -> list:
    """Classify ad records from Google/Facebook scrapers, tag with source."""
    from src.classifier import GamblingAdClassifier
    clf = GamblingAdClassifier(resolve_urls=False)
    records = []
    for query_result in raw:
        if query_result.get("error") or query_result.get("js_required"):
            continue
        for ad in query_result.get("ads", []):
            text = (ad.get("text") or "").strip()
            if not text:
                continue
            # Don't pass ad-library URLs to classifier — they resolve to facebook.com/google.com
            # and pollute the final_domain field. Only pass genuine landing URLs.
            link_url = ad.get("url") or None
            _lib_hosts = ("facebook.com/ads", "adstransparency.google.com")
            if link_url and any(h in link_url for h in _lib_hosts):
                link_url = None

            res = clf.classify(ad_text=text, link_url=link_url)
            records.append({
                "ts":               ts,
                "page_name":        query_result.get("query", ""),
                "advertiser":       ad.get("advertiser") or "",
                "paid_for_by":      ad.get("paid_for_by") or "",
                "search_url":       query_result.get("search_url", ""),
                "landing_url":      link_url or "",
                "ad_id":            ad.get("ad_id") or "",
                "ad_permalink":     ad.get("ad_permalink") or "",
                "impressions":      ad.get("impressions") or "",
                "spend_range":      ad.get("spend_range") or "",
                "country_delivery": ad.get("country_delivery") or "",
                "platforms":        ad.get("platforms") or "",
                "start_date":       ad.get("start_date") or "",
                "score":            round(res.score, 2),
                "label":            res.label,
                "final_domain":     res.final_domain or "",
                "ad_text":          text,
                "source":           source,
                "raw_signals":      [{"name": s.name, "weight": round(s.weight, 2), "detail": s.detail}
                                     for s in res.signals],
            })
    return records


def _run_scan():
    from src import config as cfg
    from src.web_scanner import scan_url
    from src.google_scanner import scan_transparency_center
    from examples.process_ad import process_ad
    import src.url_check as url_check

    scan_start = time.monotonic()
    _stop_event.clear()
    _set_state(running=True, source="", query="", query_num=0, query_total=0,
               ads_found=0, start_ts=datetime.now().strftime("%H:%M:%S"),
               _start_mono=scan_start, log_lines=deque(maxlen=40))
    try:
        _run_scan_inner(cfg, scan_url, scan_transparency_center, process_ad)
    except Exception as e:
        _log_state(f"SCAN ERROR: {e}")
        logger.exception("Scan crashed")
    finally:
        _set_state(running=False)


def _run_scan_inner(cfg, scan_url, scan_transparency_center, process_ad):
    import src.url_check as url_check

    scan_start = time.monotonic()
    settings = cfg.load()
    country = settings.get("source_country", "SI")

    extra = {op["domain"] for op in settings.get("excluded_operators", []) if op.get("domain")}
    url_check.WHITELIST_DOMAINS.update(extra)

    # ── Determine which sources are active ──────────────────────────────────
    active_sources = []
    if settings.get("scan_targets"):                        active_sources.append("web")
    if settings.get("google_transparency_enabled"):         active_sources.append("google")
    if settings.get("apify_facebook_enabled"):              active_sources.append("apify-facebook")
    if settings.get("apify_instagram_enabled"):             active_sources.append("apify-instagram")
    if settings.get("apify_google_enabled"):                active_sources.append("apify-google")
    if settings.get("apify_enabled"):                       active_sources.append("apify")  # legacy
    if settings.get("facebook_library_enabled"):            active_sources.append("facebook")
    if settings.get("instagram_library_enabled"):           active_sources.append("instagram")

    token        = settings.get("meta_access_token", "").strip()
    cookies      = settings.get("facebook_cookies", "").strip()
    apify_token  = settings.get("apify_token", "").strip()
    apify_actor  = settings.get("apify_actor_id", "apify~facebook-ads-library-scraper").strip()

    apify_fb_actor  = settings.get("apify_facebook_actor_id",  "apify~facebook-ads-library-scraper").strip() or apify_actor
    apify_ig_actor  = settings.get("apify_instagram_actor_id", "apify~facebook-ads-library-scraper").strip() or apify_actor
    apify_ggl_actor = settings.get("apify_google_actor_id",    "epctex~google-ads-transparency-center-scraper").strip()

    _log_state(f"SCAN START  country={country}  sources={active_sources or ['none']}")
    if apify_token:
        _log_state(f"  Apify token: configured")
    if token:
        _log_state("  Meta API token: configured")
    elif cookies:
        _log_state(f"  Facebook cookies: configured ({len(cookies)} chars)")
    else:
        _log_state("  Facebook cookies: none — unauthenticated")
    if extra:
        _log_state(f"  Whitelisted domains: {len(extra)}")

    ts = datetime.now().isoformat(timespec="seconds")
    all_results = []
    sources = set()
    pages_scanned = 0

    # ── Web targets ─────────────────────────────────────────────────────────
    for target in settings.get("scan_targets", []):
        if _stop_event.is_set():
            _log_state("Scan stopped by user")
            break
        url = (target.get("url") or "").strip()
        if not url:
            continue
        pages_scanned += 1
        t0 = time.monotonic()
        _set_state(source="web", query=url)
        _log_state(f"[web] Scanning {url}")
        ads = list(scan_url(url))
        for ad in ads:
            r = process_ad(ad, image_path=None, clip=None, ocr=None)
            r["source"] = "web"
            r["ts"] = ts
            all_results.append(r)
        _set_state(ads_found=len(all_results))
        _log_state(f"[web] {url} → {len(ads)} ads  ({time.monotonic()-t0:.1f}s)")
        sources.add("web")

    # ── Google Ads Transparency ──────────────────────────────────────────────
    if settings.get("google_transparency_enabled") and not _stop_event.is_set():
        t0 = time.monotonic()
        _set_state(source="google", query="initialising…", query_num=0)
        _log_state(f"[google] Starting  country={country}")
        raw_google = scan_transparency_center(country)
        classified = _classify_raw_ads(raw_google, "google", ts)
        all_results.extend(classified)
        sources.add("google")
        wall = sum(1 for r in raw_google if r.get("js_required") or r.get("error"))
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[google] Done — {len(raw_google)} queries, {len(classified)} ads, {wall} errors  ({time.monotonic()-t0:.1f}s)")

    # ── Apify — Facebook ─────────────────────────────────────────────────────
    if settings.get("apify_facebook_enabled") and apify_token and not _stop_event.is_set():
        from src.facebook_scanner import _FB_QUERIES as _FBQ
        from src.apify_scanner import fetch_facebook as apify_fb
        t0 = time.monotonic()
        _set_state(source="apify-facebook", query="starting actor…", query_num=0, query_total=len(_FBQ))
        _log_state(f"[apify-facebook] actor={apify_fb_actor}  country={country}  queries={len(_FBQ)}")
        raw = apify_fb(_FBQ, country, apify_token, actor_id=apify_fb_actor)
        classified = _classify_raw_ads(raw, "facebook", ts)
        all_results.extend(classified)
        sources.add("facebook")
        errs = sum(1 for r in raw if r.get("error"))
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[apify-facebook] Done — {len(raw)} queries, {len(classified)} ads, {errs} errors  ({time.monotonic()-t0:.1f}s)")

    # ── Apify — Instagram ────────────────────────────────────────────────────
    if settings.get("apify_instagram_enabled") and apify_token and not _stop_event.is_set():
        from src.facebook_scanner import _FB_QUERIES as _FBQ
        from src.apify_scanner import fetch_instagram as apify_ig
        t0 = time.monotonic()
        _set_state(source="apify-instagram", query="starting actor…", query_num=0, query_total=len(_FBQ))
        _log_state(f"[apify-instagram] actor={apify_ig_actor}  country={country}  queries={len(_FBQ)}")
        raw = apify_ig(_FBQ, country, apify_token, actor_id=apify_ig_actor)
        classified = _classify_raw_ads(raw, "instagram", ts)
        all_results.extend(classified)
        sources.add("instagram")
        errs = sum(1 for r in raw if r.get("error"))
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[apify-instagram] Done — {len(raw)} queries, {len(classified)} ads, {errs} errors  ({time.monotonic()-t0:.1f}s)")

    # ── Apify — Google ───────────────────────────────────────────────────────
    if settings.get("apify_google_enabled") and apify_token and not _stop_event.is_set():
        from src.facebook_scanner import _FB_QUERIES as _FBQ
        from src.apify_scanner import fetch_google as apify_ggl
        t0 = time.monotonic()
        _set_state(source="apify-google", query="starting actor…", query_num=0, query_total=len(_FBQ))
        _log_state(f"[apify-google] actor={apify_ggl_actor}  country={country}  queries={len(_FBQ)}")
        raw = apify_ggl(_FBQ, country, apify_token, actor_id=apify_ggl_actor)
        classified = _classify_raw_ads(raw, "google", ts)
        all_results.extend(classified)
        sources.add("google")
        errs = sum(1 for r in raw if r.get("error"))
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[apify-google] Done — {len(raw)} queries, {len(classified)} ads, {errs} errors  ({time.monotonic()-t0:.1f}s)")

    # ── Apify — legacy (Facebook, single actor) ──────────────────────────────
    if settings.get("apify_enabled") and apify_token and not _stop_event.is_set():
        from src.facebook_scanner import _FB_QUERIES as _FBQ
        from src.apify_scanner import fetch_facebook as apify_legacy
        t0 = time.monotonic()
        _set_state(source="apify", query="starting actor…", query_num=0, query_total=len(_FBQ))
        _log_state(f"[apify] actor={apify_actor}  country={country}  queries={len(_FBQ)}")
        raw = apify_legacy(_FBQ, country, apify_token, actor_id=apify_actor)
        classified = _classify_raw_ads(raw, "facebook", ts)
        all_results.extend(classified)
        sources.add("facebook")
        errs = sum(1 for r in raw if r.get("error"))
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[apify] Done — {len(raw)} queries, {len(classified)} ads, {errs} errors  ({time.monotonic()-t0:.1f}s)")

    # ── Facebook Ad Library ──────────────────────────────────────────────────
    if settings.get("facebook_library_enabled") and not _stop_event.is_set():
        from src.facebook_scanner import _FB_QUERIES as _FBQ
        t0 = time.monotonic()
        _set_state(source="facebook", query="initialising…", query_num=0, query_total=len(_FBQ))
        if token:
            _log_state(f"[facebook] Meta Graph API  country={country}")
            from src.meta_api import fetch_ads
            raw_fb = fetch_ads(_FBQ, country, token)
        else:
            _log_state(f"[facebook] Playwright  country={country}  cookies={'yes' if cookies else 'no'}")
            from src.facebook_scanner import scan_facebook_library
            raw_fb = scan_facebook_library(country, cookies_json=cookies,
                                           stop_event=_stop_event, state_cb=_fb_state_cb)
        classified = _classify_raw_ads(raw_fb, "facebook", ts)
        all_results.extend(classified)
        sources.add("facebook")
        wall = sum(1 for r in raw_fb if r.get("js_required"))
        errs = sum(1 for r in raw_fb if r.get("error") and not r.get("js_required"))
        ok   = len(raw_fb) - wall - errs
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[facebook] Done — {len(raw_fb)} queries ({ok} ok/{wall} wall/{errs} err), {len(classified)} ads  ({time.monotonic()-t0:.1f}s)")
        if wall:
            _log_state(f"[facebook] WARNING: {wall}/{len(raw_fb)} queries hit login wall")

    # ── Instagram Ad Library ─────────────────────────────────────────────────
    if settings.get("instagram_library_enabled") and not _stop_event.is_set():
        from src.facebook_scanner import _FB_QUERIES as _FBQ
        t0 = time.monotonic()
        _set_state(source="instagram", query="initialising…", query_num=0, query_total=len(_FBQ))
        if token:
            _log_state(f"[instagram] Meta Graph API  country={country}")
            from src.meta_api import fetch_ads
            raw_ig = fetch_ads(_FBQ, country, token, platform="INSTAGRAM")
        else:
            _log_state(f"[instagram] Playwright  country={country}  cookies={'yes' if cookies else 'no'}")
            from src.facebook_scanner import scan_facebook_library
            raw_ig = scan_facebook_library(country, platform="INSTAGRAM", cookies_json=cookies,
                                           stop_event=_stop_event, state_cb=_fb_state_cb)
        classified = _classify_raw_ads(raw_ig, "instagram", ts)
        all_results.extend(classified)
        sources.add("instagram")
        wall = sum(1 for r in raw_ig if r.get("js_required"))
        errs = sum(1 for r in raw_ig if r.get("error") and not r.get("js_required"))
        ok   = len(raw_ig) - wall - errs
        _set_state(ads_found=len(all_results), query="done")
        _log_state(f"[instagram] Done — {len(raw_ig)} queries ({ok} ok/{wall} wall/{errs} err), {len(classified)} ads  ({time.monotonic()-t0:.1f}s)")

    def _counts(subset):
        return {
            "total":        len(subset),
            "flagged_high": sum(1 for r in subset if r.get("label") == "casino_high_confidence"),
            "flagged_review": sum(1 for r in subset if r.get("label") == "casino_review"),
            "licensed":     sum(1 for r in subset if r.get("label") == "licensed_operator"),
            "not_casino":   sum(1 for r in subset if r.get("label") == "not_casino"),
        }

    entry = {
        "ts":            ts,
        "pages_scanned": pages_scanned,
        "sources":       sorted(sources),
        **_counts(all_results),
        "by_source": {
            src: _counts([r for r in all_results if r.get("source") == src])
            for src in sorted(sources)
        },
    }
    _append_history(entry)

    # Save results for dashboard drill-down, stratified by label so that
    # licensed_operator records (score=0.0) are never crowded out by high-scorers.
    _LABEL_LIMITS = {
        "casino_high_confidence": 50,
        "casino_review":          30,
        "licensed_operator":      40,
        "not_casino":             20,
    }
    display_rows = []
    for _lbl, _n in _LABEL_LIMITS.items():
        _subset = [r for r in all_results if r.get("label") == _lbl]
        display_rows.extend(sorted(_subset, key=lambda x: x.get("score", 0), reverse=True)[:_n])
    display = [
        {
            "ts":               r.get("ts", ts),
            "page_name":        r.get("page_name", ""),
            "advertiser":       r.get("advertiser", ""),
            "paid_for_by":      r.get("paid_for_by", ""),
            "search_url":       r.get("search_url", ""),
            "landing_url":      r.get("landing_url", ""),
            "ad_id":            r.get("ad_id", ""),
            "ad_permalink":     r.get("ad_permalink", ""),
            "impressions":      r.get("impressions", ""),
            "spend_range":      r.get("spend_range", ""),
            "country_delivery": r.get("country_delivery", ""),
            "platforms":        r.get("platforms", ""),
            "start_date":       r.get("start_date", ""),
            "score":            round(r.get("score", 0), 2),
            "label":            r.get("label", ""),
            "final_domain":     r.get("final_domain", "") or "",
            "ad_text":          (r.get("ad_text") or ""),
            "source":           r.get("source", "web"),
            "raw_signals":      r.get("raw_signals") or [],
        }
        for r in display_rows
    ]
    LAST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_RESULTS_PATH.write_text(json.dumps(display, indent=2))

    elapsed = time.monotonic() - scan_start
    _log_state(f"SCAN DONE  {elapsed:.1f}s  total={entry['total']}  high={entry['flagged_high']}  review={entry['flagged_review']}  licensed={entry['licensed']}")
    for src in sorted(sources):
        s = entry["by_source"].get(src, {})
        _log_state(f"  {src:<12} total={s.get('total',0):<4}  high={s.get('flagged_high',0):<3}  review={s.get('flagged_review',0):<3}  licensed={s.get('licensed',0):<3}")
    _set_state(running=False, source="idle", query="", elapsed=round(elapsed, 1))


def _append_history(entry: dict):
    history = load_history()
    history.append(entry)
    history = history[-MAX_HISTORY:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2))


def load_history() -> list:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text())
        except Exception:
            pass
    return []


def load_last_results() -> list:
    if LAST_RESULTS_PATH.exists():
        try:
            return json.loads(LAST_RESULTS_PATH.read_text())
        except Exception:
            pass
    return []


def start(interval_key: str = "off"):
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("apscheduler not installed — scheduled scans disabled")
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.start()
    reschedule(interval_key)


def get_next_run_time() -> str | None:
    """Return formatted timestamp of the next scheduled scan, or None."""
    if _scheduler is None:
        return None
    try:
        job = _scheduler.get_job("periodic_scan")
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None


def is_running() -> bool:
    """Return True if the scheduler is active and a periodic job is registered."""
    if _scheduler is None or not _scheduler.running:
        return False
    return _scheduler.get_job("periodic_scan") is not None


def run_now() -> None:
    """Trigger an immediate scan in the background (non-blocking).

    Uses APScheduler's date trigger so the HTTP request returns instantly
    while the scan executes in the background thread pool.
    Falls back to a synchronous call if the scheduler is not running.
    """
    if _scheduler is not None and _scheduler.running:
        from datetime import datetime, timedelta
        _scheduler.add_job(
            _run_scan,
            "date",
            run_date=datetime.now() + timedelta(seconds=1),
            id="immediate_scan",
            replace_existing=True,
        )
        logger.info("Immediate scan job queued")
    else:
        # Scheduler not started — run synchronously as fallback
        _run_scan()


def reschedule(interval_key: str):
    if _scheduler is None:
        return
    job_id = "periodic_scan"
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass
    hours = INTERVALS.get(interval_key, 0)
    if hours > 0:
        _scheduler.add_job(_run_scan, "interval", hours=hours,
                           id=job_id, replace_existing=True)
        logger.info("Scan scheduled every %s", interval_key)
