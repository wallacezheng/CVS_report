"""每日流程：爬取 → 合併商品主檔 → 生命週期 → 翻譯分類 → 人工校正 → 趨勢 → 輸出 CSV。

用法：
  python -m scraper.pipeline                       # 正式（GitHub Actions）
  python -m scraper.pipeline --raw FILE --today 2026-10-28 --no-lifecycle   # 離線重跑
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import traceback
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from .common import fetch, is_regional_jp, today_tw
from .config_store import ConfigStore
from .enrich import enrich, load_cache, reclassify_alcohol, save_cache
from .sources import RAW_FIELDS, cu_kr, familymart_jp, mognavi, naver_datalab, seven_jp

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS_DATA = ROOT / "docs" / "data"

CHAIN_ZH = {"seven_jp": "7-Eleven 日本", "familymart_jp": "全家 日本", "lawson_jp": "Lawson 日本",
            "cu_kr": "CU 韓國", "gs25_kr": "GS25 韓國", "seven_kr": "7-Eleven 韓國",
            "emart24_kr": "emart24 韓國"}
COUNTRY_ZH = {"JP": "日本", "KR": "韓國"}
PRODUCT_FIELDS = [
    "product_key", "country", "chain", "item_id", "name_original", "name_zh", "desc_zh",
    "category_code", "tags", "price_tax_incl", "currency", "release_date", "release_week",
    "regions", "is_regional", "source_category", "source_url", "first_seen", "last_seen",
    "times_listed", "status", "delisted_date", "lifecycle_days", "confidence", "needs_review",
]
REVIEW_THRESHOLD = 0.75
EXCLUDE_FROM_TRENDS = {"NON_FOOD", "ALCOHOL"}
NEW_WINDOW_DAYS = 28


# ---------- 小工具 ----------
def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str], bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def iso_week(d: str) -> str:
    if not d:
        return ""
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


# ---------- 1. 爬取 ----------
def crawl_all(cfg: ConfigStore, errors: list[str]) -> list[dict]:
    rows = []
    jobs = [("seven_jp", seven_jp.crawl), ("familymart_jp", familymart_jp.crawl),
            ("cu_kr", cu_kr.crawl)]
    for sid, fn in jobs:
        if not cfg.source_enabled(sid, default=(sid != "cu_kr")):
            continue
        try:
            got = fn()
            print(f"[crawl] {sid}: {len(got)} 筆")
            if not got:
                errors.append(f"{sid} 抓到 0 筆，可能網站改版")
            rows.extend(got)
        except Exception as e:
            errors.append(f"{sid} 爬取失敗：{e}")
            traceback.print_exc()
    return rows


def crawl_signals(cfg: ConfigStore, errors: list[str]) -> list[dict]:
    out = []
    try:
        if cfg.source_enabled("naver_datalab"):
            kw = {t["tag_zh"]: [s for s in str(t.get("synonyms_ko", "")).split("|") if s]
                  for t in cfg.tags()}
            out.extend(naver_datalab.crawl(kw))
    except Exception as e:
        errors.append(f"naver_datalab 失敗：{e}")
    try:
        out.extend(mognavi.crawl(cfg.ranking_urls("mognavi_ranking")))
    except Exception as e:
        errors.append(f"mognavi 失敗：{e}")
    return out


# ---------- 2. 合併商品主檔 ----------
def merge_products(master: dict[str, dict], raw: list[dict], today: date) -> None:
    t = today.isoformat()
    for r in raw:
        key = f"{r['chain']}:{r['item_id']}"
        p = master.get(key)
        if p is None:
            p = {f: "" for f in PRODUCT_FIELDS}
            p.update({"product_key": key, "first_seen": t, "times_listed": "0"})
            master[key] = p
        for f in ("country", "chain", "item_id", "name_original", "currency", "source_category",
                  "source_url"):
            if r.get(f):
                p[f] = r[f]
        if r.get("price_tax_incl") not in (None, ""):
            p["price_tax_incl"] = str(r["price_tax_incl"])
        # 同一商品在新地區再次上市：保留最早上市日，地區取最新
        if r.get("release_date"):
            if not p["release_date"] or r["release_date"] < p["release_date"]:
                p["release_date"] = r["release_date"]
        if r.get("regions"):
            p["regions"] = r["regions"]
        p["is_regional"] = "1" if (p["country"] == "JP" and is_regional_jp(p["regions"])) else "0"
        p["last_seen"] = t
        p["times_listed"] = str(int(p.get("times_listed") or 0) + 1)
        if p.get("status") == "delisted":  # 又出現在新品頁 → 復活
            p["status"], p["delisted_date"], p["lifecycle_days"] = "", "", ""


def update_status(master: dict[str, dict], today: date) -> None:
    for p in master.values():
        if p.get("status") == "delisted":
            continue
        rd = p.get("release_date") or p.get("first_seen")
        p["release_week"] = iso_week(rd)
        if rd and date.fromisoformat(rd) > today:
            p["status"] = "upcoming"
        elif rd and (today - date.fromisoformat(rd)).days <= NEW_WINDOW_DAYS:
            p["status"] = "new"
        else:
            p["status"] = "active"


# ---------- 3. 生命週期（下架偵測） ----------
def check_delisted(master: dict[str, dict], today: date, limit: int = 60) -> int:
    """新品頁消失不代表下架，所以回頭檢查商品頁本身：404 或被導回列表頁即判定下架。"""
    cands = [p for p in master.values()
             if p.get("status") == "active" and p.get("source_url") and p["chain"].endswith("_jp")]
    cands.sort(key=lambda p: p.get("last_checked", ""))
    n = 0
    for p in cands[:limit]:
        try:
            resp = fetch(p["source_url"])
        except Exception:
            continue
        gone = resp.status_code in (404, 410) or (
            resp.status_code == 200 and resp.url.rstrip("/").endswith(("goods.html", "products")))
        if gone:
            p["status"] = "delisted"
            p["delisted_date"] = today.isoformat()
            if p.get("release_date"):
                p["lifecycle_days"] = str((today - date.fromisoformat(p["release_date"])).days)
            n += 1
    return n


# ---------- 4. 翻譯分類 + 人工校正 ----------
def apply_enrichment(master, cache, overrides) -> list[dict]:
    ov = defaultdict(dict)
    for o in overrides:
        if o.get("product_key") and o.get("field"):
            ov[o["product_key"]][o["field"]] = o.get("value", "")
    review = []
    for key, p in master.items():
        e = cache.get(key, {})
        for f in ("name_zh", "desc_zh", "category_code", "tags", "confidence"):
            p[f] = e.get(f, p.get(f, ""))
        manual = ov.get(key, {})
        for f, v in manual.items():
            if f in p:
                p[f] = v
        conf = float(p.get("confidence") or 0)
        needs = (not manual) and (not e or conf < REVIEW_THRESHOLD or not p.get("category_code"))
        p["needs_review"] = "1" if needs else "0"
        if needs:
            review.append({**p, "reason": "未翻譯" if not e else f"信心度 {conf:.2f}",
                           "added": today_tw().isoformat()})
    return review


# ---------- 5. 趨勢 ----------
def build_trends(products: list[dict], cats: dict[str, dict]):
    food = [p for p in products if p.get("category_code") and p["category_code"] not in EXCLUDE_FROM_TRENDS]
    by_cat = defaultdict(list)
    tag_count = Counter()
    for p in food:
        wk = p.get("release_week")
        if not wk:
            continue
        price = float(p["price_tax_incl"]) if p.get("price_tax_incl") else None
        by_cat[(wk, p["country"], p["chain"], p["category_code"])].append(price)
        for t in filter(None, (p.get("tags") or "").split("|")):
            tag_count[(wk, p["country"], t)] += 1
    cat_rows = []
    for (wk, c, ch, code), prices in sorted(by_cat.items()):
        ps = [x for x in prices if x is not None]
        cat_rows.append({
            "week": wk, "country": c, "chain": ch, "category_code": code,
            "category_zh": cats.get(code, {}).get("name_zh", code),
            "category_group": cats.get(code, {}).get("group_zh", ""),
            "new_count": len(prices),
            "avg_price": round(statistics.mean(ps), 1) if ps else "",
            "median_price": round(statistics.median(ps), 1) if ps else "",
        })
    tag_rows = [{"week": wk, "country": c, "tag": t, "count": n}
                for (wk, c, t), n in sorted(tag_count.items())]
    return cat_rows, tag_rows


# ---------- 6. 輸出 ----------
EXPORT_HEADERS = [
    ("country_zh", "國家"), ("chain_zh", "超商"), ("category_zh", "分類"), ("name_zh", "品名（中譯）"),
    ("name_original", "原文品名"), ("desc_zh", "說明"), ("tags", "口味／素材標籤"),
    ("price_tax_incl", "含稅售價"), ("currency", "幣別"), ("release_date", "發售日"),
    ("regions", "販售地區"), ("is_regional_zh", "地區限定"), ("status_zh", "狀態"),
    ("source_url", "來源網址"),
]
STATUS_ZH = {"upcoming": "即將上市", "new": "新品", "active": "販售中", "delisted": "已下架"}


def export_for_mail(products: list[dict], cats: dict, week: str, path: Path) -> None:
    rows = []
    for p in products:
        if p.get("release_week") != week or p.get("category_code") in ("", "NON_FOOD"):
            continue
        rows.append({**p,
                     "country_zh": COUNTRY_ZH.get(p["country"], p["country"]),
                     "chain_zh": CHAIN_ZH.get(p["chain"], p["chain"]),
                     "category_zh": cats.get(p["category_code"], {}).get("name_zh", ""),
                     "tags": (p.get("tags") or "").replace("|", "、"),
                     "is_regional_zh": "是" if p.get("is_regional") == "1" else "",
                     "status_zh": STATUS_ZH.get(p.get("status"), "")})
    rows.sort(key=lambda r: (r["country_zh"], r["chain_zh"], r["category_zh"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM：Excel 直接開不亂碼
        w = csv.writer(f)
        w.writerow([h for _, h in EXPORT_HEADERS])
        for r in rows:
            w.writerow([r.get(k, "") for k, _ in EXPORT_HEADERS])


def run(args) -> int:
    today = date.fromisoformat(args.today) if args.today else today_tw()
    errors: list[str] = []
    cfg = ConfigStore()
    cats = {c["category_code"]: c for c in cfg.categories()}

    # 1. 爬取（或讀離線檔）
    raw = read_csv(Path(args.raw)) if args.raw else crawl_all(cfg, errors)
    if raw and not args.raw:
        write_csv(DATA / "raw" / f"{today.isoformat()}.csv", raw, RAW_FIELDS)

    # 2. 主檔
    master = {r["product_key"]: r for r in read_csv(DATA / "products.csv")}
    merge_products(master, raw, today)
    update_status(master, today)

    # 3. 生命週期
    if not args.no_lifecycle:
        n = check_delisted(master, today)
        print(f"[lifecycle] 判定下架 {n} 筆")

    # 4. 翻譯分類
    cache = load_cache(Path(args.cache)) if args.cache else load_cache()
    n_alc = reclassify_alcohol(cache, {k: p.get("name_original", "") for k, p in master.items()})
    if n_alc:
        print(f"[enrich] 改歸酒類 {n_alc} 支")
    errors += enrich(list(master.values()), cfg.categories(), cfg.tags(), cfg.glossary(), cache,
                     today=today.isoformat())
    save_cache(cache)
    review = apply_enrichment(master, cache, cfg.overrides())
    try:
        cfg.write_review_queue(review)
    except Exception as e:
        errors.append(f"寫入 review_queue 失敗：{e}")
    write_csv(DATA / "review_queue.csv", review,
              ["product_key", "name_original", "name_zh", "category_code", "tags", "confidence",
               "reason", "source_url", "added"])

    products = sorted(master.values(), key=lambda p: (p.get("release_date") or "", p["product_key"]),
                      reverse=True)
    write_csv(DATA / "products.csv", products, PRODUCT_FIELDS)

    # 5. 訊號與趨勢
    signals = read_csv(DATA / "signals.csv")
    if not args.raw:
        signals += crawl_signals(cfg, errors)
    sig_fields = ["date", "country", "source", "target_type", "target", "metric", "value", "chain"]
    seen, dedup = set(), []
    for s in signals:
        k = (s.get("date"), s.get("source"), s.get("target"), s.get("metric"))
        if k not in seen:
            seen.add(k)
            dedup.append(s)
    write_csv(DATA / "signals.csv", dedup, sig_fields)
    cat_rows, tag_rows = build_trends(products, cats)
    write_csv(DATA / "weekly_category.csv", cat_rows,
              ["week", "country", "chain", "category_code", "category_zh", "category_group",
               "new_count", "avg_price", "median_price"])
    write_csv(DATA / "weekly_tags.csv", tag_rows, ["week", "country", "tag", "count"])

    # 6. 前端與寄信用檔案
    this_week = iso_week(today.isoformat())
    export_for_mail(products, cats, this_week, DATA / "exports" / f"new_{this_week}.csv")
    export_for_mail(products, cats, this_week, DATA / "exports" / "new_this_week.csv")
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    for name in ("products.csv", "weekly_category.csv", "weekly_tags.csv", "signals.csv"):
        (DOCS_DATA / name).write_bytes((DATA / name).read_bytes())
    (DOCS_DATA / "categories.csv").write_bytes(
        "\n".join([",".join(["category_code", "group_zh", "name_zh", "sort"])] +
                  [",".join([c["category_code"], c["group_zh"], c["name_zh"], str(c["sort"])])
                   for c in cfg.categories()]).encode("utf-8"))
    errors = list(dict.fromkeys(errors))
    meta = {
        "updated_at": today.isoformat(), "this_week": this_week,
        "product_count": len(products),
        "review_count": len(review), "errors": errors,
        "sources": {s["source_id"]: s.get("enabled") for s in cfg.data["sources"]},
    }
    (DOCS_DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", help="離線模式：讀取既有 raw CSV，不連網爬取")
    ap.add_argument("--cache", help="指定翻譯快取來源（預設 data/enrich_cache.csv）")
    ap.add_argument("--today", help="指定今天日期 YYYY-MM-DD（測試用）")
    ap.add_argument("--no-lifecycle", action="store_true", help="略過下架檢查")
    raise SystemExit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
