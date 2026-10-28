"""7-Eleven Japan 新商品（https://www.sej.co.jp/products/a/thisweek/）

已驗證（2026-10-28）：
- 每週二更新；「今週」與「来週」兩頁，依 11 個地區分頁
- 地區頁支援每頁 100 筆：/products/a/{week}/area/{area}/{page}/l100/
- 商品連結 /products/a/item/{6碼ID}/{area}/，ID 跨地區相同，可作為商品鍵
- 文字含「税込xxx円」「YYYY年MM月DD日」「販売地域：…」
"""
from __future__ import annotations

import re

from ..common import fetch, is_regional_jp, normalize_space, parse_jp_date, parse_price
from ._html import item_blocks

BASE = "https://www.sej.co.jp/products/a"
AREAS = ["hokkaido", "tohoku", "kanto", "koshinetsu", "hokuriku", "tokai",
         "kinki", "chugoku", "shikoku", "kyushu", "okinawa"]
ITEM_RE = re.compile(r"/products/a/item/(\d+)/")
TOTAL_RE = re.compile(r"(\d+)件中")


def parse_list(html: str, list_page: str) -> list[dict]:
    rows = []
    for a, text in item_blocks(html, ITEM_RE):
        item_id = ITEM_RE.search(a["href"]).group(1)
        region_m = re.search(r"販売地域[：:]\s*([^\n]+)", text)
        regions = normalize_space(region_m.group(1)) if region_m else ""
        url = a["href"] if a["href"].startswith("http") else "https://www.sej.co.jp" + a["href"]
        rows.append({
            "country": "JP",
            "chain": "seven_jp",
            "item_id": item_id,
            "name_original": normalize_space(a.get_text()),
            "price_tax_incl": parse_price(text),
            "currency": "JPY",
            "release_date": parse_jp_date(text) or "",
            "regions": regions,
            "source_category": "",
            "source_url": url,
            "list_page": list_page,
        })
    return rows


def _debug_save(name: str, resp) -> None:
    """抓不到資料時留下收到的頁面，方便判斷是被擋、改版還是編碼問題。"""
    from pathlib import Path
    d = Path(__file__).resolve().parents[2] / "data" / "debug"
    d.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
    body = resp.text[:20000]
    (d / f"{name}.txt").write_text(
        f"URL: {resp.url}\nSTATUS: {resp.status_code}\nENCODING: {resp.encoding}\n"
        f"LENGTH: {len(resp.content)}\n\n{head}\n\n----- BODY (前 20000 字) -----\n{body}",
        encoding="utf-8")


def crawl(weeks=("thisweek", "nextweek"), areas=AREAS, max_pages: int = 5) -> list[dict]:
    seen: dict[str, dict] = {}
    saved_debug = False
    for week in weeks:
        for area in areas:
            for page in range(1, max_pages + 1):
                url = f"{BASE}/{week}/area/{area}/{page}/l100/"
                resp = fetch(url)
                if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                n_links = len(ITEM_RE.findall(resp.text))
                rows = parse_list(resp.text, week) if resp.status_code == 200 else []
                print(f"[seven_jp] {week}/{area}/p{page} status={resp.status_code} "
                      f"bytes={len(resp.content)} item_links={n_links} parsed={len(rows)}")
                if not rows and not saved_debug:
                    _debug_save(f"seven_jp_{week}_{area}_p{page}", resp)
                    saved_debug = True
                if resp.status_code in (401, 403, 429):
                    # 被網站的防護擋下：立即停止，不重複送請求
                    raise RuntimeError(f"7-Eleven 回應 {resp.status_code}（網站防護拒絕自動存取），已停止")
                if resp.status_code != 200:
                    break
                for r in rows:
                    # 同一 ID 在多個地區頁重複出現，只留一筆
                    seen.setdefault(f"{week}:{r['item_id']}", r)
                total_m = TOTAL_RE.search(resp.text)
                total = int(total_m.group(1)) if total_m else 0
                if page * 100 >= total or not rows:
                    break
    return list(seen.values())
