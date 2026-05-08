"""Background scheduler for periodic ad scanning."""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

HISTORY_PATH = Path(__file__).parent.parent / "config" / "scan_history.json"
MAX_HISTORY = 48  # keep 2 days at 1h intervals

INTERVALS = {"off": 0, "1h": 1, "4h": 4, "8h": 8, "24h": 24}

_scheduler = None


def _run_scan():
    from src import config as cfg
    from src.web_scanner import scan_url
    from src.google_scanner import scan_transparency_center
    from examples.process_ad import process_ad
    import src.url_check as url_check

    settings = cfg.load()
    extra = {op["domain"] for op in settings.get("excluded_operators", []) if op.get("domain")}
    url_check.WHITELIST_DOMAINS.update(extra)

    results = []
    sources = set()

    for target in settings.get("scan_targets", []):
        url = (target.get("url") or "").strip()
        if not url:
            continue
        for ad in scan_url(url):
            r = process_ad(ad, image_path=None, clip=None, ocr=None)
            results.append(r)
        sources.add("web")

    if settings.get("google_transparency_enabled"):
        scan_transparency_center(settings.get("source_country", "SI"))
        sources.add("google")

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "flagged_high":    sum(1 for r in results if r["label"] == "casino_high_confidence"),
        "flagged_review":  sum(1 for r in results if r["label"] == "casino_review"),
        "licensed":        sum(1 for r in results if r["label"] == "licensed_operator"),
        "not_casino":      sum(1 for r in results if r["label"] == "not_casino"),
        "sources":         sorted(sources),
    }
    _append_history(entry)
    print(f"[scheduler] scan done — {entry['total']} records, {entry['flagged_high']} high-confidence")


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
