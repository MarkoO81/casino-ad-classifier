"""Tests that run WITHOUT torch/open_clip/paddleocr (pure-logic).

Verifies fusion logic, keyword detection, and URL classification.
Visual classifier is mocked so the test is fast and self-contained.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.classifier import GamblingAdClassifier
from src.keywords import find_matches, HIGH_PRECISION, MEDIUM_PRECISION


class MockCLIP:
    """Drop-in mock that returns a fixed score, for testing fusion."""
    def __init__(self, score: float):
        self.score = score
    def zero_shot_score(self, image_path):
        return self.score


def test_keyword_detection_sl():
    text = "Najboljša spletna igralnica! 100 brezplačnih vrtenj na prvi depozit."
    hits = find_matches(text, HIGH_PRECISION)
    assert len(hits) >= 2, f"expected 2+ HIGH hits, got {hits}"
    print("PASS: SL high-precision keyword detection")


def test_keyword_detection_en():
    text = "Welcome bonus 200% + 100 free spins on first deposit. Live casino."
    hits_high = find_matches(text, HIGH_PRECISION)
    assert len(hits_high) >= 2, f"expected 2+ HIGH hits, got {hits_high}"
    print("PASS: EN high-precision keyword detection")


def test_classifier_obvious_casino():
    """Ad copy + offshore-looking domain should produce HIGH score."""
    clf = GamblingAdClassifier(resolve_urls=False)
    res = clf.classify(
        ad_text="Welcome to LuckyJet Casino! 200% bonus + 100 free spins on signup.",
        link_url="https://luckyjet-casino.com/promo")
    print(f"\n  obvious casino: score={res.score:.2f} label={res.label}")
    print(res.explain())
    assert res.score >= 0.65, f"expected casino flag, got {res.score}"
    print("PASS: obvious casino flagged")


def test_classifier_licensed_operator_suppressed():
    """Licensed Slovenian operator must not be flagged even with casino copy."""
    clf = GamblingAdClassifier(resolve_urls=False)
    res = clf.classify(
        ad_text="Bonus dobrodošlice + brezplačna vrtenja!",
        link_url="https://eloterija.si/promo")
    print(f"\n  licensed op: score={res.score:.2f} label={res.label}")
    print(res.explain())
    assert res.is_licensed, "should detect licensed operator"
    assert res.score == 0.0, f"licensed op should score 0, got {res.score}"
    print("PASS: licensed operator suppressed")


def test_classifier_unrelated_ecom():
    """Non-gambling commerce ad should NOT trigger."""
    clf = GamblingAdClassifier(resolve_urls=False)
    res = clf.classify(
        ad_text="New summer collection! 30% off all dresses, free shipping.",
        link_url="https://example-fashion.com/sale")
    print(f"\n  ecom: score={res.score:.2f} label={res.label}")
    assert res.label == "not_casino", f"expected not_casino, got {res.label}"
    print("PASS: unrelated e-commerce not flagged")


def test_classifier_with_visual():
    """Visual signal should boost score on ambiguous text."""
    # Mild text that wouldn't trigger on its own
    clf_no_clip = GamblingAdClassifier(resolve_urls=False)
    res_no_clip = clf_no_clip.classify(
        ad_text="Big win waiting for you!",
        link_url="https://random-domain-xyz.com/")

    clf_with_clip = GamblingAdClassifier(
        clip_classifier=MockCLIP(score=0.92),  # mock: visually clearly a casino
        resolve_urls=False)
    res_with_clip = clf_with_clip.classify(
        ad_text="Big win waiting for you!",
        image_path="/fake/path.jpg",  # mock ignores path, just needs non-None
        link_url="https://random-domain-xyz.com/")

    print(f"\n  ambiguous, no CLIP: {res_no_clip.score:.2f}")
    print(f"  ambiguous, w/ CLIP: {res_with_clip.score:.2f}")
    assert res_with_clip.score > res_no_clip.score
    print("PASS: visual signal boosts ambiguous case")


def test_classifier_negative_marker_dampens():
    """Sports news headline shouldn't trigger even with 'bet' keyword."""
    clf = GamblingAdClassifier(resolve_urls=False)
    res = clf.classify(
        ad_text="News: Why fans bet on Slovenia in the Euro qualifier - latest report",
        link_url="https://sport-news.example/article")
    print(f"\n  sports news: score={res.score:.2f} label={res.label}")
    assert res.label == "not_casino", f"expected not_casino, got {res.label}"
    print("PASS: sports news with negative marker not flagged")


if __name__ == "__main__":
    print("Running tests...\n")
    test_keyword_detection_sl()
    test_keyword_detection_en()
    test_classifier_obvious_casino()
    test_classifier_licensed_operator_suppressed()
    test_classifier_unrelated_ecom()
    test_classifier_with_visual()
    test_classifier_negative_marker_dampens()
    print("\n[OK] all tests passed")
