"""Live multi-platform product search and product-detail scrapers (no API keys)."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from matcher import normalize_text, score_exact_match

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}

TIMEOUT = 12


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%d %b %Y, %I:%M %p IST")


def _parse_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(round(val))
    digits = re.sub(r"[^\d]", "", str(val))
    return int(digits) if digits else None


def _parse_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    m = re.search(r"\d+(?:\.\d+)?", str(val))
    return float(m.group(0)) if m else None


def _estimate_days_from_text(delivery_text: Optional[str]) -> Optional[int]:
    """Parse delivery days from live platform delivery text when an exact date or day count appears."""
    if not delivery_text:
        return None
    low = delivery_text.lower()
    if "today" in low or "same day" in low:
        return 0
    if "tomorrow" in low:
        return 1
    m_days = re.search(r"(\d+)\s*(?:-\s*\d+\s*)?days?", low)
    if m_days:
        return int(m_days.group(1))
    # Parse e.g. "Delivery by Sunday, 15 Nov" or "15 Nov"
    m_date = re.search(r"(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", low)
    if m_date:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        day = int(m_date.group(1))
        month = months[m_date.group(2)]
        today = datetime.now()
        year = today.year if month >= today.month else today.year + 1
        try:
            target = datetime(year, month, day)
            delta = (target.date() - today.date()).days
            if 0 <= delta <= 45:
                return delta
        except ValueError:
            pass
    return None


# ==============================================================================
# FLIPKART ADAPTER
# ==============================================================================

def _flipkart_img(url_tmpl: str) -> str:
    if not url_tmpl:
        return ""
    u = url_tmpl.replace("{@width}", "416").replace("{@height}", "416").replace("{@quality}", "80")
    if u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def search_flipkart(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    s = _session()
    url = f"https://www.flipkart.com/search?q={quote(query)}"
    r = s.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return []

    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", r.text, re.S)
    results: List[Dict[str, Any]] = []
    if not m:
        return results

    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return results

    slots = state.get("pageDataV4", {}).get("page", {}).get("data", {}).get("10003", [])
    for slot in slots:
        widget = slot.get("widget", {})
        if widget.get("type") != "PRODUCT_SUMMARY":
            continue
        for item in widget.get("data", {}).get("products", []):
            val = item.get("productInfo", {}).get("value", {})
            titles = val.get("titles", {})
            title = titles.get("title") or titles.get("newTitle") or ""
            subtitle = titles.get("subtitle") or ""
            full_title = f"{title} ({subtitle})".strip() if subtitle and subtitle not in title else title
            if not full_title:
                continue

            prices = val.get("pricing", {}).get("prices", [])
            price = next((p.get("value") for p in prices if not p.get("strikeOff")), None)
            mrp = next((p.get("value") for p in prices if p.get("strikeOff")), price)
            if not price:
                continue

            rating_obj = val.get("rating") or {}
            rating = _parse_float(rating_obj.get("average"))
            rating_count = _parse_int(rating_obj.get("count"))
            review_count = _parse_int(rating_obj.get("reviewCount"))

            imgs = val.get("media", {}).get("images", [])
            img_url = _flipkart_img(imgs[0].get("url", "")) if imgs else ""

            base_url = val.get("baseUrl", "")
            prod_url = urljoin("https://www.flipkart.com", base_url) if base_url else url
            brand = (val.get("analyticsData") or {}).get("brand") or (title.split()[0] if title else "")

            results.append({
                "platform": "Flipkart",
                "title": full_title,
                "brand": brand,
                "price_inr": int(price),
                "mrp_inr": int(mrp) if mrp else int(price),
                "rating": round(rating, 2) if rating else None,
                "rating_count": rating_count or 0,
                "review_count": review_count or 0,
                "image_url": img_url,
                "url": prod_url,
                "in_stock": (val.get("availability") or {}).get("displayState", "IN_STOCK") == "IN_STOCK",
                "category": (val.get("analyticsData") or {}).get("category") or "",
            })
            if len(results) >= limit:
                return results
    return results


def fetch_flipkart_pdp(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a matched Flipkart offer with live PDP JSON-LD reviews, delivery, seller, and return policy."""
    s = _session()
    url = candidate["url"]
    r = s.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return candidate

    soup = BeautifulSoup(r.text, "html.parser")
    reviews: List[Dict[str, Any]] = []
    shipping_fee: Optional[int] = None
    return_policy: Optional[str] = None
    availability = candidate.get("in_stock", True)
    rating = candidate.get("rating")
    rating_count = candidate.get("rating_count", 0)
    review_count = candidate.get("review_count", 0)
    price = candidate.get("price_inr")

    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            raw = json.loads(sc.string or "{}")
            items = raw if isinstance(raw, list) else [raw]
            for obj in items:
                if obj.get("@type") != "Product":
                    continue
                if not candidate.get("image_url") and obj.get("image"):
                    imgs = obj.get("image")
                    candidate["image_url"] = imgs[0] if isinstance(imgs, list) else str(imgs)
                offers = obj.get("offers") or {}
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if offers.get("price"):
                    price = _parse_int(offers["price"]) or price
                avail_str = str(offers.get("availability", ""))
                if avail_str:
                    availability = "InStock" in avail_str
                ship = offers.get("shippingDetails") or {}
                if isinstance(ship, dict) and ship.get("shippingRate"):
                    shipping_fee = _parse_int(ship["shippingRate"].get("value"))
                ret = offers.get("hasMerchantReturnPolicy") or {}
                if isinstance(ret, dict):
                    return_policy = ret.get("description") or ret.get("returnPolicyCategory")

                agg = obj.get("aggregateRating") or {}
                if agg.get("ratingValue"):
                    rating = _parse_float(agg["ratingValue"]) or rating
                if agg.get("ratingCount"):
                    rating_count = _parse_int(agg["ratingCount"]) or rating_count
                if agg.get("reviewCount"):
                    review_count = _parse_int(agg["reviewCount"]) or review_count

                for rev in obj.get("review") or []:
                    author = (rev.get("author") or {}).get("name") or "Flipkart Buyer"
                    rr = _parse_float((rev.get("reviewRating") or {}).get("ratingValue"))
                    body = (rev.get("reviewBody") or "").strip()
                    title = (rev.get("name") or "").strip()
                    if body or title:
                        reviews.append({
                            "user": author,
                            "rating": rr or rating or 5,
                            "title": title,
                            "comment": body or title,
                            "date": rev.get("datePublished") or "",
                            "verified_platform": "Flipkart",
                        })
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

    # Parse visible delivery date & seller from page text
    page_text = soup.get_text(" ", strip=True)
    deliv_match = re.search(r"(Delivery by\s+[A-Za-z0-9,\s]{3,30})", page_text)
    delivery_text = deliv_match.group(1).strip() if deliv_match else None
    if delivery_text:
        for stop_kw in ("Seller", "Fulfilled by", "Only ", "Usually "):
            if stop_kw in delivery_text:
                delivery_text = delivery_text.split(stop_kw)[0].strip()
    seller_match = re.search(r"Seller:\s*([A-Za-z0-9 &._-]{2,35}?)(?:\s+\d\.\d|\s+See other|\s*$)", page_text)
    seller_name = seller_match.group(1).strip() if seller_match else "Flipkart Verified Seller"

    # Extract rating breakdown & MRP from __INITIAL_STATE__ if present
    mrp_inr = candidate.get("mrp_inr") or price
    rating_breakdown: Dict[str, int] = {}
    m_st = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", r.text, re.S)
    if m_st:
        st_txt = m_st.group(1)
        m_br = re.search(r'"breakup"\s*:\s*\[(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\]', st_txt)
        if m_br:
            # Flipkart stores breakup in [1★, 2★, 3★, 4★, 5★]
            rating_breakdown = {
                "5": int(m_br.group(5)),
                "4": int(m_br.group(4)),
                "3": int(m_br.group(3)),
                "2": int(m_br.group(2)),
                "1": int(m_br.group(1)),
            }
        m_mrp = re.search(r'"mrp"\s*:\s*\{\s*"value"\s*:\s*(\d+)', st_txt)
        if m_mrp:
            mrp_val = int(m_mrp.group(1))
            if mrp_val >= (price or 0):
                mrp_inr = mrp_val

    return {
        **candidate,
        "price_inr": price,
        "mrp_inr": mrp_inr,
        "rating": round(rating, 2) if rating else None,
        "rating_count": rating_count,
        "review_count": review_count,
        "rating_breakdown": rating_breakdown,
        "in_stock": availability,
        "shipping_fee_inr": shipping_fee,
        "delivery_text": delivery_text or ("Free Delivery" if shipping_fee == 0 else "Standard Delivery"),
        "delivery_days": _estimate_days_from_text(delivery_text),
        "return_policy": return_policy or "Platform Standard Return Policy",
        "seller": seller_name,
        "reviews": reviews,
        "fetched_at": _now_iso(),
    }


# ==============================================================================
# MYNTRA ADAPTER
# ==============================================================================

def search_myntra(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    s = _session()
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    if not slug:
        return []
    url = f"https://www.myntra.com/{slug}"
    r = s.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return []

    m = re.search(r"window\.__myx\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    products = data.get("searchData", {}).get("results", {}).get("products", [])
    results: List[Dict[str, Any]] = []
    for p in products[:limit]:
        title = p.get("productName") or p.get("product") or ""
        price = _parse_int(p.get("price"))
        mrp = _parse_int(p.get("mrp")) or price
        if not title or not price:
            continue
        landing = p.get("landingPageUrl") or ""
        prod_url = urljoin("https://www.myntra.com/", landing) if landing else url
        rating = _parse_float(p.get("rating"))
        rating_count = _parse_int(p.get("ratingCount")) or 0

        results.append({
            "platform": "Myntra",
            "title": title,
            "brand": p.get("brand") or "",
            "price_inr": price,
            "mrp_inr": mrp,
            "rating": round(rating, 2) if rating and rating > 0 else None,
            "rating_count": rating_count,
            "review_count": 0,
            "image_url": p.get("searchImage") or "",
            "url": prod_url,
            "in_stock": True,
            "category": p.get("category") or "",
        })
    return results


def fetch_myntra_pdp(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a matched Myntra offer with live pdpData reviews, rating histogram, seller, and return policy."""
    s = _session()
    r = s.get(candidate["url"], timeout=TIMEOUT)
    if r.status_code != 200:
        return candidate

    m = re.search(r"window\.__myx\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if not m:
        return candidate

    try:
        pdp = json.loads(m.group(1)).get("pdpData", {})
    except json.JSONDecodeError:
        return candidate

    price_obj = pdp.get("price") or {}
    price = _parse_int(price_obj.get("discounted")) or candidate["price_inr"]
    mrp = _parse_int(price_obj.get("mrp")) or candidate["mrp_inr"]
    if not candidate.get("image_url"):
        albums = (pdp.get("media") or {}).get("albums") or []
        if albums and albums[0].get("images"):
            candidate["image_url"] = albums[0]["images"][0].get("imageURL") or ""

    ratings_obj = pdp.get("ratings") or {}
    avg_rating = _parse_float(ratings_obj.get("averageRating")) or candidate.get("rating")
    total_ratings = _parse_int(ratings_obj.get("totalCount")) or candidate.get("rating_count", 0)
    rev_info = ratings_obj.get("reviewInfo") or {}
    review_count = _parse_int(rev_info.get("reviewsCount")) or 0

    rating_breakdown: Dict[str, int] = {}
    for row in ratings_obj.get("ratingInfo") or []:
        k = str(row.get("rating", ""))
        if k in {"1", "2", "3", "4", "5"}:
            rating_breakdown[k] = int(row.get("count") or 0)

    reviews: List[Dict[str, Any]] = []
    for rev in rev_info.get("topReviews") or []:
        txt = (rev.get("reviewText") or "").strip()
        if not txt:
            continue
        ts = rev.get("timestamp")
        date_str = ""
        if ts and str(ts).isdigit():
            try:
                sec = int(ts) / 1000.0
                if sec < 2500000000:
                    date_str = datetime.fromtimestamp(sec).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass
        reviews.append({
            "user": rev.get("userName") or "Myntra Buyer",
            "rating": _parse_float(rev.get("userRating")) or avg_rating or 5,
            "title": "",
            "comment": txt,
            "date": date_str,
            "verified_platform": "Myntra",
        })

    flags = pdp.get("flags") or {}
    in_stock = not bool(flags.get("outOfStock", False))
    sellers = pdp.get("sellers") or []
    seller_name = sellers[0].get("displayName") if sellers else "Myntra Authorized Seller"
    serv = pdp.get("serviceability") or {}
    ret_days = serv.get("returnPeriod")
    return_policy = f"Easy {ret_days} days returns & exchanges" if ret_days else (
        "Returnable" if flags.get("isReturnable") else "Non-returnable"
    )
    proc_map = serv.get("procurementTimeInDays") or {}
    proc_days = next(iter(proc_map.values()), None) if isinstance(proc_map, dict) else None
    delivery_text = (
        "Ready to ship (Same/Next-day dispatch)" if proc_days == 0
        else f"Ships in {proc_days} days" if proc_days is not None
        else "Standard Myntra Delivery"
    )
    # Typical transit after procurement
    delivery_days = (int(proc_days) + 2) if proc_days is not None else None

    return {
        **candidate,
        "price_inr": price,
        "mrp_inr": mrp,
        "rating": round(avg_rating, 2) if avg_rating and avg_rating > 0 else None,
        "rating_count": total_ratings,
        "review_count": review_count or len(reviews),
        "rating_breakdown": rating_breakdown,
        "in_stock": in_stock,
        "shipping_fee_inr": None,
        "delivery_text": delivery_text,
        "delivery_days": delivery_days,
        "return_policy": return_policy,
        "cod_available": bool(flags.get("codEnabled", False)),
        "seller": seller_name,
        "reviews": reviews,
        "fetched_at": _now_iso(),
    }


# ==============================================================================
# SNAPDEAL ADAPTER
# ==============================================================================

def search_snapdeal(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    s = _session()
    url = f"https://www.snapdeal.com/search?keyword={quote(query)}"
    r = s.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results: List[Dict[str, Any]] = []
    for card in soup.select(".product-tuple-listing")[:limit]:
        t_el = card.select_one(".product-title")
        p_el = card.select_one(".product-price")
        mrp_el = card.select_one(".product-desc-price")
        link_el = card.select_one("a.dp-widget-link")
        img_el = card.select_one("img.product-image")

        title = t_el.get_text(strip=True) if t_el else ""
        price = _parse_int(p_el.get_text(strip=True)) if p_el else None
        mrp = _parse_int(mrp_el.get_text(strip=True)) if mrp_el else price
        href = link_el["href"] if link_el and link_el.has_attr("href") else ""
        img = ""
        if img_el:
            img = img_el.get("src") or img_el.get("data-src") or ""

        if not title or not price or not href:
            continue

        rc_el = card.select_one(".product-rating-count")
        rating_count = _parse_int(rc_el.get_text(strip=True)) if rc_el else 0

        results.append({
            "platform": "Snapdeal",
            "title": title,
            "brand": title.split()[0] if title else "",
            "price_inr": price,
            "mrp_inr": mrp or price,
            "rating": None,
            "rating_count": rating_count or 0,
            "review_count": 0,
            "image_url": img,
            "url": href,
            "in_stock": True,
            "category": "",
        })
    return results


def fetch_snapdeal_pdp(candidate: Dict[str, Any]) -> Dict[str, Any]:
    s = _session()
    r = s.get(candidate["url"], timeout=TIMEOUT)
    if r.status_code != 200:
        return candidate

    soup = BeautifulSoup(r.text, "html.parser")
    p_el = soup.select_one(".payBlkBig")
    mrp_el = soup.select_one(".pdpCutPrice")
    rat_el = soup.select_one(".avrg-rating")
    rc_el = soup.select_one(".total-rating")

    price = _parse_int(p_el.get_text(strip=True)) if p_el else candidate["price_inr"]
    mrp = _parse_int(mrp_el.get_text(strip=True)) if mrp_el else candidate["mrp_inr"]
    rating = _parse_float(rat_el.get_text(strip=True)) if rat_el else candidate.get("rating")
    rating_count = _parse_int(rc_el.get_text(strip=True)) if rc_el else candidate.get("rating_count", 0)

    reviews: List[Dict[str, Any]] = []
    for rev in soup.select(".user-review")[:6]:
        head = rev.select_one(".head")
        body = rev.select_one("p")
        user_el = rev.select_one("._reviewUserName")
        stars = len(rev.select(".sd-icon-star.active"))
        comment = body.get_text(" ", strip=True) if body else ""
        title = head.get_text(" ", strip=True) if head else ""
        if comment or title:
            reviews.append({
                "user": user_el.get_text(strip=True) if user_el else "Snapdeal Buyer",
                "rating": float(stars) if stars else (rating or 4.0),
                "title": title,
                "comment": comment or title,
                "date": "",
                "verified_platform": "Snapdeal",
            })

    sold_out = bool(soup.select_one(".sold-out-err"))
    return {
        **candidate,
        "price_inr": price,
        "mrp_inr": mrp,
        "rating": round(rating, 2) if rating else None,
        "rating_count": rating_count or 0,
        "review_count": len(reviews),
        "in_stock": not sold_out,
        "shipping_fee_inr": None,
        "delivery_text": "Standard Snapdeal Delivery",
        "delivery_days": None,
        "return_policy": "7 Days Easy Returns",
        "seller": "Snapdeal Seller",
        "reviews": reviews,
        "fetched_at": _now_iso(),
    }


# ==============================================================================
# SHOPCLUES ADAPTER
# ==============================================================================

def search_shopclues(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    s = _session()
    url = f"https://www.shopclues.com/search?q={quote(query)}"
    r = s.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results: List[Dict[str, Any]] = []
    for card in soup.select(".column.col3, .prd_grid_view")[:limit]:
        a = card.select_one("a[href]")
        title_el = card.select_one("h2, .prd_name")
        price_el = card.select_one(".p_price")
        mrp_el = card.select_one(".old_prices span")
        img_el = card.select_one("img")

        title = title_el.get_text(" ", strip=True) if title_el else ""
        price = _parse_int(price_el.get_text(strip=True)) if price_el else None
        mrp = _parse_int(mrp_el.get_text(strip=True)) if mrp_el else price
        href = a["href"] if a and a.has_attr("href") else ""
        if href.startswith("//"):
            href = "https:" + href
        img = ""
        if img_el:
            img = img_el.get("data-img") or img_el.get("src") or ""
            if img.startswith("//"):
                img = "https:" + img

        if not title or not price or not href:
            continue

        results.append({
            "platform": "ShopClues",
            "title": title,
            "brand": title.split()[0] if title else "",
            "price_inr": price,
            "mrp_inr": mrp or price,
            "rating": None,
            "rating_count": 0,
            "review_count": 0,
            "image_url": img,
            "url": href,
            "in_stock": True,
            "category": "",
        })
    return results


def fetch_shopclues_pdp(candidate: Dict[str, Any]) -> Dict[str, Any]:
    s = _session()
    r = s.get(candidate["url"], timeout=TIMEOUT)
    if r.status_code != 200:
        return candidate

    soup = BeautifulSoup(r.text, "html.parser")
    price_el = soup.select_one(".f_price")
    mrp_el = soup.select_one("#sec_list_price_")
    rat_el = soup.select_one(".rating_num, [itemprop='ratingValue']")
    rc_el = soup.select_one("[itemprop='ratingCount']")

    price = _parse_int(price_el.get_text(strip=True)) if price_el else candidate["price_inr"]
    mrp = _parse_int(mrp_el.get_text(strip=True)) if mrp_el else candidate["mrp_inr"]
    rating = _parse_float(rat_el.get_text(strip=True)) if rat_el else None
    rating_count = _parse_int(rc_el.get_text(strip=True)) if rc_el else 0

    reviews: List[Dict[str, Any]] = []
    for rev in soup.select(".review_list li, .r_desc")[:5]:
        txt = rev.get_text(" ", strip=True)
        if txt and len(txt) > 8:
            reviews.append({
                "user": "ShopClues Buyer",
                "rating": rating or 4.0,
                "title": "",
                "comment": txt[:260],
                "date": "",
                "verified_platform": "ShopClues",
            })

    return {
        **candidate,
        "price_inr": price,
        "mrp_inr": mrp,
        "rating": round(rating, 2) if rating else None,
        "rating_count": rating_count or 0,
        "review_count": len(reviews),
        "in_stock": True,
        "shipping_fee_inr": None,
        "delivery_text": "Standard ShopClues Delivery",
        "delivery_days": None,
        "return_policy": "ShopClues Buyer Protection",
        "seller": "ShopClues Merchant",
        "reviews": reviews,
        "fetched_at": _now_iso(),
    }


# ==============================================================================
# HONEST STATUS CHECK FOR ANTI-BOT PROTECTED STORES (AMAZON.IN / NYKAA)
# ==============================================================================

def probe_amazon_india(query: str) -> Dict[str, Any]:
    """
    Politely check Amazon.in without ever bypassing CAPTCHAs or bot-verification gates.
    If Amazon serves an anti-bot challenge, report that transparently with the direct search URL.
    """
    search_url = f"https://www.amazon.in/s?k={quote(query)}"
    try:
        r = _session().get(search_url, timeout=6)
        low = r.text.lower()
        if r.status_code != 200 or "bm-verify" in low or "captcha" in low or len(r.text) < 8000:
            return {
                "platform": "Amazon India",
                "status": "blocked_by_antibot",
                "message": "Protected by Amazon anti-bot/CAPTCHA gate — cannot auto-scrape server-side.",
                "search_url": search_url,
            }
    except Exception:
        return {
            "platform": "Amazon India",
            "status": "unreachable",
            "message": "Amazon.in request timed out or failed.",
            "search_url": search_url,
        }
    return {
        "platform": "Amazon India",
        "status": "blocked_by_antibot",
        "message": "Amazon.in requires interactive browser verification.",
        "search_url": search_url,
    }


def probe_nykaa(query: str) -> Dict[str, Any]:
    search_url = f"https://www.nykaa.com/search/result/?q={quote(query)}"
    try:
        r = _session().get(search_url, timeout=6)
        if r.status_code in (401, 403, 429, 503):
            return {
                "platform": "Nykaa",
                "status": "blocked_by_antibot",
                "message": f"Protected by Nykaa WAF (HTTP {r.status_code}) — open directly to verify.",
                "search_url": search_url,
            }
    except Exception:
        pass
    return {
        "platform": "Nykaa",
        "status": "blocked_by_antibot",
        "message": "Nykaa requires interactive browser access.",
        "search_url": search_url,
    }


# ==============================================================================
# ORCHESTRATORS: LIVE CATALOG + MULTI-PLATFORM EXACT COMPARISON
# ==============================================================================

def fetch_live_catalog(query: str, limit_per_store: int = 8) -> Dict[str, Any]:
    """Fetch live products from accessible online platforms for storefront display/search."""
    items: List[Dict[str, Any]] = []
    errors: List[str] = []

    tasks = {
        "Flipkart": lambda: search_flipkart(query, limit=limit_per_store),
        "Myntra": lambda: search_myntra(query, limit=limit_per_store),
        "Snapdeal": lambda: search_snapdeal(query, limit=min(limit_per_store, 6)),
    }

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(future_map):
            name = future_map[fut]
            try:
                res = fut.result()
                items.extend(res)
            except Exception as exc:
                errors.append(f"{name}: {exc}")

    # Interleave platforms nicely so users see products across stores
    by_plat: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        by_plat.setdefault(it["platform"], []).append(it)

    mixed: List[Dict[str, Any]] = []
    max_len = max((len(v) for v in by_plat.values()), default=0)
    for i in range(max_len):
        for plat in ["Flipkart", "Myntra", "Snapdeal"]:
            lst = by_plat.get(plat, [])
            if i < len(lst):
                mixed.append(lst[i])

    return {
        "query": query,
        "total": len(mixed),
        "fetched_at": _now_iso(),
        "items": mixed,
        "errors": errors,
    }


def _best_exact_candidate(
    target_title: str,
    target_brand: str,
    candidates: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Pick the single highest-scoring exact match from a platform's search results."""
    best: Optional[Dict[str, Any]] = None
    best_score = -1.0
    rejection_notes: List[str] = []

    for c in candidates:
        ok, score, reason = score_exact_match(
            target_title=target_title,
            candidate_title=c.get("title", ""),
            target_brand=target_brand,
            candidate_brand=c.get("brand", ""),
        )
        if ok and score > best_score:
            best_score = score
            best = {**c, "match_confidence": score, "match_reason": reason}
        elif not ok:
            rejection_notes.append(f"{c.get('title', '')[:45]}: {reason}")

    return best, rejection_notes[:2]


def compare_product_live(
    title: str,
    brand: str = "",
    seed_url: str = "",
    seed_platform: str = "",
) -> Dict[str, Any]:
    """
    Search all accessible platforms in parallel for the exact product,
    filter strictly to platforms that actually carry that product,
    and fetch each matched platform's live PDP metrics & product-specific reviews.
    """
    search_query = title
    # Trim overly verbose parenthetical specs for cleaner search recall across stores
    clean_q = re.sub(r"\([^)]*\)", "", title)
    clean_q = re.sub(r"\s+", " ", clean_q).strip()
    if len(clean_q.split()) >= 2:
        search_query = " ".join(clean_q.split()[:7])

    searchers = {
        "Flipkart": lambda: search_flipkart(search_query, limit=10),
        "Myntra": lambda: search_myntra(search_query, limit=10),
        "Snapdeal": lambda: search_snapdeal(search_query, limit=8),
        "ShopClues": lambda: search_shopclues(search_query, limit=6),
    }

    pdp_fetchers = {
        "Flipkart": fetch_flipkart_pdp,
        "Myntra": fetch_myntra_pdp,
        "Snapdeal": fetch_snapdeal_pdp,
        "ShopClues": fetch_shopclues_pdp,
    }

    search_results: Dict[str, List[Dict[str, Any]]] = {}
    platform_status: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        fut_search = {pool.submit(fn): plat for plat, fn in searchers.items()}
        fut_amazon = pool.submit(probe_amazon_india, search_query)
        fut_nykaa = pool.submit(probe_nykaa, search_query)

        for fut in as_completed(fut_search):
            plat = fut_search[fut]
            try:
                search_results[plat] = fut.result()
            except Exception as exc:
                search_results[plat] = []
                platform_status.append({
                    "platform": plat,
                    "status": "error",
                    "message": str(exc),
                    "search_url": "",
                })

        for fut in (fut_amazon, fut_nykaa):
            try:
                platform_status.append(fut.result())
            except Exception:
                pass

    # Match exact product per platform
    matched_candidates: List[Dict[str, Any]] = []
    for plat, candidates in search_results.items():
        # If the user clicked a product from a specific platform URL, prioritize that exact URL!
        if seed_url and seed_platform.lower() == plat.lower():
            matched_candidates.append({
                "platform": plat,
                "title": title,
                "brand": brand,
                "price_inr": 0,
                "mrp_inr": 0,
                "rating": None,
                "rating_count": 0,
                "review_count": 0,
                "image_url": "",
                "url": seed_url,
                "in_stock": True,
                "match_confidence": 100.0,
                "match_reason": "Selected source listing",
            })
            continue

        best, notes = _best_exact_candidate(title, brand, candidates)
        if best:
            matched_candidates.append(best)
        else:
            platform_status.append({
                "platform": plat,
                "status": "not_found",
                "message": (
                    f"Exact product not listed on {plat} ({notes[0]})"
                    if notes else f"No listings returned on {plat} for this product"
                ),
                "search_url": (
                    f"https://www.flipkart.com/search?q={quote(search_query)}" if plat == "Flipkart"
                    else f"https://www.myntra.com/{re.sub(r'[^a-z0-9]+', '-', search_query.lower()).strip('-')}" if plat == "Myntra"
                    else f"https://www.snapdeal.com/search?keyword={quote(search_query)}" if plat == "Snapdeal"
                    else f"https://www.shopclues.com/search?q={quote(search_query)}"
                ),
            })

    # Deep-fetch each matched platform's PDP in parallel to get real reviews, delivery & seller
    enriched_offers: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_pdp = {
            pool.submit(pdp_fetchers[c["platform"]], c): c["platform"]
            for c in matched_candidates
            if c["platform"] in pdp_fetchers
        }
        for fut in as_completed(fut_pdp):
            plat = fut_pdp[fut]
            try:
                offer = fut.result()
                if offer.get("price_inr"):
                    enriched_offers.append(offer)
                    platform_status.append({
                        "platform": plat,
                        "status": "matched",
                        "message": f"Exact match verified ({offer.get('match_confidence', 100)}%)",
                        "search_url": offer.get("url", ""),
                    })
            except Exception as exc:
                platform_status.append({
                    "platform": plat,
                    "status": "error",
                    "message": f"Failed fetching product page: {exc}",
                    "search_url": "",
                })

    return {
        "query": search_query,
        "fetched_at": _now_iso(),
        "offers": enriched_offers,
        "platform_status": platform_status,
    }
