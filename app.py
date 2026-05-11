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

from flask import Flask, render_template, request, redirect, url_for, flash
from examples.process_ad import process_ad, DEMO_ADS
from src.classifier import GamblingAdClassifier
from src.web_scanner import scan_url
from src.google_scanner import scan_transparency_center
from src.facebook_scanner import scan_facebook_library
from src import config as cfg
from src import scheduler
from src import persona as persona_mod
import src.url_check as url_check

app = Flask(__name__)
app.secret_key = "casino-classifier-dev"

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


@app.route("/", methods=["GET", "POST"])
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
    personas = persona_mod.list_personas()
    next_run = scheduler.get_next_run_time()
    scheduler_running = scheduler.is_running()

    return render_template("index.html",
                           custom_result=custom_result,
                           settings=settings,
                           scan_history=history,
                           last_results=last_results,
                           source_stats=source_stats,
                           personas=personas,
                           next_run=next_run,
                           scheduler_running=scheduler_running)


@app.route("/results")
def results_page():
    label = request.args.get("label", "").strip()
    source = request.args.get("source", "").strip()
    all_results = scheduler.load_last_results()
    filtered = [r for r in all_results
                if (not label or r.get("label") == label)
                and (not source or r.get("source") == source)]
    return render_template("results.html",
                           results=filtered,
                           label_filter=label,
                           source_filter=source,
                           total_records=len(all_results))


@app.route("/scan", methods=["GET", "POST"])
def scan():
    settings = cfg.load()
    apply_settings(settings)
    scan_results = []
    scanned_urls = []
    google_results = []
    facebook_results = []

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
                           personas=personas,
                           selected_persona=request.form.get("scan_persona", ""))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    data = cfg.load()

    if request.method == "POST":
        data["meta_access_token"]          = request.form.get("meta_access_token", "").strip()
        data["source_country"]             = request.form.get("source_country", "SI").strip().upper()
        data["google_transparency_enabled"]  = "1" in request.form.getlist("google_transparency_enabled")
        data["facebook_library_enabled"]     = "1" in request.form.getlist("facebook_library_enabled")
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

    return render_template("settings.html", settings=data)


@app.route("/run-now", methods=["POST"])
def run_now():
    try:
        scheduler.run_now()
        flash("Scan started in background — refresh in ~30 s to see results.", "success")
    except Exception as e:
        flash(f"Scan failed to start: {e}", "error")
    return redirect(url_for("index"))


@app.route("/personas/create", methods=["POST"])
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
def persona_warm():
    name = request.form.get("persona_name", "").strip()
    if name:
        try:
            persona_mod.warm_persona(name)
            flash(f"Persona '{name}' warmed up — {persona_mod._load_status(name).cookie_count} cookies stored.", "success")
        except Exception as e:
            flash(f"Warm-up failed for '{name}': {e}", "error")
    return redirect(url_for("index") + "#personas")


@app.route("/personas/delete", methods=["POST"])
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
