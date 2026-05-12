"""Flask reporting app for the casino ad classifier."""

import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from functools import wraps
from examples.process_ad import process_ad, DEMO_ADS
from src.classifier import GamblingAdClassifier
from src.web_scanner import scan_url
from src.google_scanner import scan_transparency_center
from src.facebook_scanner import scan_facebook_library
from src import config as cfg
from src import scheduler
from src import persona as persona_mod
import src.url_check as url_check

import json as _json

app = Flask(__name__)
app.secret_key = "casino-classifier-dev"
app.jinja_env.filters["from_json"] = lambda s: (_json.loads(s) if isinstance(s, str) else (s or []))

_LOGIN_USER = "admin"
_LOGIN_PASS = "admin"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# Start background scheduler on first import
_settings = cfg.load()
scheduler.start(_settings.get("scan_interval", "off"))


def apply_settings(settings: dict):
    extra = {op["domain"] for op in settings.get("excluded_operators", []) if op.get("domain")}
    url_check.WHITELIST_DOMAINS.update(extra)


def classify_records(records):
    results = []
    for ad in records:
        r = process_ad(ad, image_path=None, clip=None, ocr=None)
        r["ad_text"] = " | ".join(
            part
            for field in ("ad_creative_bodies", "ad_creative_link_titles",
                          "ad_creative_link_captions")
            for part in (ad.get(field) or [])
            if part
        )
        r["source"] = ad.get("_source", "demo")
        r["scan_error"] = ad.get("_scan_error")
        results.append(r)
    return results


def _classify_google_results(raw: list) -> list:
    """Classify each ad extracted from Google Transparency Center.
    Returns only positive hits (casino_high_confidence or casino_review).
    """
    clf = GamblingAdClassifier(resolve_urls=False)
    positives = []
    for query_result in raw:
        if query_result.get("error") or query_result.get("js_required"):
            continue
        for ad in query_result.get("ads", []):
            text = (ad.get("text") or "").strip()
            url  = ad.get("url") or ""
            if not text:
                continue
            res = clf.classify(ad_text=text, link_url=url or None)
            if res.label in ("casino_high_confidence", "casino_review"):
                positives.append({
                    "query":        query_result["query"],
                    "search_url":   query_result["search_url"],
                    "ad_text":      text[:200],
                    "score":        round(res.score, 2),
                    "label":        res.label,
                    "final_domain": res.final_domain or "",
                    "raw_signals":  [
                        {"name": s.name, "weight": s.weight, "detail": s.detail}
                        for s in res.signals
                    ],
                })
    return positives


def label_counts(results):
    return {
        "casino_high_confidence": sum(1 for r in results if r["label"] == "casino_high_confidence"),
        "casino_review":          sum(1 for r in results if r["label"] == "casino_review"),
        "licensed_operator":      sum(1 for r in results if r["label"] == "licensed_operator"),
        "not_casino":             sum(1 for r in results if r["label"] == "not_casino"),
    }


def _compute_keyword_stats(results: list) -> list:
    """Group results by keyword; return rows sorted by flagged count descending."""
    return _kw_rows(results)


def _kw_rows(results: list) -> list:
    from collections import defaultdict
    stats = defaultdict(lambda: {"high": 0, "review": 0, "licensed": 0, "not_casino": 0, "advertisers": set()})
    for r in results:
        kw = (r.get("page_name") or "").strip() or "unknown"
        lbl = r.get("label", "")
        if lbl == "casino_high_confidence":
            stats[kw]["high"] += 1
        elif lbl == "casino_review":
            stats[kw]["review"] += 1
        elif lbl == "licensed_operator":
            stats[kw]["licensed"] += 1
        else:
            stats[kw]["not_casino"] += 1
        adv = (r.get("advertiser") or "").strip()
        if adv:
            stats[kw]["advertisers"].add(adv)
    rows = [
        {"keyword": kw, "high": s["high"], "review": s["review"],
         "licensed": s["licensed"], "not_casino": s["not_casino"],
         "advertisers": len(s["advertisers"]),
         "total": s["high"] + s["review"] + s["licensed"] + s["not_casino"]}
        for kw, s in stats.items()
        if s["high"] + s["review"] + s["licensed"] + s["not_casino"] > 0
    ]
    rows.sort(key=lambda x: x["high"] + x["review"], reverse=True)
    return rows


def _compute_keyword_stats_by_source(results: list) -> dict:
    """Return keyword stats broken down by source + an 'all' aggregate."""
    from collections import defaultdict
    by_source: dict = defaultdict(list)
    for r in results:
        by_source[r.get("source", "web")].append(r)
    out = {"all": _kw_rows(results)}
    for src, rows in by_source.items():
        out[src] = _kw_rows(rows)
    return out


def _compute_source_stats(results: list) -> dict:
    stats = {}
    for r in results:
        src = r.get("source", "web")
        if src not in stats:
            stats[src] = {"total": 0, "flagged_high": 0, "flagged_review": 0, "licensed": 0, "not_casino": 0}
        stats[src]["total"] += 1
        lbl = r.get("label", "")
        if lbl == "casino_high_confidence":
            stats[src]["flagged_high"] += 1
        elif lbl == "casino_review":
            stats[src]["flagged_review"] += 1
        elif lbl == "licensed_operator":
            stats[src]["licensed"] += 1
        elif lbl == "not_casino":
            stats[src]["not_casino"] += 1
    return stats


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == _LOGIN_USER and
                request.form.get("password") == _LOGIN_PASS):
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    settings = cfg.load()
    apply_settings(settings)
    custom_result = None

    if request.method == "POST":
        ad_text = request.form.get("ad_text", "").strip()
        link_url = request.form.get("link_url", "").strip()
        if ad_text or link_url:
            clf = GamblingAdClassifier(resolve_urls=bool(link_url))
            res = clf.classify(ad_text=ad_text or None, link_url=link_url or None)
            custom_result = {
                "page_name": "Custom ad",
                "ad_archive_id": "—",
                "score": res.score,
                "label": res.label,
                "is_licensed": res.is_licensed,
                "final_domain": res.final_domain,
                "explanation": res.explain(),
                "raw_signals": [
                    {"name": s.name, "weight": s.weight, "detail": s.detail}
                    for s in res.signals
                ],
                "ad_text": ad_text,
            }

    history = scheduler.load_history()
    last_results = scheduler.load_last_results()
    source_stats = _compute_source_stats(last_results)
    keyword_stats = _compute_keyword_stats(last_results)
    keyword_stats_by_source = _compute_keyword_stats_by_source(last_results)
    personas = persona_mod.list_personas()
    next_run = scheduler.get_next_run_time()
    scheduler_running = scheduler.is_running()

    from src.feedback import get_stats as fb_stats
    feedback_stats = fb_stats()

    return render_template("index.html",
                           custom_result=custom_result,
                           settings=settings,
                           scan_history=history,
                           last_results=last_results,
                           source_stats=source_stats,
                           keyword_stats=keyword_stats,
                           keyword_stats_by_source=keyword_stats_by_source,
                           feedback_stats=feedback_stats,
                           personas=personas,
                           next_run=next_run,
                           scheduler_running=scheduler_running)


@app.route("/results")
@login_required
def results_page():
    from src import database as db
    label      = request.args.get("label", "").strip()
    source     = request.args.get("source", "").strip()
    advertiser = request.args.get("advertiser", "").strip()
    days       = int(request.args.get("days", 0) or 0)
    page       = max(1, int(request.args.get("page", 1) or 1))
    per_page   = 100

    total   = db.count_ads(label=label, source=source, days=days)
    results = db.query_ads(label=label, source=source, advertiser=advertiser,
                           days=days, limit=per_page, offset=(page - 1) * per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template("results.html",
                           results=results,
                           label_filter=label,
                           source_filter=source,
                           advertiser_filter=advertiser,
                           days_filter=days,
                           total_records=total,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages)


@app.route("/history")
@login_required
def history_page():
    from src import database as db
    scans = db.query_scans(limit=200)
    return render_template("history.html", scans=scans)


@app.route("/scan", methods=["GET", "POST"])
@login_required
def scan():
    settings = cfg.load()
    apply_settings(settings)
    scan_results = []
    scanned_urls = []
    google_results = []
    facebook_results = []
    instagram_results = []

    if request.method == "POST":
        persona_name = request.form.get("scan_persona", "").strip()
        country = settings.get("source_country", "SI")

        url = request.form.get("url", "").strip()
        if url:
            if not url.startswith("http"):
                url = "https://" + url
            scanned_urls = [url]
        else:
            scanned_urls = [t["url"] for t in settings.get("scan_targets", []) if t.get("url")]

        if persona_name and scanned_urls:
            scan_results.extend(classify_records(
                persona_mod.scan_urls_as_persona(scanned_urls, persona_name)
            ))
        else:
            for target_url in scanned_urls:
                scan_results.extend(classify_records(scan_url(target_url)))

        if settings.get("google_transparency_enabled"):
            if persona_name:
                raw = persona_mod.scrape_as_persona(persona_name, country)
            else:
                raw = scan_transparency_center(country)
            google_results = _classify_google_results(raw)

        if settings.get("facebook_library_enabled"):
            raw_fb = scan_facebook_library(country)
            facebook_results = _classify_google_results(raw_fb)

        if settings.get("instagram_library_enabled"):
            from src.instagram_scanner import scan_instagram_library
            raw_ig = scan_instagram_library(country)
            instagram_results = _classify_google_results(raw_ig)

    personas = persona_mod.list_personas()
    return render_template("scan.html",
                           results=scan_results,
                           counts=label_counts(scan_results),
                           targets=settings.get("scan_targets", []),
                           scanned_urls=scanned_urls,
                           google_results=google_results,
                           google_enabled=settings.get("google_transparency_enabled", False),
                           facebook_results=facebook_results,
                           facebook_enabled=settings.get("facebook_library_enabled", False),
                           instagram_results=instagram_results,
                           instagram_enabled=settings.get("instagram_library_enabled", False),
                           personas=personas,
                           selected_persona=request.form.get("scan_persona", ""))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    data = cfg.load()

    if request.method == "POST":
        data["meta_access_token"]          = request.form.get("meta_access_token", "").strip()
        data["facebook_cookies"]           = request.form.get("facebook_cookies", "").strip()
        data["apify_token"]                = request.form.get("apify_token", "").strip()
        data["apify_actor_id"]             = request.form.get("apify_actor_id", "apify~facebook-ads-library-scraper").strip()
        data["apify_enabled"]              = "1" in request.form.getlist("apify_enabled")
        data["apify_facebook_enabled"]     = "1" in request.form.getlist("apify_facebook_enabled")
        data["apify_facebook_actor_id"]    = request.form.get("apify_facebook_actor_id", "apify~facebook-ads-library-scraper").strip()
        data["apify_instagram_enabled"]    = "1" in request.form.getlist("apify_instagram_enabled")
        data["apify_instagram_actor_id"]   = request.form.get("apify_instagram_actor_id", "apify~facebook-ads-library-scraper").strip()
        data["apify_google_enabled"]       = "1" in request.form.getlist("apify_google_enabled")
        data["apify_google_actor_id"]      = request.form.get("apify_google_actor_id", "epctex~google-ads-transparency-center-scraper").strip()
        data["facebook_proxy"]              = request.form.get("facebook_proxy", "").strip()
        data["facebook_persona"]            = request.form.get("facebook_persona", "").strip()
        data["meta_ads_collector_enabled"]  = "1" in request.form.getlist("meta_ads_collector_enabled")
        data["meta_ads_collector_instagram"] = "1" in request.form.getlist("meta_ads_collector_instagram")
        data["web_scanning_enabled"]        = "1" in request.form.getlist("web_scanning_enabled")
        data["source_country"]             = request.form.get("source_country", "SI").strip().upper()
        data["google_transparency_enabled"]  = "1" in request.form.getlist("google_transparency_enabled")
        data["facebook_library_enabled"]     = "1" in request.form.getlist("facebook_library_enabled")
        data["instagram_library_enabled"]    = "1" in request.form.getlist("instagram_library_enabled")
        data["scan_interval"]               = request.form.get("scan_interval", "off")

        names   = request.form.getlist("op_name")
        domains = request.form.getlist("op_domain")
        data["excluded_operators"] = [
            {"name": n.strip(), "domain": d.strip().lower()}
            for n, d in zip(names, domains) if d.strip()
        ]

        target_urls   = request.form.getlist("target_url")
        target_labels = request.form.getlist("target_label")
        data["scan_targets"] = [
            {"url": u.strip(), "label": l.strip()}
            for u, l in zip(target_urls, target_labels) if u.strip()
        ]

        cfg.save(data)
        scheduler.reschedule(data["scan_interval"])
        flash("Settings saved.", "success")
        return redirect(url_for("settings"))

    return render_template("settings.html", settings=data,
                           personas=persona_mod.list_personas())


@app.route("/run-now", methods=["POST"])
@login_required
def run_now():
    try:
        scheduler.run_now()
    except Exception as e:
        flash(f"Scan failed to start: {e}", "error")
    return redirect(url_for("index"))


@app.route("/scan-status")
@login_required
def scan_status():
    return jsonify(scheduler.get_status())


@app.route("/stop-scan", methods=["POST"])
@login_required
def stop_scan():
    scheduler.stop_scan()
    return jsonify({"ok": True})


@app.route("/personas/create", methods=["POST"])
@login_required
def persona_create():
    name = request.form.get("persona_name", "").strip()
    if name:
        try:
            persona_mod.create_persona(name)
            flash(f"Persona '{name}' created.", "success")
        except ValueError as e:
            flash(str(e), "error")
    return redirect(url_for("index") + "#personas")


@app.route("/personas/warm", methods=["POST"])
@login_required
def persona_warm():
    name = request.form.get("persona_name", "").strip()
    if name:
        try:
            persona_mod.warm_persona(name)
            flash(f"Persona '{name}' warmed up — {persona_mod._load_status(name).cookie_count} cookies stored.", "success")
        except Exception as e:
            flash(f"Warm-up failed for '{name}': {e}", "error")
    return redirect(url_for("index") + "#personas")


@app.route("/feedback", methods=["POST"])
@login_required
def feedback():
    from src.feedback import save as save_feedback
    data = request.get_json(silent=True) or {}
    verdict = data.get("verdict", "")
    if verdict not in ("correct", "false_positive"):
        return jsonify({"error": "invalid verdict"}), 400
    result = save_feedback(data.get("record", {}), verdict)
    return jsonify(result)


@app.route("/personas/delete", methods=["POST"])
@login_required
def persona_delete():
    name = request.form.get("persona_name", "").strip()
    if name:
        persona_mod.delete_persona(name)
        flash(f"Persona '{name}' deleted.", "success")
    return redirect(url_for("index") + "#personas")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port)
