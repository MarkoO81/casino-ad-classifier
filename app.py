"""Simple Flask reporting page for the casino ad classifier."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, render_template, request
from examples.process_ad import process_ad, DEMO_ADS
from src.classifier import GamblingAdClassifier

app = Flask(__name__)


def run_all(ads):
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

    results = run_all(DEMO_ADS)
    counts = {
        "casino_high_confidence": sum(1 for r in results if r["label"] == "casino_high_confidence"),
        "casino_review":          sum(1 for r in results if r["label"] == "casino_review"),
        "licensed_operator":      sum(1 for r in results if r["label"] == "licensed_operator"),
        "not_casino":             sum(1 for r in results if r["label"] == "not_casino"),
    }
    return render_template("index.html", results=results, counts=counts,
                           custom_result=custom_result)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port)
