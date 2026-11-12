"""Price, rating, review, delivery, and value analysis engine."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

PRIORITY_WEIGHTS: Dict[str, Dict[str, float]] = {
    "balanced": {"price": 0.45, "rating": 0.35, "delivery": 0.12, "policy": 0.08},
    "price": {"price": 0.70, "rating": 0.18, "delivery": 0.07, "policy": 0.05},
    "rating": {"price": 0.20, "rating": 0.65, "delivery": 0.08, "policy": 0.07},
    "delivery": {"price": 0.25, "rating": 0.20, "delivery": 0.45, "policy": 0.10},
}

POSITIVE_THEMES = [
    "quality", "comfortable", "comfort", "original", "genuine", "camera",
    "battery", "sound", "bass", "fit", "value", "worth", "fast", "durable",
    "lightweight", "waterproof", "long lasting", "smooth", "stylish",
]
CONCERN_THEMES = [
    "fake", "damaged", "delay", "late", "small", "tight", "loose", "expensive",
    "battery drain", "heating", "clump", "smudge", "broken", "defective", "poor",
]


def analyze_offer_reviews(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize live reviews for a single platform without inventing any claims."""
    if not reviews:
        return {
            "sample_count": 0,
            "positive_count": 0,
            "neutral_count": 0,
            "negative_count": 0,
            "avg_sample_rating": None,
            "pros_mentioned": [],
            "cons_mentioned": [],
        }

    pos = sum(1 for r in reviews if (r.get("rating") or 0) >= 4)
    neu = sum(1 for r in reviews if 2.5 <= (r.get("rating") or 0) < 4)
    neg = sum(1 for r in reviews if (r.get("rating") or 0) < 2.5)
    ratings = [float(r["rating"]) for r in reviews if r.get("rating") is not None]
    avg_r = round(sum(ratings) / len(ratings), 2) if ratings else None

    full_text = " ".join(f"{r.get('title', '')} {r.get('comment', '')}" for r in reviews).lower()
    pros = [kw for kw in POSITIVE_THEMES if re.search(rf"\b{re.escape(kw)}\b", full_text)][:4]
    cons = [kw for kw in CONCERN_THEMES if re.search(rf"\b{re.escape(kw)}\b", full_text)][:3]

    return {
        "sample_count": len(reviews),
        "positive_count": pos,
        "neutral_count": neu,
        "negative_count": neg,
        "avg_sample_rating": avg_r,
        "pros_mentioned": pros,
        "cons_mentioned": cons,
    }


def _bayesian_rating_score(rating: Optional[float], count: int) -> float:
    """Compute a 0-100 confidence-adjusted rating score."""
    if rating is None or rating <= 0:
        return 50.0  # neutral when platform listing has no ratings yet
    prior_m = 25.0
    prior_c = 3.9
    v = max(0, int(count or 0))
    bayes = (v * rating + prior_m * prior_c) / (v + prior_m)
    base_score = (bayes / 5.0) * 85.0
    volume_bonus = min(15.0, math.log10(v + 1) * 3.5)
    return round(min(100.0, base_score + volume_bonus), 1)


def _delivery_score(offer: Dict[str, Any]) -> float:
    score = 65.0
    ship_fee = offer.get("shipping_fee_inr")
    if ship_fee == 0:
        score += 15.0
    elif isinstance(ship_fee, int) and ship_fee > 0:
        score -= 10.0

    days = offer.get("delivery_days")
    if days is not None:
        if days <= 1:
            score += 20.0
        elif days <= 3:
            score += 14.0
        elif days <= 5:
            score += 6.0
        else:
            score -= 5.0
    else:
        txt = (offer.get("delivery_text") or "").lower()
        if "ready to ship" in txt or "express" in txt or "same/next-day" in txt:
            score += 12.0
    return round(max(10.0, min(100.0, score)), 1)


def _policy_score(offer: Dict[str, Any]) -> float:
    score = 60.0
    if offer.get("in_stock", True):
        score += 15.0
    else:
        score -= 35.0
    ret = (offer.get("return_policy") or "").lower()
    if "return" in ret or "replacement" in ret or "exchange" in ret:
        if "not permitted" not in ret and "non-returnable" not in ret:
            score += 18.0
        else:
            score += 8.0
    if offer.get("cod_available"):
        score += 7.0
    return round(max(10.0, min(100.0, score)), 1)


def run_price_analysis(offers: List[Dict[str, Any]], priority: str = "balanced") -> Dict[str, Any]:
    """
    Score and compare all verified platform offers for the exact product,
    producing price spread statistics, per-platform scores, and buying recommendations.
    """
    if not offers:
        return {
            "priority": priority,
            "offer_count": 0,
            "offers": [],
            "price_summary": None,
            "picks": {},
            "recommendation_summary": "No accessible online platforms returned an exact match for this product.",
        }

    weights = PRIORITY_WEIGHTS.get(priority, PRIORITY_WEIGHTS["balanced"])

    for o in offers:
        ship = o.get("shipping_fee_inr") or 0
        o["effective_price_inr"] = int(o["price_inr"]) + int(ship)

    prices = [o["effective_price_inr"] for o in offers]
    min_price = min(prices)
    max_price = max(prices)
    avg_price = round(sum(prices) / len(prices))
    spread_inr = max_price - min_price
    spread_pct = round((spread_inr / max_price) * 100.0, 1) if max_price > 0 else 0.0

    scored_offers: List[Dict[str, Any]] = []
    for o in offers:
        eff = o["effective_price_inr"]
        mrp = o.get("mrp_inr") or o["price_inr"]
        discount_pct = round(((mrp - o["price_inr"]) / mrp) * 100.0, 1) if mrp > o["price_inr"] else 0.0

        p_score = round((min_price / eff) * 100.0, 1) if eff > 0 else 0.0
        r_score = _bayesian_rating_score(o.get("rating"), o.get("rating_count") or 0)
        d_score = _delivery_score(o)
        pol_score = _policy_score(o)

        composite = round(
            p_score * weights["price"]
            + r_score * weights["rating"]
            + d_score * weights["delivery"]
            + pol_score * weights["policy"],
            1,
        )
        if not o.get("in_stock", True):
            composite = round(composite * 0.5, 1)

        rev_summary = analyze_offer_reviews(o.get("reviews") or [])

        scored_offers.append({
            **o,
            "discount_pct": discount_pct,
            "savings_vs_highest_inr": max_price - eff,
            "extra_over_lowest_inr": eff - min_price,
            "is_lowest_price": eff == min_price,
            "scores": {
                "price": p_score,
                "rating": r_score,
                "delivery": d_score,
                "policy": pol_score,
                "overall": composite,
            },
            "review_analysis": rev_summary,
        })

    scored_offers.sort(key=lambda x: x["scores"]["overall"], reverse=True)
    winner = scored_offers[0]
    winner["is_recommended"] = True

    cheapest = min(scored_offers, key=lambda x: x["effective_price_inr"])
    best_rated = max(scored_offers, key=lambda x: (x["scores"]["rating"], x.get("rating_count") or 0))
    fastest = max(scored_offers, key=lambda x: x["scores"]["delivery"])

    # Build clear, data-backed recommendation text
    reasons: List[str] = [
        f"<strong>{winner['platform']}</strong> leads with an overall score of <strong>{winner['scores']['overall']}/100</strong>",
        f"priced at <strong>₹{winner['price_inr']:,}</strong>"
        + (f" ({winner['discount_pct']}% off MRP ₹{winner['mrp_inr']:,})" if winner["discount_pct"] > 0 else ""),
    ]
    if winner["is_lowest_price"] and len(scored_offers) > 1 and spread_inr > 0:
        reasons.append(f"saving you <strong>₹{spread_inr:,} ({spread_pct}%)</strong> compared to the highest listing")
    elif not winner["is_lowest_price"]:
        reasons.append(
            f"only ₹{winner['extra_over_lowest_inr']:,} above the cheapest option ({cheapest['platform']}) "
            f"while offering stronger buyer confidence"
        )
    if winner.get("rating"):
        reasons.append(
            f"backed by <strong>{winner['rating']} ★</strong> across <strong>{winner.get('rating_count', 0):,}</strong> platform ratings"
        )
    if winner.get("delivery_text"):
        reasons.append(f"with <strong>{winner['delivery_text']}</strong>")

    summary_html = ", ".join(reasons) + "."

    return {
        "priority": priority,
        "weights": weights,
        "offer_count": len(scored_offers),
        "offers": scored_offers,
        "price_summary": {
            "lowest_price_inr": min_price,
            "highest_price_inr": max_price,
            "average_price_inr": avg_price,
            "price_spread_inr": spread_inr,
            "price_spread_pct": spread_pct,
        },
        "picks": {
            "best_overall": {
                "platform": winner["platform"],
                "price_inr": winner["price_inr"],
                "score": winner["scores"]["overall"],
            },
            "lowest_price": {
                "platform": cheapest["platform"],
                "price_inr": cheapest["price_inr"],
                "savings_inr": spread_inr,
            },
            "best_rated": {
                "platform": best_rated["platform"],
                "rating": best_rated.get("rating"),
                "rating_count": best_rated.get("rating_count", 0),
            },
            "fastest_delivery": {
                "platform": fastest["platform"],
                "delivery_text": fastest.get("delivery_text"),
            },
        },
        "recommendation_summary": summary_html,
    }

