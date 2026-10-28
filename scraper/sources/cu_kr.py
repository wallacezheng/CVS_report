"""CU（韓國）全商品列表 — 實驗性，尚未驗證。

已知（2026-10-28）：
- https://cu.bgfretail.com/product/product.do?category=product&depth2=4 的商品清單
  不在靜態 HTML 裡，由前端 AJAX 載入，頁面上有「최신등록순（最新登錄）」排序。
- 下方端點與參數是依該站常見結構推測，需在 GitHub Actions 第一次執行時確認；
  失敗時 pipeline 會記錄錯誤並略過，不影響日本來源。
- 官網未提供上架日期，以「第一次被我們抓到的日期」當作上架日。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space, parse_price

AJAX_URL = "https://cu.bgfretail.com/product/productAjax.do"
# 大分類代碼（간편식사、즉석조리、과자류、아이스크림、식품、음료）
MAIN_CATEGORIES = {"10": "간편식사", "20": "즉석조리", "30": "과자류",
                   "40": "아이스크림", "50": "식품", "60": "음료"}


def parse_list(html: str, cat_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for li in soup.select("li.prod_list, div.prod_list, li"):
        name_el = li.select_one(".name p, .prod_text .name, .name")
        price_el = li.select_one(".price strong, .price")
        if not name_el or not price_el:
            continue
        name = normalize_space(name_el.get_text())
        onclick = " ".join(str(x) for x in [li.get("onclick", ""), li.decode_contents()[:400]])
        m = re.search(r"view\((\d+)\)|gdIdx=(\d+)|(\d{8,})", onclick)
        item_id = next((g for g in (m.groups() if m else []) if g), name)
        rows.append({
            "country": "KR", "chain": "cu_kr", "item_id": item_id,
            "name_original": name,
            "price_tax_incl": parse_price(price_el.get_text(), prefer_tax_incl=False),
            "currency": "KRW", "release_date": "", "regions": "全國",
            "source_category": cat_name,
            "source_url": "https://cu.bgfretail.com/product/product.do?category=product&depth2=4",
            "list_page": "newest",
        })
    return rows


def crawl(pages_per_category: int = 2) -> list[dict]:
    out = []
    for code, name in MAIN_CATEGORIES.items():
        for page in range(1, pages_per_category + 1):
            resp = fetch(AJAX_URL, method="POST", data={
                "pageIndex": page, "searchMainCategory": code, "searchSubCategory": "",
                "listType": 1, "searchCondition": "setC",  # setC = 최신등록순（待確認）
                "searchUseYn": "", "gdIdx": 0, "codeParent": code,
            })
            if resp.status_code != 200:
                break
            rows = parse_list(resp.text, name)
            if not rows:
                break
            out.extend(rows)
    return out
