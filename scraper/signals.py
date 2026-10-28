"""熱賣訊號：官方新聞稿擷取（Gemini）＋地區擴張＋聯名，彙整到商品主檔的 hit_signals 欄位。

公開 repo 只存「事實摘要」（我們自己寫的中文摘要與數字），不存新聞稿原文。
"""
from __future__ import annotations

import csv
import difflib
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from .enrich import call_gemini, gemini_mode, resolve_model

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRESS_FIELDS = ["release_id", "chain", "date", "title", "url", "is_food", "summary_zh"]
SIGNAL_FIELDS = ["release_id", "chain", "date", "signal_type", "signal_zh", "target_ja", "detail_zh",
                 "number_text", "matched_keys", "url"]
SIGNAL_ZH = {
    "sales_milestone": "官方公布銷量", "resale": "再販／復刻", "sold_out": "熱賣販售休止",
    "increase_production": "增產／擴大販售", "ranking_no1": "官方第一名", "collab": "聯名／監修",
    "series_expand": "系列擴充",
}
HIT_TYPES = {"sales_milestone", "resale", "sold_out", "increase_production", "ranking_no1"}

PRESS_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "release_id": {"type": "STRING"},
        "is_food": {"type": "BOOLEAN"},
        "summary_zh": {"type": "STRING"},
        "signals": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "signal_type": {"type": "STRING", "enum": list(SIGNAL_ZH)},
            "target_ja": {"type": "STRING"},
            "detail_zh": {"type": "STRING"},
            "number_text": {"type": "STRING"},
        }, "required": ["signal_type", "target_ja", "detail_zh"]}},
    }, "required": ["release_id", "is_food", "summary_zh", "signals"]},
}


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _press_prompt(batch: list[dict]) -> str:
    docs = [{"release_id": b["release_id"], "title": b["title"], "text": b["text"]} for b in batch]
    return f"""你是台灣超商商品開發分析師。以下是日本便利商店的官方新聞稿。請判斷每篇是否與食品／飲料商品有關，
並擷取「熱賣或策略訊號」，只根據原文明確寫出的事實，不要推測。

signal_type 只能用：
- sales_milestone：公布銷售數字或累計銷量（例：累計販売4億本突破、発売○日で○万個）
- resale：因要望再販、復刻、定番化
- sold_out：因熱賣而販售休止、品薄、一時停止
- increase_production：增產、擴大販售地區、擴大至全國
- ranking_no1：官方公布的第一名（再販要望No.1、売上No.1 等）
- collab：與品牌、名店、名人、IP 的聯名或監修
- series_expand：系列擴充、新增品項

每個訊號輸出：target_ja（日文商品名或系列名，盡量照原文，用於比對）、detail_zh（30 字內中文說明）、
number_text（原文中的數字敘述，例如「累計販売4億本」，沒有就空字串）。
summary_zh：整篇 50 字內中文摘要。非食品新聞稿 is_food=false、signals 為空陣列。

新聞稿（JSON）：
{json.dumps(docs, ensure_ascii=False)}
"""


def _norm(s: str) -> str:
    return re.sub(r"[\s　（）()【】「」『』・&＆!！]", "", s or "")


def match_products(target: str, chain: str, products: list[dict]) -> list[str]:
    t = _norm(target)
    if len(t) < 3:
        return []
    hits = []
    for p in products:
        if p.get("chain") != chain:
            continue
        n = _norm(p["name_original"])
        if t in n or (len(n) >= 4 and n in t) or difflib.SequenceMatcher(None, t, n).ratio() >= 0.8:
            hits.append(p["product_key"])
    return hits[:30]


def update_press(products: list[dict], today: date, errors: list[str]) -> None:
    from .sources import press_fm
    press = _read(DATA / "press_releases.csv")
    sigs = _read(DATA / "press_signals.csv")
    seen = {p["release_id"] for p in press}
    since = (today - timedelta(weeks=int(os.environ.get("PRESS_LOOKBACK_WEEKS") or 8))).isoformat()
    try:
        new = press_fm.crawl(seen, sorted({today.year, (today - timedelta(weeks=8)).year}), since,
                             int(os.environ.get("MAX_PRESS_PER_RUN") or 30))
    except Exception as e:
        errors.append(f"全家新聞稿抓取失敗：{e}")
        return
    if new and os.environ.get("ENRICH_ENABLED", "1").strip() != "0" and gemini_mode():
        try:
            resolve_model()
            for i in range(0, len(new), 5):
                batch = new[i:i + 5]
                by_id = {b["release_id"]: b for b in batch}
                for r in call_gemini(_press_prompt(batch), PRESS_SCHEMA):
                    b = by_id.get(r.get("release_id"))
                    if not b:
                        continue
                    press.append({**b, "is_food": "1" if r.get("is_food") else "0",
                                  "summary_zh": r.get("summary_zh", "")})
                    for s in r.get("signals", []):
                        sigs.append({"release_id": b["release_id"], "chain": b["chain"], "date": b["date"],
                                     "signal_type": s.get("signal_type", ""),
                                     "signal_zh": SIGNAL_ZH.get(s.get("signal_type", ""), ""),
                                     "target_ja": s.get("target_ja", ""), "detail_zh": s.get("detail_zh", ""),
                                     "number_text": s.get("number_text", ""), "url": b["url"]})
        except Exception as e:
            errors.append(f"新聞稿分析失敗：{e}")
    elif new:
        errors.append(f"全家新聞稿有 {len(new)} 篇待分析（需開啟翻譯）")
    # 每次都重新比對（商品主檔會增加）
    for s in sigs:
        s["matched_keys"] = "|".join(match_products(s.get("target_ja", ""), s.get("chain", ""), products))
    _write(DATA / "press_releases.csv", press, PRESS_FIELDS)
    _write(DATA / "press_signals.csv", sigs, SIGNAL_FIELDS)


def apply_hit_signals(products: list[dict]) -> None:
    """把各種訊號彙整到 products 的 hit_signals 欄位（| 分隔）。"""
    by_key: dict[str, set] = {}
    for s in _read(DATA / "press_signals.csv"):
        for k in filter(None, (s.get("matched_keys") or "").split("|")):
            by_key.setdefault(k, set()).add(s.get("signal_zh") or s.get("signal_type"))
    for p in products:
        flags = set(by_key.get(p["product_key"], set()))
        if int(p.get("expansion_count") or 0) > 0:
            flags.add("熱賣擴張")
        if any(t in (p.get("tags") or "").split("|") for t in ("聯名・IP", "名店監修")):
            flags.add("聯名／監修")
        p["hit_signals"] = "|".join(sorted(flags))
