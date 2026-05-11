"""Background scheduler for periodic ad scanning."""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

HISTORY_PATH      = Path(__file__).parent.parent / "config" / "scan_history.json"
LAST_RESULTS_PATH = Path(__file__).parent.parent / "config" / "last_scan_results.json"
MAX_HISTORY = 48  # keep 2 days at 1h intervals

INTERVALS = {"off": 0, "1h": 1, "4h": 4, "8h": 8, "24h": 24}

_scheduler = None


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
                "ts":           ts,
                "page_name":    query_result.get("query", ""),
                "advertiser":   ad.get("advertiser") or "",
                "search_url":   query_result.get("search_url", ""),
                "landing_url":  link_url or "",
                "score":        round(res.score, 2),
                "label":        res.label,
                "final_domain": res.final_domain or "",
                "ad_text":      text,
                "source":       source,
                "raw_signals":  [{"name": s.name, "weight": round(s.weight, 2), "detail": s.detail}
                                 for s in res.signals],
            })
    return records


def _run_scan():
    from src import config as cfg
    from src.web_scanner import scan_url
    from src.google_scanner import scan_transparency_center
    from examples.process_ad import process_ad
    import src.url_check as url_check

    settings = cfg.load()
    extra = {op["domain"] for op in settings.get("excluded_operators", []) if op.get("domain")}
    url_check.WHITELIST_DOMAINS.update(extra)

    ts = datetime.now().isoformat(timespec="seconds")
    all_results = []
    sources = set()
    pages_scanned = 0

    for target in settings.get("scan_targets", []):
        url = (target.get("url") or "").strip()
        if not url:
            continue
        pages_scanned += 1
        for ad in scan_url(url):
            r = process_ad(ad, image_path=None, clip=None, ocr=None)
            r["source"] = "web"
            r["ts"] = ts
            all_results.append(r)
        sources.add("web")

    if settings.get("google_transparency_enabled"):
        raw_google = scan_transparency_center(settings.get("source_country", "SI"))
        all_results.extend(_classify_raw_ads(raw_google, "google", ts))
        sources.add("google")

    if settings.get("facebook_library_enabled"):
        from src.facebook_scanner import scan_facebook_library
        raw_fb = scan_facebook_library(settings.get("source_country", "SI"))
        all_results.extend(_classify_raw_ads(raw_fb, "facebook", ts))
        sources.add("facebook")

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
            "ts":           r.get("ts", ts),
            "page_name":    r.get("page_name", ""),
            "advertiser":   r.get("advertiser", ""),
            "search_url":   r.get("search_url", ""),
            "landing_url":  r.get("landing_url", ""),
            "score":        round(r.get("score", 0), 2),
            "label":        r.get("label", ""),
            "final_domain": r.get("final_domain", "") or "",
            "ad_text":      (r.get("ad_text") or ""),
            "source":       r.get("source", "web"),
            "raw_signals":  r.get("raw_signals") or [],
        }
        for r in display_rows
    ]
    LAST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_RESULTS_PATH.write_text(json.dumps(display, indent=2))

    print(f"[scheduler] scan done — {pages_scanned} pages, {len(all_results)} records "
          f"({entry['flagged_high']} high-conf) sources={sorted(sources)}")


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
        print("[scheduler] apscheduler not installed — scheduled scans disabled")
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
        print("[scheduler] immediate scan job queued")
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
        print(f"[scheduler] scan scheduled every {interval_key}")
