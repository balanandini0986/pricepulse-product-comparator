"""HTTP server and SQLite live-cache for the Multi-Platform Product Comparator."""

from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from analyzer import run_price_analysis
from chatbot import handle_customer_chat
from scrapers import compare_product_live, fetch_live_catalog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(BASE_DIR, "live_cache.db")
CACHE_TTL_SECONDS = 900  # 15 minutes
_default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
HOST = os.environ.get("HOST", _default_host)
PORT = int(os.environ.get("PORT", "8085"))

CATEGORY_QUERIES: Dict[str, List[str]] = {
    "Featured Cross-Store": [
        "Maybelline Lash Sensational Mascara",
        "Puma Smashic Sneakers",
        "boAt Airdopes 141",
        "Apple iPhone 15 128GB",
    ],
    "Smartphones": ["Apple iPhone 15", "Samsung Galaxy S24"],
    "Laptops": ["Apple MacBook Pro M3", "HP Pavilion 15 Laptop"],
    "Sneakers & Shoes": ["Puma Smashic Sneakers", "Nike Running Shoes"],
    "Beauty & Makeup": ["Maybelline Lash Sensational Mascara", "Lakme 9 to 5 Primer Matte"],
    "Audio & Earbuds": ["boAt Airdopes 141", "JBL Tune 510BT"],
    "Watches": ["Casio Vintage Watch", "Fastrack Analog Watch"],
    "Backpacks": ["Skybags Backpack", "American Tourister Backpack"],
}


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def cache_get(key: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT payload_json, created_at FROM api_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    payload_json, created_at = row
    if (time.time() - created_at) > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(payload_json)
    except json.JSONDecodeError:
        return None


def cache_set(key: str, data: Dict[str, Any]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO api_cache (cache_key, payload_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            created_at = excluded.created_at
        """,
        (key, json.dumps(data), time.time()),
    )
    conn.commit()
    conn.close()


def get_catalog_data(category: str, query: str, refresh: bool) -> Dict[str, Any]:
    clean_q = query.strip()
    cache_key = f"catalog::{category}::{clean_q.lower()}"
    if not refresh:
        cached = cache_get(cache_key)
        if cached:
            return {**cached, "cached": True}

    if clean_q:
        res = fetch_live_catalog(clean_q, limit_per_store=8)
    else:
        queries = CATEGORY_QUERIES.get(category, CATEGORY_QUERIES["Featured Cross-Store"])
        all_items: List[Dict[str, Any]] = []
        errors: List[str] = []
        fetched_at = ""
        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            futures = [pool.submit(fetch_live_catalog, q, 4) for q in queries]
            for fut in futures:
                part = fut.result()
                all_items.extend(part.get("items", []))
                errors.extend(part.get("errors", []))
                fetched_at = part.get("fetched_at") or fetched_at

        # Deduplicate by URL
        seen_urls = set()
        unique_items = []
        for it in all_items:
            u = it.get("url", "")
            if u and u not in seen_urls:
                seen_urls.add(u)
                unique_items.append(it)
        res = {
            "query": category,
            "total": len(unique_items),
            "fetched_at": fetched_at,
            "items": unique_items,
            "errors": errors,
        }

    if res.get("items"):
        cache_set(cache_key, res)
    return {**res, "cached": False}


def get_compare_data(
    title: str,
    brand: str,
    seed_url: str,
    seed_platform: str,
    priority: str,
    refresh: bool,
) -> Dict[str, Any]:
    cache_key = f"compare::{title.lower()}::{brand.lower()}::{seed_url}"
    raw_live = None if refresh else cache_get(cache_key)
    cached = raw_live is not None

    if raw_live is None:
        raw_live = compare_product_live(
            title=title,
            brand=brand,
            seed_url=seed_url,
            seed_platform=seed_platform,
        )
        if raw_live.get("offers"):
            cache_set(cache_key, raw_live)

    analysis = run_price_analysis(
        [dict(o) for o in raw_live.get("offers", [])],
        priority=priority,
    )
    return {
        "product": {
            "title": title,
            "brand": brand,
            "image_url": next(
                (o.get("image_url") for o in analysis.get("offers", []) if o.get("image_url")),
                "",
            ),
        },
        "query": raw_live.get("query", title),
        "fetched_at": raw_live.get("fetched_at", ""),
        "cached": cached,
        "platform_status": raw_live.get("platform_status", []),
        "analysis": analysis,
    }


class ComparatorHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Concise server logging
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filepath: str) -> None:
        if not os.path.isfile(filepath):
            self._send_json(404, {"error": "File not found"})
            return
        mime, _ = mimetypes.guess_type(filepath)
        with open(filepath, "rb") as fh:
            content = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._send_file(os.path.join(STATIC_DIR, "index.html"))
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):].lstrip("/")
            safe_path = os.path.normpath(os.path.join(STATIC_DIR, rel))
            if not safe_path.startswith(STATIC_DIR):
                self._send_json(403, {"error": "Forbidden"})
                return
            self._send_file(safe_path)
            return

        if path == "/api/health":
            self._send_json(200, {"status": "ok", "mode": "live-no-api-keys"})
            return

        if path == "/api/categories":
            self._send_json(200, list(CATEGORY_QUERIES.keys()))
            return

        if path == "/api/catalog":
            category = qs.get("category", ["Featured Cross-Store"])[0]
            query = qs.get("q", [""])[0]
            refresh = qs.get("refresh", ["0"])[0] == "1"
            try:
                data = get_catalog_data(category, query, refresh)
                self._send_json(200, data)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/compare":
            title = qs.get("title", [""])[0].strip()
            if not title:
                self._send_json(400, {"error": "Missing 'title' parameter"})
                return
            brand = qs.get("brand", [""])[0].strip()
            seed_url = qs.get("url", [""])[0].strip()
            seed_platform = qs.get("platform", [""])[0].strip()
            priority = qs.get("priority", ["balanced"])[0].strip()
            refresh = qs.get("refresh", ["0"])[0] == "1"
            try:
                data = get_compare_data(title, brand, seed_url, seed_platform, priority, refresh)
                self._send_json(200, data)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/chat":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
                message = body.get("message", "")
                history = body.get("history") or []
                context = body.get("context") or {}
                priority = (context.get("priority") or "balanced").strip()

                def _live_lookup(query: str) -> Dict[str, Any]:
                    cat = get_catalog_data("Featured Cross-Store", query, refresh=False)
                    items = cat.get("items") or []
                    cmp_data = None
                    if items:
                        first = items[0]
                        cmp_data = get_compare_data(
                            title=first.get("title", query),
                            brand=first.get("brand", ""),
                            seed_url=first.get("url", ""),
                            seed_platform=first.get("platform", ""),
                            priority=priority,
                            refresh=False,
                        )
                    return {"catalog_items": items, "compareData": cmp_data}

                res = handle_customer_chat(
                    message=message,
                    history=history,
                    context=context,
                    live_lookup_fn=_live_lookup,
                )
                self._send_json(200, res)
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/reanalyze":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
                offers = body.get("offers") or []
                priority = body.get("priority", "balanced")
                analysis = run_price_analysis([dict(o) for o in offers], priority=priority)
                self._send_json(200, {"analysis": analysis})
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/chat":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
                message = body.get("message", "")
                history = body.get("history") or []
                context = body.get("context") or {}
                priority = (context.get("priority") or "balanced").strip()

                def _live_lookup(query: str) -> Dict[str, Any]:
                    cat = get_catalog_data("Featured Cross-Store", query, refresh=False)
                    items = cat.get("items") or []
                    cmp_data = None
                    if items:
                        first = items[0]
                        cmp_data = get_compare_data(
                            title=first.get("title", query),
                            brand=first.get("brand", ""),
                            seed_url=first.get("url", ""),
                            seed_platform=first.get("platform", ""),
                            priority=priority,
                            refresh=False,
                        )
                    return {"catalog_items": items, "compareData": cmp_data}

                res = handle_customer_chat(
                    message=message,
                    history=history,
                    context=context,
                    live_lookup_fn=_live_lookup,
                )
                self._send_json(200, res)
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})
            return

        self._send_json(404, {"error": "Not found"})


def run_server() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), ComparatorHandler)
    print(f"PricePulse Live Comparator running at http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()

