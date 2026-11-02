"""もぐナビ每週工作：爬排行與口碑 → Gemini 對應官網商品＋中文口碑摘要 → 只寫入 Google Sheet。

  python -m scraper.mognavi_job

不寫入 repo（repo 為公開），也不進寄信 CSV。Sheet 分頁：
  もぐナビ排行   每週排行（累加歷史，同一週重跑會先刪該週再寫）
  もぐナビ口碑   口碑摘錄原文（以 review_id 去重，累加）
  mognavi_cache  商品詳情與 Gemini 結果快取（程式維護，勿手動編輯）
"""
from __future__ import annotations

import csv
import difflib
import json
import os
import re
import sys
from pathlib import Path

from .common import today_tw
from .config_store import ConfigStore
from .enrich import call_gemini, gemini_mode, resolve_model
from .sources import mognavi

ROOT = Path(__file__).resolve().parent.parent
NOTE = "內部研究・不寄信"
CHAIN_PREFIX = {"seven_jp": "セブン-イレブン", "familymart_jp": "ファミリーマート", "lawson_jp": "ローソン"}
CACHE_FIELDS = ["mognavi_id", "chain", "name_ja", "release_date", "tabetai", "avg_score", "review_count",
                "fetched_at", "reviews_json", "matched_key", "match_confidence", "name_zh", "summary_zh",
                "sentiment", "summarized_review_count", "summarized_at", "parser_version", "summarized_version"]
RANK_HEADER = ["週次", "抓取日", "超商", "排行類型", "品類", "名次", "もぐナビ商品名", "中譯", "對應商品key",
               "對應信心", "分數(7分制)", "口碑數", "食べたい數", "發售日", "口碑摘要", "評價傾向", "網址", "備註"]
REVIEW_HEADER = ["review_id", "mognavi_id", "商品名", "日期", "內容（原文摘錄）", "抓取日"]

MATCH_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "mognavi_id": {"type": "STRING"},
        "matched_key": {"type": "STRING"},
        "match_confidence": {"type": "NUMBER"},
        "name_zh": {"type": "STRING"},
        "summary_zh": {"type": "STRING"},
        "sentiment": {"type": "NUMBER"},
    }, "required": ["mognavi_id", "matched_key", "match_confidence", "name_zh", "summary_zh", "sentiment"]},
}


def _norm(s: str) -> str:
    s = re.sub(r"^(セブン-イレブン|ファミリーマート|ローソン|ファミマ)\s*", "", s or "")
    return re.sub(r"[\s　（）()【】「」・&＆]", "", s)


def candidates(name_ja: str, chain: str, products: list[dict], k: int = 6) -> list[dict]:
    pool = [p for p in products if p.get("chain") == chain] or products
    target = _norm(name_ja)
    scored = sorted(pool, key=lambda p: difflib.SequenceMatcher(None, target, _norm(p["name_original"])).ratio(),
                    reverse=True)
    return [{"product_key": p["product_key"], "name": p["name_original"], "release_date": p.get("release_date", "")}
            for p in scored[:k]]


def build_prompt(batch: list[dict], products: list[dict]) -> str:
    items = []
    for d in batch:
        items.append({
            "mognavi_id": d["mognavi_id"], "name": d["name_ja"], "release_date": d.get("release_date", ""),
            "reviews": [r["text"] for r in json.loads(d.get("reviews_json") or "[]")][:5],
            "candidates": candidates(d["name_ja"], d.get("chain", ""), products),
        })
    return f"""你是台灣超商商品開發顧問。以下是日本食品口碑網站上的超商商品，以及我們從超商官網收集的候選商品。

對每個商品輸出：
1. matched_key：從 candidates 中選出「同一個商品」的 product_key。品名可能有些微差異（容量、促銷字樣、品牌前綴），
   但口味或內容物不同就不是同一個。都不符合就輸出空字串。
2. match_confidence：0～1。
3. name_zh：繁體中文（台灣用語）意譯品名。
4. summary_zh：根據 reviews 寫 60 字內的中文口碑重點，格式「好評：…；負評：…」，沒有負評就省略該段；
   沒有 reviews 就輸出空字串。只寫消費者實際提到的內容，不要推測。
5. sentiment：-1（多負評）～1（多好評），沒有 reviews 給 0。

商品（JSON）：
{json.dumps(items, ensure_ascii=False)}
"""


def read_tab(sh, name: str) -> list[dict]:
    """分頁不存在回傳空清單；其他讀取錯誤直接拋出，避免誤以為沒有舊資料而把它覆蓋掉。"""
    import gspread
    try:
        ws = sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return []
    return ws.get_all_records()


BACKUP_TABS = ["もぐナビ排行", "もぐナビ口碑", "mognavi_cache"]


def backup_tabs(sh) -> None:
    """寫入前把三個分頁各複製一份到「備份_分頁名」（只保留最近一次）。"""
    for name in BACKUP_TABS:
        try:
            src = sh.worksheet(name)
        except Exception:
            continue
        bname = f"備份_{name}"
        try:
            sh.del_worksheet(sh.worksheet(bname))
        except Exception:
            pass
        src.duplicate(new_sheet_name=bname)
    print("[mognavi] 寫入前已備份：" + "、".join(f"備份_{n}" for n in BACKUP_TABS))


def write_tab(sh, name: str, header: list[str], rows: list[list], mode: str = "replace"):
    try:
        ws = sh.worksheet(name)
    except Exception:
        ws = sh.add_worksheet(name, rows=max(1000, len(rows) + 10), cols=len(header) + 2)
    if mode == "replace":
        ws.clear()
        ws.update([header] + rows, "A1", value_input_option="RAW")
    else:
        if not ws.row_values(1):
            ws.update([header], "A1")
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
    ws.freeze(rows=1)
    return ws


ENC_PATH = ROOT / "docs" / "data" / "mognavi.enc.json"


def build_export(cache_rows: list[dict], rank_rows: list[dict], review_rows: list[dict]) -> dict:
    """整理成儀表板「もぐナビ口碑」頁籤用的資料（與 apps_script/Mognavi.gs 的 getMognaviData 相同結構）。"""
    def fmt_date(v):
        return str(v or "").replace("/", "-")

    reviews_by = {}
    for r in review_rows:
        pid, rid = str(r.get("mognavi_id", "")), str(r.get("review_id", ""))
        text = mognavi.clean_review(str(r.get("內容（原文摘錄）", "")))
        if not pid or len(rid) < 5 or len(text) < 8 or re.match(r"^(画像提供者|メーカー|製造者)", text):
            continue
        reviews_by.setdefault(pid, []).append({"date": fmt_date(r.get("日期")), "text": text})

    latest_week = max((str(r.get("週次", "")) for r in rank_rows), default="")
    ranks_by = {}
    for r in rank_rows:
        if str(r.get("週次", "")) != latest_week:
            continue
        pid = re.sub(r".*/product/", "", str(r.get("網址", "")))
        ranks_by.setdefault(pid, []).append({"type": r.get("排行類型", ""), "section": r.get("品類", ""),
                                             "rank": int(r["名次"]) if str(r.get("名次", "")).isdigit() else None})

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    products = []
    for c in cache_rows:
        pid = str(c.get("mognavi_id", ""))
        name = mognavi.clean_product_name(str(c.get("name_ja", "")))
        if not pid or not name:
            continue
        revs = reviews_by.get(pid)
        if not revs:
            try:
                revs = [{"date": fmt_date(x.get("date")), "text": mognavi.clean_review(str(x.get("text", "")))}
                        for x in json.loads(c.get("reviews_json") or "[]")]
                revs = [x for x in revs if len(x["text"]) >= 8 and not re.match(r"^(画像提供者|メーカー|製造者)", x["text"])]
            except (ValueError, TypeError, AttributeError):
                revs = []
        revs.sort(key=lambda x: x["date"], reverse=True)
        senti = num(c.get("sentiment")) if str(c.get("sentiment", "")) != "" else None
        products.append({
            "id": pid, "chain": str(c.get("chain", "")), "name_ja": name, "name_zh": str(c.get("name_zh", "")),
            "release_date": fmt_date(c.get("release_date")), "tabetai": int(num(c.get("tabetai")) or 0),
            "score": num(c.get("avg_score")), "review_count": int(num(c.get("review_count")) or 0),
            "summary": str(c.get("summary_zh", "")), "sentiment": senti,
            "matched_key": str(c.get("matched_key", "")), "fetched_at": fmt_date(c.get("fetched_at")),
            "ranks": ranks_by.get(pid, []), "reviews": revs[:5], "url": f"https://mognavi.jp/product/{pid}",
        })
    fetched = sorted(p["fetched_at"] for p in products if p["fetched_at"])
    return {"products": products, "rankWeek": latest_week,
            "fetchedFrom": fetched[0] if fetched else "", "fetchedTo": fetched[-1] if fetched else "",
            "generatedAt": today_tw().isoformat()}


def export_encrypted(sh) -> bool:
    """從 Sheet 讀取もぐナビ資料，加密後寫到 docs/data/mognavi.enc.json（需設定 DASHBOARD_PASSWORD）。"""
    from .secure_export import encrypt_json
    pw = os.environ.get("DASHBOARD_PASSWORD", "")
    if not pw:
        print("::warning::未設定 DASHBOARD_PASSWORD，未產生儀表板用的加密檔")
        return False
    try:
        payload = build_export(read_tab(sh, "mognavi_cache"), read_tab(sh, "もぐナビ排行"), read_tab(sh, "もぐナビ口碑"))
    except Exception as e:
        print(f"::error::讀取 Sheet 失敗，未更新加密檔：{e}")
        return False
    if not payload["products"]:
        print("::warning::Sheet 中沒有もぐナビ商品資料，未更新加密檔")
        return False
    ENC_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENC_PATH.write_text(json.dumps(encrypt_json(payload, pw, payload["fetchedTo"]), ensure_ascii=False),
                        encoding="utf-8")
    print(f"::notice::已產生加密檔 docs/data/mognavi.enc.json：{len(payload['products'])} 個商品、"
          f"排行週次 {payload['rankWeek'] or '無'}")
    return True


def cleanup(sh) -> int:
    """不連もぐナビ，只整理 Sheet 上已寫入的排行：移除側欄雜訊列、品類改成簡短名稱。"""
    try:
        rows = read_tab(sh, "もぐナビ排行")
    except Exception as e:
        print(f"::error::讀取 Sheet 失敗，不做任何變更：{e}")
        return 1
    if not rows:
        print("::notice::もぐナビ排行是空的，不需整理")
        return 0
    kept, dropped = [], 0
    for r in rows:
        sec = str(r.get("品類", ""))
        if re.match(r"^第\s*\d+\s*位", sec):   # 10/28 舊版解析的錯誤品類：保留這列，品類清空
            r["品類"] = ""
        else:
            name = mognavi.section_name(sec)
            if name is None:
                dropped += 1
                continue
            r["品類"] = name
        kept.append([r.get(h, "") for h in RANK_HEADER])
    backup_tabs(sh)
    write_tab(sh, "もぐナビ排行", RANK_HEADER, kept)
    print(f"::notice::整理完成（未連線もぐナビ）：保留 {len(kept)} 列、移除非排行雜訊 {dropped} 列")
    return 0


def main() -> int:
    cfg = ConfigStore()
    sh = cfg.sheet
    if sh is None:
        print("::error::もぐナビ資料只寫入 Google Sheet，需設定 SHEET_ID 與 GOOGLE_SERVICE_ACCOUNT_JSON")
        return 1
    forced = os.environ.get("MOGNAVI_FORCE") == "1"
    if not forced and not cfg.source_enabled("mognavi", default=False):
        print("sources 分頁中 mognavi 已停用，略過（手動執行時可選擇強制執行一次）")
        return 0
    mode = os.environ.get("MOGNAVI_MODE", "update")
    if mode == "export":
        return 0 if export_encrypted(sh) else 1
    if mode == "cleanup":
        rc = cleanup(sh)
        export_encrypted(sh)
        return rc
    if os.environ.get("MOGNAVI_MODE", "update") == "probe":
        code, msg = mognavi.probe()
        print(f"::notice::試探結果（未寫入任何資料）：{msg}")
        if code != 200:
            print(f"::warning::もぐナビ仍回應 {code}，封鎖尚未解除，請不要執行更新")
        return 0 if code == 200 else 1
    today = today_tw()
    iso = today.isocalendar()
    week = f"{iso[0]}-W{iso[1]:02d}"
    top_n = int(os.environ.get("MOGNAVI_TOP_N") or 5)
    max_products = int(os.environ.get("MOGNAVI_MAX_PRODUCTS") or 60)

    try:
        old_cache_rows = read_tab(sh, "mognavi_cache")
        old_rank_rows = read_tab(sh, "もぐナビ排行")
        old_review_rows = read_tab(sh, "もぐナビ口碑")
    except Exception as e:
        print(f"::error::讀取 Sheet 失敗，為避免覆蓋舊資料，本次不寫入：{e}")
        return 1
    cache = {str(r["mognavi_id"]): {k: str(v) for k, v in r.items()} for r in old_cache_rows}
    known = {}
    for pid, c in cache.items():
        known[pid] = {**c, "reviews": json.loads(c.get("reviews_json") or "[]")}

    ranks, details = mognavi.crawl(top_n=top_n, max_products=max_products, known=known)
    for d in mognavi.DIAG:
        print(f"::notice::{d}")
    for b in mognavi.BLOCKED:
        print(f"::warning::{b}")
    # 只把「本次真的抓到新內容」的商品算進來；沿用快取的不算
    fresh = {pid: d for pid, d in details.items()
             if d.get("fetched_at") == today.isoformat() and d.get("parser_version") == mognavi.PARSER_VERSION}
    if not ranks and not fresh:
        print("::warning::もぐナビ沒有抓到任何新資料，Sheet 完全未變更")
        return 1
    backup_tabs(sh)

    chain_of = {}
    for r in ranks:
        chain_of.setdefault(r["mognavi_id"], r["chain"])
    for pid, d in fresh.items():
        c = cache.get(pid, {f: "" for f in CACHE_FIELDS})
        upd = {"mognavi_id": pid, "chain": chain_of.get(pid, c.get("chain", "")),
               "fetched_at": d.get("fetched_at", ""), "parser_version": d.get("parser_version", "")}
        # 新值是空白就保留舊值，不用空白覆蓋
        for f in ("name_ja", "release_date", "tabetai", "avg_score", "review_count"):
            upd[f] = d.get(f) or c.get(f, "")
        if d.get("reviews"):  # 這次沒解析到口碑就保留舊的口碑
            upd["reviews_json"] = json.dumps(d["reviews"], ensure_ascii=False)
        c.update(upd)
        if not c["name_ja"]:
            c["name_ja"] = next((r["name_ja"] for r in ranks if r["mognavi_id"] == pid), "")
        cache[pid] = c

    # Gemini：沒比對過、或口碑數變多的才重跑
    with (ROOT / "data" / "products.csv").open(encoding="utf-8") as f:
        products = [p for p in csv.DictReader(f) if p.get("country") == "JP"]
    todo = [c for c in cache.values() if c["mognavi_id"] in fresh and
            (not c.get("summarized_at") or c.get("summarized_review_count") != c.get("review_count")
             or c.get("summarized_version") != mognavi.PARSER_VERSION)]
    errors = []
    if todo and os.environ.get("ENRICH_ENABLED", "1") != "0" and gemini_mode():
        try:
            resolve_model()
            for i in range(0, len(todo), 10):
                batch = todo[i:i + 10]
                for r in call_gemini(build_prompt(batch, products), MATCH_SCHEMA):
                    c = cache.get(str(r.get("mognavi_id")))
                    if not c:
                        continue
                    valid = {x["product_key"] for x in candidates(c["name_ja"], c["chain"], products)}
                    c["matched_key"] = r.get("matched_key") if r.get("matched_key") in valid else ""
                    c["match_confidence"] = f"{float(r.get('match_confidence') or 0):.2f}"
                    c["name_zh"] = r.get("name_zh", "")
                    c["summary_zh"] = r.get("summary_zh", "")
                    c["sentiment"] = f"{float(r.get('sentiment') or 0):.2f}"
                    c["summarized_review_count"] = c.get("review_count", "")
                    c["summarized_at"] = today.isoformat()
                    c["summarized_version"] = mognavi.PARSER_VERSION
        except Exception as e:
            errors.append(f"Gemini：{e}")
    print(f"[mognavi] Gemini 比對／摘要 {len(todo)} 筆待處理；錯誤：{errors or '無'}")

    # 寫入 Sheet
    rank_rows = []
    for r in ranks:
        c = cache.get(r["mognavi_id"], {})
        rank_rows.append([week, today.isoformat(), r["chain"], r["ranking_type"], r["section"], r["rank"],
                          r["name_ja"], c.get("name_zh", ""), c.get("matched_key", ""),
                          c.get("match_confidence", ""), r.get("score") or c.get("avg_score", ""),
                          c.get("review_count") or r.get("review_count", ""),
                          c.get("tabetai") or r.get("tabetai", ""), c.get("release_date", ""),
                          c.get("summary_zh", ""), c.get("sentiment", ""), r["url"], NOTE])
    if rank_rows:
        # 只取代「本週、且本次有成功抓到的那幾個排行頁」的舊列；其他週與沒抓到的頁面一律保留
        pages_ok = {(r["chain"], r["ranking_type"]) for r in ranks}
        keep = [[o.get(h, "") for h in RANK_HEADER] for o in old_rank_rows
                if str(o.get("週次")) != week or (str(o.get("超商")), str(o.get("排行類型"))) not in pages_ok]
        write_tab(sh, "もぐナビ排行", RANK_HEADER, keep + rank_rows)

    # 口碑分頁：舊資料＋本次資料合併後整頁重寫（以 review_id 去重，本次重新解析的覆蓋舊的）
    merged: dict[str, list] = {}
    refetched = {pid for pid, d in fresh.items() if d.get("reviews")}
    for o in old_review_rows:
        rid, pid = str(o.get("review_id", "")), str(o.get("mognavi_id", ""))
        if pid in refetched or len(rid) < 5 or not str(o.get("內容（原文摘錄）", "")).strip():
            continue  # 重新抓過的商品以新結果為準；舊的錯誤列（年份 ID、空白內容）一併清掉
        merged[rid] = [o.get(h, "") for h in REVIEW_HEADER]
    new_count = 0
    for pid, d in fresh.items():
        for rv in d.get("reviews", []):
            rid = str(rv["review_id"])
            new_count += rid not in merged
            merged[rid] = [rid, pid, cache.get(pid, {}).get("name_ja", ""),
                           rv.get("date", ""), rv.get("text", ""), today.isoformat()]
    rows = sorted(merged.values(), key=lambda r: (str(r[1]), str(r[3])), reverse=True)
    write_tab(sh, "もぐナビ口碑", REVIEW_HEADER, rows)
    new_reviews = [None] * new_count
    write_tab(sh, "mognavi_cache", CACHE_FIELDS, [[c.get(f, "") for f in CACHE_FIELDS] for c in cache.values()])

    print(f"::notice::寫入 Sheet：排行 {len(rank_rows)} 列、新口碑 {len(new_reviews)} 則、商品頁更新 {len(fresh)} 個、快取共 {len(cache)} 筆"
          + ("（中途被擋，只寫入已抓到的部分；舊資料保留）" if mognavi.BLOCKED else ""))
    for e in errors:
        print(f"::warning::{e}")
    export_encrypted(sh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
