"""7-Eleven Japan 新商品（https://www.sej.co.jp/products/a/thisweek/）

已驗證（2026-10-28）：
- 每週二更新；「今週」與「来週」兩頁，依 11 個地區分頁
- 地區頁支援每頁 100 筆：/products/a/{week}/area/{area}/{page}/l100/
- 商品連結 /products/a/item/{6碼ID}/{area}/，ID 跨地區相同，可作為商品鍵
- 文字含「税込xxx円」「YYYY年MM月DD日」「販売地域：…」
"""
from __future__ import annotations

import re

from ..common import fetch, is_regional_jp, normalize_space, parse_jp_date, parse_price
from ._html import item_blocks

BASE = "https://www.sej.co.jp/products/a"
AREAS = ["hokkaido", "tohoku", "kanto", "koshinetsu", "hokuriku", "tokai",
         "kinki", "chugoku", "shikoku", "kyushu", "okinawa"]
ITEM_RE = re.compile(r"/products/a/item/(\d+)/")
TOTAL_RE = re.compile(r"(\d+)件中")


def parse_list(html: str, list_page: str) -> list[dict]:
    rows = []
    for a, text in item_blocks(html, ITEM_RE):
        item_id = ITEM_RE.search(a["href"]).group(1)
        region_m = re.search(r"販売地域[：:]\s*([^\n]+)", text)
        regions = normalize_space(region_m.group(1)) if region_m else ""
        url = a["href"] if a["href"].startswith("http") else "https://www.sej.co.jp" + a["href"]
        rows.append({
            "country": "JP",
            "chain": "seven_jp",
            "item_id": item_id,
            "name_original": normalize_space(a.get_text()),
            "price_tax_incl": parse_price(text),
            "currency": "JPY",
            "release_date": parse_jp_date(text) or "",
            "regions": regions,
            "source_category": "",
            "source_url": url,
            "list_page": list_page,
        })
    return rows


def crawl(weeks=("thisweek", "nextweek"), areas=AREAS, max_pages: int = 5) -> list[dict]:
    seen: dict[str, dict] = {}
    for week in weeks:
        for area in areas:
            for page in range(1, max_pages + 1):
                url = f"{BASE}/{week}/area/{area}/{page}/l100/"
                resp = fetch(url)
                if resp.status_code != 200:
                    break
                rows = parse_list(resp.text, week)
                for r in rows:
                    # 同一 ID 在多個地區頁重複出現，只留一筆
                    seen.setdefault(f"{week}:{r['item_id']}", r)
                total_m = TOTAL_RE.search(resp.text)
                total = int(total_m.group(1)) if total_m else 0
                if page * 100 >= total or not rows:
                    break
    return list(seen.values())
