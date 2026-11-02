"""Lawson Japan 新商品（https://www.lawson.co.jp/recommend/new/）

已驗證（2026-11-02，以 probe 工作流程實際抓取）：
- /recommend/new/ 是 meta refresh，轉到最新一個發售日的清單頁 /recommend/new/list/{ID}_5162.html
- 每個清單頁＝一個發售日；頁首「発売開始日」導覽列出約 7 個發售日（約前後三週），一週通常兩天（週一、週二）
- 商品卡：<ul class="col-3"> 下每個 <li><a href="/recommend/original/detail/{ID}_1996.html">，
  內含 p.ttl 品名、p.price 含稅價、p.date「発売日 2026.11.03」、.smalltxt 註記
- 地區限制寫在註記裡：「※沖縄地域のローソンではお取り扱いしておりません。」（排除）、
  「※中部地域のローソンのみのお取り扱いとなります。」（僅限），沒有註記＝全國
- 清單頁沒有官網分類，分類交給 AI；robots.txt 只禁止 /service/collaboration/ubereats/pharmaceuticals/
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space, parse_price

BASE = "https://www.lawson.co.jp"
ENTRY = f"{BASE}/recommend/new/"
LIST_RE = re.compile(r"/recommend/new/list/\d+_\d+\.html")
DETAIL_RE = re.compile(r"/recommend/original/detail/(\d+)_\d+\.html")

# 統一用 7-Eleven 的大區名稱，讓 is_regional_jp／地區擴張判斷與其他超商一致
ALL_AREAS = ["北海道", "東北", "関東", "甲信越", "北陸", "東海", "近畿", "中国", "四国", "九州", "沖縄"]
LAWSON_AREA = {
    "北海道": ["北海道"], "東北": ["東北"], "関東": ["関東"], "甲信越": ["甲信越"], "北陸": ["北陸"],
    "東海": ["東海"], "中部": ["甲信越", "北陸", "東海"], "近畿": ["近畿"], "関西": ["近畿"],
    "中国・四国": ["中国", "四国"], "中四国": ["中国", "四国"], "中国": ["中国"], "四国": ["四国"],
    "九州": ["九州"], "沖縄": ["沖縄"],
}
EXCLUDE_RE = re.compile(r"([^\s※]+?)地域の(?:ローソン|店舗)?では(?:お)?取り?扱いして(?:おりません|いません)")
ONLY_RE = re.compile(r"([^\s※]+?)地域の(?:ローソン|店舗)?のみ(?:の|での)?(?:お)?取り?扱い|([^\s※]+?)地域限定")


def _areas(text: str) -> list[str]:
    out = []
    for part in re.split(r"[・、,，]", text):
        part = part.strip()
        # 「中国・四国」被「・」切開時各自也能對到
        out.extend(LAWSON_AREA.get(part, []))
    return out


def regions_from_notes(notes: list[str]) -> str:
    """把註記轉成販售地區字串；沒有地區限制回傳「全国」。"""
    only, excluded = [], []
    for n in notes:
        m = ONLY_RE.search(n)
        if m:
            only.extend(_areas(m.group(1) or m.group(2)))
            continue
        m = EXCLUDE_RE.search(n)
        if m:
            excluded.extend(_areas(m.group(1)))
    if only:
        areas = [a for a in ALL_AREAS if a in only]
    elif excluded:
        areas = [a for a in ALL_AREAS if a not in excluded]
    else:
        return "全国"
    return "、".join(areas)


def _date(text: str) -> str:
    m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def list_links(html: str) -> list[str]:
    """發售日導覽中的其他清單頁（不含目前這頁）。"""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.select("ul.contentsNav a[href]"):
        if LIST_RE.search(a["href"]) and a["href"] not in seen:
            seen.add(a["href"])
            out.append(BASE + LIST_RE.search(a["href"]).group(0))
    return out


def parse_list(html: str, list_page: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.select("ul.col-3 > li > a[href]"):
        m = DETAIL_RE.search(a["href"])
        ttl = a.select_one(".ttl")
        if not m or not ttl:
            continue
        notes = [normalize_space(li.get_text()) for li in a.select(".smalltxt li")]
        price = a.select_one(".price")
        date_el = a.select_one(".date")
        rows.append({
            "country": "JP",
            "chain": "lawson_jp",
            "item_id": m.group(1),
            "name_original": normalize_space(ttl.get_text(" ")),
            "price_tax_incl": parse_price(price.get_text("") if price else ""),
            "currency": "JPY",
            "release_date": _date(date_el.get_text(" ") if date_el else ""),
            "regions": regions_from_notes(notes),
            "source_category": "",
            "source_url": BASE + m.group(0),
            "list_page": list_page,
        })
    return rows


def _resolve_entry(html: str) -> str:
    m = re.search(r'URL=([^"\'>\s]+)', html)
    return BASE + m.group(1) if m and m.group(1).startswith("/") else ""


def crawl() -> list[dict]:
    resp = fetch(ENTRY)
    if resp.status_code != 200:
        raise RuntimeError(f"Lawson 新商品入口回應 {resp.status_code}")
    first = _resolve_entry(resp.text) if "Refresh" in resp.text or "refresh" in resp.text else resp.url
    if not first:
        raise RuntimeError("Lawson 新商品入口沒有找到轉址目標，可能改版")
    page = fetch(first)
    if page.status_code != 200:
        raise RuntimeError(f"Lawson 清單頁回應 {page.status_code}")
    out = parse_list(page.text, first)
    for url in list_links(page.text):
        r = fetch(url)
        if r.status_code == 200:
            out.extend(parse_list(r.text, url))
    return out
