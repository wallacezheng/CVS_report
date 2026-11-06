// 本機測試 n8n 每週健檢邏輯：node tools/test_health.js [執行紀錄.json] [日期 YYYY-MM-DD]
// 用 repo 內的真實資料；執行紀錄預設為假的「今天成功」。結果的 HTML 寫到 stdout 以外的檔案不做（測試不可寫 data/）。
const fs = require('fs');
const path = require('path');
const { healthCheck, isoWeek } = require('../n8n/weekly-health.code.js');
const root = path.join(__dirname, '..');
const now = process.argv[3] ? new Date(process.argv[3] + 'T01:00:00Z') : new Date();
const runs = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
  : { workflow_runs: [{ event: 'schedule', status: 'completed', conclusion: 'success', created_at: now.toISOString(), html_url: 'https://example.invalid' }] };
const r = healthCheck({
  runs,
  productsCsv: fs.readFileSync(path.join(root, 'data/products.csv'), 'utf8'),
  usageCsv: fs.readFileSync(path.join(root, 'data/gemini_usage.csv'), 'utf8'),
  now, maxUsd: 10,
});
const assert = require('assert');
assert.strictEqual(isoWeek('2026-11-04'), '2026-W45');
assert.strictEqual(isoWeek('2027-01-01'), '2026-W53');
assert.strictEqual(isoWeek('2026-01-01'), '2026-W01');
console.log(JSON.stringify({ ...r, html: `(${r.html.length} chars)` }, null, 1));
if (process.env.HTML_OUT) fs.writeFileSync(process.env.HTML_OUT, r.html);
