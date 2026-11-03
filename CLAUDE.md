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
| 各來源爬蟲（`crawl() -> list[dict]`，欄位見 `sources/__init__.py` RAW_FIELDS） | `scraper/sources/`：seven_jp、familymart_jp、lawson_jp、press_fm、press_lawson、press_sej、mognavi、cu_kr、emart24_kr |
| Gemini（Vertex AI 服務帳號）、用量紀錄、每月花費上限 | `scraper/enrich.py` |
| 熱賣訊號（新聞稿擷取、比對商品） | `scraper/signals.py` |
| 每週洞察快照（規則計算，與網站算法一致） | `scraper/insights.py` → `data/insights/` |
| もぐナビ（手動、只寫 Sheet、加密輸出到儀表板） | `scraper/mognavi_job.py`、`scraper/secure_export.py` |
| Sheet 讀寫（分類、標籤、術語、校正、來源開關） | `scraper/config_store.py` |
| 儀表板（單頁、頁籤：使用導覽／新品總覽／熱賣訊號／日韓共同趨勢／もぐナビ口碑） | `docs/index.html`，資料在 `docs/data/` |
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
- **功能或資料來源有變動時，一併更新**：儀表板「使用導覽」頁籤（`#p-guide`：網站在做什麼、每一區怎麼讀、名詞解釋、限制）、`config/manual.csv`（再用 init-sheet tabs=使用手冊 同步）、Project 文件「來源評估與決策紀錄」。（Wallace 2026-11-02 要求）

## 目前進度（每步更新）
- 2026-11-02 已完成：三家新品（7-Eleven、全家、Lawson）、三家新聞稿熱賣訊號、地區擴張、もぐナビ（加密頁籤）、密碼鎖、使用導覽頁籤、Gemini 用量紀錄與每月上限、probe 工具。
- 2026-11-02 Lawson 新聞稿已上線（19 篇 → 32 個訊號，官方銷量 9 個）。
- 2026-11-02 7-Eleven 新聞稿已上線（13 篇 → 18 個訊號，官方銷量 3 個）。**日本部分完成**：三家新品＋三家新聞稿熱賣訊號。
- 2026-11-02 韓國第一步：CU（`cu_kr.py`，POST productAjax.do，只收 NEW）、emart24（`emart24_kr.py`，差異化商品最新順 2 頁）已接上；GS25 robots 禁止 /api/、7-Eleven 韓國 robots 全禁 → 都不爬。已上線：首跑 481 支（CU 441、emart24 40）全部翻譯完成（US$0.51）；W45 為基準週，數字偏高（儀表板已註記）。
- 2026-11-02 Wallace 決定順序：① 品類×口味交叉表 → ② 標籤細分＋口感／型態，既有商品重新貼標 → ③ C 類計算指標（標籤溢價、跨超商同步、熱賣關聯、新組合、日韓對照；存活期、口碑對照待資料累積）。
  - ① 完成：`docs/index.html` 的 `renderXtab`／`renderXdet`（瀏覽器端計算，新品總覽頁「品類 × 口味」）。
  - ② 程式完成：`config/tags.csv` 加 parent 欄與口感／型態／風格／機能／價格訴求維度（約 80 個）；`enrich.retag` 依字典指紋 tag_version 自動重貼（只改標籤）；`expand_tags` 自動補父標籤；儀表板交叉表加維度選單、讀 `docs/data/tags.csv`。已重貼 1,357/1,408 支（US$1.11，思考 token 偏多；一批逾時後批量改 30）；本國風格標籤（日本商品的「日式」、韓國商品的「韓式」）由 `expand_tags` 自動剔除，既有資料已清 301 筆。
  - ③ 完成（`renderXdet`）：明細表加超商數、價格溢價（標籤商品價格中位數／品類中位數）、熱賣支數與倍數（排除聯名監修訊號）、另一國同品類占比、新組合與跨超商升溫標記，上方自動摘要。存活期、もぐナビ口碑對照待資料累積。
- 2026-11-02 ①②③ 全部完成並驗證：1,408 支全部依新字典（版本 d86b856c）重貼；使用導覽、使用手冊已同步更新。
- 2026-11-03 Wallace 提議「日韓共同趨勢」（供台灣借鏡）：新頁籤 `#p-common`（`renderCommon`）。同品類兩國占比都 ≥ 門檻（預設 10%、各 ≥3 支、品類各 ≥10 支）依共通度＝min(日, 韓) 排序；趨勢＝後半段占比－前半段，只用 ≥20 支的週、每段 ≥20 支，否則顯示累積中／樣本不足；兩國都升溫標「兩國同步升溫」。導覽、手冊、決策紀錄已同步。
- 2026-11-03 依 Wallace 回饋改版日韓共同趨勢版面：品類按鈕列（數字＝共通標籤數）→ 總覽卡片 → 單一品類用日韓並排長條對照、趨勢、實例收合（`commonRows`／`renderCommon`，選取狀態 `S.cCat`）。
- 2026-11-03 可追蹤性（Wallace 要求，三件都做）：
  - AI 指令版本：`enrich.prompt_version(fn)`＝指令函式原始碼指紋；寫入 enrich_cache（prompt_version、tag_prompt_version）、press_releases（model、prompt_version、analyzed_at）、mognavi_cache（summary_model、summary_prompt_version）、gemini_usage（prompt_version）。既有資料依 git 歷史回補（翻譯 3de9c58e、貼標 e6746ca2、新聞稿 b106d7bb）。
  - 標籤異動：`data/tag_history.csv`（retag 時 `append_tag_history`）；11-02 三批已從 git 回補 1,526 筆。
  - 每週洞察快照：`scraper/insights.py` → `data/insights/{週}.json`（並複製到 `docs/data/insights/`＋index.json）。**算法須與 `docs/index.html` 的 renderXdet／renderCommon 一致，改一邊要同步改另一邊**（定義版本 insights-v1）。
- 下一步：韓國熱賣訊號（評估見 Project 文件「來源評估與決策紀錄」：emart24 可直接做；CU／GS25 需 probe；Naver 新聞 API 需 Wallace 申請金鑰）。
- 待 Wallace：GCP 預算快訊與 Vertex AI 配額；もぐナビbiz 授權洽詢。
