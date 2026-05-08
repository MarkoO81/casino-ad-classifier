"""CLIP zero-shot prompts for gambling-ad image classification.

Strategy: prompt-engineer multiple positive and negative class descriptions,
take the softmax across them, and sum the probability mass on the positive
class. This avoids brittle single-prompt zero-shot.

Recommended models (in order of quality / size):
  - "ViT-L-14-quickgelu" / "openai"  -- strong English baseline
  - "xlm-roberta-base-ViT-B-32" / "laion5b_s13b_b90k"  -- multilingual
  - "ViT-SO400M-14-SigLIP" / "webli"  -- best quality, larger

For ads with embedded SL/HR text, the multilingual variant matters less than
you'd expect because OCR pulls the text out separately. Default to the
English ViT-L for speed and quality.
"""

# Positive prompts — different ways to describe a casino/gambling ad creative.
# More variations = more robust score. Aim for 8-15 variations.
POSITIVE_PROMPTS = [
    "an advertisement for an online casino",
    "a banner ad promoting casino games",
    "a promotional image for online slot machines",
    "a banner advertising free spins and casino bonuses",
    "an ad for a sports betting site",
    "a banner showing roulette wheels and casino chips",
    "a promotional image for online poker",
    "a casino welcome bonus banner advertisement",
    "an online gambling website advertisement",
    "an ad showing a slot machine jackpot",
    "a banner promoting a deposit bonus for casino games",
    "live dealer casino promotional banner",
]

# Negative prompts — competing classes to soak up probability mass.
# Pick categories that visually overlap with casino ads (bright colors,
# money imagery, "WIN" copy) but aren't gambling.
NEGATIVE_PROMPTS = [
    "a mobile video game advertisement",
    "an e-commerce product banner",
    "a financial services advertisement",
    "a cryptocurrency exchange banner",
    "a sports news article header",
    "a state lottery advertisement",
    "a fashion brand banner ad",
    "a food delivery promotional image",
    "a travel booking banner ad",
    "an electronics promotional banner",
    "a news article thumbnail",
    "a social media post about a personal event",
    "a charity fundraising banner",
    "a software or app advertisement",
    "a real estate listing image",
]


def build_prompt_set():
    """Return (all_prompts, positive_indices) for classifier setup."""
    all_prompts = POSITIVE_PROMPTS + NEGATIVE_PROMPTS
    positive_indices = list(range(len(POSITIVE_PROMPTS)))
    return all_prompts, positive_indices
