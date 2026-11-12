# PricePulse — Live Multi-Platform Product Comparator & AI Support

**Live Working URL:** https://ba2aeb1568cd57.lhr.life  
**Local Server URL:** http://127.0.0.1:8085  

> **Note on the Live URL:** `https://ba2aeb1568cd57.lhr.life` is an active public HTTPS tunnel to the running application on your machine (keep the server and SSH tunnel running for live access). For permanent 24/7 hosting, deploy this folder directly to Render (`render.yaml`) or Railway (`Procfile` + `requirements.txt`).

---

## Key Features
1. **100% Original Live Platform Metrics (No API Keys, No Hardcoded Prices)**
   - Fetches live listings, ₹ prices, MRPs, discounts, ratings, rating counts, delivery estimates, seller info, return policies, and verified buyer reviews directly from **Flipkart**, **Myntra**, **Snapdeal**, and **ShopClues**.
   - Transparently probes **Amazon India** and **Nykaa** without bypassing CAPTCHAs/anti-bot gates, providing direct storefront links and an optional browser-verified quote input to include them in the analysis.
2. **Strict Exact-Product Matching (`matcher.py`)**
   - Verifies brand, storage/volume specs, model codes, and pack sizes while rejecting accessories (e.g. phone covers) and lookalike brands so **only platforms carrying that exact product** and **only that product's reviews** are displayed.
3. **Price & Value Analysis Engine (`analyzer.py`)**
   - Computes price spread, savings, Bayesian-adjusted rating confidence, delivery/shipping score, return policy score, and a **0–100 Composite Recommendation Score** across 4 user priorities (*Balanced*, *Lowest Price*, *Best Ratings*, *Fastest Delivery*).
4. **Product-Specific Review Sentiment & AI Support Chatbot (`chatbot.py`)**
   - Summarizes platform-specific verified buyer reviews (pros/cons themes & sentiment counts).
   - Floating **PricePulse AI Support** widget answers real-time customer questions about the active comparison, performs live product lookups inside chat, and troubleshoots store questions.

---

## Quick Start (Local)
```powershell
pip install -r requirements.txt
python server.py
```
Open `http://127.0.0.1:8085` in your browser.

## Run Automated Tests
```powershell
python -m unittest discover -s . -p "test_*.py" -v
```

