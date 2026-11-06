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
| 儀表板（單頁、頁籤：使用導覽／新品總覽／熱賣訊號／日韓共同趨勢／歷週洞察／もぐナビ口碑） | `docs/index.html`，資料在 `docs/data/` |
| 設定的預設值（Sheet 初始化來源） | `config/*.csv`；`config/manual.csv` = Sheet「使用手冊」分頁 |
| 工具 | `tools/init_sheet.py`（寫入 Sheet 分頁）、`tools/probe.py`（試探新來源） |
| 公司 n8n 流程（定義檔＋API 部署） | `n8n/*.json`（Code 節點程式另存 `n8n/*.code.js`，`@file:` 引用）、`tools/deploy_n8n.py`、本機測試 `node tools/test_health.js` |

## GitHub Actions
| 工作流程 | 用途 |
|---|---|
| crawl | 每週三 07:17（台灣）自動；手動可勾 translate、填 max_translate |
| mognavi | 手動。mode：probe／update／cleanup／export／restore。**每週最多 update 一次** |
| init-sheet | confirm=YES、tabs=分頁名（逗號分隔）。改 `config/manual.csv` 後用 tabs=使用手冊 同步 |
| probe | urls=要試探的網址（空白分隔，最多 10）。回應存到 `data/probe/` |
| deploy-n8n | 改 `n8n/` 或部署程式 push 到 main 自動執行，也可手動。依流程名稱更新或新增；寄信帳號（n8n 上名稱「CVS Gmail」）找不到時只部署、不啟用 |

從工作階段觸發（GraphQL 被擋，用 REST）：
`gh api -X POST repos/wallacezheng/CVS_report/actions/workflows/<檔名>.yml/dispatches -f ref=main -f 'inputs[mode]=export'`
查狀態：`gh api 'repos/wallacezheng/CVS_report/actions/workflows/<檔名>.yml/runs?per_page=1'`。
注意：check-runs annotations 會混入舊訊息，**驗證一律看 commit 回來的資料檔**。

Secrets：GOOGLE_SERVICE_ACCOUNT_JSON、SHEET_ID、DASHBOARD_PASSWORD、N8N_API_KEY。
n8n 用 Variables：N8N_URL、N8N_ALERT_TO（必填）、N8N_ALERT_FROM、N8N_SMTP_CREDENTIAL_ID（可選）。**n8n 網址與收件信箱不可寫進 repo**（公開），只放 Variables。
工作階段的代理禁止讀寫 Actions Secrets／Variables，需請 Wallace 在 GitHub 網頁設定。
Variables（可選）：MAX_ENRICH_PER_RUN、MAX_ENRICH_PER_MONTH、MAX_GEMINI_USD_PER_MONTH、AUTO_TRANSLATE、GEMINI_MODEL、MOGNAVI_TOP_N、MOGNAVI_MAX_PRODUCTS。
空的 Variables 會以 "" 傳入，程式一律寫 `os.environ.get(X) or 預設值`。

## 不可違反的規則
- **もぐナビ**：被擋（403）時不得以換 IP、偽裝瀏覽器等方式繞過；手動、每週最多一次、間隔 ≥10 秒。資料只放 Sheet 與加密檔，不寄信、不放公開檔案。
- **金鑰**：服務帳號 JSON、密碼只放 GitHub Secrets，不進 repo、聊天或信件。
- 遵守各站 robots.txt（GS25 禁止 /api/ → 不爬）。網站封鎖我們（403、連線逾時、地區限制）時，不換 IP、不走代理或其他網路繞過；改用合規來源（新聞 API、官方授權）。7-Eleven 韓國從 GitHub 主機連線逾時（2026-11-03 確認），同樣不繞過。
- 寫 Sheet 前要能還原：もぐナビ寫入前會備份到「備份_*」分頁；讀取失敗時不寫入。
- 公開 repo：除了加密檔外的資料都是公開的，不要放內部敏感內容（含公司 n8n 網址、內部信箱；probe 公司內部網址後要刪掉回存的檔案）。
- **n8n 分工**（Wallace 2026-11-06 決定）：n8n 只做排程串接、監控通知、日後的地端模型橋接；爬蟲、もぐナビ、快照計算、加密輸出留在 GitHub，不搬到 n8n，也不從公司網路爬站（等同換 IP）。流程一律改 `n8n/*.json` 後由 deploy-n8n 部署，不在 n8n 畫面上直接改。地端模型翻譯：最後再做，方案為 n8n 取待翻譯檔→地端模型→送回 repo，翻譯後端可切換 gemini／onprem。

## 開發慣例與已知陷阱
- 測試：`python -m unittest tests.test_parsers tests.test_signals tests.test_secure_export tests.test_mognavi_cleanup`（本機需 `pip install -r requirements.txt`）。新來源要存真實頁面到 `tests/fixtures/` 做回歸測試。
- **測試不可寫入 `data/`**：CI 在正式執行前跑測試，測試若寫到真實檔案會污染資料（2026-11-03 曾有測試寫入 2 筆假標籤紀錄）。用到 TAG_HISTORY、USAGE、CACHE 等路徑的測試一律 mock 到暫存資料夾。
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
- 2026-11-02 韓國第一步：CU（`cu_kr.py`，POST productAjax.do，只收 NEW）、emart24（`emart24_kr.py`，差異化商品最新順 2 頁）已接上；GS25 robots 禁止 /api/ → 不爬；7-Eleven 韓國從 GitHub 主機連線逾時（11-03 更正：先前寫「robots 全禁」是誤判，實際上 robots 內容無法讀取）→ 不爬。已上線：首跑 481 支（CU 441、emart24 40）全部翻譯完成（US$0.51）；W45 為基準週，數字偏高（儀表板已註記）。
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
- 2026-11-03 歷週洞察頁 `#p-history`（`initHistory`／`renderHistory`）：選快照週次與對照週次，逐筆判定後續並算「後續持續」比例。快照加 kind（weekly／backfill）與 note；`tools/backfill_insights.py` 回溯產生 W43、W44（不覆蓋正式快照）；index.json 改為 [{week, kind, generated_at}]。首次對照 W44→W45：跨超商升溫 25/83、新組合 72/93、價格溢價 12/20（W43 樣本少使 W44 訊號偏多；熱賣倍數因同一份資料而必然 100%，不具意義）。
- 2026-11-03 翻譯 301 支新品（US$0.34，指令版本 aae788f0），全部商品 1,802 支皆已翻譯。
- 2026-11-06 n8n 第一個流程「[CVS_report] 每週健檢」：每週三 09:00（台灣）檢查排程爬取成功與否、資料新鮮度、各來源近 7 天新品數（0 支＝異常、低於前 4 週中位數 30%＝提醒，排除開始收錄的第一週）、本月 Gemini 花費（≥80% 提醒）、待翻譯支數，寄 HTML 信。只讀公開資料，不需 GitHub 權杖。GitHub 已確認連得到公司 n8n。**待 Wallace**：設 Variables N8N_URL、N8N_ALERT_TO；在 n8n 建 SMTP 帳號「CVS Gmail」（smtp.gmail.com:465、應用程式密碼）；之後手動執行 deploy-n8n 並試寄。
- 下一步：韓國熱賣訊號（評估見 Project 文件「來源評估與決策紀錄」：emart24 可直接做；CU／GS25 需 probe；Naver 新聞 API 需 Wallace 申請金鑰）。
- 待 Wallace：GCP 預算快訊與 Vertex AI 配額；もぐナビbiz 授權洽詢。
