"""Unit and HTTP integration tests for PricePulse Live Comparator."""

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from analyzer import analyze_offer_reviews, run_price_analysis
from chatbot import handle_customer_chat
from matcher import score_exact_match
from server import ComparatorHandler, init_db


class TestMatcher(unittest.TestCase):
    def test_rejects_accessory_for_phone(self):
        ok, _, reason = score_exact_match(
            "Apple iPhone 15 (Blue, 128 GB)",
            "Spigen Back Cover Case for Apple iPhone 15 128 GB",
            target_brand="Apple",
            candidate_brand="Spigen",
        )
        self.assertFalse(ok, reason)

    def test_rejects_pack_size_mismatch(self):
        ok, _, reason = score_exact_match(
            "Maybelline New York Lash Sensational Sky High Mascara 6 ml",
            "Maybelline New York Pack of 2 Lash Sensational Sky High Mascara 6 ml",
            target_brand="Maybelline",
            candidate_brand="Maybelline",
        )
        self.assertFalse(ok, reason)

    def test_rejects_wrong_brand_on_snapdeal(self):
        ok, _, reason = score_exact_match(
            "Puma Smashic Unisex Sneakers",
            "Columbus SMASH Blue Men's Sneakers",
            target_brand="Puma",
            candidate_brand="Columbus",
        )
        self.assertFalse(ok, reason)

    def test_accepts_same_product_across_platforms(self):
        ok, score, reason = score_exact_match(
            "PUMA Smashic Sneakers For Men",
            "Puma Smashic Comfort Casual Sneakers",
            target_brand="PUMA",
            candidate_brand="Puma",
        )
        self.assertTrue(ok, reason)
        self.assertGreaterEqual(score, 60.0)


class TestAnalyzer(unittest.TestCase):
    def test_price_analysis_and_recommendation(self):
        offers = [
            {
                "platform": "Flipkart",
                "title": "boAt Airdopes 141",
                "price_inr": 1299,
                "mrp_inr": 4490,
                "rating": 4.2,
                "rating_count": 125000,
                "delivery_days": 2,
                "delivery_text": "Delivery by Sun, 15 Nov",
                "shipping_fee_inr": 0,
                "return_policy": "7 Days Replacement",
                "in_stock": True,
                "reviews": [
                    {"user": "A", "rating": 5, "comment": "Great bass and battery life"},
                ],
            },
            {
                "platform": "Myntra",
                "title": "boAt Airdopes 141",
                "price_inr": 1099,
                "mrp_inr": 4490,
                "rating": 4.4,
                "rating_count": 1800,
                "delivery_days": 2,
                "delivery_text": "Ready to ship",
                "shipping_fee_inr": 0,
                "return_policy": "14 days returns",
                "in_stock": True,
                "reviews": [
                    {"user": "B", "rating": 5, "comment": "Original product and fast delivery"},
                ],
            },
        ]
        res = run_price_analysis(offers, priority="balanced")
        self.assertEqual(res["offer_count"], 2)
        self.assertEqual(res["price_summary"]["lowest_price_inr"], 1099)
        self.assertEqual(res["price_summary"]["highest_price_inr"], 1299)
        self.assertEqual(res["price_summary"]["price_spread_inr"], 200)
        self.assertEqual(res["picks"]["lowest_price"]["platform"], "Myntra")
        self.assertEqual(res["picks"]["best_overall"]["platform"], "Myntra")

    def test_review_sentiment_counts(self):
        summary = analyze_offer_reviews([
            {"rating": 5, "title": "Great", "comment": "Awesome quality and comfortable fit"},
            {"rating": 2, "title": "Small", "comment": "Slightly tight sizing"},
        ])
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["positive_count"], 1)
        self.assertEqual(summary["negative_count"], 1)
        self.assertIn("quality", summary["pros_mentioned"])
        self.assertIn("tight", summary["cons_mentioned"])


class TestServerRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ComparatorHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_health_and_categories_and_static(self):
        base = f"http://127.0.0.1:{self.port}"
        with urllib.request.urlopen(f"{base}/api/health") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(json.loads(r.read())["status"], "ok")

        with urllib.request.urlopen(f"{base}/api/categories") as r:
            cats = json.loads(r.read())
            self.assertIn("Smartphones", cats)

        with urllib.request.urlopen(f"{base}/") as r:
            html = r.read().decode("utf-8")
            self.assertIn("PricePulse", html)

        req = urllib.request.Request(
            f"{base}/api/reanalyze",
            data=json.dumps({
                "priority": "price",
                "offers": [
                    {"platform": "A", "title": "Item", "price_inr": 500, "mrp_inr": 800, "in_stock": True},
                    {"platform": "B", "title": "Item", "price_inr": 700, "mrp_inr": 800, "in_stock": True},
                ],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
            self.assertEqual(data["analysis"]["picks"]["best_overall"]["platform"], "A")

        chat_req = urllib.request.Request(
            f"{base}/api/chat",
            data=json.dumps({
                "message": "Why is Amazon not showing?",
                "history": [],
                "context": {},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(chat_req) as r:
            cdata = json.loads(r.read())
            self.assertIn("anti-bot", cdata["reply"].lower())


class TestChatbot(unittest.TestCase):
    def test_context_aware_review_and_recommendation(self):
        sample_cmp = {
            "product": {"title": "boAt Airdopes 141", "brand": "boAt"},
            "analysis": {
                "price_summary": {
                    "lowest_price_inr": 1099,
                    "highest_price_inr": 1299,
                    "price_spread_inr": 200,
                    "price_spread_pct": 15.4,
                },
                "picks": {"best_overall": {"platform": "Myntra", "price_inr": 1099, "score": 92.5}},
                "offers": [
                    {
                        "platform": "Myntra",
                        "price_inr": 1099,
                        "rating": 4.4,
                        "rating_count": 1800,
                        "delivery_text": "Ready to ship",
                        "return_policy": "14 days returns",
                        "seller": "Appario",
                        "scores": {"overall": 92.5},
                        "is_recommended": True,
                        "review_analysis": {"sample_count": 1, "positive_count": 1, "neutral_count": 0, "negative_count": 0, "pros_mentioned": ["sound"], "cons_mentioned": []},
                        "reviews": [{"user": "Riya", "rating": 5, "comment": "Great sound quality"}],
                    }
                ],
            },
        }
        rec = handle_customer_chat("Which platform should I buy from?", [], {"compareData": sample_cmp})
        self.assertIn("Myntra", rec["reply"])

        rev = handle_customer_chat("What do reviews say?", [], {"compareData": sample_cmp})
        self.assertIn("Riya", rev["reply"])
        self.assertIn("Great sound quality", rev["reply"])

    def test_live_lookup_callback_and_followup(self):
        fake_cmp = {
            "product": {"title": "Puma Smashic Sneakers", "brand": "Puma"},
            "analysis": {
                "price_summary": {"lowest_price_inr": 1799, "highest_price_inr": 2249, "price_spread_inr": 450, "price_spread_pct": 20.0},
                "picks": {"best_overall": {"platform": "Flipkart", "price_inr": 1799, "score": 94.0}},
                "offers": [
                    {
                        "platform": "Flipkart",
                        "price_inr": 1799,
                        "rating": 4.2,
                        "rating_count": 47244,
                        "delivery_text": "Delivery by Sun",
                        "return_policy": "7 days return",
                        "seller": "PumaSports",
                        "scores": {"overall": 94.0},
                        "is_recommended": True,
                        "review_analysis": {"sample_count": 1, "positive_count": 1, "neutral_count": 0, "negative_count": 0},
                        "reviews": [{"user": "Atharv", "rating": 4, "comment": "Very comfortable"}],
                    }
                ],
            },
        }
        r1 = handle_customer_chat(
            "Compare Puma Smashic Sneakers",
            [],
            {},
            live_lookup_fn=lambda q: {"catalog_items": [], "compareData": fake_cmp},
        )
        self.assertIn("Flipkart", r1["reply"])
        r2 = handle_customer_chat("What about delivery?", [{"role": "assistant", "compareData": r1["compareData"]}], {})
        self.assertIn("Delivery by Sun", r2["reply"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

