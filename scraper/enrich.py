"""翻譯＋分類＋標籤：Gemini（一次呼叫完成），結果快取在 data/enrich_cache.csv。

憑證二選一：GEMINI_API_KEY（AI Studio）或 GOOGLE_SERVICE_ACCOUNT_JSON（Vertex AI，
服務帳號需有「Vertex AI 使用者」角色）。

- 每個商品只送一次 Gemini；之後一律讀快取，所以譯名穩定、成本低。
- 術語表（glossary）與分類表、標籤字典都從 Google Sheet 帶入 prompt。
- 回傳 confidence；低於門檻的進 review_queue 讓人工確認。
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
CACHE_FIELDS = ["product_key", "name_zh", "desc_zh", "category_code", "tags", "confidence", "model"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
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


def _vertex_target() -> tuple[str, dict]:
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
           f"/publishers/google/models/{MODEL}:generateContent")
    return url, {"Authorization": f"Bearer {creds.token}"}


def gemini_mode() -> str:
    """有 GEMINI_API_KEY 用 AI Studio；否則有服務帳號就用 Vertex AI；都沒有回傳空字串。"""
    if os.environ.get("GEMINI_API_KEY"):
        return "aistudio"
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return "vertex"
    return ""


def call_gemini(prompt: str) -> list[dict]:
    if gemini_mode() == "aistudio":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
        params, headers = {"key": os.environ["GEMINI_API_KEY"]}, {}
    else:
        url, headers = _vertex_target()
        params = {}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "responseSchema": RESPONSE_SCHEMA},
    }
    for attempt in range(3):
        resp = requests.post(url, params=params, headers=headers, json=body, timeout=120)
        if resp.status_code == 200:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        if resp.status_code in (429, 500, 503):
            time.sleep(20 * (attempt + 1))
            continue
        raise RuntimeError(f"Gemini（{gemini_mode()}）{resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("Gemini 重試失敗")


def enrich(items: list[dict], categories, tags, glossary, cache: dict[str, dict]) -> list[str]:
    """補齊快取中沒有的商品。回傳錯誤訊息列表（不丟例外，讓每日流程照常產出）。"""
    todo = [i for i in items if i["product_key"] not in cache]
    errors = []
    if not todo:
        return errors
    if not gemini_mode():
        return [f"未設定 Gemini 憑證（GEMINI_API_KEY 或 GOOGLE_SERVICE_ACCOUNT_JSON），{len(todo)} 筆商品未翻譯"]
    valid_cats = {c["category_code"] for c in categories}
    valid_tags = {t["tag_zh"] for t in tags}
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
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
            }
    return errors
