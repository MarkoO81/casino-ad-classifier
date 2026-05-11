"""Feedback loop — store human corrections and apply auto-corrections.

Verdicts:
  "correct"        — classification was right, no action needed
  "false_positive" — flagged ad is NOT a casino violation

Auto-corrections triggered by false_positive verdicts:
  - Domain gets 2+ false positive votes → auto-added to excluded_operators
    (whitelist) in settings.json and the in-memory WHITELIST_DOMAINS set.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FEEDBACK_PATH = Path(__file__).parent.parent / "data" / "feedback.jsonl"
AUTO_WHITELIST_THRESHOLD = 2


def save(record: dict, verdict: str) -> dict:
    """Persist a feedback entry and run auto-corrections.

    Returns {"ok": True, "actions": [list of strings describing side effects]}.
    """
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
    return {
        "total":          len(entries),
        "correct":        sum(1 for e in entries if e.get("verdict") == "correct"),
        "false_positive": sum(1 for e in entries if e.get("verdict") == "false_positive"),
        "top_fp_domains": sorted(fp_domains.items(), key=lambda x: -x[1])[:10],
    }


# ── Auto-correction ──────────────────────────────────────────────────────────

_SKIP_DOMAINS = {"", "facebook.com", "google.com", "instagram.com",
                 "adstransparency.google.com"}


def _auto_correct(entry: dict) -> list[str]:
    actions: list[str] = []
    domain = entry.get("final_domain", "")
    if domain in _SKIP_DOMAINS:
        return actions

    # Count false positives for this domain across all saved feedback
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
        return []  # already whitelisted

    # Persist to settings
    settings = cfg.load()
    existing = {op["domain"] for op in settings.get("excluded_operators", [])}
    if domain not in existing:
        settings.setdefault("excluded_operators", []).append(
            {"name": domain, "domain": domain}
        )
        cfg.save(settings)

    # Apply immediately to in-memory set
    url_check.WHITELIST_DOMAINS.add(domain)
    logger.info("auto-whitelisted domain: %s", domain)
    return [f"auto_whitelisted:{domain}"]
