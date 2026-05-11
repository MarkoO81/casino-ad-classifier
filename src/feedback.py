"""Feedback loop — store human verdicts and apply auto-corrections.

Verdicts:
  "acknowledged"   — real violation, analyst has noted it
  "not_relevant"   — not applicable to this investigation
  "false_positive" — misclassified (not actually a casino ad)
  "deleted"        — removed from current results view

Auto-corrections on false_positive:
  - Domain gets 2+ false positive votes → auto-whitelisted in
    settings.json and the in-memory WHITELIST_DOMAINS set.

Deleted records are removed from last_scan_results.json immediately.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FEEDBACK_PATH     = Path(__file__).parent.parent / "data" / "feedback.jsonl"
LAST_RESULTS_PATH = Path(__file__).parent.parent / "config" / "last_scan_results.json"
AUTO_WHITELIST_THRESHOLD = 2

VALID_VERDICTS = {"acknowledged", "not_relevant", "false_positive", "deleted"}


def save(record: dict, verdict: str) -> dict:
    """Persist a feedback entry and run side-effects.

    Returns {"ok": True, "actions": [...]} where actions is a list of
    strings describing any automatic corrections that were applied.
    """
    if verdict not in VALID_VERDICTS:
        return {"ok": False, "error": f"unknown verdict: {verdict}"}

    entry = {
        "ts":           datetime.now().isoformat(timespec="seconds"),
        "verdict":      verdict,
        "label":        record.get("label", ""),
        "score":        record.get("score"),
        "ad_text":      (record.get("ad_text") or "")[:500],
        "final_domain": (record.get("final_domain") or "").strip().lower(),
        "source":       record.get("source", ""),
        "page_name":    record.get("page_name", ""),
        "advertiser":   record.get("advertiser", ""),
        "landing_url":  record.get("landing_url", ""),
    }

    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    actions: list[str] = []
    if verdict == "false_positive":
        actions = _auto_correct(entry)
    elif verdict == "deleted":
        _remove_from_results(record)
        actions = ["deleted_from_results"]

    logger.info("feedback saved — verdict=%s domain=%s actions=%s",
                verdict, entry["final_domain"], actions)
    return {"ok": True, "actions": actions}


def load_all() -> list[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    entries = []
    with FEEDBACK_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return entries


def get_stats() -> dict:
    entries = load_all()
    fp_domains: dict[str, int] = {}
    for e in entries:
        if e.get("verdict") == "false_positive":
            d = e.get("final_domain", "")
            if d:
                fp_domains[d] = fp_domains.get(d, 0) + 1

    acked = [e for e in entries if e.get("verdict") == "acknowledged"]
    return {
        "total":            len(entries),
        "acknowledged":     len(acked),
        "acked_high":       sum(1 for e in acked if e.get("label") == "casino_high_confidence"),
        "acked_review":     sum(1 for e in acked if e.get("label") == "casino_review"),
        "not_relevant":     sum(1 for e in entries if e.get("verdict") == "not_relevant"),
        "false_positive":   sum(1 for e in entries if e.get("verdict") == "false_positive"),
        "deleted":          sum(1 for e in entries if e.get("verdict") == "deleted"),
        "top_fp_domains":   sorted(fp_domains.items(), key=lambda x: -x[1])[:10],
    }


# ── Delete from results file ─────────────────────────────────────────────────

def _remove_from_results(record: dict):
    """Remove a matching record from last_scan_results.json."""
    if not LAST_RESULTS_PATH.exists():
        return
    try:
        results = json.loads(LAST_RESULTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    ts        = record.get("ts", "")
    source    = record.get("source", "")
    page_name = record.get("page_name", "")
    ad_text   = (record.get("ad_text") or "")[:80]

    filtered = [
        r for r in results
        if not (
            r.get("ts") == ts
            and r.get("source") == source
            and r.get("page_name") == page_name
            and (r.get("ad_text") or "")[:80] == ad_text
        )
    ]

    if len(filtered) < len(results):
        LAST_RESULTS_PATH.write_text(
            json.dumps(filtered, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("deleted %d result(s) from last_scan_results.json",
                    len(results) - len(filtered))


# ── Auto-correction ──────────────────────────────────────────────────────────

_SKIP_DOMAINS = {"", "facebook.com", "google.com", "instagram.com",
                 "adstransparency.google.com"}


def _auto_correct(entry: dict) -> list[str]:
    actions: list[str] = []
    domain = entry.get("final_domain", "")
    if domain in _SKIP_DOMAINS:
        return actions

    all_fp = [
        e for e in load_all()
        if e.get("verdict") == "false_positive"
        and e.get("final_domain", "").lower() == domain
    ]

    if len(all_fp) >= AUTO_WHITELIST_THRESHOLD:
        actions.extend(_whitelist_domain(domain))

    return actions


def _whitelist_domain(domain: str) -> list[str]:
    from src import config as cfg
    import src.url_check as url_check

    if domain in url_check.WHITELIST_DOMAINS:
        return []

    settings = cfg.load()
    existing = {op["domain"] for op in settings.get("excluded_operators", [])}
    if domain not in existing:
        settings.setdefault("excluded_operators", []).append(
            {"name": domain, "domain": domain}
        )
        cfg.save(settings)

    url_check.WHITELIST_DOMAINS.add(domain)
    logger.info("auto-whitelisted domain: %s", domain)
    return [f"auto_whitelisted:{domain}"]
