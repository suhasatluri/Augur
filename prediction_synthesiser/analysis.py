"""Pure computation — probability distribution, swing factors, sentiment cascade."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from prediction_synthesiser.models import (
    ProbabilityDistribution,
    SentimentCascade,
    SwingFactor,
)


def compute_distribution(agents: list[dict]) -> ProbabilityDistribution:
    """Compute final probability breakdown from agent final states."""
    probs = [a["current_probability"] for a in agents]
    n = len(probs)

    beat = sum(1 for p in probs if p > 0.55) / n
    miss = sum(1 for p in probs if p < 0.45) / n
    inline = 1.0 - beat - miss

    mean = statistics.mean(probs)
    median = statistics.median(probs)
    std = statistics.stdev(probs) if n > 1 else 0.0

    return ProbabilityDistribution(
        p_beat=round(beat, 3),
        p_miss=round(miss, 3),
        p_inline=round(inline, 3),
        mean_probability=round(mean, 4),
        median_probability=round(median, 4),
        std_dev=round(std, 4),
        confidence_band_low=round(max(0, mean - std), 4),
        confidence_band_high=round(min(1, mean + std), 4),
    )


def extract_swing_factors(round_results: list[dict], agents: list[dict]) -> list[SwingFactor]:
    """Identify top 5 themes driving bull/bear disagreement from reasoning text."""
    # Build archetype lookup
    agent_arch = {a["id"]: a["archetype"] for a in agents}

    # Collect reasoning by archetype camp
    bull_reasons: list[str] = []
    bear_reasons: list[str] = []
    for rr in round_results:
        arch = agent_arch.get(str(rr["agent_id"]), rr.get("archetype", ""))
        if arch == "bull_analyst":
            bull_reasons.append(rr["reasoning"])
        elif arch == "bear_analyst":
            bear_reasons.append(rr["reasoning"])

    # Extract theme keywords — look for recurring noun phrases
    theme_keywords = {
        "iron_ore_pricing": ["iron ore", "ore price", "ore demand", "iron ore price"],
        "china_demand": ["china", "chinese", "stimulus", "property sector", "steel demand"],
        "copper_outlook": ["copper", "copper price", "copper production", "copper margin"],
        "currency_fx": ["aud", "usd", "currency", "exchange rate", "fx", "translation"],
        "cost_inflation": ["cost", "inflation", "wage", "energy cost", "operational cost", "margin pressure"],
        "capital_allocation": ["dividend", "capex", "capital allocation", "shareholder return", "buyback"],
        "guidance_credibility": ["guidance", "management", "credibility", "target", "forecast"],
        "esg_decarbonisation": ["esg", "decarboni", "carbon", "emission", "green", "transition"],
        "operational_execution": ["production", "operational", "execution", "volume", "output"],
        "macro_rates": ["interest rate", "rba", "fed", "monetary policy", "rate cut"],
    }

    all_text = " ".join(r["reasoning"].lower() for r in round_results)
    bull_text = " ".join(r.lower() for r in bull_reasons)
    bear_text = " ".join(r.lower() for r in bear_reasons)

    # Score each theme by: total mentions × bull-bear divergence
    theme_scores: list[tuple[str, int, float]] = []
    for theme, keywords in theme_keywords.items():
        total = sum(all_text.count(kw) for kw in keywords)
        if total < 2:
            continue
        bull_count = sum(bull_text.count(kw) for kw in keywords)
        bear_count = sum(bear_text.count(kw) for kw in keywords)
        # Divergence: how differently do bulls and bears talk about this?
        divergence = abs(bull_count - bear_count) / max(bull_count + bear_count, 1)
        theme_scores.append((theme, total, divergence))

    # Sort by total mentions × divergence
    theme_scores.sort(key=lambda x: x[1] * (1 + x[2]), reverse=True)

    # Build SwingFactor objects for top 5
    factors: list[SwingFactor] = []
    for theme, mentions, divergence in theme_scores[:5]:
        # Extract representative bull and bear reasoning snippets
        bull_snippets = _find_snippets(bull_reasons, theme_keywords[theme])
        bear_snippets = _find_snippets(bear_reasons, theme_keywords[theme])

        factors.append(SwingFactor(
            theme=theme.replace("_", " ").title(),
            description=f"Mentioned {mentions} times across all rounds",
            bull_view=bull_snippets[0] if bull_snippets else "Not a primary bull concern",
            bear_view=bear_snippets[0] if bear_snippets else "Not a primary bear concern",
            mentions=mentions,
            disagreement_score=round(divergence, 3),
        ))

    return factors


def _find_snippets(reasons: list[str], keywords: list[str], max_len: int = 150) -> list[str]:
    """Find reasoning sentences containing keywords."""
    snippets = []
    for reason in reasons:
        lower = reason.lower()
        if any(kw in lower for kw in keywords):
            # Take first sentence or truncate
            text = reason.split(".")[0].strip()
            if len(text) > max_len:
                text = text[:max_len] + "..."
            snippets.append(text)
            if len(snippets) >= 2:
                break
    return snippets


def compute_sentiment_cascade(agents: list[dict]) -> SentimentCascade:
    """Assess market reaction severity based on retail investor conviction."""
    retail = [a for a in agents if a["archetype"] == "retail_investor"]

    if not retail:
        return SentimentCascade(
            direction="muted",
            severity="mild",
            retail_conviction=0.5,
            retail_mean_probability=0.5,
            reasoning="No retail investor agents available for cascade analysis.",
        )

    retail_probs = [a["current_probability"] for a in retail]
    retail_convictions = [a["conviction"] for a in retail]
    retail_mean = statistics.mean(retail_probs)
    retail_conv = statistics.mean(retail_convictions)

    # Determine direction
    all_probs = [a["current_probability"] for a in agents]
    overall_mean = statistics.mean(all_probs)

    if overall_mean < 0.45:
        # Miss scenario — assess cascade severity
        direction = "miss_cascade"
        # Severity based on retail conviction + how far below 0.5
        if retail_conv > 0.6 and retail_mean < 0.40:
            severity = "severe"
            reasoning = (
                f"Retail investors are strongly convicted (avg conviction {retail_conv:.2f}) "
                f"and bearish (mean P={retail_mean:.2f}). High-conviction retail selling "
                f"typically amplifies post-earnings downside via momentum cascades."
            )
        elif retail_conv > 0.45 or retail_mean < 0.45:
            severity = "moderate"
            reasoning = (
                f"Retail conviction is moderate ({retail_conv:.2f}) with bearish lean "
                f"(P={retail_mean:.2f}). Expect some sentiment-driven selling but "
                f"institutional flows should provide a floor."
            )
        else:
            severity = "mild"
            reasoning = (
                f"Retail conviction is low ({retail_conv:.2f}) suggesting limited "
                f"sentiment cascade risk despite bearish lean."
            )
    elif overall_mean > 0.55:
        direction = "beat_cascade"
        if retail_conv > 0.6 and retail_mean > 0.60:
            severity = "severe"
            reasoning = (
                f"Retail investors are strongly bullish (P={retail_mean:.2f}, "
                f"conviction={retail_conv:.2f}). FOMO-driven buying could amplify upside."
            )
        else:
            severity = "moderate"
            reasoning = (
                f"Retail moderately bullish (P={retail_mean:.2f}). "
                f"Some upside momentum expected but limited FOMO risk."
            )
    else:
        direction = "muted"
        severity = "mild"
        reasoning = (
            f"Overall swarm is near consensus neutral (mean P={overall_mean:.2f}). "
            f"Retail conviction at {retail_conv:.2f} suggests muted post-earnings reaction."
        )

    return SentimentCascade(
        direction=direction,
        severity=severity,
        retail_conviction=round(retail_conv, 3),
        retail_mean_probability=round(retail_mean, 3),
        reasoning=reasoning,
    )


def compute_verdict(mean_prob: float) -> str:
    """Map mean probability to a human verdict."""
    if mean_prob >= 0.65:
        return "LIKELY BEAT"
    elif mean_prob >= 0.55:
        return "LEAN BEAT"
    elif mean_prob >= 0.45:
        return "TOSS-UP"
    elif mean_prob >= 0.35:
        return "LEAN MISS"
    else:
        return "LIKELY MISS"
