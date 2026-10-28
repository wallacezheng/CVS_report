"""全家日本 官方新聞稿（https://www.family.co.jp/company/news_releases.html）

用途：擷取「熱賣訊號」——官方公布的累計銷量、再販、熱賣販售休止、增產、聯名等。
已確認（2026-10-28）：
- 年度列表 /company/news_releases/{YYYY}.html
- 單篇 /company/news_releases/{YYYY}/{YYYYMMDD}_{NN}.html（少數為 PDF，略過）
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import fetch, normalize_space

BASE = "https://www.family.co.jp"
LINK_RE = re.compile(r"/company/news_releases/(\d{4})/(\d{8}_\d{2})\.html")


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=LINK_RE):
        m = LINK_RE.search(a["href"])
        rid = m.group(2)
        if rid in seen:
            continue
        seen.add(rid)
        title = normalize_space(a.get_text(" "))
        out.append({"release_id": f"fm_{rid}", "chain": "familymart_jp",
                    "date": f"{rid[:4]}-{rid[4:6]}-{rid[6:8]}", "title": title,
                    "url": BASE + m.group(0)})
    return out


def parse_release(html: str, max_chars: int = 5000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(id="contents") or soup.body or soup
    text = normalize_space(main.get_text(" "))
    return text[:max_chars]


def crawl(seen_ids: set[str], years: list[int], since: str, max_items: int = 30) -> list[dict]:
    """回傳新出現的新聞稿（含內文），since 之前的不抓。"""
    items = []
    for y in years:
        resp = fetch(f"{BASE}/company/news_releases/{y}.html")
        if resp.status_code == 200:
            items.extend(parse_list(resp.text))
    new = [i for i in items if i["release_id"] not in seen_ids and i["date"] >= since]
    new.sort(key=lambda i: i["date"], reverse=True)
    out = []
    for i in new[:max_items]:
        resp = fetch(i["url"])
        if resp.status_code == 200:
            i["text"] = parse_release(resp.text)
            out.append(i)
    print(f"[press] 全家新聞稿：列表 {len(items)} 篇，新增 {len(new)} 篇，本次讀取 {len(out)} 篇")
    return out
