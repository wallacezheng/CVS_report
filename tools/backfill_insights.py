"""回溯產生過去週次的洞察快照（只在開始存快照之前的週使用）。

做法：用目前的商品資料與標籤，只取「發售週 ≤ 該週」的商品，當作那一週的狀態重算。
限制：用的是現在的標籤（11-02 重新貼標後），不是當時真的看到的結果；檔案標記 kind=backfill，
網站會標示「回溯計算」。正式的每週快照（kind=weekly）一律由 pipeline 產生，不會被這支工具覆蓋。

用法：python tools/backfill_insights.py 2026-W43 2026-W44
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.enrich import tag_version  # noqa: E402
from scraper.insights import OUT, build_snapshot, save_snapshot  # noqa: E402
from scraper.common import today_tw  # noqa: E402


def main(weeks: list[str]) -> int:
    products = list(csv.DictReader((ROOT / "data" / "products.csv").open(encoding="utf-8")))
    tags = list(csv.DictReader((ROOT / "config" / "tags.csv").open(encoding="utf-8")))
    for w in weeks:
        existing = OUT / f"{w}.json"
        if existing.exists() and json.loads(existing.read_text(encoding="utf-8")).get("kind") == "weekly":
            print(f"{w} 已有正式快照，略過")
            continue
        ps = [p for p in products if p.get("release_week") and p["release_week"] <= w]
        snap = build_snapshot(ps, w, tags, today_tw().isoformat(), tag_version(tags), kind="backfill",
                              note=f"回溯計算：以 {today_tw().isoformat()} 的商品資料與標籤，只取發售週 ≤ {w} 的商品重算")
        print(save_snapshot(snap).name, len(snap["category_tags"]), "筆品類×標籤")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
