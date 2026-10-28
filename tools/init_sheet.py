"""第一次設定：把 config/*.csv 寫進空白 Google Sheet，建立所有分頁。

  export SHEET_ID=...  GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)"
  python tools/init_sheet.py
（Sheet 要先「共用」給服務帳號的 email，權限：編輯者）
"""
import csv
import json
import os
import sys
from pathlib import Path

import gspread

ROOT = Path(__file__).resolve().parent.parent
TABS = {
    "使用手冊": "manual",
    "categories": None, "tags": None, "glossary": None, "overrides": None, "sources": None,
    "review_queue": ["product_key", "name_original", "name_zh", "category_code", "tags",
                     "confidence", "reason", "source_url", "added"],
    "mail_list": ["email", "name", "frequency", "enabled", "note"],
}


def fail(msg: str) -> None:
    print(f"::error::{msg}")
    sys.exit(1)


sheet_id = os.environ.get("SHEET_ID", "").strip()
raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
if not sheet_id:
    fail("GitHub Secrets 裡沒有 SHEET_ID（或名稱拼錯）")
if not raw:
    fail("GitHub Secrets 裡沒有 GOOGLE_SERVICE_ACCOUNT_JSON（或名稱拼錯）")
if "/" in sheet_id:
    fail("SHEET_ID 看起來貼了整段網址，只要 /d/ 和 /edit 中間那串")
try:
    info = json.loads(raw)
except json.JSONDecodeError as e:
    fail(f"GOOGLE_SERVICE_ACCOUNT_JSON 不是完整的 JSON（{e}），請用記事本打開檔案全選後重貼")
print(f"服務帳號：{info.get('client_email')}｜專案：{info.get('project_id')}")

try:
    gc = gspread.service_account_from_dict(info)
    sh = gc.open_by_key(sheet_id)
except gspread.exceptions.APIError as e:
    text = str(e)
    if "has not been used" in text or "is disabled" in text or "SERVICE_DISABLED" in text:
        fail(f"專案 {info.get('project_id')} 尚未啟用 Google Sheets API／Google Drive API，請到 API 程式庫啟用。原始訊息：{text[:300]}")
    if "PERMISSION_DENIED" in text or "403" in text:
        fail(f"沒有權限開這份 Sheet：請把 Sheet 共用給 {info.get('client_email')}（編輯者）。原始訊息：{text[:300]}")
    if "NOT_FOUND" in text or "404" in text:
        fail("找不到這份 Sheet：SHEET_ID 可能貼錯")
    fail(f"Google API 錯誤：{text[:500]}")
except gspread.exceptions.SpreadsheetNotFound:
    fail(f"找不到 Sheet：確認 SHEET_ID 正確，且已共用給 {info.get('client_email')}（編輯者）")

# INIT_TABS：all（全部，會覆蓋）或以逗號分隔的分頁名稱，例如「使用手冊」
want = os.environ.get("INIT_TABS", "").strip() or "all"
selected = TABS if want == "all" else {t: TABS[t] for t in [x.strip() for x in want.split(",")] if t in TABS}
if not selected:
    fail(f"tabs 填的分頁名稱不認得：{want}。可用：{'、'.join(TABS)}")

existing = {ws.title for ws in sh.worksheets()}
for tab, header in selected.items():
    if header is None or isinstance(header, str):
        name = header if isinstance(header, str) else tab
        with (ROOT / "config" / f"{name}.csv").open(encoding="utf-8-sig") as f:
            values = list(csv.reader(f))
    else:
        values = [header]
    ws = sh.worksheet(tab) if tab in existing else sh.add_worksheet(tab, rows=500, cols=12)
    ws.clear()
    ws.update(values, "A1")
    ws.freeze(rows=1)
    if tab == "使用手冊":
        ws.format("A1:C1", {"textFormat": {"bold": True}})
        ws.columns_auto_resize(0, 1)
        ws.format("C:C", {"wrapStrategy": "WRAP"})
        try:
            sh.reorder_worksheets([ws] + [w for w in sh.worksheets() if w.id != ws.id])
        except Exception:
            pass
    print(f"{tab}: {len(values) - 1} 列")
