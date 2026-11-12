# PricePulse — Project Documentation
### Real-Time Multi-Platform Product Comparator, Price Analysis Engine & AI Customer Support

**Live Working URL:** https://ba2aeb1568cd57.lhr.life  
**Tech Stack:** Python 3.12 (`ThreadingHTTPServer`), SQLite 3, `requests`, `BeautifulSoup4`, HTML5 / CSS3 / Vanilla JS  
**API Keys Required:** None (100% self-contained & API-key-free)

---

## 1. Problem Statement
Online shoppers frequently switch across multiple Indian e-commerce platforms (Flipkart, Myntra, Snapdeal, ShopClues, Amazon, Nykaa) to check whether a product is cheaper, better rated, or delivered faster elsewhere. Existing comparison tools often:
- Show **stale or hardcoded dummy prices** instead of live store metrics.
- Mix **unrelated products, accessories, or different pack sizes** (causing irrelevant reviews to appear).
- Lack a unified **multi-factor buying verdict** combining price, rating volume, delivery speed, and return policy.

**PricePulse** solves all three problems by fetching original live metrics in real time, filtering strictly for the exact product on each platform, displaying only that product's verified reviews per store, running a transparent Price & Value Analysis, and providing an interactive AI Customer Support chatbot.

---

## 2. System Architecture

| Module | File | Responsibility |
| :--- | :--- | :--- |
| **HTTP Server & TTL Cache** | `server.py` | Serves UI & REST endpoints (`/api/catalog`, `/api/compare`, `/api/reanalyze`, `/api/chat`, `/api/health`) and manages the 15-minute SQLite live cache (`live_cache.db`). |
| **Live Store Adapters** | `scrapers.py` | Parallel live fetchers for Flipkart (`__INITIAL_STATE__` + JSON-LD), Myntra (`__myx.pdpData`), Snapdeal, and ShopClues, plus honest anti-bot probing for Amazon India & Nykaa. |
| **Exact-Product Matcher** | `matcher.py` | Tokenizes titles, verifies brand, storage/volume specs, model numbers, and pack counts, and rejects accessories (`score_exact_match`). |
| **Price & Value Analyzer** | `analyzer.py` | Computes price spread, Bayesian rating scores, delivery & return policy scores, composite recommendations (`run_price_analysis`), and review theme extraction (`analyze_offer_reviews`). |
| **AI Support Chatbot** | `chatbot.py` | Context-aware conversational assistant (`handle_customer_chat`) that answers questions about active comparisons, triggers live product searches in chat, and handles customer troubleshooting. |
| **Frontend & Chat UI** | `static/index.html`, `static/style.css`, `static/app.js`, `static/chat.js` | Responsive product grid, comparison & score visualization dashboard, platform-filtered review viewer, and floating AI Support widget. |

---

## 3. Database Architecture (`live_cache.db`)
- **Location:** `product-comparator/live_cache.db` (auto-created on server startup via `init_db()` in `server.py`).
- **Table `api_cache`:**
  - `cache_key` (`TEXT PRIMARY KEY`)
  - `payload_json` (`TEXT NOT NULL`)
  - `created_at` (`REAL NOT NULL`)
- **Design Rationale:** No static or synthetic prices are stored in the database. `live_cache.db` acts strictly as a 15-minute TTL cache (`CACHE_TTL_SECONDS = 900`) for live-scraped responses, with a **↻ Refresh Live** button to bypass cache on demand.

---

## 4. AI & Intelligent Decision Logic

1. **NLP Exact-Product Entity Matching (`matcher.py`):**
   - Rejects accessory listings (e.g., phone covers/cases when comparing a smartphone).
   - Rejects pack-quantity mismatches (`Pack of 2` vs single unit) and storage/model mismatches (`128GB` vs `256GB`, wrong brands).
   - Computes weighted token recall and F1 overlap to ensure only stores carrying the exact item are included.

2. **Multi-Criteria Decision Analysis (`analyzer.py`):**
   - **Composite Score (0–100)** with selectable priority modes (`balanced`, `price`, `rating`, `delivery`):
     - **Price Value (45% in Balanced):** `100 × (lowest_effective_price / offer_effective_price)`
     - **Bayesian Rating Confidence (35%):** Combines star rating with rating count (`prior_m = 25, prior_c = 3.9` + log volume bonus) so high-confidence listings are rewarded fairly.
     - **Delivery & Shipping (12%):** Rewards free shipping and faster dispatch/delivery days.
     - **Return Policy & Stock (8%):** Scores return/exchange coverage, stock availability, and COD support.

3. **Review Sentiment & Aspect Extraction (`analyzer.py`):**
   - Classifies sampled verified buyer reviews per platform into positive ($\ge 4\star$), neutral, and negative ($< 2.5\star$) counts and extracts pros/cons themes directly from live review text.

4. **Context-Aware Conversational Chatbot (`chatbot.py`):**
   - Reads the active comparison context (`compareData`) and conversation history (`history`) to answer follow-up questions on price savings, buyer feedback, delivery dates, sellers, and return policies.
   - Detects live product lookup requests (`"Compare Puma Smashic Sneakers"`) and runs real-time multi-platform comparisons inside chat.
   - Handles anti-bot store explanations (Amazon India / Nykaa) and directs users to the optional browser-verified quote input.

---

## 5. REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Health check (`{"status": "ok", "mode": "live-no-api-keys"}`) |
| `GET` | `/api/categories` | Returns supported product categories |
| `GET` | `/api/catalog?category=...&q=...&refresh=0\|1` | Fetches live product listings across stores |
| `GET` | `/api/compare?title=...&brand=...&url=...&platform=...&priority=...&refresh=0\|1` | Runs exact-product matching, live PDP review extraction, and price analysis |
| `POST` | `/api/reanalyze` | Recalculates price & value analysis with updated priority or user-verified store quote |
| `POST` | `/api/chat` | Handles real-time customer support queries and live chat product comparisons |

---

## 6. Verification & Automated Testing
All 9 automated unit and HTTP integration tests in `test_comparator.py` pass:
- `TestMatcher`: Rejects accessories, pack mismatches, and wrong brands; accepts true cross-platform matches.
- `TestAnalyzer`: Verifies price spread, savings, Bayesian scoring, recommendation winner, and review sentiment counts.
- `TestChatbot`: Verifies context-aware review/recommendation Q&A, live lookup callbacks, and multi-turn follow-ups.
- `TestServerRoutes`: Verifies `/api/health`, `/api/categories`, `/`, `/api/reanalyze`, and `/api/chat`.

