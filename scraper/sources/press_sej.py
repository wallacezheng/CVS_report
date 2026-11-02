"""7-Eleven Japan 官方新聞稿（https://www.sej.co.jp/company/news_release/news/）

已確認（2026-11-02，probe）：
- /company/news_release/news/ 以 JavaScript 轉到年度列表 /company/news_release/news/{YYYY}.html（列表本身是靜態 HTML）
- 年度列表 <ul class="list-news"><li>：p.date「2026/10/29」＋分類標籤 span（商品、キャンペーン、企業情報、サステナビリティ…），
  連結 /company/news_release/news/{YYYY}/{YYYYMMDDHHMM}.html（少數檔名格式不同，日期以列表為準）
- 只取有「商品」標籤的新聞稿；網站有 Imperva 防護，需帶正常 User-Agent（common.fetch 已處理）
- robots.txt 不存在（404），無禁止規則
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space
from .press_fm import parse_release

BASE = "https://www.sej.co.jp"
LINK_RE = re.compile(r"/company/news_release/news/(\d{4})/([0-9A-Za-z_-]+)\.html")
DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
BODY_START = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日\s*株式会社セブン")


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for li in soup.select("ul.list-news > li"):
        a = li.find("a", href=LINK_RE)
        date_p = li.select_one("p.date")
        if not a or not date_p:
            continue
        labels = {normalize_space(s.get_text()) for s in date_p.find_all("span")}
        if "商品" not in labels:
            continue
        d = DATE_RE.search(date_p.get_text(" "))
        m = LINK_RE.search(a["href"])
        rid = f"sej_{m.group(1)}_{m.group(2)}"
        if not d or rid in seen:
            continue
        seen.add(rid)
        out.append({"release_id": rid, "chain": "seven_jp",
                    "date": f"{int(d.group(1)):04d}-{int(d.group(2)):02d}-{int(d.group(3)):02d}",
                    "title": normalize_space(a.get_text(" ")), "url": BASE + m.group(0)})
    return out


def parse_body(html: str, max_chars: int = 5000) -> str:
    """去掉重複三次的麵包屑：從「2026年10月29日 株式会社セブン‐イレブン・ジャパン」開始取內文。"""
    text = parse_release(html, max_chars=40000)
    m = BODY_START.search(text)
    return (text[m.start():] if m else text)[:max_chars]


def crawl(seen_ids: set[str], years: list[int], since: str, max_items: int = 30) -> list[dict]:
    items = []
    for y in years:
        resp = fetch(f"{BASE}/company/news_release/news/{y}.html")
        if resp.status_code == 200:
            items.extend(parse_list(resp.text))
    if not items:
        raise RuntimeError("7-Eleven 新聞稿列表沒有抓到任何商品類新聞，可能改版或被擋")
    new = [i for i in items if i["release_id"] not in seen_ids and i["date"] >= since]
    new.sort(key=lambda i: i["date"], reverse=True)
    out = []
    for i in new[:max_items]:
        r = fetch(i["url"])
        if r.status_code == 200:
            i["text"] = parse_body(r.text)
            out.append(i)
    print(f"[press] 7-Eleven 新聞稿：列表（商品類）{len(items)} 篇，新增 {len(new)} 篇，本次讀取 {len(out)} 篇")
    return out
