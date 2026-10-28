"""把 docs/data 的資料嵌進 docs/index.html，產生單檔預覽（不需伺服器即可開啟）。

  python tools/build_preview.py [輸出路徑] [樣本說明文字]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs" / "data"
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "preview.html"
note = sys.argv[2] if len(sys.argv) > 2 else ""

meta = json.loads((D / "meta.json").read_text(encoding="utf-8"))
if note:
    meta["sample"] = note
embed = {k: (D / f"{k}.csv").read_text(encoding="utf-8")
         for k in ("products", "categories", "weekly_category", "weekly_tags", "signals")}
embed["meta"] = meta
html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
script = "<script>window.EMBED=" + json.dumps(embed, ensure_ascii=False).replace("</", "<\\/") + ";</script>\n"
html = html.replace("<script>\nconst CHAIN_ZH", script + "<script>\nconst CHAIN_ZH", 1)
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
