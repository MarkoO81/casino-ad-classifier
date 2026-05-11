"""Main entry point: fuse keyword + visual + OCR + URL signals.

Design philosophy
-----------------
Each signal is independently meaningful and survives independently of the
others. The fusion logic uses *evidence accumulation*, not a black-box
ensemble. This is important for two reasons:

1. AUDITABILITY. When you report an ad to FURS you need to explain WHY it
   was flagged. The classifier output includes a per-signal breakdown so
   compliance officers can read it.
2. PROGRESSIVE DEGRADATION. If you don't have OCR yet, the classifier
   still works. If CLIP is down, keywords + URL still produce reports.

Signal weights are calibrated for HIGH PRECISION (we want very few false
reports to FURS). Tune `THRESHOLD` after running on your eval set.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, Union
from pathlib import Path

from .keywords import HIGH_PRECISION, MEDIUM_PRECISION, NEGATIVES, find_matches, normalize
from .url_check import resolve_redirects, classify_domain


# -------------------- Configuration --------------------

# Weights are additive into [0, 1+] before final clamp.
W_KEYWORD_HIGH      = 0.70   # one HIGH-precision keyword (enough alone for review)
W_KEYWORD_MEDIUM    = 0.20   # per MEDIUM keyword (caps at 3)
W_NEGATIVE          = -0.40  # per NEGATIVE marker (sports news, video games)
W_OCR_HIGH          = 0.60   # HIGH keyword found via OCR (image text)
W_OCR_MEDIUM        = 0.15
W_VISUAL            = 0.55   # full weight if CLIP score >= 0.85; scaled below
W_URL_BLACKLIST     = 0.80   # known offshore operator
W_URL_WHITELIST     = -1.00  # licensed operator => suppress
W_URL_UNKNOWN       = 0.10   # mild prior (most random domains aren't gambling)

THRESHOLD_REPORT    = 0.50   # flag for human review
THRESHOLD_AUTO_HI   = 0.80   # high-confidence: surface to top of queue


@dataclass
class Signal:
    name: str
    score: float
    weight: float
    detail: str = ""


@dataclass
class ClassificationResult:
    score: float                                # final fused score, clamped [0, 1]
    label: str                                  # "casino" | "review" | "not_casino"
    is_licensed: bool                           # short-circuit if whitelist hit
    signals: list = field(default_factory=list) # list[Signal]
    final_url: Optional[str] = None
    final_domain: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d["signals"] = [asdict(s) for s in self.signals]
        return d

    def explain(self) -> str:
        lines = [f"Score: {self.score:.2f}  ->  {self.label}"]
        if self.final_domain:
            lines.append(f"Lands on: {self.final_domain}")
        for s in self.signals:
            lines.append(f"  [{s.weight:+.2f}] {s.name}: {s.detail}")
        return "\n".join(lines)


# -------------------- Classifier --------------------

class GamblingAdClassifier:
    """Fuses signals from text, OCR, image, and URL into a single score."""

    def __init__(self,
                 clip_classifier=None,    # CLIPGamblingClassifier or None
                 ocr_engine=None,          # OCREngine or None
                 resolve_urls: bool = True):
        self.clip = clip_classifier
        self.ocr = ocr_engine
        self.resolve_urls = resolve_urls

    # ---------- individual signal scorers ----------

    def _score_text(self, text: str, source: str = "copy") -> list[Signal]:
        """Score keyword matches in a text blob (ad caption OR OCR output)."""
        if not text:
            return []
        signals = []
        text_n = normalize(text)

        high_hits = find_matches(text_n, HIGH_PRECISION)
        med_hits = find_matches(text_n, MEDIUM_PRECISION)
        neg_hits = [n for n in NEGATIVES if n in text_n]

        w_high = W_OCR_HIGH if source == "ocr" else W_KEYWORD_HIGH
        w_med = W_OCR_MEDIUM if source == "ocr" else W_KEYWORD_MEDIUM

        if high_hits:
            kws = ", ".join(f"{l}:{k}" for l, k in high_hits[:3])
            signals.append(Signal(
                name=f"keyword_high ({source})",
                score=1.0, weight=w_high,
                detail=f"{len(high_hits)} HIGH match(es): {kws}"))

        if med_hits:
            n = min(len(med_hits), 3)  # cap contribution
            kws = ", ".join(f"{l}:{k}" for l, k in med_hits[:3])
            signals.append(Signal(
                name=f"keyword_medium ({source})",
                score=n / 3.0, weight=w_med * n,
                detail=f"{len(med_hits)} MEDIUM match(es): {kws}"))

        if neg_hits:
            signals.append(Signal(
                name=f"negative_marker ({source})",
                score=1.0, weight=W_NEGATIVE * len(neg_hits),
                detail=f"negatives: {', '.join(neg_hits[:3])}"))

        return signals

    def _score_visual(self, image_path) -> list[Signal]:
        if self.clip is None or image_path is None:
            return []
        try:
            score = self.clip.zero_shot_score(image_path)
        except Exception as e:
            return [Signal(name="clip_error", score=0.0, weight=0.0,
                           detail=f"CLIP failed: {e}")]
        # Scale: full weight at >=0.85, half at 0.65, zero below 0.40
        if score >= 0.85:
            w = W_VISUAL
        elif score >= 0.65:
            w = W_VISUAL * 0.6
        elif score >= 0.40:
            w = W_VISUAL * 0.2
        else:
            w = 0.0
        return [Signal(name="visual_clip", score=score, weight=w,
                       detail=f"CLIP zero-shot score: {score:.3f}")]

    def _score_ocr(self, image_path) -> list[Signal]:
        if self.ocr is None or image_path is None:
            return []
        try:
            text = self.ocr.extract(image_path)
        except Exception as e:
            return [Signal(name="ocr_error", score=0.0, weight=0.0,
                           detail=f"OCR failed: {e}")]
        if not text.strip():
            return []
        return self._score_text(text, source="ocr")

    def _score_url(self, link_url: str) -> tuple[list[Signal], Optional[str], Optional[str]]:
        if not link_url:
            return [], None, None

        if self.resolve_urls:
            res = resolve_redirects(link_url)
            final_url = res["final"]
            final_domain = res["final_domain"]
        else:
            from .url_check import extract_domain
            final_url = link_url
            final_domain = extract_domain(link_url)

        cls = classify_domain(final_domain)
        weight_map = {
            "whitelist": W_URL_WHITELIST,
            "blacklist": W_URL_BLACKLIST,
            "greylist": 0.0,
            "unknown": W_URL_UNKNOWN,
        }
        sig = Signal(name=f"url_{cls['category']}",
                     score=cls["score"], weight=weight_map[cls["category"]],
                     detail=f"{final_domain}: {cls['reason']}")
        return [sig], final_url, final_domain

    # ---------- main entry ----------

    def classify(self,
                 ad_text: Optional[str] = None,
                 image_path: Optional[Union[str, Path]] = None,
                 link_url: Optional[str] = None) -> ClassificationResult:
        """Classify a single ad. All inputs optional (degrades gracefully).

        Args:
            ad_text:    ad copy / caption / link description
            image_path: local path to creative (image)
            link_url:   ad's destination URL (will be redirect-resolved)
        """
        signals: list[Signal] = []
        signals.extend(self._score_text(ad_text or "", source="copy"))
        signals.extend(self._score_visual(image_path))
        signals.extend(self._score_ocr(image_path))
        url_signals, final_url, final_domain = self._score_url(link_url or "")
        signals.extend(url_signals)

        # Whitelist short-circuit: if URL resolves to a licensed operator,
        # never flag — even if visuals/text scream casino (because they ARE
        # a casino, just a legal one).
        is_licensed = any(s.name == "url_whitelist" for s in signals)

        # Sum weights; clamp to [0, 1].
        raw = sum(s.weight for s in signals)
        score = max(0.0, min(1.0, raw))
        if is_licensed:
            score = 0.0

        if is_licensed:
            label = "licensed_operator"
        elif score >= THRESHOLD_AUTO_HI:
            label = "casino_high_confidence"
        elif score >= THRESHOLD_REPORT:
            label = "casino_review"
        else:
            label = "not_casino"

        return ClassificationResult(
            score=score, label=label, is_licensed=is_licensed,
            signals=signals, final_url=final_url, final_domain=final_domain,
        )
