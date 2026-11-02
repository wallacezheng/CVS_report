"""emart24（韓國）差異化商品（自有品牌與合作商品）

已驗證（2026-11-02，probe）：
- https://emart24.co.kr/goods/pl?page=N&align=RECENT：靜態 HTML，每頁 20 件，align=RECENT＝最新順
- 每件 div.itemWrap：.itemtitle 品名、a.price「1,800 원」、圖片檔名＝商品條碼
- 頁面上的「NEW」字樣每件都有（以 opacity 控制顯示），不能用來判斷新品
- 官網沒有發售日：只取最新順前 MAX_PAGES 頁，以第一次抓到的日期當上架日
- robots.txt 只禁止 /founded/brief/req、/founded/recommend
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space, parse_price

LIST_URL = "https://emart24.co.kr/goods/pl?search=&page={page}&category_seq=&align=RECENT"
MAX_PAGES = 2


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for w in soup.select("div.itemWrap"):
        title = w.select_one(".itemtitle")
        img = w.select_one(".itemSpImg img")
        code = re.search(r"/(\d{8,14})\.(?:jpe?g|png)", img.get("src", ""), re.I) if img else None
        if not title or not code:
            continue
        price = w.select_one("a.price")
        rows.append({
            "country": "KR", "chain": "emart24_kr", "item_id": code.group(1),
            "name_original": normalize_space(title.get_text()),
            "price_tax_incl": parse_price(price.get_text() if price else "", prefer_tax_incl=False),
            "currency": "KRW", "release_date": "", "regions": "",
            "source_category": "차별화 상품",
            "source_url": "https://emart24.co.kr/goods/pl?align=RECENT",
            "list_page": "pl:recent",
        })
    return rows


def crawl() -> list[dict]:
    out = []
    for page in range(1, MAX_PAGES + 1):
        resp = fetch(LIST_URL.format(page=page))
        if resp.status_code != 200:
            break
        rows = parse_list(resp.text)
        if not rows:
            break
        out.extend(rows)
    return out
