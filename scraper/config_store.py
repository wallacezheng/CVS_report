"""設定來源：Google Sheets（主控台）或本機 config/*.csv（備援／本機測試）。

Google Sheet 分頁（第一列為欄名，與 config/*.csv 相同）：
  categories   分類表
  tags         口味／素材標籤字典（含日韓同義詞）
  glossary     翻譯術語表
  overrides    人工校正：product_key, field, value, note, editor
  sources      來源開關與排行網址：source_id, type, url, enabled, note
  review_queue 待人工確認（由程式寫入）
  mail_list    收件名單（Apps Script 讀取）
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TABS = ["categories", "tags", "glossary", "overrides", "sources"]


def _read_local(name: str) -> list[dict]:
    p = CONFIG_DIR / f"{name}.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _sheet():
    sheet_id = os.environ.get("SHEET_ID")
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheet_id or not sa_json:
        return None
    import gspread  # 只有在雲端執行時需要
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    return gc.open_by_key(sheet_id)


class ConfigStore:
    def __init__(self):
        self.sheet = None
        try:
            self.sheet = _sheet()
        except Exception as e:  # 連不到 Sheet 時退回本機設定，不中斷每日流程
            print(f"[config] Google Sheet 無法連線，改用本機 config/：{e}")
        self.data = {t: self._load(t) for t in TABS}

    def _load(self, tab: str) -> list[dict]:
        if self.sheet is not None:
            try:
                return self.sheet.worksheet(tab).get_all_records()
            except Exception as e:
                print(f"[config] 讀取分頁 {tab} 失敗，改用本機檔：{e}")
        return _read_local(tab)

    # --- 便利存取 ---
    def categories(self) -> list[dict]:
        return [c for c in self.data["categories"] if str(c.get("active", "1")) == "1"]

    def tags(self) -> list[dict]:
        return [t for t in self.data["tags"] if str(t.get("active", "1")) == "1"]

    def glossary(self) -> list[dict]:
        return self.data["glossary"]

    def overrides(self) -> list[dict]:
        return self.data["overrides"]

    def source_enabled(self, source_id: str, default: bool = True) -> bool:
        for s in self.data["sources"]:
            if s.get("source_id") == source_id:
                return str(s.get("enabled", "1")) == "1"
        return default

    def ranking_urls(self, type_: str) -> dict[str, str]:
        return {s["source_id"]: s["url"] for s in self.data["sources"]
                if s.get("type") == type_ and str(s.get("enabled", "1")) == "1" and s.get("url")}

    def write_review_queue(self, rows: list[dict]) -> None:
        """把待確認項目附加到 review_queue 分頁（已存在的 product_key 不重複寫）。"""
        if self.sheet is None or not rows:
            return
        ws = self.sheet.worksheet("review_queue")
        existing = {r.get("product_key") for r in ws.get_all_records()}
        header = ["product_key", "name_original", "name_zh", "category_code", "tags",
                  "confidence", "reason", "source_url", "added"]
        new = [[r.get(h, "") for h in header] for r in rows if r["product_key"] not in existing]
        if new:
            if not ws.row_values(1):
                ws.append_row(header)
            ws.append_rows(new, value_input_option="RAW")
