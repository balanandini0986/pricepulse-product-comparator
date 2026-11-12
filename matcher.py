"""Exact-product matching and title normalization helpers."""

import re
from typing import Dict, Optional, Set, Tuple

STOP_WORDS: Set[str] = {
    "for", "with", "and", "the", "new", "original", "latest", "best", "buy",
    "online", "india", "men", "mens", "women", "womens", "unisex", "boys",
    "girls", "kids", "pack", "set", "edition", "series", "color", "colour",
    "black", "white", "blue", "red", "green", "grey", "gray", "silver",
    "gold", "pink", "brown", "navy", "beige", "yellow", "purple", "orange",
    "from", "all", "inch", "inches", "cm", "mm", "ml", "gm", "gram", "grams",
    "kg", "gb", "tb", "ram", "rom", "ssd", "hdd",
}

ACCESSORY_WORDS: Set[str] = {
    "cover", "case", "backcover", "flipcover", "pouch", "sleeve", "tempered",
    "screen", "guard", "protector", "skin", "sticker", "decal", "strap",
    "band", "charger", "adapter", "cable", "earbuds", "earphone", "stand",
    "holder", "mount", "battery", "keyboard", "mouse", "bag", "dock",
    "refill", "compatible", "replacement", "spare",
}

PACK_PATTERNS = [
    re.compile(r"\bpack\s*of\s*(\d+)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:pcs|pc|pieces|units|pack)\b", re.I),
    re.compile(r"\bcombo\s*of\s*(\d+)\b", re.I),
]


def normalize_text(text: str) -> str:
    """Lowercase and strip punctuation while keeping alphanumeric tokens."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_pack_size(text: str) -> int:
    """Return pack quantity if explicitly specified (defaults to 1)."""
    for pat in PACK_PATTERNS:
        m = pat.search(text or "")
        if m:
            try:
                val = int(m.group(1))
                if 1 <= val <= 20:
                    return val
            except ValueError:
                pass
    return 1


def extract_spec_tokens(text: str) -> Set[str]:
    """Extract critical variant/model tokens like storage, size, or model codes."""
    low = (text or "").lower()
    tokens: Set[str] = set()
    # Storage / volume / processor codes (e.g., 128gb, 512gb, 6ml, 100ml, m3, a158wa)
    for m in re.findall(r"\b\d+\s*(?:gb|tb|ml|g|mg|l|w|mah)\b", low):
        tokens.add(re.sub(r"\s+", "", m))
    # Alphanumeric model codes (e.g., a158wa, m3, s24, 141, 15, 13)
    for tok in re.findall(r"\b[a-z]*\d+[a-z0-9]*\b", low):
        if len(tok) >= 2 and not re.fullmatch(r"\d{4}", tok):  # avoid years like 2024
            tokens.add(tok)
    return tokens


def tokenize_meaningful(text: str) -> Set[str]:
    """Return meaningful non-stopword tokens from a product title."""
    norm = normalize_text(text)
    return {t for t in norm.split() if len(t) > 1 and t not in STOP_WORDS}


def is_accessory(text: str) -> bool:
    """Detect whether a product title is primarily an accessory."""
    words = set(normalize_text(text).split())
    return bool(words & ACCESSORY_WORDS)


def score_exact_match(
    target_title: str,
    candidate_title: str,
    target_brand: str = "",
    candidate_brand: str = "",
) -> Tuple[bool, float, str]:
    """
    Evaluate whether candidate_title represents the exact same product as target_title.
    Returns (is_match, confidence_0_to_100, reason).
    """
    t_norm = normalize_text(target_title)
    c_norm = normalize_text(candidate_title)
    if not t_norm or not c_norm:
        return False, 0.0, "Empty product title"

    # 1. Reject accessory mismatches (e.g. phone case when searching for a phone)
    t_acc = is_accessory(target_title)
    c_acc = is_accessory(candidate_title)
    if not t_acc and c_acc:
        return False, 10.0, "Rejected: accessory listing instead of main product"

    # 2. Reject pack-size mismatches (single unit vs multi-pack)
    t_pack = extract_pack_size(target_title)
    c_pack = extract_pack_size(candidate_title)
    if t_pack != c_pack:
        return False, 20.0, f"Rejected: pack size mismatch ({t_pack} vs {c_pack})"

    # 3. Brand verification
    t_brand = normalize_text(target_brand)
    c_brand = normalize_text(candidate_brand)
    if not t_brand:
        t_brand = t_norm.split()[0] if t_norm.split() else ""

    brand_matched = False
    if t_brand:
        t_brand_first = t_brand.split()[0]
        if t_brand_first in c_norm or (c_brand and t_brand_first in c_brand):
            brand_matched = True
        elif c_brand and c_brand.split()[0] != t_brand_first:
            return False, 15.0, f"Rejected: brand mismatch ('{t_brand}' vs '{c_brand}')"
        elif len(t_brand_first) >= 3 and t_brand_first not in c_norm:
            return False, 15.0, f"Rejected: target brand '{t_brand}' missing in listing"

    # 4. Critical spec/model number verification
    t_specs = extract_spec_tokens(target_title)
    c_specs = extract_spec_tokens(candidate_title)
    storage_t = {s for s in t_specs if re.search(r"(gb|tb)$", s)}
    storage_c = {s for s in c_specs if re.search(r"(gb|tb)$", s)}
    if storage_t and storage_c and not (storage_t & storage_c):
        return False, 25.0, f"Rejected: storage variant mismatch ({storage_t} vs {storage_c})"

    # Model numbers (pure digits or alphanumeric codes like 141, 15, m3)
    model_t = {s for s in t_specs if not re.search(r"(gb|tb|ml|g|mg|l|w|mah)$", s)}
    model_c = {s for s in c_specs if not re.search(r"(gb|tb|ml|g|mg|l|w|mah)$", s)}
    if model_t and model_c and not (model_t & model_c):
        # Allow if target model tokens appear anywhere as substring in candidate
        if not any(mt in c_norm for mt in model_t):
            return False, 30.0, f"Rejected: model code mismatch ({model_t} vs {model_c})"

    # 5. Meaningful token overlap (asymmetric recall against target + Jaccard)
    t_toks = tokenize_meaningful(target_title)
    c_toks = tokenize_meaningful(candidate_title)
    if not t_toks or not c_toks:
        return False, 0.0, "Insufficient tokens"

    overlap = t_toks & c_toks
    recall = len(overlap) / len(t_toks)
    precision = len(overlap) / len(c_toks)
    f1 = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0

    score = (recall * 0.65 + f1 * 0.35) * 100.0
    if brand_matched:
        score = min(99.0, score + 8.0)

    # Require strong recall on target product terms
    min_required_recall = 0.50 if len(t_toks) >= 3 else 0.65
    if recall < min_required_recall or score < 55.0:
        return False, round(score, 1), f"Low title match ({round(score, 1)}%)"

    return True, round(min(99.0, score), 1), f"Exact product match ({round(score, 1)}%)"

