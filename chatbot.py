"""Real-time conversational customer support & shopping assistant (no API keys required)."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


GREETINGS = {"hi", "hello", "hey", "hii", "hola", "namaste", "good morning", "good evening", "good afternoon"}
THANKS = {"thanks", "thank you", "thx", "ty", "awesome", "great", "perfect", "cool", "nice"}


def _fmt_inr(val: Any) -> str:
    try:
        return f"₹{int(round(float(val))):,}"
    except (TypeError, ValueError):
        return "N/A"


def _extract_search_query(msg: str) -> Optional[str]:
    """Detect when a customer asks to look up or compare a specific product live."""
    text = msg.strip()
    patterns = [
        r"^(?:please\s+)?(?:compare|check|find|search|lookup|look up|show|fetch|get)\s+(?:live\s+)?(?:(?:prices?|offers?|reviews?|ratings?|deals?)\s+)?(?:(?:for|of|on)\s+)?(.+)$",
        r"^(?:price|cost|rate|reviews?|ratings?|delivery)\s+(?:for|of)\s+(.+)$",
        r"^how much (?:is|does)\s+(.+?)(?:\s+cost)?\??$",
        r"^where (?:can i|to) buy\s+(.+?)\??$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            q = m.group(1).strip(" ?!.,\"'")
            # Ignore generic pronouns referring to the already-open product
            low_q = q.lower()
            if low_q in {"this", "it", "this product", "this item", "the product", "that", "one"}:
                return None
            if len(q) >= 3 and not low_q.startswith(("this ", "that ", "the same")):
                # Strip trailing filler like "on flipkart/amazon/online"
                q = re.sub(r"\s+(?:online|on\s+(?:amazon|flipkart|myntra|snapdeal).*)$", "", q, flags=re.I).strip()
                if len(q) >= 3:
                    return q
    return None


def _resolve_active_compare(
    context: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return currently open comparison data, or the last live comparison fetched in chat."""
    cmp_data = context.get("compareData")
    if isinstance(cmp_data, dict) and cmp_data.get("analysis"):
        return cmp_data
    for turn in reversed(history):
        if isinstance(turn, dict) and isinstance(turn.get("compareData"), dict):
            return turn["compareData"]
    return None


def _summarize_offer_reviews(offers: List[Dict[str, Any]], target_platform: Optional[str] = None) -> str:
    relevant = [
        o for o in offers
        if not target_platform or target_platform.lower() in o.get("platform", "").lower()
    ]
    if not relevant:
        return f"I couldn't find an exact matched listing on **{target_platform}** for this product."

    lines: List[str] = []
    for o in relevant:
        plat = o.get("platform", "Store")
        rating = o.get("rating")
        rc = o.get("rating_count") or 0
        ra = o.get("review_analysis") or {}
        revs = o.get("reviews") or []

        header = f"• **{plat}**: {rating} ★ ({rc:,} ratings)" if rating else f"• **{plat}**: No rating yet"
        if ra.get("sample_count"):
            header += f" — {ra['positive_count']} positive, {ra['neutral_count']} neutral, {ra['negative_count']} negative in sampled reviews"
        lines.append(header)

        if ra.get("pros_mentioned"):
            lines.append(f"  Pros mentioned: {', '.join(ra['pros_mentioned'])}")
        if ra.get("cons_mentioned"):
            lines.append(f"  Concerns mentioned: {', '.join(ra['cons_mentioned'])}")
        if revs:
            top = revs[0]
            snippet = (top.get("comment") or "").strip().replace("\n", " ")[:150]
            lines.append(f"  Buyer sample ({top.get('user', 'Verified Buyer')}, {top.get('rating', '')}★): \"{snippet}\"")
        else:
            lines.append("  (No text review comments exposed on this store page; rating summary above is live.)")

    return "\n".join(lines)


def _format_comparison_overview(cmp_data: Dict[str, Any]) -> str:
    prod = cmp_data.get("product") or {}
    analysis = cmp_data.get("analysis") or {}
    offers = analysis.get("offers") or []
    ps = analysis.get("price_summary")
    picks = analysis.get("picks") or {}

    if not offers or not ps:
        statuses = cmp_data.get("platform_status") or []
        blocked = [s["platform"] for s in statuses if s.get("status") == "blocked_by_antibot"]
        msg = f"I checked live platforms for **{prod.get('title', 'this product')}**, but none of the accessible stores returned an exact match."
        if blocked:
            msg += f" ({', '.join(blocked)} currently require direct browser verification due to anti-bot protection.)"
        return msg

    lines = [
        f"Here is the live cross-platform breakdown for **{prod.get('title', 'this product')}**:",
        f"• **Price Range**: {_fmt_inr(ps['lowest_price_inr'])} – {_fmt_inr(ps['highest_price_inr'])} (Save up to **{_fmt_inr(ps['price_spread_inr'])} / {ps['price_spread_pct']}%**)",
    ]
    for o in offers:
        tag = " 🏆 Recommended" if o.get("is_recommended") else ""
        r_str = f"{o['rating']}★ ({o.get('rating_count', 0):,})" if o.get("rating") else "Unrated"
        lines.append(
            f"• **{o['platform']}**{tag}: **{_fmt_inr(o['price_inr'])}** | {r_str} | {o.get('delivery_text', 'Standard')} | Score **{o.get('scores', {}).get('overall', 0)}/100**"
        )
    best = picks.get("best_overall")
    if best:
        lines.append(f"\n**Verdict:** I recommend **{best['platform']}** at **{_fmt_inr(best['price_inr'])}** (Score {best['score']}/100).")
    return "\n".join(lines)


def handle_customer_chat(
    message: str,
    history: List[Dict[str, Any]],
    context: Dict[str, Any],
    live_lookup_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Process a customer support or live shopping query and return a structured reply,
    optional suggested quick-actions, and any live comparison data fetched.
    """
    msg = (message or "").strip()
    if not msg:
        return {
            "reply": "Hi! I'm the PricePulse live support assistant. Ask me about any product, price comparison, reviews, delivery, or store availability.",
            "suggestions": [
                "Compare boAt Airdopes 141",
                "Why is Amazon not showing?",
                "How is the best buy score calculated?",
            ],
        }

    low = re.sub(r"[!?.,]+$", "", msg.lower()).strip()

    # 1. Greetings & politeness
    if low in GREETINGS:
        active = _resolve_active_compare(context, history)
        if active:
            title = (active.get("product") or {}).get("title", "this product")
            return {
                "reply": f"Hello! I see you're checking **{title}**. Ask me which platform gives the best value, what verified buyers say in reviews, delivery timelines, or tell me another product to search live!",
                "suggestions": [
                    "Which platform should I buy from?",
                    "Summarize buyer reviews",
                    "Compare delivery & return policies",
                ],
            }
        return {
            "reply": "Hello! Welcome to PricePulse Live Support. I can help you compare exact products across online stores in real time, explain buyer reviews, or troubleshoot any store issue. What are you looking for today?",
            "suggestions": [
                "Compare Puma Smashic Sneakers",
                "Compare Maybelline Sky High Mascara",
                "Why is Amazon showing anti-bot notice?",
            ],
        }

    if low in THANKS:
        return {
            "reply": "You're welcome! Let me know if you want to compare another product, switch priority mode (Lowest Price vs Best Ratings vs Fastest Delivery), or need any other help.",
            "suggestions": ["Compare another product", "Explain scoring weights"],
        }

    # 2. Customer troubleshooting & platform/support questions
    if any(k in low for k in ["amazon", "nykaa"]) and any(
        w in low for w in ["why", "not showing", "missing", "blocked", "cant", "can't", "no price", "where", "add"]
    ):
        return {
            "reply": (
                "**Why Amazon India / Nykaa may show a verification notice:**\n"
                "Amazon India and Nykaa protect their pages with interactive anti-bot/CAPTCHA gates. PricePulse **never bypasses CAPTCHAs or invents fake prices**.\n\n"
                "**How to include them right now:**\n"
                "1. Click **Check on Store ↗** under *Platform Availability* to view the live price in your browser.\n"
                "2. Expand **\"+ Optional: Add price/rating you checked on Amazon.in or Nykaa\"** below the comparison cards and enter what you see—the Price & Value Analysis will immediately recalculate with that store included!"
            ),
            "suggestions": ["Compare current matched stores", "Explain scoring weights"],
        }

    if any(k in low for k in ["how", "score", "calculate", "algorithm", "recommend", "weight", "ranking"]):
        return {
            "reply": (
                "**How PricePulse calculates the Best Buy recommendation (0–100):**\n"
                "In **Balanced** mode:\n"
                "• **45% Price Value** — How close the effective price (price + shipping) is to the lowest live price.\n"
                "• **35% Ratings & Review Volume** — Bayesian-adjusted rating combining star score and total rating count.\n"
                "• **12% Delivery & Shipping** — Free shipping and estimated dispatch/delivery speed.\n"
                "• **8% Return Policy & Stock** — Active stock, return/replacement terms, and COD availability.\n\n"
                "You can also switch the **Analysis Priority** dropdown anytime to focus on *Lowest Price*, *Best Ratings*, or *Fastest Delivery*."
            ),
            "suggestions": ["Which platform should I buy from?", "Why are some stores excluded?"],
        }

    if any(k in low for k in ["why", "excluded", "not listed", "not found", "missing store", "snapdeal", "shopclues"]) and any(
        w in low for w in ["store", "platform", "excluded", "not", "missing", "match", "review"]
    ):
        return {
            "reply": (
                "**Why some platforms are excluded from a comparison:**\n"
                "Our Exact-Product Matcher checks brand, model/storage codes, and pack size, and rejects accessories (like phone cases or lookalike brands). If a store doesn't carry that exact product, it is excluded so you **never see reviews or prices from the wrong product**.\n\n"
                "Tip: If a search returned too few matches, try searching with just `Brand + Model` (for example, `Puma Smashic` instead of a long variant string)."
            ),
            "suggestions": ["Search a product live", "How is the score calculated?"],
        }

    if any(k in low for k in ["refresh", "outdated", "old price", "stale", "wrong price", "updated"]):
        return {
            "reply": (
                "Live results are cached for up to 15 minutes so browsing stays fast. Click **↻ Refresh Live** (top-right) or **↻ Re-fetch Live** inside the comparison view to pull fresh real-time prices, stock, and reviews directly from the online platforms."
            ),
            "action": "refresh",
            "suggestions": ["Which platform is cheapest right now?"],
        }

    # 3. Live product search / compare intent from chat
    search_q = _extract_search_query(msg)
    if search_q and live_lookup_fn is not None:
        live_res = live_lookup_fn(search_q)
        items = live_res.get("catalog_items") or []
        cmp_data = live_res.get("compareData")
        if cmp_data and (cmp_data.get("analysis") or {}).get("offers"):
            reply = _format_comparison_overview(cmp_data)
            return {
                "reply": reply,
                "compareData": cmp_data,
                "suggested_items": items[:3],
                "suggestions": [
                    "Summarize buyer reviews for this",
                    "Compare delivery & return policies",
                    "Which is cheapest?",
                ],
            }
        if items:
            top_lines = "\n".join(
                f"• **[{it['platform']}]** {it['title']} — **{_fmt_inr(it['price_inr'])}**"
                + (f" ({it['rating']}★)" if it.get("rating") else "")
                for it in items[:4]
            )
            return {
                "reply": f"I searched live stores for **{search_q}** and found these listings (click any card below to open full cross-platform comparison):\n{top_lines}",
                "suggested_items": items[:4],
                "suggestions": ["Compare top result", "Search a different brand"],
            }
        return {
            "reply": f"I checked live platforms for **{search_q}**, but didn't find an exact matching listing right now. Try shortening the product name to `Brand + Model` (e.g., `boAt Airdopes 141` or `iPhone 15`).",
            "suggestions": ["Compare Puma Smashic Sneakers", "Compare boAt Airdopes 141"],
        }

    # 4. Questions about the active (or last discussed) product comparison
    active_cmp = _resolve_active_compare(context, history)
    if active_cmp:
        prod = active_cmp.get("product") or {}
        analysis = active_cmp.get("analysis") or {}
        offers = analysis.get("offers") or []
        ps = analysis.get("price_summary")
        picks = analysis.get("picks") or {}

        if not offers:
            return {
                "reply": f"No accessible online platforms returned an exact match for **{prod.get('title', 'this product')}** yet. Try searching with a shorter `Brand + Model` name or add a browser-verified quote from Amazon/Nykaa.",
                "suggestions": ["Why are some stores excluded?", "Compare boAt Airdopes 141"],
            }

        target_plat = None
        for p_name in ("Flipkart", "Myntra", "Snapdeal", "ShopClues", "Amazon", "Nykaa"):
            if p_name.lower() in low:
                target_plat = p_name
                break

        # Reviews / pros / cons / quality questions
        if any(k in low for k in ["review", "feedback", "comment", "complaint", "bad", "negative", "positive", "pros", "cons", "good", "worth", "quality", "genuine", "original", "fake"]):
            rev_text = _summarize_offer_reviews(offers, target_platform=target_plat)
            return {
                "reply": f"**Verified Buyer Reviews for {prod.get('title', 'this product')}:**\n{rev_text}",
                "suggestions": ["Which platform should I buy from?", "Compare delivery & return policies"],
            }

        # Delivery / shipping / return / seller questions
        if any(k in low for k in ["deliver", "shipping", "fast", "arrive", "when", "return", "refund", "replace", "exchange", "seller", "cod", "cash on delivery", "stock"]):
            lines = [f"**Delivery, Return & Seller Details for {prod.get('title', 'this product')}:**"]
            for o in offers:
                if target_plat and target_plat.lower() not in o.get("platform", "").lower():
                    continue
                ship = "Free shipping" if o.get("shipping_fee_inr") == 0 else (
                    f"+{_fmt_inr(o['shipping_fee_inr'])} shipping" if o.get("shipping_fee_inr") else "Standard shipping"
                )
                cod = "COD available" if o.get("cod_available") else "Check COD at checkout"
                lines.append(
                    f"• **{o['platform']}**: {o.get('delivery_text', 'Standard')} ({ship}) | Returns: {o.get('return_policy', 'Standard')} | Seller: {o.get('seller', o['platform'])} | {cod}"
                )
            return {
                "reply": "\n".join(lines),
                "suggestions": ["Which platform should I buy from?", "Summarize buyer reviews"],
            }

        # Price / cheapest / savings / recommendation questions
        if any(k in low for k in ["cheap", "lowest", "price", "cost", "save", "discount", "offer", "which", "best", "suggest", "recommend", "buy", "compare", "difference", "vs"]):
            return {
                "reply": _format_comparison_overview(active_cmp),
                "suggestions": ["Summarize buyer reviews", "Compare delivery & return policies"],
            }

        # Fallback when a product is open
        return {
            "reply": (
                f"I'm looking at **{prod.get('title', 'this product')}** across {len(offers)} matched platform(s).\n"
                + _format_comparison_overview(active_cmp)
            ),
            "suggestions": ["Summarize buyer reviews", "Compare delivery & return policies"],
        }

    # 5. General fallback when no product is open yet
    return {
        "reply": (
            "I can help you in real time! Try asking me things like:\n"
            "• `Compare boAt Airdopes 141`\n"
            "• `Find price of Puma Smashic Sneakers`\n"
            "• Or click any product card on the page and ask me **\"Which store should I buy from?\"**, **\"What do reviews say?\"**, or **\"Compare delivery & returns\"**."
        ),
        "suggestions": [
            "Compare boAt Airdopes 141",
            "Compare Puma Smashic Sneakers",
            "Why is Amazon not showing?",
        ],
    }
