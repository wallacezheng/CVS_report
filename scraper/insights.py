"""每週洞察快照：把網站上即時計算的指標，每週存一份到 data/insights/{週}.json，供日後回顧與驗證。

計算方式與 docs/index.html 一致（改任何一邊都要同步改另一邊）：
- 品類 × 標籤（renderXdet）：近 4 週（≤ 本週）、各週支數、週增減、超商數、跨超商升溫（≥2 家本週比上週多）、
  價格溢價（標籤商品價格中位數／品類中位數，≥3 支才算）、熱賣支數與倍數（不含「聯名／監修」，≥2 支才算）、
  新組合（本週有、本週以前在該品類從未出現）
- 日韓共同趨勢（renderCommon）：只用新品數 ≥20 支的週、近 4 週；品類兩國各 ≥10 支；標籤兩國各 ≥3 支且占比 ≥10%；
  共通度＝min(日占比, 韓占比)；趨勢＝後半段占比－前半段（每段 ≥20 支）
這些是規則計算，不是 AI 生成；AI 只負責商品的分類與標籤。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "insights"
DOCS_OUT = ROOT / "docs" / "data" / "insights"
DEFINITION_VERSION = "insights-v1"
EXCL = {"NON_FOOD", "ALCOHOL"}
HIT_EXCL = {"聯名／監修"}
SAME_COUNTRY_STYLE = {"日式", "韓式"}


def _tags(p) -> list[str]:
    return [t for t in (p.get("tags") or "").split("|") if t]


def _price(p) -> float:
    try:
        return float(p.get("price_tax_incl") or 0)
    except ValueError:
        return 0.0


def _is_hit(p) -> bool:
    return any(h and h not in HIT_EXCL for h in (p.get("hit_signals") or "").split("|"))


def _med(vals):
    v = [x for x in vals if x > 0]
    return median(v) if v else None


def _food(products, cc):
    return [p for p in products if (p.get("country") or "JP") == cc and p.get("category_code")
            and p["category_code"] not in EXCL]


def _recent_weeks(ps, this_week, n=4, min_count=0):
    cnt = defaultdict(int)
    for p in ps:
        if p.get("release_week"):
            cnt[p["release_week"]] += 1
    weeks = sorted(w for w, c in cnt.items() if c >= min_count and w <= this_week)
    return weeks[-n:]


def category_tags(products, cc, this_week, tag_type) -> list[dict]:
    food = _food(products, cc)
    ws = _recent_weeks(food, this_week)
    if not ws:
        return []
    wset = set(ws)
    prev = ws[ws.index(this_week) - 1] if this_week in ws and ws.index(this_week) > 0 else None
    out = []
    by_cat = defaultdict(list)
    for p in food:
        if p.get("release_week") in wset:
            by_cat[p["category_code"]].append(p)
    for cat, ps in by_cat.items():
        if len(ps) < 3:
            continue
        cat_med = _med([_price(p) for p in ps])
        hit_base = sum(_is_hit(p) for p in ps) / len(ps)
        before = {t for p in food if p["category_code"] == cat and p.get("release_week")
                  and p["release_week"] < this_week for t in _tags(p)}
        tags = {t for p in ps for t in _tags(p)}
        for t in tags:
            its = [p for p in ps if t in _tags(p)]
            if len(its) < 2:
                continue
            weekly = {w: sum(p["release_week"] == w for p in its) for w in ws}
            delta = weekly[this_week] - weekly[prev] if prev and this_week in weekly else None
            chains = sorted({p["chain"] for p in its})
            rising = [c for c in chains if prev and
                      sum(p["chain"] == c and p["release_week"] == this_week for p in its) >
                      sum(p["chain"] == c and p["release_week"] == prev for p in its)]
            med = _med([_price(p) for p in its]) if len(its) >= 3 else None
            hits = sum(_is_hit(p) for p in its)
            out.append({
                "country": cc, "category": cat, "tag": t, "tag_type": tag_type.get(t, ""),
                "n": len(its), "share": round(len(its) / len(ps), 4), "category_n": len(ps),
                "weekly": weekly, "delta": delta, "chains": chains,
                "cross_chain_rising": rising if len(rising) >= 2 else [],
                "price_premium": round(med / cat_med - 1, 4) if med and cat_med else None,
                "hits": hits,
                "hit_lift": round((hits / len(its)) / hit_base, 2) if hit_base > 0 and hits >= 2 else None,
                "new_combo": this_week in wset and weekly.get(this_week, 0) > 0 and t not in before,
            })
    return out


def _trend(its, base, ws):
    if len(ws) < 2:
        return None
    h = len(ws) // 2
    a, b = set(ws[:h]), set(ws[h:])

    def share(s):
        bb = [p for p in base if p.get("release_week") in s]
        return sum(p.get("release_week") in s for p in its) / len(bb) if len(bb) >= 20 else None
    x, y = share(a), share(b)
    return None if x is None or y is None else round(y - x, 4)


def common_trends(products, this_week, min_share=0.1) -> list[dict]:
    D = {}
    for cc in ("JP", "KR"):
        food = _food(products, cc)
        ws = _recent_weeks(food, this_week, min_count=20)
        D[cc] = (ws, [p for p in food if p.get("release_week") in set(ws)])
    if not D["JP"][1] or not D["KR"][1]:
        return []
    out = []
    for cat in {p["category_code"] for p in D["JP"][1]}:
        J = [p for p in D["JP"][1] if p["category_code"] == cat]
        K = [p for p in D["KR"][1] if p["category_code"] == cat]
        if len(J) < 10 or len(K) < 10:
            continue
        for t in {t for p in J + K for t in _tags(p)} - SAME_COUNTRY_STYLE:
            jt = [p for p in J if t in _tags(p)]
            kt = [p for p in K if t in _tags(p)]
            js, ks = len(jt) / len(J), len(kt) / len(K)
            if len(jt) < 3 or len(kt) < 3 or js < min_share or ks < min_share:
                continue
            jtr, ktr = _trend(jt, J, D["JP"][0]), _trend(kt, K, D["KR"][0])
            out.append({"category": cat, "tag": t, "score": round(min(js, ks), 4),
                        "jp_share": round(js, 4), "kr_share": round(ks, 4), "jp_n": len(jt), "kr_n": len(kt),
                        "jp_trend": jtr, "kr_trend": ktr,
                        "both_rising": bool(jtr and ktr and jtr > 0.02 and ktr > 0.02)})
    return sorted(out, key=lambda r: -r["score"])


def build_snapshot(products, this_week, tag_dict, generated_at, tag_version="") -> dict:
    tag_type = {t["tag_zh"]: t.get("tag_type", "") for t in tag_dict}
    rows = []
    for cc in ("JP", "KR"):
        rows += category_tags(products, cc, this_week, tag_type)
    common = common_trends(products, this_week)
    hl = {
        "new_combo": [r for r in rows if r["new_combo"]],
        "cross_chain_rising": [r for r in rows if r["cross_chain_rising"]],
        "price_premium": sorted([r for r in rows if r["price_premium"] is not None and r["price_premium"] >= 0.1],
                                key=lambda r: -r["price_premium"]),
        "hit_lift": sorted([r for r in rows if r["hit_lift"] and r["hit_lift"] >= 1.5], key=lambda r: -r["hit_lift"]),
        "common_both_rising": [r for r in common if r["both_rising"]],
    }
    short = lambda r: {k: r[k] for k in ("country", "category", "tag", "n", "share", "delta", "cross_chain_rising",
                                         "price_premium", "hit_lift") if k in r}
    return {
        "week": this_week, "generated_at": generated_at, "definition_version": DEFINITION_VERSION,
        "tag_version": tag_version,
        "counts": {cc: len(_food(products, cc)) for cc in ("JP", "KR")},
        "highlights": {k: [short(r) for r in v][:30] if k != "common_both_rising" else v[:30] for k, v in hl.items()},
        "category_tags": rows,
        "common_trends": common,
    }


def save_snapshot(snap: dict) -> Path:
    """同一週重跑會覆蓋該週檔案（保留最後一次）；不同週各一份，永不刪除。"""
    name = f"{snap['week']}.json"
    text = json.dumps(snap, ensure_ascii=False, indent=1)
    for d in (OUT, DOCS_OUT):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
    weeks = sorted(p.stem for p in OUT.glob("*.json"))
    (DOCS_OUT / "index.json").write_text(json.dumps({"weeks": weeks}, ensure_ascii=False), encoding="utf-8")
    return OUT / name
