"""翻譯＋分類＋標籤：Gemini（一次呼叫完成），結果快取在 data/enrich_cache.csv。

憑證二選一：GEMINI_API_KEY（AI Studio）或 GOOGLE_SERVICE_ACCOUNT_JSON（Vertex AI，
服務帳號需有「Vertex AI 使用者」角色）。

- 每個商品只送一次 Gemini；之後一律讀快取，所以譯名穩定、成本低。
- 術語表（glossary）與分類表、標籤字典都從 Google Sheet 帶入 prompt。
- 回傳 confidence；低於門檻的進 review_queue 讓人工確認。

費用控管（GitHub → Settings → Secrets and variables → Actions → Variables 可調）：
- MAX_ENRICH_PER_RUN    單次執行最多翻譯幾支（預設 200）
- MAX_ENRICH_PER_MONTH  每月最多翻譯幾支（預設 2000），超過就停到下個月
- 非食品分類（一番賞、日用品、化妝品等）直接標為 NON_FOOD，不送 Gemini
- 酒類（官網分類「お酒」或品名含啤酒、沙瓦等關鍵字）直接標為 ALCOHOL，不送 Gemini
- ENRICH_ENABLED=0 時完全不呼叫 Gemini（只爬不翻）
- 每次呼叫的 token 用量記錄在 data/gemini_usage.csv
超過上限的商品留到下次執行再翻，期間在 review_queue 顯示「未翻譯」。
"""
from __future__ import annotations

import csv
import json
import re
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "enrich_cache.csv"
CACHE_FIELDS = ["product_key", "name_zh", "desc_zh", "category_code", "tags", "confidence", "model",
                "enriched_at", "tag_version", "prompt_version", "tag_prompt_version",
                "category_version", "category_prompt_version"]
MAX_RETAG_PER_RUN = int(os.environ.get("MAX_RETAG_PER_RUN") or 1500)
RETAG_BATCH = 30  # 50 支一批曾逾時（120 秒）
USAGE = ROOT / "data" / "gemini_usage.csv"
TAG_HISTORY = ROOT / "data" / "tag_history.csv"
TAG_HISTORY_FIELDS = ["date", "product_key", "old_tags", "new_tags", "old_tag_version", "new_tag_version",
                      "reason", "model", "prompt_version"]
CATEGORY_HISTORY = ROOT / "data" / "category_history.csv"
CATEGORY_HISTORY_FIELDS = ["date", "product_key", "old_category", "new_category", "old_category_version",
                           "new_category_version", "reason", "model", "prompt_version"]
USAGE_FIELDS = ["date", "source", "calls", "items", "prompt_tokens", "output_tokens", "thought_tokens",
                "est_usd", "model", "prompt_version"]
# 每月 Gemini 花費上限（美元，估算值）。超過就不再呼叫，下個月自動恢復。
MAX_USD_PER_MONTH = float(os.environ.get("MAX_GEMINI_USD_PER_MONTH") or 10)
# 每百萬 token 單價（美元）。刻意用偏高的定價估算：3.x Flash 2027 年起的正式價，寧可高估。
PRICES = [("gemini-2.5-flash-lite", 0.10, 0.40), ("gemini-2.5-flash", 0.30, 2.50),
          ("flash-lite", 0.30, 2.50), ("flash", 1.50, 7.50)]
DEFAULT_PRICE = (1.50, 9.00)


def _price(model: str) -> tuple[float, float]:
    for key, i, o in PRICES:
        if key in (model or ""):
            return i, o
    return DEFAULT_PRICE


def _est_usd(model: str, prompt: int, output: int, thoughts: int = 0) -> float:
    i, o = _price(model)
    return (prompt * i + (output + thoughts) * o) / 1_000_000


class BudgetExceeded(RuntimeError):
    pass


def month_spend(month: str) -> float:
    """本月已記錄的估算花費（美元）。舊紀錄沒有 est_usd 時依 token 數重算。"""
    if not USAGE.exists():
        return 0.0
    total = 0.0
    with USAGE.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not str(r.get("date", "")).startswith(month):
                continue
            try:
                total += float(r.get("est_usd") or 0) or _est_usd(
                    r.get("model", ""), int(r.get("prompt_tokens") or 0), int(r.get("output_tokens") or 0),
                    int(r.get("thought_tokens") or 0))
            except ValueError:
                continue
    return total
MAX_PER_RUN = int(os.environ.get("MAX_ENRICH_PER_RUN") or 200)
MAX_PER_MONTH = int(os.environ.get("MAX_ENRICH_PER_MONTH") or 2000)
# 官網分類屬於非食品的，直接歸 NON_FOOD，不花 Gemini 費用
NON_FOOD_SOURCE_CATEGORIES = {"キャラクターくじ・エンタメ雑貨など", "日用品", "スキンケア・コスメ",
                              "コンビニエンスウェア", "생활용품"}
ALCOHOL_SOURCE_CATEGORIES = {"お酒", "주류"}
# 品名含這些字就判定為酒類（7-Eleven 官網沒有分類，只能看品名）
ALCOHOL_PATTERN = re.compile(
    r"ビール|発泡酒|サワー|チューハイ|酎ハイ|ハイボール|焼酎|日本酒|清酒|純米|吟醸|ワンカップ|"
    r"ワイン|ウイスキー|ウィスキー|梅酒|リキュール|ジン(?!ジャー)|ウォッカ|ラム酒|"
    r"ＡＬＣ|ALC\.|アルコール|氷結|ほろよい|檸檬堂|本麒麟|黒ラベル|スーパードライ|"
    r"オリオン|ビタリスト|一番搾り|プレミアムモルツ|ヱビス|エビス|ストロングゼロ|のどごし|金麦|"
    r"맥주|소주|막걸리|와인|하이볼")
# 注意：GitHub 未設定的 vars 會傳入空字串，所以用 or 判斷
# 未指定時，依序試用下列型號（新 → 舊），用第一個可用的；Flash 系列便宜、速度快，足夠做翻譯分類
MODEL_CANDIDATES = [
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
    "gemini-3-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
    "gemini-flash-latest",
]
MODEL = os.environ.get("GEMINI_MODEL") or MODEL_CANDIDATES[0]
_MODEL_RESOLVED = bool(os.environ.get("GEMINI_MODEL"))
BATCH = 20

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "product_key": {"type": "STRING"},
            "name_zh": {"type": "STRING"},
            "desc_zh": {"type": "STRING"},
            "category_code": {"type": "STRING"},
            "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["product_key", "name_zh", "category_code", "tags", "confidence"],
    },
}


def load_cache(path: Path = CACHE) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {r["product_key"]: r for r in csv.DictReader(f)}


def save_cache(cache: dict[str, dict], path: Path = CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_FIELDS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(cache):
            w.writerow(cache[k])


def build_prompt(items: list[dict], categories, tags, glossary) -> str:
    cat_lines = "\n".join(f"- {c['category_code']}：{c['name_zh']}（{c['description_zh']}）"
                          for c in categories)
    groups: dict[str, list[str]] = {}
    for t in tags:
        groups.setdefault(t.get("tag_type") or "其他", []).append(t["tag_zh"])
    tag_list = "\n".join(f"   - {k}：{'、'.join(v)}" for k, v in groups.items())
    gloss = "\n".join(f"- {g['source_term']} → {g['zh_term']}" for g in glossary)
    payload = [{"product_key": i["product_key"], "name": i["name_original"],
                "chain": i["chain"], "source_category": i.get("source_category", ""),
                "price": i.get("price_tax_incl", "")} for i in items]
    return f"""你是台灣超商商品開發顧問，負責把日本、韓國便利商店的新商品整理成繁體中文（台灣用語）資料。

對每個商品輸出：
1. name_zh：意譯品名，讓台灣讀者一看就懂內容物（例：濃厚チーズ焼きカレーおにぎり → 濃厚起司焗咖哩飯糰）。
   名店、品牌、人名保留原文或常見譯名；「監修」保留。不要加引號或括號說明。
2. desc_zh：一句 30 字內的補充說明，只寫品名看不出來的資訊（系列名、在地食材、特殊做法、地區限定）；沒有就空字串。
3. category_code：只能從下列代碼擇一：
{cat_lines}
4. tags：從下列標籤選 0～8 個，只能用清單內的字。口味／素材盡量選到最細的項目（例：草莓、哈密瓜，而不是只寫水果）；
   口感、型態、風格、機能、企劃、價格訴求、季節只在品名或說明明確看得出來時才選，不要猜：
{tag_list}
5. confidence：0～1，你對分類與翻譯的把握。看不懂品名、無法判斷是否為食品時請給 0.6 以下。

翻譯術語表（必須遵守）：
{gloss}

商品清單（JSON）：
{json.dumps(payload, ensure_ascii=False)}
"""


def prompt_version(fn) -> str:
    """AI 指令版本：取指令模板（函式原始碼）的指紋。改了指令文字就會得到新版本，不必手動編號。"""
    import hashlib
    import inspect
    return hashlib.sha1(inspect.getsource(fn).encode("utf-8")).hexdigest()[:8]


def append_tag_history(rows: list[dict]) -> None:
    """標籤異動紀錄（只增不改）：每次商品標籤被改寫時記一筆舊值與新值。"""
    if not rows:
        return
    new_file = not TAG_HISTORY.exists()
    TAG_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with TAG_HISTORY.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TAG_HISTORY_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def tag_version(tags) -> str:
    """標籤字典的指紋：字典改了（增刪標籤），已貼過標籤的商品就會在下次執行時重新貼標。"""
    import hashlib
    names = sorted(t["tag_zh"] for t in tags)
    return hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:8]


# 本國風格標籤對本國商品沒有意義（日本商品標「日式」只是雜訊）
SAME_COUNTRY_STYLE = {"JP": "日式", "KR": "韓式"}


def expand_tags(chosen, tags, country: str = "") -> str:
    """只保留字典內的標籤，並自動補上父標籤（草莓 → 水果），維持大類趨勢可比。"""
    parent = {t["tag_zh"]: (t.get("parent") or "").strip() for t in tags}
    drop = SAME_COUNTRY_STYLE.get(country, "")
    out = []
    for t in chosen:
        if t == drop:
            continue
        if t in parent and t not in out:
            out.append(t)
            p = parent[t]
            if p and p in parent and p not in out:
                out.append(p)
    return "|".join(out)


RETAG_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "product_key": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    }, "required": ["product_key", "tags"]},
}


def _retag_prompt(items: list[dict], tags) -> str:
    groups: dict[str, list[str]] = {}
    for t in tags:
        groups.setdefault(t.get("tag_type") or "其他", []).append(t["tag_zh"])
    tag_list = "\n".join(f"- {k}：{'、'.join(v)}" for k, v in groups.items())
    payload = [{"product_key": i["product_key"], "name": i.get("name_original", ""),
                "name_zh": i.get("name_zh", ""), "desc_zh": i.get("desc_zh", "")} for i in items]
    return f"""你是台灣超商商品開發顧問。為下列日本、韓國便利商店商品重新貼標籤，每支 0～8 個，只能用清單內的字。
口味／素材盡量選到最細的項目（例：草莓、哈密瓜，而不是只寫水果）；口感、型態、風格、機能、企劃、價格訴求、季節
只在品名或說明明確看得出來時才選，不要猜。

標籤清單（依維度）：
{tag_list}

商品（JSON）：
{json.dumps(payload, ensure_ascii=False)}
"""


def retag(items: list[dict], tags, cache: dict[str, dict], today: str) -> list[str]:
    """標籤字典改版後，只重新貼標籤（不重翻品名、不動分類），依 tag_version 判斷要不要重貼。"""
    ver = tag_version(tags)
    by_key = {i["product_key"]: i for i in items}
    todo = [{**by_key[k], **c} for k, c in cache.items()
            if k in by_key and c.get("model") != "rule" and c.get("category_code") not in ("", "NON_FOOD")
            and c.get("tag_version") != ver]
    if not todo:
        return []
    if os.environ.get("ENRICH_ENABLED", "1").strip() == "0" or not gemini_mode():
        return [f"標籤字典已更新，{len(todo)} 支商品待重新貼標（手動執行時勾選翻譯即可）"]
    errors = []
    if len(todo) > MAX_RETAG_PER_RUN:
        todo.sort(key=lambda i: i.get("release_date") or "", reverse=True)
        errors.append(f"重新貼標：本次上限 {MAX_RETAG_PER_RUN} 支，{len(todo) - MAX_RETAG_PER_RUN} 支留待下次")
        todo = todo[:MAX_RETAG_PER_RUN]
    try:
        resolve_model()
    except Exception as e:
        return errors + [str(e)]
    done = 0
    pv = prompt_version(_retag_prompt)
    history = []
    for i in range(0, len(todo), RETAG_BATCH):
        batch = todo[i:i + RETAG_BATCH]
        try:
            results = call_gemini(_retag_prompt(batch, tags), RETAG_SCHEMA)
        except BudgetExceeded as e:
            errors.append(str(e))
            break
        except Exception as e:
            errors.append(f"重新貼標失敗：{e}")
            continue
        for r in results:
            c = cache.get(r.get("product_key", ""))
            if c is None:
                continue
            new_tags = expand_tags(r.get("tags", []), tags, by_key.get(r.get("product_key", ""), {}).get("country", ""))
            if new_tags != c.get("tags", ""):
                history.append({"date": today, "product_key": c["product_key"], "old_tags": c.get("tags", ""),
                                "new_tags": new_tags, "old_tag_version": c.get("tag_version", ""),
                                "new_tag_version": ver, "reason": "標籤字典更新，重新貼標",
                                "model": MODEL, "prompt_version": pv})
            c["tags"] = new_tags
            c["tag_version"] = ver
            c["tag_prompt_version"] = pv
            done += 1
    append_tag_history(history)
    log_usage(today, "retag", done, pv)
    print(f"[retag] 重新貼標 {done}/{len(todo)} 支（字典版本 {ver}）")
    return errors


def category_version(categories) -> str:
    """分類表的指紋（代碼＋說明）：分類拆分或改定義時會變。"""
    import hashlib
    sig = sorted(f"{c['category_code']}={c.get('description_zh', '')}" for c in categories)
    return hashlib.sha1("|".join(sig).encode("utf-8")).hexdigest()[:8]


def split_map(categories) -> dict[str, list[str]]:
    """原分類 → 從它拆出去的新分類（categories 的 split_from 欄，多個用 | 分隔）。"""
    out: dict[str, list[str]] = {}
    for c in categories:
        for src in (c.get("split_from") or "").split("|"):
            src = src.strip()
            if src:
                out.setdefault(src, []).append(c["category_code"])
    return out


def append_category_history(rows: list[dict]) -> None:
    """分類異動紀錄（只增不改）。"""
    if not rows:
        return
    new_file = not CATEGORY_HISTORY.exists()
    CATEGORY_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with CATEGORY_HISTORY.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATEGORY_HISTORY_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


RECLASS_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "product_key": {"type": "STRING"},
        "category_code": {"type": "STRING"},
    }, "required": ["product_key", "category_code"]},
}


def _reclass_prompt(items: list[dict], categories) -> str:
    by = {c["category_code"]: c for c in categories}
    payload = [{"product_key": i["product_key"], "name": i.get("name_original", ""), "name_zh": i.get("name_zh", ""),
                "desc_zh": i.get("desc_zh", ""), "choices": i["_choices"]} for i in items]
    defs = "\n".join(f"- {k}：{by[k]['name_zh']}（{by[k].get('description_zh', '')}）"
                      for k in sorted({k for i in items for k in i["_choices"]}) if k in by)
    return f"""你是台灣超商商品分類人員。分類表新增了更細的分類，請為下列日本、韓國便利商店商品重新判定分類。
每支商品只能從它自己的 choices 裡擇一；不確定時保留原分類（choices 的第一個）。

分類定義：
{defs}

商品（JSON）：
{json.dumps(payload, ensure_ascii=False)}
"""


def reclassify(items: list[dict], categories, cache: dict[str, dict], today: str) -> list[str]:
    """分類拆分後，只重新判定受影響分類的商品（例：麵包 → 麵包／吐司），其餘商品只蓋上新版本。
    依 category_version 判斷；分類異動記到 data/category_history.csv。"""
    ver = category_version(categories)
    smap = split_map(categories)
    by_key = {i["product_key"]: i for i in items}
    todo, stamp = [], []
    for k, c in cache.items():
        if c.get("category_version") == ver or k not in by_key:
            continue
        old = c.get("category_code", "")
        if c.get("model") != "rule" and old in smap:
            todo.append({**by_key[k], **c, "_choices": [old] + smap[old]})
        else:
            stamp.append(c)
    if todo and (os.environ.get("ENRICH_ENABLED", "1").strip() == "0" or not gemini_mode()):
        return [f"分類表已更新，{len(todo)} 支商品待重新分類（手動執行時勾選翻譯即可）"]
    for c in stamp:
        c["category_version"] = ver
    if not todo:
        return []
    try:
        resolve_model()
    except Exception as e:
        return [str(e)]
    errors, history, done = [], [], 0
    pv = prompt_version(_reclass_prompt)
    for i in range(0, len(todo), RETAG_BATCH):
        batch = todo[i:i + RETAG_BATCH]
        choices = {b["product_key"]: b["_choices"] for b in batch}
        try:
            results = call_gemini(_reclass_prompt(batch, categories), RECLASS_SCHEMA)
        except BudgetExceeded as e:
            errors.append(str(e))
            break
        except Exception as e:
            errors.append(f"重新分類失敗：{e}")
            continue
        for r in results:
            key = r.get("product_key", "")
            c = cache.get(key)
            if c is None or key not in choices:
                continue
            new = r.get("category_code", "")
            if new not in choices[key]:
                new = choices[key][0]
            if new != c.get("category_code"):
                history.append({"date": today, "product_key": key, "old_category": c.get("category_code", ""),
                                "new_category": new, "old_category_version": c.get("category_version", ""),
                                "new_category_version": ver, "reason": "分類拆分，重新分類",
                                "model": MODEL, "prompt_version": pv})
                c["category_code"] = new
            c["category_version"] = ver
            c["category_prompt_version"] = pv
            done += 1
    append_category_history(history)
    log_usage(today, "reclassify", done, pv)
    print(f"[reclassify] 重新分類 {done}/{len(todo)} 支，改變 {len(history)} 支（分類表版本 {ver}）")
    return errors


def _vertex_target(model: str) -> tuple[str, dict]:
    """用服務帳號呼叫 Vertex AI 上的 Gemini（公司禁用 API 金鑰時的做法）。"""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    project = os.environ.get("GCP_PROJECT") or info["project_id"]
    location = os.environ.get("GCP_LOCATION") or "global"
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    url = (f"https://{host}/v1/projects/{project}/locations/{location}"
           f"/publishers/google/models/{model}:generateContent")
    return url, {"Authorization": f"Bearer {creds.token}"}


def gemini_mode() -> str:
    """有 GEMINI_API_KEY 用 AI Studio；否則有服務帳號就用 Vertex AI；都沒有回傳空字串。"""
    if os.environ.get("GEMINI_API_KEY"):
        return "aistudio"
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return "vertex"
    return ""


_LAST_USAGE = {"prompt": 0, "output": 0, "thoughts": 0, "calls": 0, "usd": 0.0}


def _endpoint(model: str) -> tuple[str, dict, dict]:
    if gemini_mode() == "aistudio":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        return url, {"key": os.environ["GEMINI_API_KEY"]}, {}
    url, headers = _vertex_target(model)
    return url, {}, headers


def resolve_model() -> str:
    """找出這個專案可用的 Gemini 型號（只在未指定 GEMINI_MODEL 時自動偵測，每次執行一次）。"""
    global MODEL, _MODEL_RESOLVED
    if _MODEL_RESOLVED:
        return MODEL
    tried = []
    for m in MODEL_CANDIDATES:
        url, params, headers = _endpoint(m)
        body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 5}}
        resp = requests.post(url, params=params, headers=headers, json=body, timeout=60)
        if resp.status_code == 200:
            MODEL, _MODEL_RESOLVED = m, True
            print(f"[gemini] 使用型號 {m}")
            return m
        tried.append(f"{m}:{resp.status_code}")
        if resp.status_code in (401, 403):  # 權限問題，換型號也沒用
            break
    raise RuntimeError("找不到可用的 Gemini 型號（" + "、".join(tried) +
                       "）。請到 Vertex AI Model Garden 確認可用型號，在 GitHub Variables 設 GEMINI_MODEL")


def call_gemini(prompt: str, schema: dict | None = None) -> list[dict]:
    model = resolve_model()
    from datetime import date as _date
    spent = month_spend(_date.today().isoformat()[:7]) + _LAST_USAGE["usd"]
    if spent >= MAX_USD_PER_MONTH:
        raise BudgetExceeded(f"本月 Gemini 估算花費 US${spent:.2f} 已達上限 US${MAX_USD_PER_MONTH:.2f}，"
                             "本次不再呼叫（可在 GitHub Variables 調整 MAX_GEMINI_USD_PER_MONTH）")
    url, params, headers = _endpoint(model)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "responseSchema": schema or RESPONSE_SCHEMA},
    }
    for attempt in range(3):
        resp = requests.post(url, params=params, headers=headers, json=body, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            u = data.get("usageMetadata", {})
            pt, ot, tt = (u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0),
                          u.get("thoughtsTokenCount", 0))
            _LAST_USAGE["prompt"] += pt
            _LAST_USAGE["output"] += ot
            _LAST_USAGE["thoughts"] += tt
            _LAST_USAGE["calls"] += 1
            _LAST_USAGE["usd"] += _est_usd(model, pt, ot, tt)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        if resp.status_code in (429, 500, 503):
            time.sleep(20 * (attempt + 1))
            continue
        hint = ""
        if resp.status_code == 404:
            hint = f"（找不到模型 {MODEL}：可在 GitHub Variables 設 GEMINI_MODEL 換成可用型號）"
        elif resp.status_code == 403:
            hint = "（權限不足：確認服務帳號有 Vertex AI 使用者角色、專案已啟用 Vertex AI API 並綁定帳單）"
        raise RuntimeError(f"Gemini（{gemini_mode()}）{resp.status_code}{hint}: {resp.text[:300]}")
    raise RuntimeError("Gemini 重試失敗")


def reclassify_alcohol(cache: dict[str, dict], names: dict[str, str]) -> int:
    """把先前被 Gemini 歸到飲料、但其實是酒的商品改成 ALCOHOL（不重新呼叫 Gemini）。"""
    n = 0
    for key, r in cache.items():
        if r.get("category_code") in ("DRINK", "") and ALCOHOL_PATTERN.search(names.get(key, "")):
            r["category_code"] = "ALCOHOL"
            n += 1
    return n


def _month_count(cache: dict[str, dict], month: str) -> int:
    return sum(1 for r in cache.values()
               if (r.get("enriched_at") or "").startswith(month) and r.get("model") != "rule")


def log_usage(today: str, source: str, items: int, prompt_ver: str = "") -> None:
    """把這段期間的 Gemini 用量寫一列到 data/gemini_usage.csv，然後歸零計數。沒呼叫就不寫。"""
    if not _LAST_USAGE["calls"]:
        return
    rows = []
    if USAGE.exists():
        with USAGE.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    rows.append({"date": today, "source": source, "calls": _LAST_USAGE["calls"], "items": items,
                 "prompt_tokens": _LAST_USAGE["prompt"], "output_tokens": _LAST_USAGE["output"],
                 "thought_tokens": _LAST_USAGE["thoughts"], "est_usd": f"{_LAST_USAGE['usd']:.4f}",
                 "model": MODEL, "prompt_version": prompt_ver})
    for k in _LAST_USAGE:
        _LAST_USAGE[k] = 0.0 if k == "usd" else 0
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=USAGE_FIELDS)
        w.writeheader()
        w.writerows(rows)


def enrich(items: list[dict], categories, tags, glossary, cache: dict[str, dict],
           today: str = "") -> list[str]:
    """補齊快取中沒有的商品。回傳錯誤／提醒訊息列表（不丟例外，讓每日流程照常產出）。"""
    from datetime import date as _date
    today = today or _date.today().isoformat()
    errors = []
    todo = [i for i in items if i["product_key"] not in cache]

    # 非食品、酒類不送 Gemini
    for i in todo:
        code = ""
        if i.get("source_category") in NON_FOOD_SOURCE_CATEGORIES:
            code = "NON_FOOD"
        elif i.get("source_category") in ALCOHOL_SOURCE_CATEGORIES or \
                ALCOHOL_PATTERN.search(i.get("name_original", "")):
            code = "ALCOHOL"
        if code:
            cache[i["product_key"]] = {"product_key": i["product_key"], "name_zh": "", "desc_zh": "",
                                       "category_code": code, "tags": "", "confidence": "1.00",
                                       "model": "rule", "enriched_at": today}
    todo = [i for i in todo if i["product_key"] not in cache]
    if not todo:
        return errors
    if os.environ.get("ENRICH_ENABLED", "1").strip() == "0":
        return [f"本次未開啟翻譯，{len(todo)} 支商品待翻譯（手動執行時勾選翻譯即可補上）"]
    if not gemini_mode():
        return [f"未設定 Gemini 憑證（GEMINI_API_KEY 或 GOOGLE_SERVICE_ACCOUNT_JSON），{len(todo)} 筆商品未翻譯"]

    # 費用上限：先看本月額度，再看單次額度；新上市（release_date 較新）的優先
    month_left = MAX_PER_MONTH - _month_count(cache, today[:7])
    allow = max(0, min(MAX_PER_RUN, month_left))
    if allow < len(todo):
        todo.sort(key=lambda i: i.get("release_date") or "", reverse=True)
        errors.append(f"翻譯額度：本次上限 {allow} 支（本月已用 {MAX_PER_MONTH - month_left}/{MAX_PER_MONTH}），"
                      f"{len(todo) - allow} 支留待下次")
        todo = todo[:allow]
    if not todo:
        return errors

    try:
        resolve_model()
    except Exception as e:
        return errors + [str(e)]
    valid_cats = {c["category_code"] for c in categories}
    valid_tags = {t["tag_zh"] for t in tags}
    calls = done = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            calls += 1
            results = call_gemini(build_prompt(batch, categories, tags, glossary))
        except BudgetExceeded as e:
            errors.append(str(e))
            break
        except Exception as e:
            errors.append(str(e))
            continue
        for r in results:
            key = r.get("product_key")
            if not key:
                continue
            cat = r.get("category_code") if r.get("category_code") in valid_cats else ""
            conf = float(r.get("confidence", 0) or 0)
            if not cat:
                conf = min(conf, 0.5)
            cache[key] = {
                "product_key": key,
                "name_zh": (r.get("name_zh") or "").strip(),
                "desc_zh": (r.get("desc_zh") or "").strip(),
                "category_code": cat,
                "tags": expand_tags(r.get("tags", []), tags, key.split(":")[0][-2:].upper()),
                "confidence": f"{conf:.2f}",
                "model": MODEL,
                "enriched_at": today,
                "tag_version": tag_version(tags),
                "prompt_version": prompt_version(build_prompt),
                "tag_prompt_version": prompt_version(build_prompt),
                "category_version": category_version(categories),
                "category_prompt_version": prompt_version(build_prompt),
            }
            done += 1
    log_usage(today, "translate", done, prompt_version(build_prompt))
    return errors
