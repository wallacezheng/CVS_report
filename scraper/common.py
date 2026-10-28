"""共用工具：HTTP、價格/日期解析、地區判斷。"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

TZ_TW = timezone(timedelta(hours=8))
USER_AGENT = os.environ.get("CRAWLER_UA") or \
    "DSR-CVS-Intel/0.1 (internal research; +https://github.com/wallacezheng/CVS_report)"
REQUEST_INTERVAL = float(os.environ.get("CRAWLER_INTERVAL_SEC") or 2.0)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,ko;q=0.8,en;q=0.5"})
_last_request = 0.0


def fetch(url: str, *, method: str = "GET", data: dict | None = None, timeout: int = 30,
          retries: int = 2) -> requests.Response:
    """禮貌性抓取：每次請求間隔 REQUEST_INTERVAL 秒，失敗重試。"""
    global _last_request
    for attempt in range(retries + 1):
        wait = REQUEST_INTERVAL - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            resp = _session.request(method, url, data=data, timeout=timeout)
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            return resp
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def today_tw() -> date:
    return datetime.now(TZ_TW).date()


_num = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_price(text: str, prefer_tax_incl: bool = True) -> float | None:
    """'350円（税込378円）' -> 378.0；'4,500원' -> 4500.0"""
    if not text:
        return None
    if prefer_tax_incl:
        m = re.search(r"税込\s*([\d,]+(?:\.\d+)?)", text)
        if m:
            return float(m.group(1).replace(",", ""))
    m = _num.search(text)
    return float(m.group(0).replace(",", "")) if m else None


def parse_jp_date(text: str) -> str | None:
    """'2026年10月27日（火）以降順次発売' -> '2026-10-27'"""
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def infer_year(month: int, day: int, ref: date | None = None) -> date:
    """只有月/日時推年份（處理跨年：12月抓到1月的下週商品）。"""
    ref = ref or today_tw()
    cand = date(ref.year, month, day)
    if (cand - ref).days > 180:
        cand = date(ref.year - 1, month, day)
    elif (ref - cand).days > 180:
        cand = date(ref.year + 1, month, day)
    return cand


# 日本「全國」判斷：涵蓋 8 個以上大區即視為全國販售
JP_MAJOR_AREAS = ["北海道", "東北", "関東", "甲信越", "北陸", "東海", "近畿", "関西",
                  "中国", "四国", "中国・四国", "九州", "沖縄"]


def is_regional_jp(regions: str) -> bool:
    if not regions:
        return False
    if "全国" in regions:
        return False
    parts = [p.strip() for p in re.split(r"[、,，]", regions) if p.strip()]
    majors = {p for p in parts if p in JP_MAJOR_AREAS}
    return len(majors) < 8


def normalize_space(s: str) -> str:
    return re.sub(r"[\s　]+", " ", s or "").strip()


# 都道府縣 → 7-Eleven／全家使用的大區（全家用「関西」「中国・四国」）
PREF_TO_AREA = {
    "北海道": {"北海道"},
    **{x: {"東北"} for x in ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"]},
    **{x: {"関東"} for x in ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"]},
    **{x: {"甲信越"} for x in ["山梨県", "長野県", "新潟県"]},
    **{x: {"北陸"} for x in ["富山県", "石川県", "福井県"]},
    **{x: {"東海"} for x in ["岐阜県", "静岡県", "愛知県", "三重県"]},
    **{x: {"近畿", "関西"} for x in ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"]},
    **{x: {"中国", "中国・四国"} for x in ["鳥取県", "島根県", "岡山県", "広島県", "山口県"]},
    **{x: {"四国", "中国・四国"} for x in ["徳島県", "香川県", "愛媛県", "高知県"]},
    **{x: {"九州"} for x in ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"]},
    "沖縄県": {"沖縄"}, "沖縄": {"沖縄"},
}


def _parts(regions: str) -> list[str]:
    return [p.strip() for p in re.split(r"[、,，]", regions or "") if p.strip()]


def new_areas(before: str, after: str) -> list[str]:
    """after 中有、但 before 未涵蓋的地區（縣已被大區涵蓋就不算新）。"""
    b = set(_parts(before))
    if "全国" in b:
        return []
    out = []
    for x in _parts(after):
        if x in b:
            continue
        if x in PREF_TO_AREA and (PREF_TO_AREA[x] & b):
            continue  # 這個縣所屬的大區本來就在販售範圍內
        out.append(x)  # 新的縣，或新的大區（大區可能只多出部分縣，仍算擴大）
    return out
