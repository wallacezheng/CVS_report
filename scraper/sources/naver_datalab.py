"""韓國聲量：Naver DataLab 搜尋趨勢 API（官方，需申請 Client ID/Secret）

申請：https://developers.naver.com/ → Application 註冊 → 勾選「데이터랩(검색어트렌드)」
每日呼叫上限 1,000 次；每次最多 5 個關鍵字群組。
回傳的是相對指數（期間內最高 = 100），所以每次查詢都帶固定的基準詞，
不同批次之間才能比較（基準詞預設「편의점」）。
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

import requests

from ..common import today_tw

API = "https://openapi.naver.com/v1/datalab/search"
ANCHOR = {"groupName": "__anchor__", "keywords": ["편의점"]}


def crawl(tag_keywords: dict[str, list[str]], weeks: int = 12) -> list[dict]:
    """tag_keywords: {'地瓜': ['고구마', ...]} → signals 列表（週資料）"""
    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not secret:
        return []
    end = today_tw()
    start = end - timedelta(weeks=weeks)
    items = [(k, v[:5]) for k, v in tag_keywords.items() if v]
    out = []
    for i in range(0, len(items), 4):  # 4 個標籤 + 1 個基準詞
        batch = items[i:i + 4]
        body = {
            "startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "week",
            "keywordGroups": [ANCHOR] + [{"groupName": k, "keywords": kws} for k, kws in batch],
        }
        resp = requests.post(API, data=json.dumps(body), timeout=30, headers={
            "X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret,
            "Content-Type": "application/json"})
        if resp.status_code != 200:
            continue
        for res in resp.json().get("results", []):
            if res["title"] == "__anchor__":
                continue
            for d in res.get("data", []):
                out.append({"date": d["period"], "country": "KR", "source": "naver_datalab",
                            "target_type": "tag", "target": res["title"],
                            "metric": "search_index", "value": d["ratio"]})
    return out
