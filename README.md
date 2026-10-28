# 日韓超商新品雷達（MVP）

每天自動抓取日韓便利商店的食品新品，以 Gemini 翻譯成繁體中文並分類、加口味標籤，
產出 CSV（可直接寄給同事），前端儀表板讀 CSV 顯示趨勢。

```
GitHub Actions（每天 07:17）
  爬官網新品頁 → 合併商品主檔 → 檢查下架 → Gemini 翻譯分類（有快取）
  → 套用人工校正 → 算週趨勢 → 輸出 CSV → commit 回 repo
GitHub Pages：docs/index.html 讀 docs/data/*.csv
Google Sheet：分類表、標籤字典、術語表、人工校正、來源開關、收件名單
Apps Script：每週一 09:00 寄出本週新品 CSV
```

## 來源狀態（2026-10-28 驗證）

| 來源 | 狀態 | 說明 |
|---|---|---|
| 7-Eleven 日本 | ✅ 可用 | 每週二更新；今週／来週，11 個地區，每頁可 100 筆 |
| 全家 日本 | ✅ 可用 | 每週二更新；先週／今週／来週（来週頁週五傍晚才公開） |
| Lawson 日本 | ⛔ 關閉 | 新品頁回傳錯誤，待找其他入口 |
| CU 韓國 | ⚠ 實驗 | 清單由 AJAX 載入，端點參數待首次執行確認（sources 分頁開關） |
| GS25 韓國 | ⛔ 關閉 | 官網改為單頁應用，待找資料端點 |
| Naver DataLab | 🔑 需金鑰 | 官方 API，標籤韓文同義詞的週搜尋指數 |
| もぐナビ | ⏸ 待開 | 在 sources 分頁登記排行文章網址後啟用 |

## 第一次設定

1. **建立 GitHub repo**，把這個資料夾推上去；Settings → Pages → 來源選 `main` 分支 `/docs`。
2. **Google Sheet**：建一份空白試算表 → Google Cloud 建服務帳號、下載 JSON 金鑰 →
   把 Sheet 共用給服務帳號 email（編輯者）→ 本機執行：
   ```bash
   pip install -r requirements.txt
   export SHEET_ID=<試算表 ID> GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)"
   python tools/init_sheet.py
   ```
3. **GitHub Secrets**（Settings → Secrets and variables → Actions）：
   `SHEET_ID`、`GOOGLE_SERVICE_ACCOUNT_JSON`；Gemini 二選一：
   公司禁用 API 金鑰時，給服務帳號「Vertex AI 使用者」角色並在專案啟用 Vertex AI API，
   程式會自動用服務帳號呼叫 Vertex AI；能用金鑰時則加 `GEMINI_API_KEY`。
   選填 `NAVER_CLIENT_ID`、`NAVER_CLIENT_SECRET`；
   Variables 選填 `GEMINI_MODEL`（預設 gemini-2.5-flash，請依目前可用型號調整）、`CRAWLER_UA`（建議填入公司聯絡信箱）。
4. **寄信**：Sheet → 擴充功能 → Apps Script，貼上 `apps_script/Code.gs`，
   在指令碼屬性設定 `GITHUB_RAW_BASE`、`DASHBOARD_URL`（私有 repo 再加 `GITHUB_TOKEN`），
   先執行 `sendTestToMe()` 測試，再執行 `setupTriggers()`。收件人在 `mail_list` 分頁管理。
5. Actions 頁面手動執行一次 `daily-crawl`，確認 log 中各來源筆數不是 0。

## 日常維護

- **人工校正**：看 `review_queue` 分頁（信心度 < 0.75 或未翻譯），把正確值填進 `overrides`
  （product_key, field, value）。下次執行就會套用，之後不會被 AI 覆蓋。
- **調整分類**：在 `categories` 新增／停用代碼。已翻譯的商品不會重跑；要重新分類某些商品，
  刪掉 `data/enrich_cache.csv` 裡那幾列即可。
- **加標籤**：在 `tags` 加一列，附上日文／韓文同義詞（Naver 查詢會用韓文同義詞）。
- **來源壞掉**：Actions 失敗或某來源 0 筆時，儀表板頂端「資料狀態」會顯示；可先在 `sources` 關掉。

## 資料檔

| 檔案 | 內容 |
|---|---|
| `data/products.csv` | 商品主檔（一商品一列；first_seen／last_seen／status／下架日／生命週期天數） |
| `data/weekly_category.csv` | 週 × 超商 × 分類：新品數、平均與中位數價格 |
| `data/weekly_tags.csv` | 週 × 國家 × 標籤：出現支數 |
| `data/signals.csv` | 聲量與口碑訊號（日期｜來源｜對象｜指標｜數值） |
| `data/exports/new_this_week.csv` | 本週新品中文欄位版（含 BOM，Excel 直接開），寄信附件 |
| `data/enrich_cache.csv` | Gemini 翻譯分類快取 |
| `data/raw/YYYY-MM-DD.csv` | 每日原始爬取結果（除錯與回溯用） |

## 本機測試

```bash
python -m unittest discover -s tests -v
python -m scraper.pipeline --raw tests/fixtures/raw_sample_2026-10-28.csv \
  --cache tests/fixtures/enrich_cache_sample.csv --today 2026-10-28 --no-lifecycle
python tools/build_preview.py preview.html   # 產生免伺服器的單檔預覽
```

## 已知限制

- 解析器的測試片段是依實際頁面結構重建，非官網原始 HTML；第一次連網執行後請存下實際頁面補回歸測試。
- 「下架」以商品頁 404 或被導回列表判定，每天最多檢查 60 支，從販售超過 4 週的商品開始。
- 7-Eleven 沒有「上週」頁，歷史資料從開始每日執行後才連續。
- 使用條款：目前為內部研究、低頻率（每請求間隔 2 秒）抓取公開頁面；未來對外提供訂閱前需另做合規評估。
