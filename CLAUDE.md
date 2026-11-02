# CLAUDE.md — 日韓超商新品雷達（CVS_report）交接說明

給接手的 AI 工作階段或工程師：先讀完這份，再看「目前進度」從斷點繼續。
**每完成一個步驟就更新「目前進度」並 commit**，確保中斷後能接上。

## 這是什麼
DSR（典通 智慧數據整合研究部）內部工具：每週自動抓日本超商新品 → Gemini 翻成繁中、分類、加口味標籤 →
疊上熱賣訊號（官方新聞稿銷量、地區擴張、聯名）與もぐナビ口碑 → GitHub Pages 儀表板（密碼保護）＋ Google Sheet 主控台。
負責人：Wallace。溝通、確認事項一律用**繁體中文（台灣用語）**。

## 架構
| 部分 | 位置 |
|---|---|
| 主流程（爬取→合併→生命週期→翻譯→熱賣訊號→輸出） | `scraper/pipeline.py` |
| 各來源爬蟲（`crawl() -> list[dict]`，欄位見 `sources/__init__.py` RAW_FIELDS） | `scraper/sources/`：seven_jp、familymart_jp、lawson_jp、press_fm、press_lawson、press_sej、mognavi、cu_kr（未啟用） |
| Gemini（Vertex AI 服務帳號）、用量紀錄、每月花費上限 | `scraper/enrich.py` |
| 熱賣訊號（新聞稿擷取、比對商品） | `scraper/signals.py` |
| もぐナビ（手動、只寫 Sheet、加密輸出到儀表板） | `scraper/mognavi_job.py`、`scraper/secure_export.py` |
| Sheet 讀寫（分類、標籤、術語、校正、來源開關） | `scraper/config_store.py` |
| 儀表板（單頁、頁籤：使用導覽／新品總覽／熱賣訊號／もぐナビ口碑） | `docs/index.html`，資料在 `docs/data/` |
| 設定的預設值（Sheet 初始化來源） | `config/*.csv`；`config/manual.csv` = Sheet「使用手冊」分頁 |
| 工具 | `tools/init_sheet.py`（寫入 Sheet 分頁）、`tools/probe.py`（試探新來源） |

## GitHub Actions
| 工作流程 | 用途 |
|---|---|
| crawl | 每週三 07:17（台灣）自動；手動可勾 translate、填 max_translate |
| mognavi | 手動。mode：probe／update／cleanup／export／restore。**每週最多 update 一次** |
| init-sheet | confirm=YES、tabs=分頁名（逗號分隔）。改 `config/manual.csv` 後用 tabs=使用手冊 同步 |
| probe | urls=要試探的網址（空白分隔，最多 10）。回應存到 `data/probe/` |

從工作階段觸發（GraphQL 被擋，用 REST）：
`gh api -X POST repos/wallacezheng/CVS_report/actions/workflows/<檔名>.yml/dispatches -f ref=main -f 'inputs[mode]=export'`
查狀態：`gh api 'repos/wallacezheng/CVS_report/actions/workflows/<檔名>.yml/runs?per_page=1'`。
注意：check-runs annotations 會混入舊訊息，**驗證一律看 commit 回來的資料檔**。

Secrets：GOOGLE_SERVICE_ACCOUNT_JSON、SHEET_ID、DASHBOARD_PASSWORD。
Variables（可選）：MAX_ENRICH_PER_RUN、MAX_ENRICH_PER_MONTH、MAX_GEMINI_USD_PER_MONTH、AUTO_TRANSLATE、GEMINI_MODEL、MOGNAVI_TOP_N、MOGNAVI_MAX_PRODUCTS。
空的 Variables 會以 "" 傳入，程式一律寫 `os.environ.get(X) or 預設值`。

## 不可違反的規則
- **もぐナビ**：被擋（403）時不得以換 IP、偽裝瀏覽器等方式繞過；手動、每週最多一次、間隔 ≥10 秒。資料只放 Sheet 與加密檔，不寄信、不放公開檔案。
- **金鑰**：服務帳號 JSON、密碼只放 GitHub Secrets，不進 repo、聊天或信件。
- 遵守各站 robots.txt（7-Eleven 韓國全站禁止 → 不爬）。
- 寫 Sheet 前要能還原：もぐナビ寫入前會備份到「備份_*」分頁；讀取失敗時不寫入。
- 公開 repo：除了加密檔外的資料都是公開的，不要放內部敏感內容。

## 開發慣例與已知陷阱
- 測試：`python -m unittest tests.test_parsers tests.test_signals tests.test_secure_export tests.test_mognavi_cleanup`（本機需 `pip install -r requirements.txt`）。新來源要存真實頁面到 `tests/fixtures/` 做回歸測試。
- 新來源流程：probe 抓頁面 → 研究結構 → 寫 parser＋fixture 測試 → `config/sources.csv` 開啟並用 init-sheet 同步 sources 分頁 → crawl（translate=true）實跑 → 檢查 `data/products.csv`。
- 伺服器沒宣告編碼的網站（Lawson）：`common.fetch` 已自動讀頁面 meta；若看到「ã」「æ」這類字就是亂碼。
- 地區欄位統一用 7-Eleven 大區名稱（北海道、東北、関東、甲信越、北陸、東海、近畿、中国、四国、九州、沖縄）或「全国」。
- Sheet 整理類功能必須可重複執行（2026-11-02 曾因 cleanup 重跑刪光排行，靠備份還原）。
- 儀表板改版後用 playwright 截圖檢查桌機、手機、深色模式；もぐナビ頁籤需用測試密碼產生假加密檔。

## 目前進度（每步更新）
- 2026-11-02 已完成：三家新品（7-Eleven、全家、Lawson）、全家新聞稿熱賣訊號、地區擴張、もぐナビ（加密頁籤）、密碼鎖、使用導覽頁籤、Gemini 用量紀錄與每月上限、probe 工具。
- 2026-11-02 Lawson 新聞稿已上線（19 篇 → 32 個訊號，官方銷量 9 個）。
- 2026-11-02 7-Eleven 新聞稿：`scraper/sources/press_sej.py` 已接進 `signals.update_press`（只收「商品」標籤）。**待驗證**：crawl（translate=true）後檢查 `data/press_signals.csv` 有 chain=seven_jp 的訊號。
- 下一步：日本完成 → 韓國（評估見 Project 文件「來源評估與決策紀錄」：emart24 可直接做；CU／GS25 需 probe；Naver 新聞 API 需 Wallace 申請金鑰）。
- 待 Wallace：GCP 預算快訊與 Vertex AI 配額；もぐナビbiz 授權洽詢。
