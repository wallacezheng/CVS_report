"""FamilyMart Japan 新商品（https://www.family.co.jp/goods/newgoods.html）

已驗證（2026-10-28）：
- 今週 newgoods.html、先週 newgoods/lastweek.html、来週 newgoods/nextweek.html
- 来週頁在週五傍晚才公開，平日抓會是空的（正常）
- 頁面為單頁全列表，無分頁；標題含週期「(10/27～11/02)」
- 商品連結 /goods/{分類}/{7碼ID}.html，連結文字＝「分類 名稱 價格（税込xxx円）」
- 鹿兒島・宮崎、沖繩為不同公司網站，未納入
"""
from __future__ import annotations

import re

from ..common import fetch, infer_year, is_regional_jp, normalize_space, parse_price
from ._html import item_blocks

BASE = "https://www.family.co.jp/goods"
PAGES = {
    "thisweek": f"{BASE}/newgoods.html",
    "lastweek": f"{BASE}/newgoods/lastweek.html",
    "nextweek": f"{BASE}/newgoods/nextweek.html",
}
ITEM_RE = re.compile(r"/goods/([a-z_]+)/(\d+)\.html")
WEEK_RE = re.compile(r"\((\d{1,2})/(\d{1,2})～(\d{1,2})/(\d{1,2})\)")
LIMITED_PREFIX = re.compile(r"^(?:\S+限定\s+)")

# 官網分類（URL 路徑）→ 日文分類名，連結文字開頭會帶這個名字
CATEGORY_LABELS = {
    "omusubi": "おむすび", "obento": "お弁当", "sushi": "お寿司",
    "sandwich": "サンドイッチ・ロールパン・バーガー", "bread": "パン",
    "noodle": "そば・うどん・中華めん", "pasta": "パスタ", "salad": "サラダ",
    "sidedishes": "チルド惣菜", "deli": "スープ・グラタン・お好み焼き",
    "friedfoods": "ホットスナック・惣菜", "chukaman": "中華まん", "oden": "おでん",
    "dessert": "デザート", "baked_sweets": "焼き菓子・和菓子", "cafe": "コーヒー・フラッペ",
    "processed_foods": "加工食品", "snack": "お菓子", "drink": "飲料", "alcohol": "お酒",
    "frozen_foods": "冷凍食品", "ice": "アイス", "charakuji": "キャラクターくじ・エンタメ雑貨など",
    "daily_necessities": "日用品", "cosmetics": "スキンケア・コスメ", "cw": "コンビニエンスウェア",
}


def _clean_name(link_text: str, cat_key: str) -> str:
    t = normalize_space(link_text)
    t = re.sub(r"\s*[\d,]+円（税込[\d,]+円）\s*$", "", t)
    t = LIMITED_PREFIX.sub("", t)  # 「関東限定 」等前綴
    label = CATEGORY_LABELS.get(cat_key, "")
    if label and t.startswith(label):
        t = t[len(label):].strip()
    return t


def parse_list(html: str, list_page: str) -> list[dict]:
    week_m = WEEK_RE.search(html)
    release = ""
    if week_m:
        release = infer_year(int(week_m.group(1)), int(week_m.group(2))).isoformat()
    rows = []
    for a, text in item_blocks(html, ITEM_RE):
        m = ITEM_RE.search(a["href"])
        cat_key, item_id = m.group(1), m.group(2)
        region_m = re.search(r"発売地域[：:]\s*([^\n]+)", text)
        regions = normalize_space(region_m.group(1)) if region_m else ""
        url = a["href"] if a["href"].startswith("http") else "https://www.family.co.jp" + a["href"]
        rows.append({
            "country": "JP",
            "chain": "familymart_jp",
            "item_id": item_id,
            "name_original": _clean_name(a.get_text(" "), cat_key),
            "price_tax_incl": parse_price(a.get_text(" ")),
            "currency": "JPY",
            "release_date": release,
            "regions": regions,
            "source_category": CATEGORY_LABELS.get(cat_key, cat_key),
            "source_url": url,
            "list_page": list_page,
        })
    return rows


def crawl(pages=("lastweek", "thisweek", "nextweek")) -> list[dict]:
    out = []
    for key in pages:
        resp = fetch(PAGES[key])
        if resp.status_code == 200:
            out.extend(parse_list(resp.text, key))
    return out
