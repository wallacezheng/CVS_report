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
- 每次呼叫的 token 用量記錄在 data/gemini_usage.csv
超過上限的商品留到下次執行再翻，期間在 review_queue 顯示「未翻譯」。
"""
from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "enrich_cache.csv"
CACHE_FIELDS = ["product_key", "name_zh", "desc_zh", "category_code", "tags", "confidence", "model",
                "enriched_at"]
USAGE = ROOT / "data" / "gemini_usage.csv"
USAGE_FIELDS = ["date", "calls", "items", "prompt_tokens", "output_tokens", "model"]
MAX_PER_RUN = int(os.environ.get("MAX_ENRICH_PER_RUN") or 200)
MAX_PER_MONTH = int(os.environ.get("MAX_ENRICH_PER_MONTH") or 2000)
# 官網分類屬於非食品的，直接歸 NON_FOOD，不花 Gemini 費用
NON_FOOD_SOURCE_CATEGORIES = {"キャラクターくじ・エンタメ雑貨など", "日用品", "スキンケア・コスメ",
                              "コンビニエンスウェア", "お酒", "생활용품"}
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
    tag_list = "、".join(t["tag_zh"] for t in tags)
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
4. tags：從下列標籤選 0～5 個，只能用清單內的字：{tag_list}
5. confidence：0～1，你對分類與翻譯的把握。看不懂品名、無法判斷是否為食品時請給 0.6 以下。

翻譯術語表（必須遵守）：
{gloss}

商品清單（JSON）：
{json.dumps(payload, ensure_ascii=False)}
"""


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


_LAST_USAGE = {"prompt": 0, "output": 0}


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


def call_gemini(prompt: str) -> list[dict]:
    model = resolve_model()
    url, params, headers = _endpoint(model)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "responseSchema": RESPONSE_SCHEMA},
    }
    for attempt in range(3):
        resp = requests.post(url, params=params, headers=headers, json=body, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            u = data.get("usageMetadata", {})
            _LAST_USAGE["prompt"] += u.get("promptTokenCount", 0)
            _LAST_USAGE["output"] += u.get("candidatesTokenCount", 0)
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


def _month_count(cache: dict[str, dict], month: str) -> int:
    return sum(1 for r in cache.values()
               if (r.get("enriched_at") or "").startswith(month) and r.get("model") != "rule")


def _log_usage(today: str, calls: int, items: int) -> None:
    rows = []
    if USAGE.exists():
        with USAGE.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    rows.append({"date": today, "calls": calls, "items": items,
                 "prompt_tokens": _LAST_USAGE["prompt"], "output_tokens": _LAST_USAGE["output"],
                 "model": MODEL})
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

    # 非食品不送 Gemini
    for i in [i for i in todo if i.get("source_category") in NON_FOOD_SOURCE_CATEGORIES]:
        cache[i["product_key"]] = {"product_key": i["product_key"], "name_zh": "", "desc_zh": "",
                                   "category_code": "NON_FOOD", "tags": "", "confidence": "1.00",
                                   "model": "rule", "enriched_at": today}
    todo = [i for i in todo if i["product_key"] not in cache]
    if not todo:
        return errors
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
                "tags": "|".join(t for t in r.get("tags", []) if t in valid_tags),
                "confidence": f"{conf:.2f}",
                "model": MODEL,
                "enriched_at": today,
            }
            done += 1
    _log_usage(today, calls, done)
    return errors
