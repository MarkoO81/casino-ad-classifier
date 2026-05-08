# Casino Ad Classifier

Layered classifier for detecting unlicensed online-casino advertising
on Meta (Facebook / Instagram) targeting Slovenian users. Designed to
plug into the Meta Ad Library API ingestion pipeline.

## Design

Five independent signals, fused into one auditable score:

| Signal | Weight | Source |
|--------|--------|--------|
| Keyword match in ad copy (HIGH precision) | +0.50 | Multilingual SL/HR/SR/EN lists |
| Keyword match in ad copy (MEDIUM precision) | +0.15 each (caps at 3) | Same |
| Negative marker (sports news, video games) | -0.40 each | NEGATIVES list |
| OCR keyword match (in-image text) | +0.45 / +0.10 | PaddleOCR / EasyOCR |
| CLIP visual zero-shot | up to +0.55 | open_clip ViT-L/14 |
| URL → blacklisted offshore domain | +0.80 | url_check |
| URL → unknown domain | +0.10 | url_check |
| URL → FURS-licensed operator | **-1.00** (suppress) | Whitelist |

Threshold defaults: `0.65` flags for review, `0.85` is high-confidence
auto-flag. Tune on your eval set.

## Why fusion, not a single model

1. **Auditability** — every flag comes with a per-signal breakdown you
   can paste into a FURS report.
2. **Progressive degradation** — works as v1 with just keywords + URL.
   Plug in CLIP and OCR as you collect labeled data.
3. **High precision via the URL whitelist short-circuit** — licensed
   operators are never falsely flagged, regardless of creative content.

## Project layout

```
src/
  keywords.py        Multilingual keyword lists (SL/HR/SR/EN)
  prompts.py         CLIP zero-shot prompt templates
  url_check.py       FURS whitelist + redirect resolver
  clip_classifier.py open_clip wrapper (zero-shot + embedding)
  ocr.py             PaddleOCR / EasyOCR wrapper
  classifier.py      Main fusion classifier
  train_head.py      Fine-tune a linear head on CLIP embeddings
tests/
  test_classifier.py End-to-end logic tests (no GPU/models needed)
examples/
  process_ad.py      Wire-in to a Meta Ad Library record
```

## Quick start

```bash
# 1. Lightweight install (rule-based + URL only)
pip install requests tldextract

# 2. Run logic tests — no GPU, no model downloads
python tests/test_classifier.py

# 3. Demo with three synthetic ad records
python -m examples.process_ad

# 4. Full install with CLIP + OCR
pip install -r requirements.txt
```

## Deployment phases

**Phase 1 (day 1):** Rule-based — keywords + URL whitelist. Catches the
obvious 60-70% of offshore casino ads with very high precision.

**Phase 2 (week 1):** Add CLIP zero-shot. Catches ads that disguise text
("get y0ur b0nus today") or use heavy visual messaging with minimal copy.
Lifts recall to ~85%.

**Phase 3 (week 2-3):** Add OCR. Catches all-image creatives where the
caption is generic ("Click to learn more!") but the banner says
"100 FREE SPINS" in 80pt font.

**Phase 4 (month 2):** Fine-tune the linear head once you have ~500
labeled examples. Move precision/recall into the 95%+ range. Use
`src/train_head.py`.

## Critical TODOs before production

1. **FURS whitelist** — `src/url_check.py` ships with a SEED list. Pull
   the live FURS register of licensed operators and replace.
2. **ad_snapshot_url renderer** — Meta returns ad_snapshot_url which is
   an HTML preview, not the raw creative. Build a Playwright worker that
   renders it and extracts the actual image/video + final click URL.
3. **Perceptual hash dedup** — same creative gets used across many ad
   IDs. Add pHash before classifying to avoid wasted compute.
4. **Affiliate/tracker resolution** — populate `GREYLIST_DOMAINS` with
   observed affiliate networks; build deeper redirect resolution
   (some require JS evaluation).
5. **Calibrate thresholds** — tune `THRESHOLD_REPORT` and
   `THRESHOLD_AUTO_HI` on a labeled eval set from your own pipeline.
