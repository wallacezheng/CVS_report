"""日本口碑排行：もぐナビ（食品口碑網站）

已知（2026-10-28）：
- 編輯部「トレンド人気ランキング」文章依品類（コンビニおにぎり、パン、スイーツ、
  ホットスナック、ドリンク…）發布，排行每週五更新
- 文章格式「第N位：『品牌 商品名』」
- 本模組讀取 Google Sheet `sources` 分頁中登記的排行頁網址（type=mognavi_ranking），
  不自行遍歷全站。使用條款與是否改用付費資料服務，待確認後再擴大。
"""
from __future__ import annotations

import re

from ..common import fetch, today_tw

RANK_RE = re.compile(r"第\s*(\d+)\s*位[：:]\s*『([^』]+)』")
CHAINS = {"セブン-イレブン": "seven_jp", "ファミリーマート": "familymart_jp",
          "ローソン": "lawson_jp"}


def parse_ranking(html: str, ranking_name: str) -> list[dict]:
    text = re.sub(r"<[^>]+>", " ", html)
    out = []
    for rank, title in RANK_RE.findall(text):
        chain = next((v for k, v in CHAINS.items() if title.startswith(k)), "")
        out.append({"date": today_tw().isoformat(), "country": "JP", "source": "mognavi",
                    "target_type": "product_name", "target": title,
                    "metric": f"rank:{ranking_name}", "value": int(rank), "chain": chain})
    return out


def crawl(ranking_urls: dict[str, str]) -> list[dict]:
    out = []
    for name, url in ranking_urls.items():
        resp = fetch(url)
        if resp.status_code == 200:
            out.extend(parse_ranking(resp.text, name))
    return out
