"""CU（韓國）新商品

已驗證（2026-11-02，probe 從 GitHub Actions 美國主機可存取）：
- 商品頁 https://cu.bgfretail.com/product/product.do?category=product&depth2=4 的清單由
  POST /product/productAjax.do 載入（回傳 HTML 片段，每頁 40 件）
- 參數：pageIndex、searchMainCategory／codeParent（10 간편식사、20 즉석조리、30 과자류、40 아이스크림、
  50 식품、60 음료、70 생활용품）、searchCondition=setC（최신등록순，新到舊）、listType=0
- 每件 li.prod_list：onclick="view(28707)" 的數字＝商品代碼、.name p 品名、.price strong 價格、
  .tag .new＝官網標示 NEW；圖片檔名是商品條碼
- 官網沒有發售日：只收 NEW 標示的商品，發售日留空（pipeline 以第一次抓到的日期當上架日）
- robots.txt 不存在（404）
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space, parse_price

AJAX_URL = "https://cu.bgfretail.com/product/productAjax.do"
VIEW_URL = "https://cu.bgfretail.com/product/view.do?category=product&gdIdx={}"
# 생활용품（70）不收
MAIN_CATEGORIES = {"10": "간편식사", "20": "즉석조리", "30": "과자류",
                   "40": "아이스크림", "50": "식품", "60": "음료"}
MAX_PAGES = 6


def parse_list(html: str, cat_name: str) -> list[dict]:
    """回傳這一頁所有商品（含 is_new 標記）。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for li in soup.select("li.prod_list"):
        name_el = li.select_one(".name p") or li.select_one(".name")
        m = re.search(r"view\((\d+)\)", str(li))
        if not name_el or not m:
            continue
        price_el = li.select_one(".price strong") or li.select_one(".price")
        img = li.select_one("img.prod_img")
        barcode = re.search(r"(\d{8,14})\.(?:jpg|png)", img.get("src", "")) if img else None
        rows.append({
            "country": "KR", "chain": "cu_kr", "item_id": m.group(1),
            "name_original": normalize_space(name_el.get_text()),
            "price_tax_incl": parse_price(price_el.get_text() if price_el else "", prefer_tax_incl=False),
            "currency": "KRW", "release_date": "", "regions": "",
            "source_category": cat_name,
            "source_url": VIEW_URL.format(m.group(1)),
            "list_page": f"newest:{barcode.group(1) if barcode else ''}",
            "is_new": bool(li.select_one(".tag .new")),
        })
    return rows


def crawl() -> list[dict]:
    out = []
    for code, name in MAIN_CATEGORIES.items():
        for page in range(1, MAX_PAGES + 1):
            resp = fetch(AJAX_URL, method="POST", data={
                "pageIndex": page, "searchMainCategory": code, "searchSubCategory": "",
                "listType": 0, "searchCondition": "setC", "searchUseYn": "N", "gdIdx": 0,
                "codeParent": code, "search1": "", "search2": "", "searchKeyword": "",
            })
            if resp.status_code != 200:
                break
            rows = parse_list(resp.text, name)
            new = [r for r in rows if r.pop("is_new")]
            out.extend(new)
            if not rows or len(new) < len(rows):  # 已讀到非 NEW 的舊商品，後面不必再翻
                break
    return out
