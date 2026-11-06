"""Ministop 日本：店內現做冷甜點（霜淇淋、聖代、哈囉哈囉）

只收冷甜點頁，用來補「霜淇淋」分類（三大超商新品頁沒有店內霜淇淋）。Wallace 2026-11-06 決定。
已驗證（2026-11-06，probe）：
- /syohin/sweets/softcream/、/syohin/sweets/parfait/：div.syohinColumnBox02 > div.column，
  .name 品名、.sale01「10/30(金)より」或「10月30日より」或「発売中」、<p>「594.00円(税込)」；
  同一張卡可列多個口味（「ごほうびソフト-至福のモンブラン-：594.00円」「和栗ソフト：410.40円」）→ 各算一支；
  「得盛○○」是大份量規格，不另算。
- /syohin/sweets/halohalo/：ul.halohaloList li，品名與價格在圖片 alt（「ハロハロ X 本体価格¥500(税込¥540.00)」），
  季節結束時每項標 SOLD OUT → 不收。
- robots.txt 只禁止 /syohin/nutrition/pdf/；利用規約禁止營利目的使用（同 7-Eleven，限內部研究）。
"""
from __future__ import annotations

import hashlib
import re
from datetime import date

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space

BASE = "https://www.ministop.co.jp"
PAGES = [
    ("softcream", f"{BASE}/syohin/sweets/softcream/", "店内ソフトクリーム"),
    ("parfait", f"{BASE}/syohin/sweets/parfait/", "店内パフェ"),
    ("halohalo", f"{BASE}/syohin/sweets/halohalo/", "ハロハロ（店内）"),
]
PRICE_RE = re.compile(r"([\d,]+(?:\.\d+)?)円\s*\(税込\)")
LINE_RE = re.compile(r"^(.+?)\s*[:：]\s*([\d,]+(?:\.\d+)?)円\s*\(税込\)")
DATE_RE = re.compile(r"(\d{1,2})\s*(?:/|月)\s*(\d{1,2})")
HALO_RE = re.compile(r"^(.+?)\s*本体価格¥[\d,]+\(税込¥([\d,]+(?:\.\d+)?)\)")


def _release(text: str, today: date) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    mo, d = int(m.group(1)), int(m.group(2))
    y = today.year
    try:
        dt = date(y, mo, d)
    except ValueError:
        return ""
    if (dt - today).days > 60:      # 例：1 月看到「12/20より」→ 去年
        dt = date(y - 1, mo, d)
    return dt.isoformat()


def _id(slug: str, name: str) -> str:
    return f"{slug}:{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"


def parse_column_page(html: str, kind: str, source_category: str, url: str, today: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for col in soup.select("div.syohinColumnBox02 div.column"):
        name_el = col.select_one(".name")
        if not name_el:
            continue
        card = normalize_space(name_el.get_text())
        sale = col.select_one(".sale01")
        rel = _release(sale.get_text(" ") if sale else "", today)
        link = col.select_one(".photo a[href]")
        href = link["href"] if link else ""
        detail = href if href.startswith("http") else (BASE + href if href.startswith("/") else url)
        if "/syohin/sweets/" not in detail:
            detail = url
        lines = []
        for p in col.find_all("p"):
            for ln in p.get_text("\n").split("\n"):
                ln = normalize_space(ln)
                if ln:
                    lines.append(ln)
        items = []
        for ln in lines:
            m = LINE_RE.match(ln)
            if m and not m.group(1).startswith("得盛"):
                items.append((normalize_space(m.group(1)), m.group(2)))
        if not items:
            m = next((PRICE_RE.search(ln) for ln in lines if PRICE_RE.search(ln)), None)
            if not m:            # 沒有價格的卡片是說明或宣傳（例：「食べるスプーンで提供します！」）
                continue
            items = [(card, m.group(1))]
        for name, price in items:
            rows.append({
                "country": "JP", "chain": "ministop_jp", "item_id": _id(kind, name),
                "name_original": name,
                "price_tax_incl": float(price.replace(",", "")),
                "currency": "JPY", "release_date": rel, "regions": "全国",
                "source_category": source_category, "source_url": detail, "list_page": kind,
            })
    return rows


def parse_halohalo(html: str, source_category: str, url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for li in soup.select("ul.halohaloList li"):
        if li.select_one(".soldOut"):
            continue
        img = li.select_one("dt img[alt]")
        m = HALO_RE.match(normalize_space(img["alt"])) if img else None
        if not m:
            continue
        a = li.select_one("dt a[href]")
        href = a["href"] if a else ""
        name = m.group(1)
        rows.append({
            "country": "JP", "chain": "ministop_jp", "item_id": _id("halohalo", name),
            "name_original": name, "price_tax_incl": float(m.group(2).replace(",", "")),
            "currency": "JPY", "release_date": "", "regions": "全国",
            "source_category": source_category,
            "source_url": BASE + href if href.startswith("/") else url, "list_page": "halohalo",
        })
    return rows


def crawl(today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for kind, url, cat in PAGES:
        resp = fetch(url)
        if resp.status_code != 200:
            continue
        if kind == "halohalo":
            out.extend(parse_halohalo(resp.text, cat, url))
        else:
            out.extend(parse_column_page(resp.text, kind, cat, url, today))
    return out
