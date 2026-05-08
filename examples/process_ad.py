"""End-to-end example: process one ad from the Meta Ad Library.

Demonstrates wiring the classifier into the pipeline described in the
architecture doc. In production you'd loop over API results, but the
single-ad version is clearer.

Run from project root:
    python -m examples.process_ad
"""

import sys, os, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.classifier import GamblingAdClassifier


def process_ad(ad_record: dict, image_path: str = None, clip=None, ocr=None):
    """Process a single ad record from Meta Ad Library API.

    Expected `ad_record` shape (subset of Meta Ad Library API fields):
        {
          "id": "...",
          "page_id": "...",
          "page_name": "...",
          "ad_creative_bodies": ["..."],
          "ad_creative_link_titles": ["..."],
          "ad_creative_link_descriptions": ["..."],
          "ad_creative_link_captions": ["..."],
          "ad_snapshot_url": "https://...",
          "eu_total_reach": "1000-2000",
          ...
        }
    """
    # Concatenate all the text-like fields Meta returns.
    parts = []
    for field in ("ad_creative_bodies", "ad_creative_link_titles",
                  "ad_creative_link_descriptions", "ad_creative_link_captions"):
        v = ad_record.get(field) or []
        if isinstance(v, list):
            parts.extend(v)
        elif isinstance(v, str):
            parts.append(v)
    ad_text = " | ".join(p for p in parts if p)

    # The destination URL is in link_captions (typically the bare domain) or
    # extracted from ad_snapshot_url after rendering. Real production code
    # renders ad_snapshot_url with Playwright and pulls the actual click URL
    # from the rendered preview. For this example we assume it's been done
    # and stored under "_resolved_link".
    link_url = ad_record.get("_resolved_link") or \
               (ad_record.get("ad_creative_link_captions") or [None])[0]

    classifier = GamblingAdClassifier(clip_classifier=clip, ocr_engine=ocr)
    result = classifier.classify(ad_text=ad_text, image_path=image_path,
                                 link_url=link_url)

    return {
        "ad_archive_id": ad_record.get("id"),
        "page_name": ad_record.get("page_name"),
        "score": result.score,
        "label": result.label,
        "is_licensed": result.is_licensed,
        "final_domain": result.final_domain,
        "explanation": result.explain(),
        "raw_signals": [
            {"name": s.name, "weight": s.weight, "detail": s.detail}
            for s in result.signals
        ],
    }


# -------- demo with two synthetic ad records --------

DEMO_ADS = [
    # --- POSITIVES: unlicensed casino ads ---

    # EN keywords, unknown offshore domain
    {
        "id": "1234567890",
        "page_id": "987",
        "page_name": "Lucky Spins Daily ⚡",
        "ad_creative_bodies": [
            "🎰 200% WELCOME BONUS + 100 FREE SPINS on your first deposit! "
            "Join LuckyJet Casino today, withdraw winnings instantly. "
            "18+ play responsibly."
        ],
        "ad_creative_link_titles": ["LuckyJet Casino"],
        "ad_creative_link_captions": ["luckyjet-casino.com"],
        "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=1234567890",
        "eu_total_reach": "50000-100000",
        "_resolved_link": "https://luckyjet-casino.com/promo?aff=fb1",
    },
    # SL keywords, offshore domain via affiliate redirect
    {
        "id": "4444444444",
        "page_id": "111",
        "page_name": "Casino Zvezda",
        "ad_creative_bodies": [
            "Registriraj se danes in prejmi 50 brezplačnih vrtilj brez depozita! "
            "Najboljša spletna igralnica z živo ruleto in blackjackom. "
            "Samo za odrasle 18+."
        ],
        "ad_creative_link_titles": ["Casino Zvezda"],
        "ad_creative_link_captions": ["casino-zvezda.com"],
        "_resolved_link": "https://casino-zvezda.com/sl/register?ref=fb_si",
    },
    # HR keywords, generic image caption (only hope is text + URL)
    {
        "id": "5555555555",
        "page_id": "222",
        "page_name": "Bonus King HR",
        "ad_creative_bodies": [
            "Kazino bonus dobrodošlice do 500€ + 200 besplatnih spinova! "
            "Online kockarnica s najboljšim RTP-jem. Uplata bonus odmah."
        ],
        "ad_creative_link_captions": ["kingcasino-hr.net"],
        "_resolved_link": "https://kingcasino-hr.net/bonus",
    },
    # Obfuscated text — avoids exact keyword matches, uses leetspeak/symbols
    {
        "id": "6666666666",
        "page_id": "333",
        "page_name": "Sp1ns4U",
        "ad_creative_bodies": [
            "G3t your FR33 SP1NS today — 0nline cas1no with the b3st 0dds! "
            "W1n b1g, w1thdraw fast. 18+ T&C apply."
        ],
        "ad_creative_link_captions": ["sp1ns4u.com"],
        "_resolved_link": "https://sp1ns4u.com/lp?aff=si_fb",
    },
    # Affiliate/tracker link — goes through bit.ly (greylist), lands on casino
    {
        "id": "7777777777",
        "page_id": "444",
        "page_name": "Best Casino Deals",
        "ad_creative_bodies": [
            "No deposit bonus — 30 free spins just for signing up! "
            "Live dealer tables, instant payouts. Click below."
        ],
        "ad_creative_link_captions": ["bit.ly"],
        "_resolved_link": "https://bit.ly/casino-promo-si",  # stays on greylist in demo
    },

    # --- LICENSED: Slovenian whitelisted operators (should score 0) ---

    # eloterija.si — licensed, Slovenian-language casino ad
    {
        "id": "2222222222",
        "page_id": "555",
        "page_name": "Eloterija Slovenije",
        "ad_creative_bodies": [
            "Bonus dobrodošlice za nove igralce – brezplačna vrtenja na "
            "spletni igralnici. Igrajte odgovorno."
        ],
        "ad_creative_link_titles": ["Eloterija"],
        "ad_creative_link_captions": ["eloterija.si"],
        "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=2222222222",
        "eu_total_reach": "10000-20000",
        "_resolved_link": "https://eloterija.si/promo",
    },
    # hit.si — licensed land-based + online operator
    {
        "id": "8888888888",
        "page_id": "666",
        "page_name": "HIT Casinos",
        "ad_creative_bodies": [
            "Obiščite Hit Casino Nova Gorica ali igrajte online na hit.si. "
            "Jackpot igre, rulet in poker turnirji vsak teden."
        ],
        "ad_creative_link_captions": ["hit.si"],
        "_resolved_link": "https://www.hit.si/online",
    },

    # --- HARD NEGATIVES: should NOT be flagged ---

    # Grocery store
    {
        "id": "3333333333",
        "page_id": "777",
        "page_name": "Mercator d.d.",
        "ad_creative_bodies": [
            "Tedenska akcija: 30% popust na vse mlečne izdelke. "
            "Velja od ponedeljka do nedelje."
        ],
        "ad_creative_link_captions": ["mercator.si"],
        "_resolved_link": "https://www.mercator.si/akcije",
    },
    # Mobile game (Coin Master) — NEGATIVES list should suppress
    {
        "id": "9999999999",
        "page_id": "888",
        "page_name": "Coin Master Official",
        "ad_creative_bodies": [
            "Coin Master — spin the wheel and win big! "
            "Collect free spins every day. Download now on Play Store."
        ],
        "ad_creative_link_captions": ["coinmaster.com"],
        "_resolved_link": "https://coinmaster.com/download",
    },
    # Sports news editorial — uses betting vocabulary but is a news site
    {
        "id": "1010101010",
        "page_id": "999",
        "page_name": "SportNews.si",
        "ad_creative_bodies": [
            "Analiza stavnic pred nedeljsko tekmo: kdo ima najboljše kvote? "
            "Novice in report o nogometnih stavah. Preberi članek."
        ],
        "ad_creative_link_captions": ["sportnews.si"],
        "_resolved_link": "https://sportnews.si/stave-analiza",
    },
    # Finance / investment ad — uses "bonus", "win", "odds" loosely
    {
        "id": "1111111111",
        "page_id": "101",
        "page_name": "TradeNow Investments",
        "ad_creative_bodies": [
            "Start trading today — get a €50 welcome bonus on your first deposit. "
            "Better odds than traditional savings. Capital at risk."
        ],
        "ad_creative_link_captions": ["tradenow-invest.eu"],
        "_resolved_link": "https://tradenow-invest.eu/register",
    },
]


if __name__ == "__main__":
    print("Processing demo ads...\n" + "=" * 60)
    for ad in DEMO_ADS:
        result = process_ad(ad, image_path=None, clip=None, ocr=None)
        print(f"\nAd: {result['page_name']} (id={result['ad_archive_id']})")
        print(f"  Score: {result['score']:.2f}   Label: {result['label']}")
        print(f"  Domain: {result['final_domain']}")
        print(f"  Reasoning:")
        for line in result['explanation'].split("\n")[2:]:
            print(f"  {line}")
