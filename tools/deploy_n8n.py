"""把 n8n/*.json 的流程定義部署到公司 n8n（透過 n8n 公開 API）。

環境變數（GitHub Actions 帶入）：
  N8N_URL            n8n 網址（Variables），例：https://n8n.dataa.com.tw
  N8N_API_KEY        n8n API 金鑰（Secrets）
  N8N_ALERT_TO       健檢信收件人（Variables，不寫進公開 repo）
  N8N_ALERT_FROM     寄件人（可選，預設同收件人；Gmail 會以登入帳號寄出）
  N8N_SMTP_CREDENTIAL_ID  寄信帳號 ID（可選；API 無法列出帳號時才需要）
  MAX_GEMINI_USD_PER_MONTH、DASHBOARD_URL（可選）

做法：依流程名稱找 n8n 上既有的流程 → 有就更新、沒有就新增；寄信帳號依名稱對應，
找到才啟用排程，找不到就只部署、不啟用（避免排程跑了卻寄不出信）。
--dry-run：只印出組好的流程，不連 n8n（本機檢查用）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FLOW_DIR = ROOT / "n8n"
DEFAULT_DASHBOARD = "https://wallacezheng.github.io/CVS_report/"


def env(name, default=""):
    return os.environ.get(name) or default


def build(path: Path) -> dict:
    flow = json.loads(path.read_text(encoding="utf-8"))
    to = env("N8N_ALERT_TO")
    subs = {
        "__ALERT_TO__": to,
        "__ALERT_FROM__": env("N8N_ALERT_FROM", to),
        "__MAX_USD__": env("MAX_GEMINI_USD_PER_MONTH", "10"),
        "__DASHBOARD_URL__": env("DASHBOARD_URL", DEFAULT_DASHBOARD),
    }
    for node in flow["nodes"]:
        p = node.get("parameters", {})
        code = p.get("jsCode", "")
        if code.startswith("@file:"):
            p["jsCode"] = (path.parent / code[6:]).read_text(encoding="utf-8")
    text = json.dumps(flow, ensure_ascii=False)
    for k, v in subs.items():
        text = text.replace(k, v.replace("\\", "\\\\").replace('"', '\\"'))
    flow = json.loads(text)
    flow.pop("_comment", None)
    return flow


class N8n:
    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/api/v1"
        self.s = requests.Session()
        self.s.headers.update({"X-N8N-API-KEY": key, "Accept": "application/json"})

    def req(self, method, path, **kw):
        r = self.s.request(method, self.base + path, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} → {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}

    def find_workflow(self, name):
        cursor = None
        while True:
            q = {"limit": 100, **({"cursor": cursor} if cursor else {})}
            d = self.req("GET", "/workflows", params=q)
            for w in d.get("data", []):
                if w.get("name") == name:
                    return w
            cursor = d.get("nextCursor")
            if not cursor:
                return None

    def credentials_by_name(self):
        try:
            d = self.req("GET", "/credentials", params={"limit": 100})
        except RuntimeError as e:
            print(f"（無法列出寄信帳號，改用 N8N_SMTP_CREDENTIAL_ID：{e}）")
            return {}
        return {(c.get("name"), c.get("type")): c["id"] for c in d.get("data", [])}


def attach_credentials(flow, creds) -> list[str]:
    """把 credentials 的名稱換成 n8n 上的 ID；回傳找不到的帳號名稱。"""
    missing = []
    for node in flow["nodes"]:
        for ctype, ref in list((node.get("credentials") or {}).items()):
            cid = creds.get((ref["name"], ctype)) or (env("N8N_SMTP_CREDENTIAL_ID") if ctype == "smtp" else "")
            if cid:
                node["credentials"][ctype] = {"id": cid, "name": ref["name"]}
            else:
                missing.append(f"{ref['name']}（類型 {ctype}）")
                del node["credentials"][ctype]
        if node.get("credentials") == {}:
            node.pop("credentials")
    return missing


def main():
    dry = "--dry-run" in sys.argv
    flows = [build(p) for p in sorted(FLOW_DIR.glob("*.json"))]
    if dry:
        for f in flows:
            print(json.dumps(f, ensure_ascii=False, indent=1)[:3000])
        return
    for need in ("N8N_URL", "N8N_API_KEY", "N8N_ALERT_TO"):
        if not env(need):
            sys.exit(f"缺少 {need}（請設定 GitHub Secrets／Variables）")
    api = N8n(env("N8N_URL"), env("N8N_API_KEY"))
    creds = api.credentials_by_name()
    failed = False
    for flow in flows:
        missing = attach_credentials(flow, creds)
        body = {k: flow[k] for k in ("name", "nodes", "connections", "settings")}
        old = api.find_workflow(flow["name"])
        if old:
            wid = old["id"]
            api.req("PUT", f"/workflows/{wid}", json=body)
            print(f"更新：{flow['name']}（{wid}）")
        else:
            wid = api.req("POST", "/workflows", json=body)["id"]
            print(f"新增：{flow['name']}（{wid}）")
        if missing:
            print(f"⚠ 找不到寄信帳號：{'、'.join(missing)}。已部署但未啟用排程；在 n8n 建立同名帳號後重新部署。")
            try:
                api.req("POST", f"/workflows/{wid}/deactivate")
            except RuntimeError:
                pass
            failed = True
        else:
            api.req("POST", f"/workflows/{wid}/activate")
            print("已啟用排程")
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
