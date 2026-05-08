"""Simple Flask reporting page for the casino ad classifier."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, render_template, request, redirect, url_for, flash
from examples.process_ad import process_ad, DEMO_ADS
from src.classifier import GamblingAdClassifier
from src import config as cfg
import src.url_check as url_check

app = Flask(__name__)
app.secret_key = "casino-classifier-dev"


def apply_settings(settings: dict):
    """Merge excluded operators from settings into the runtime whitelist."""
    extra = {op["domain"] for op in settings.get("excluded_operators", []) if op.get("domain")}
    url_check.WHITELIST_DOMAINS.update(extra)


def run_all(ads, settings):
    apply_settings(settings)
    results = []
    for ad in ads:
        r = process_ad(ad, image_path=None, clip=None, ocr=None)
        r["ad_text"] = " | ".join(
            part
            for field in ("ad_creative_bodies", "ad_creative_link_titles",
                          "ad_creative_link_captions")
            for part in (ad.get(field) or [])
            if part
        )
        results.append(r)
    return results


@app.route("/", methods=["GET", "POST"])
def index():
    settings = cfg.load()
    custom_result = None

    if request.method == "POST":
        ad_text = request.form.get("ad_text", "").strip()
        link_url = request.form.get("link_url", "").strip()
        if ad_text or link_url:
            apply_settings(settings)
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

    results = run_all(DEMO_ADS, settings)
    counts = {
        "casino_high_confidence": sum(1 for r in results if r["label"] == "casino_high_confidence"),
        "casino_review":          sum(1 for r in results if r["label"] == "casino_review"),
        "licensed_operator":      sum(1 for r in results if r["label"] == "licensed_operator"),
        "not_casino":             sum(1 for r in results if r["label"] == "not_casino"),
    }
    return render_template("index.html", results=results, counts=counts,
                           custom_result=custom_result, settings=settings)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    data = cfg.load()

    if request.method == "POST":
        data["meta_access_token"] = request.form.get("meta_access_token", "").strip()
        data["source_country"] = request.form.get("source_country", "SI").strip().upper()

        # Rebuild excluded operators list from parallel name/domain fields
        names   = request.form.getlist("op_name")
        domains = request.form.getlist("op_domain")
        data["excluded_operators"] = [
            {"name": n.strip(), "domain": d.strip().lower()}
            for n, d in zip(names, domains)
            if d.strip()
        ]

        cfg.save(data)
        flash("Settings saved.", "success")
        return redirect(url_for("settings"))

    return render_template("settings.html", settings=data)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port)
