// 每週健檢：n8n Code 節點的程式（部署時由 tools/deploy_n8n.py 嵌入 weekly-health.json）。
// 只讀公開資料：GitHub 執行紀錄、data/products.csv、data/gemini_usage.csv。不讀、不寄もぐナビ資料。
// 本機測試：node tools/test_health.js（用 repo 內的真實資料）。

const CHAIN_NAMES = {
  seven_jp: '7-Eleven 日本', familymart_jp: '全家 日本', lawson_jp: 'Lawson 日本',
  cu_kr: 'CU 韓國', emart24_kr: 'emart24 韓國',
};

function parseCsv(text) {
  const rows = [];
  let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else q = false;
      } else cell += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell.replace(/\r$/, '')); rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const head = rows.shift() || [];
  return rows.filter(r => r.length > 1).map(r => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ''])));
}

const DAY = 86400e3;
const tpe = d => new Date(d.getTime() + 8 * 3600e3);           // 台灣時間
const ymd = d => tpe(d).toISOString().slice(0, 10);
function isoWeek(dateStr) {
  const d = new Date(dateStr + 'T00:00:00Z');
  const day = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - day + 3);
  const y = d.getUTCFullYear();
  const w = 1 + Math.floor((d - Date.UTC(y, 0, 1)) / DAY / 7);
  return `${y}-W${String(w).padStart(2, '0')}`;
}
const median = a => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function healthCheck({ runs, productsCsv, usageCsv, now = new Date(), maxUsd = 10, dashboardUrl = '' }) {
  const today = ymd(now);
  const week = isoWeek(today);
  const issues = [];   // 異常（需要處理）
  const warns = [];    // 提醒
  const lines = [];    // [項目, 結果, 狀態]

  // 1. 排程爬取
  const list = (runs && runs.workflow_runs) || [];
  const sched = list.filter(r => r.event === 'schedule');
  const last = sched[0];
  if (!last) {
    issues.push('找不到排程爬取的執行紀錄');
    lines.push(['排程爬取', '找不到紀錄', 'bad']);
  } else {
    const ageDays = (now - new Date(last.created_at)) / DAY;
    const when = ymd(new Date(last.created_at));
    const okRun = last.status === 'completed' && last.conclusion === 'success';
    if (ageDays > 2) {
      issues.push(`最近一次排程爬取是 ${when}，本週沒有執行`);
      lines.push(['排程爬取', `最近一次 ${when}（本週未執行）`, 'bad']);
    } else if (last.status !== 'completed') {
      warns.push('本週排程爬取仍在執行中');
      lines.push(['排程爬取', `${when} 執行中`, 'warn']);
    } else if (!okRun) {
      issues.push(`本週排程爬取失敗（${last.conclusion}）`);
      lines.push(['排程爬取', `${when} 失敗（${esc(last.conclusion)}）<a href="${esc(last.html_url)}">看紀錄</a>`, 'bad']);
    } else {
      lines.push(['排程爬取', `${when} 成功`, 'ok']);
    }
  }

  // 2. 資料新鮮度與各來源新品數
  const products = parseCsv(productsCsv || '');
  const lastSeen = products.reduce((m, p) => (p.last_seen > m ? p.last_seen : m), '');
  if (!lastSeen || (new Date(today) - new Date(lastSeen)) / DAY > 2) {
    issues.push(`商品資料最後更新是 ${lastSeen || '（無）'}，資料沒有更新`);
    lines.push(['資料更新', `最後更新 ${lastSeen || '無'}`, 'bad']);
  } else {
    lines.push(['資料更新', `最後更新 ${lastSeen}`, 'ok']);
  }

  const chains = [...new Set(products.map(p => p.chain).filter(Boolean))].sort();
  const counts = {};
  const firstDay = {};
  for (const p of products) {
    if (!p.first_seen) continue;
    if (!firstDay[p.chain] || p.first_seen < firstDay[p.chain]) firstDay[p.chain] = p.first_seen;
  }
  const t = new Date(today + 'T00:00:00Z').getTime();
  const inWindow = (d, k) => { const x = new Date(d + 'T00:00:00Z').getTime(); return x > t - 7 * (k + 1) * DAY && x <= t - 7 * k * DAY; };
  const chainRows = [];
  for (const c of chains) {
    const ps = products.filter(p => p.chain === c && p.first_seen);
    const thisWeek = ps.filter(p => inWindow(p.first_seen, 0)).length;
    const base = [];
    for (let k = 1; k <= 4; k++) {
      const start = new Date(t - 7 * (k + 1) * DAY).toISOString().slice(0, 10);
      if (firstDay[c] > start) break;           // 排除開始收錄的第一週（首跑數字偏高）
      base.push(ps.filter(p => inWindow(p.first_seen, k)).length);
    }
    const med = base.length >= 2 ? median(base) : null;
    let st = 'ok', note = med === null ? '基準累積中' : `前 ${base.length} 週中位數 ${med}`;
    if (thisWeek === 0) { st = 'bad'; issues.push(`${CHAIN_NAMES[c] || c} 本週抓到 0 支新品`); }
    else if (med !== null && thisWeek < med * 0.3) { st = 'warn'; warns.push(`${CHAIN_NAMES[c] || c} 本週 ${thisWeek} 支，明顯低於平常（${med}）`); }
    counts[c] = thisWeek;
    chainRows.push([CHAIN_NAMES[c] || c, `${thisWeek} 支（${note}）`, st]);
  }

  // 3. Gemini 花費
  const month = today.slice(0, 7);
  const usd = parseCsv(usageCsv || '').filter(u => (u.date || '').startsWith(month))
    .reduce((s, u) => s + (parseFloat(u.est_usd) || 0), 0);
  const pct = maxUsd > 0 ? usd / maxUsd : 0;
  if (pct >= 1) { issues.push(`本月 Gemini 花費 US$${usd.toFixed(2)} 已達上限 US$${maxUsd}，翻譯已暫停`); lines.push(['Gemini 本月花費', `US$${usd.toFixed(2)}／上限 ${maxUsd}`, 'bad']); }
  else if (pct >= 0.8) { warns.push(`本月 Gemini 花費 US$${usd.toFixed(2)}，已達上限的 ${Math.round(pct * 100)}%`); lines.push(['Gemini 本月花費', `US$${usd.toFixed(2)}／上限 ${maxUsd}`, 'warn']); }
  else lines.push(['Gemini 本月花費', `US$${usd.toFixed(2)}／上限 ${maxUsd}`, 'ok']);

  // 4. 待翻譯
  const untranslated = products.filter(p => !p.name_zh).length;
  if (untranslated > 0) {
    warns.push(`${untranslated} 支新品尚未翻譯（GitHub Actions → crawl → 勾選 translate 執行）`);
    lines.push(['待翻譯', `${untranslated} 支`, 'warn']);
  } else lines.push(['待翻譯', '0 支', 'ok']);

  const subject = issues.length
    ? `[CVS 雷達] ${week} 健檢：⚠ ${issues.length} 項異常`
    : warns.length ? `[CVS 雷達] ${week} 健檢：正常（${warns.length} 項提醒）` : `[CVS 雷達] ${week} 健檢：正常`;

  const color = { ok: '#1a7f37', warn: '#9a6700', bad: '#cf222e' };
  const label = { ok: '正常', warn: '提醒', bad: '異常' };
  const tr = ([a, b, s]) => `<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">${esc(a)}</td><td style="padding:6px 10px;border-bottom:1px solid #eee">${b}</td><td style="padding:6px 10px;border-bottom:1px solid #eee;color:${color[s]};font-weight:600">${label[s]}</td></tr>`;
  const ul = (arr) => arr.length ? `<ul>${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
  const html = `<div style="font-family:'Noto Sans TC',sans-serif;font-size:14px;color:#222;max-width:640px">
<h2 style="font-size:18px;margin:0 0 8px">日韓超商新品雷達 ${week} 每週健檢</h2>
<p style="margin:0 0 12px;color:#555">檢查時間：${today}（台灣時間）</p>
${issues.length ? `<h3 style="font-size:15px;color:${color.bad};margin:12px 0 4px">需要處理</h3>${ul(issues)}` : ''}
${warns.length ? `<h3 style="font-size:15px;color:${color.warn};margin:12px 0 4px">提醒</h3>${ul(warns)}` : ''}
<h3 style="font-size:15px;margin:16px 0 4px">系統狀態</h3>
<table style="border-collapse:collapse;width:100%">${lines.map(tr).join('')}</table>
<h3 style="font-size:15px;margin:16px 0 4px">本週新抓到的新品（近 7 天）</h3>
<table style="border-collapse:collapse;width:100%">${chainRows.map(tr).join('')}</table>
${dashboardUrl ? `<p style="margin-top:16px"><a href="${esc(dashboardUrl)}">開啟儀表板</a></p>` : ''}
<p style="margin-top:16px;color:#888;font-size:12px">本信由公司 n8n「[CVS_report] 每週健檢」自動寄出，只使用公開資料。流程定義：GitHub repo n8n/weekly-health.json。</p>
</div>`;
  return { week, today, subject, html, issues, warns, counts, untranslated, gemini_usd: +usd.toFixed(4) };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { healthCheck, parseCsv, isoWeek };

// ---- n8n 執行入口（本機測試時 $ 不存在，不會執行） ----
if (typeof $ === 'function') {
  const result = healthCheck({
    runs: $('取得爬取紀錄').first().json,
    productsCsv: $('讀取商品資料').first().json.data,
    usageCsv: $('讀取 Gemini 用量').first().json.data,
    now: new Date(),
    maxUsd: Number('__MAX_USD__') || 10,
    dashboardUrl: '__DASHBOARD_URL__',
  });
  return [{ json: result }];
}
