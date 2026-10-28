"""日本口碑排行：もぐナビ（https://mognavi.jp/）— 內部研究用

依 Wallace 2026-10-28 決定：每週抓一次超商品牌的週排行，請求間隔 ≥10 秒，
存「名次＋商品名＋口碑摘錄」，只寫入私有的 Google Sheet，不進公開 repo、不寄信。
注意：もぐナビ利用規約限制私的利用以外的使用、資料探勘與 AI 分析；
對外提供前需改用正式授權（もぐナビbiz）。

已確認的頁面結構（2026-10-28）：
- 品牌週排行：/brand/{id}/ranking/1（おすすめ）、/ranking/2（食べたい），依品類分段，每段前 5 名
- 商品頁：/product/{id}，有發售日、食べたい數、平均分數（7 分制）、口碑摘錄
- 口碑全文：/assessment/{id}（本模組不逐則抓，只取商品頁上的摘錄，減少請求數）
- robots.txt 禁止 /do/、/param/ 等路徑，本模組只存取 /brand/ 與 /product/
"""
from __future__ import annotations

import os
import re
import time
from datetime import date

from bs4 import BeautifulSoup, Tag

from ..common import fetch, normalize_space, today_tw

BASE = "https://mognavi.jp"
INTERVAL = max(10.0, float(os.environ.get("MOGNAVI_INTERVAL_SEC") or 12))
# 品牌 ID（もぐナビ）：可在 Google Sheet 的 sources 分頁以 type=mognavi_brand 覆寫
DEFAULT_BRANDS = {
    "seven_jp": ("セブン-イレブン", "2113"),
    "familymart_jp": ("ファミリーマート", "2088"),
    "lawson_jp": ("ローソン", "2087"),
}
RANKING_TYPES = {"1": "おすすめ", "2": "食べたい"}
PRODUCT_RE = re.compile(r"/product/(\d+)")
ASSESS_RE = re.compile(r"/assessment/(\d+)")

_last = 0.0


def _get(url: str):
    """もぐナビ專用：每次請求至少間隔 INTERVAL 秒。"""
    global _last
    wait = INTERVAL - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    resp = fetch(url)
    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp


def _container(a: Tag, pattern: re.Pattern) -> Tag:
    """往上找只包含「這一個」目標連結的最大區塊，當作該商品／口碑的範圍。"""
    target = pattern.search(a["href"]).group(1)
    node = a
    while node.parent is not None and node.parent.name not in ("body", "html", "[document]"):
        ids = {pattern.search(x["href"]).group(1)
               for x in node.parent.find_all("a", href=True) if pattern.search(x["href"])}
        if ids != {target}:
            break
        node = node.parent
    return node


def _heading_before(el: Tag) -> str:
    h = el.find_previous(["h2", "h3", "h4"])
    return normalize_space(h.get_text()) if h else ""


def _num(pattern: str, text: str):
    m = re.search(pattern, text)
    return m.group(1).replace(",", "") if m else ""


def parse_ranking(html: str, chain: str, ranking_type: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    updated = _num(r"情報更新[：:\s]*(\d{4}年\d{1,2}月\d{1,2}日)", soup.get_text(" "))
    rows, seen, rank_in_section = [], set(), {}
    for a in soup.find_all("a", href=PRODUCT_RE):
        pid = PRODUCT_RE.search(a["href"]).group(1)
        name = normalize_space(a.get_text())
        if not name or len(name) < 4:  # 圖片連結或「詳細」之類
            continue
        box = _container(a, PRODUCT_RE)
        section = _heading_before(box) or "總合"
        if (section, pid) in seen:
            continue
        seen.add((section, pid))
        rank_in_section[section] = rank_in_section.get(section, 0) + 1
        text = normalize_space(box.get_text(" "))
        explicit = _num(r"(\d+)\s*位", text)
        rows.append({
            "chain": chain,
            "ranking_type": RANKING_TYPES.get(ranking_type, ranking_type),
            "section": section,
            "rank": int(explicit) if explicit and int(explicit) <= 50 else rank_in_section[section],
            "mognavi_id": pid,
            "name_ja": name,
            "score": _num(r"(\d\.\d)", text),
            "review_count": _num(r"(\d[\d,]*)\s*件", text),
            "tabetai": _num(r"食べたい\D{0,6}?(\d[\d,]*)", text),
            "url": f"{BASE}/product/{pid}",
            "site_updated": updated,
        })
    return rows


def parse_product(html: str, pid: str, max_reviews: int = 5) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    full = normalize_space(soup.get_text(" "))
    h1 = soup.find("h1")
    rel = re.search(r"発売日[：:\s]*(\d{4})/(\d{1,2})/(\d{1,2})", full)
    reviews, seen = [], set()
    for a in soup.find_all("a", href=ASSESS_RE):
        rid = ASSESS_RE.search(a["href"]).group(1)
        if rid in seen:
            continue
        seen.add(rid)
        box = _container(a, ASSESS_RE)
        text = normalize_space(box.get_text(" "))
        date = re.search(r"(\d{4}/\d{1,2}/\d{1,2})", text)
        body = re.sub(r"続きを読む.*$", "", text)
        body = re.sub(r"^.*?\d{4}/\d{1,2}/\d{1,2}\s*\d{1,2}:\d{2}\s*", "", body)
        reviews.append({"review_id": rid, "date": date.group(1) if date else "",
                        "text": body[:400]})
        if len(reviews) >= max_reviews:
            break
    return {
        "mognavi_id": pid,
        "name_ja": normalize_space(h1.get_text()) if h1 else "",
        "release_date": f"{rel.group(1)}-{int(rel.group(2)):02d}-{int(rel.group(3)):02d}" if rel else "",
        "tabetai": _num(r"食べたい\D{0,6}?(\d[\d,]*)", full),
        "avg_score": _num(r"(\d\.\d)\s*(?:点|/\s*7)", full),
        "review_count": _num(r"クチコミ\D{0,6}?(\d[\d,]*)\s*件", full),
        "reviews": reviews,
    }


def crawl(brands: dict[str, tuple[str, str]] | None = None, top_n: int = 5,
          max_products: int = 60, max_reviews: int = 5,
          known: dict[str, dict] | None = None, refresh_days: int = 7) -> tuple[list[dict], dict[str, dict]]:
    """回傳（排行列，商品詳情）。known 是先前抓過的商品詳情，未過期就不重抓，減少請求。"""
    brands = brands or DEFAULT_BRANDS
    known = known or {}
    today = today_tw()
    ranks: list[dict] = []
    for chain, (_label, brand_id) in brands.items():
        for rtype in RANKING_TYPES:
            resp = _get(f"{BASE}/brand/{brand_id}/ranking/{rtype}")
            print(f"[mognavi] brand {brand_id} ranking/{rtype} status={resp.status_code} bytes={len(resp.content)}")
            if resp.status_code != 200:
                continue
            got = [r for r in parse_ranking(resp.text, chain, rtype) if r["rank"] <= top_n]
            ranks.extend(got)
    for r in ranks:
        r["crawled_at"] = today.isoformat()

    details: dict[str, dict] = {}
    todo = []
    for pid in dict.fromkeys(r["mognavi_id"] for r in ranks):
        k = known.get(pid)
        if k and k.get("fetched_at") and \
                (today - date.fromisoformat(k["fetched_at"])).days < refresh_days:
            details[pid] = k
        else:
            todo.append(pid)
    for pid in todo[:max_products]:
        resp = _get(f"{BASE}/product/{pid}")
        if resp.status_code != 200:
            print(f"[mognavi] product {pid} status={resp.status_code}")
            continue
        d = parse_product(resp.text, pid, max_reviews)
        d["fetched_at"] = today.isoformat()
        details[pid] = d
    print(f"[mognavi] 排行 {len(ranks)} 列、商品頁新抓 {min(len(todo), max_products)} 筆、沿用 {len(details) - min(len(todo), max_products)} 筆")
    return ranks, details
