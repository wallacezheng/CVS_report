"""來源試探：對指定網址各送一次 GET，記錄狀態碼並把回應存到 data/probe/，供工程端研究頁面結構。

用法（GitHub Actions → probe → Run workflow）：urls 填一個或多個網址，以空白或換行分隔，最多 10 個。
請求間隔沿用 REQUEST_INTERVAL（預設 3 秒），只抓指定網址，不跟隨頁面上的連結。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.common import fetch  # noqa: E402

OUT = ROOT / "data" / "probe"
MAX_URLS = 10


def slug(url: str) -> str:
    s = re.sub(r"^https?://", "", url)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120]


def main() -> int:
    urls = [u for u in re.split(r"\s+", os.environ.get("PROBE_URLS", "")) if u.startswith("http")][:MAX_URLS]
    if not urls:
        print("::error::沒有網址")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for u in urls:
        try:
            r = fetch(u, retries=0)
            ext = ".json" if "json" in r.headers.get("content-type", "") else ".html"
            path = OUT / (slug(u) + ext)
            path.write_bytes(r.content[:3_000_000])
            msg = f"{r.status_code} {len(r.content):>8} bytes {r.headers.get('content-type', '')} {u} → {path.name}"
            if r.url != u:
                msg += f"（轉址到 {r.url}）"
        except Exception as e:  # noqa: BLE001
            msg = f"ERR {u}：{e}"
        print(msg)
        lines.append(msg)
    with (OUT / "_log.txt").open("a", encoding="utf-8") as f:
        f.write(f"# {datetime.utcnow().isoformat()}Z\n" + "\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
