"""Lawson 官方新聞稿（https://www.lawson.co.jp/company/news/）

用途：與全家新聞稿相同，擷取熱賣訊號（累計銷量、再販、增產、聯名等）。
已確認（2026-11-02，probe）：
- /company/news/ 列出近一年約 190 篇；每列 <tr>：<th>2026年10月30日</th>、分類圖示 alt（商品／サービス／…）、
  標題連結 /company/news/detail/{ID}_2504.html
- 只取分類「商品」「ナチュラルローソン」，略過企業、CSR 等新聞
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space
from .press_fm import parse_release

BASE = "https://www.lawson.co.jp"
LIST_URL = f"{BASE}/company/news/"
LINK_RE = re.compile(r"/company/news/detail/(\d+)_\d+\.html")
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
KEEP_CATEGORIES = {"商品", "ナチュラルローソン"}


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=LINK_RE)
        if not a:
            continue
        img = tr.find("img")
        if (img.get("alt") if img else "") not in KEEP_CATEGORIES:
            continue
        m = LINK_RE.search(a["href"])
        d = DATE_RE.search(tr.get_text(" "))
        if not d or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append({"release_id": f"lw_{m.group(1)}", "chain": "lawson_jp",
                    "date": f"{int(d.group(1)):04d}-{int(d.group(2)):02d}-{int(d.group(3)):02d}",
                    "title": normalize_space(a.get_text(" ")), "url": BASE + m.group(0)})
    return out


def crawl(seen_ids: set[str], since: str, max_items: int = 30) -> list[dict]:
    resp = fetch(LIST_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"Lawson 新聞稿列表回應 {resp.status_code}")
    items = parse_list(resp.text)
    new = [i for i in items if i["release_id"] not in seen_ids and i["date"] >= since]
    new.sort(key=lambda i: i["date"], reverse=True)
    out = []
    for i in new[:max_items]:
        r = fetch(i["url"])
        if r.status_code == 200:
            i["text"] = parse_release(r.text)
            out.append(i)
    print(f"[press] Lawson 新聞稿：列表（商品類）{len(items)} 篇，新增 {len(new)} 篇，本次讀取 {len(out)} 篇")
    return out
