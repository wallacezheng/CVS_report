"""第一次設定：把 config/*.csv 寫進空白 Google Sheet，建立所有分頁。

  export SHEET_ID=...  GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)"
  python tools/init_sheet.py
（Sheet 要先「共用」給服務帳號的 email，權限：編輯者）
"""
import csv
import json
import os
from pathlib import Path

import gspread

ROOT = Path(__file__).resolve().parent.parent
TABS = {
    "categories": None, "tags": None, "glossary": None, "overrides": None, "sources": None,
    "review_queue": ["product_key", "name_original", "name_zh", "category_code", "tags",
                     "confidence", "reason", "source_url", "added"],
    "mail_list": ["email", "name", "frequency", "enabled", "note"],
}

gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
sh = gc.open_by_key(os.environ["SHEET_ID"])
existing = {ws.title for ws in sh.worksheets()}
for tab, header in TABS.items():
    if header is None:
        with (ROOT / "config" / f"{tab}.csv").open(encoding="utf-8-sig") as f:
            values = list(csv.reader(f))
    else:
        values = [header]
    ws = sh.worksheet(tab) if tab in existing else sh.add_worksheet(tab, rows=500, cols=12)
    ws.clear()
    ws.update(values, "A1")
    ws.freeze(rows=1)
    print(f"{tab}: {len(values) - 1} 列")
