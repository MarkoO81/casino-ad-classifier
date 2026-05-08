"""Multilingual keyword lists for gambling ad detection.

Tuned for the Slovenian / ex-Yugoslav market, where offshore operators
commonly run ads in SL, HR, SR/BS, and EN. Keywords are split into
HIGH precision (rarely appear outside gambling) and MEDIUM precision
(appear in gambling but also in mainstream marketing).

Match strategy:
  - HIGH keyword in copy => very strong gambling signal
  - 2+ MEDIUM keywords co-occurring => strong gambling signal
  - 1 MEDIUM keyword => weak signal, needs visual/URL confirmation
"""

HIGH_PRECISION = {
    # Slovenian
    "sl": [
        "igralnica", "spletna igralnica", "vrtenja", "brezplačna vrtenja",
        "depozitni bonus", "bonus dobrodošlice", "stavnica", "stavnice",
        "rulet", "ruleta", "blackjack", "baccarat", "kazino",
        "pokerni turnir", "živi krupje", "živa igralnica",
    ],
    # Croatian / Bosnian / Serbian (offshore operators often reuse creatives)
    "hr": [
        "kockarnica", "online kockarnica", "kladionica", "klađenje",
        "bonus dobrodošlice", "besplatni spinovi", "depozit", "rulet",
        "kazino bonus", "uplata bonus",
    ],
    # English (used directly even in SL-targeted ads)
    "en": [
        "online casino", "free spins", "no deposit bonus", "welcome bonus",
        "deposit bonus", "live casino", "live dealer", "sportsbook",
        "betting bonus", "wager free", "wagering requirement",
        "match bonus", "reload bonus", "cashback bonus",
        "200% bonus", "100% bonus", "100 free spins", "50 free spins",
    ],
}

MEDIUM_PRECISION = {
    "sl": [
        "bonus", "stava", "stave", "dobitek", "jackpot",
        "vrtenje", "igre na srečo", "loterija", "srečka",
    ],
    "hr": [
        "bonus", "oklada", "dobitak", "jackpot", "spin", "spinovi",
        "igre na sreću", "lutrija", "srećka",
    ],
    "en": [
        "bet", "bets", "betting", "casino", "slots", "slot",
        "jackpot", "spin", "spins", "win", "wins", "winner",
        "odds", "stake", "wager", "lucky", "fortune",
        "roulette", "poker", "blackjack", "baccarat",
    ],
}

# Strong NEGATIVE indicators — if we see these, the ad is probably NOT
# a casino even if some MEDIUM keywords match. Reduces false positives
# from sports news, video games (Candy Crush etc.), state lottery, etc.
NEGATIVES = [
    # Mobile/social games that use casino-adjacent language
    "candy crush", "coin master", "slotomania",  # social casino - debatable
    "play store", "app store", "google play",
    # Video game contexts
    "esports", "league of legends", "fortnite", "fifa",
    # News/editorial
    "news", "novice", "članek", "report",
    # Licensed Slovenian operators - whitelist names (also handled in url_check)
    "loterija slovenije", "športna loterija", "e-stave",
]


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace for matching."""
    if not text:
        return ""
    return " ".join(text.lower().split())


def find_matches(text: str, keyword_lists: dict) -> list:
    """Return list of (lang, keyword) tuples for matches found in text."""
    text_n = normalize(text)
    if not text_n:
        return []
    hits = []
    for lang, kws in keyword_lists.items():
        for kw in kws:
            if kw in text_n:
                hits.append((lang, kw))
    return hits
