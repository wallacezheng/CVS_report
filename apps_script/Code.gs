/**
 * 日韓超商食品情報｜每週寄信（Google Apps Script，綁在同一份 Google Sheet）
 *
 * 1. Sheet 新增分頁 mail_list，欄位：email | name | frequency(weekly/daily) | enabled(1/0) | note
 * 2. 專案設定 → 指令碼屬性：
 *      GITHUB_RAW_BASE  例：https://raw.githubusercontent.com/<帳號>/<repo>/main
 *      DASHBOARD_URL    例：https://<帳號>.github.io/<repo>/
 *      GITHUB_TOKEN     （repo 為私有時才需要，fine-grained token，只給 Contents: read）
 * 3. 執行一次 setupTriggers() 授權並建立排程（每週一 09:00；每日名單 08:30）
 */

const SHEET_MAIL = 'mail_list';

function setupTriggers() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('sendWeekly').timeBased().onWeekDay(ScriptApp.WeekDay.MONDAY).atHour(9).create();
  ScriptApp.newTrigger('sendDaily').timeBased().everyDays(1).atHour(8).nearMinute(30).create();
}

function sendWeekly() { send_('weekly'); }
function sendDaily() { send_('daily'); }

/** 手動測試：只寄給自己 */
function sendTestToMe() { send_('weekly', [Session.getActiveUser().getEmail()]); }

function send_(frequency, overrideList) {
  const props = PropertiesService.getScriptProperties();
  const base = props.getProperty('GITHUB_RAW_BASE');
  const dash = props.getProperty('DASHBOARD_URL') || '';
  const token = props.getProperty('GITHUB_TOKEN');
  const headers = token ? { Authorization: 'token ' + token } : {};

  const meta = JSON.parse(UrlFetchApp.fetch(base + '/docs/data/meta.json', { headers }).getContentText());
  const csvResp = UrlFetchApp.fetch(base + '/data/exports/new_this_week.csv', { headers, muteHttpExceptions: true });
  if (csvResp.getResponseCode() !== 200) throw new Error('抓不到本週 CSV：' + csvResp.getResponseCode());
  const blob = csvResp.getBlob().setName('日韓超商新品_' + meta.this_week + '.csv');
  const rows = Utilities.parseCsv(csvResp.getContentText('UTF-8').replace(/^﻿/, ''));
  const body = rows.slice(1);

  const recipients = overrideList || readRecipients_(frequency);
  if (!recipients.length) return;

  const byCat = {};
  body.forEach(r => { byCat[r[2]] = (byCat[r[2]] || 0) + 1; });
  const catLines = Object.keys(byCat).sort((a, b) => byCat[b] - byCat[a])
    .map(k => `<li>${k}：${byCat[k]} 支</li>`).join('');
  const picks = body.slice(0, 8).map(r =>
    `<li><b>${r[3]}</b>（${r[1]}｜${r[7]} ${r[8]}）<br><span style="color:#888">${r[4]}</span></li>`).join('');
  const warn = (meta.errors || []).length
    ? `<p style="color:#b45309">⚠ 本次資料有 ${meta.errors.length} 項異常：${meta.errors.join('；')}</p>` : '';

  const html = `
    <p>本週（${meta.this_week}）日韓超商食品新品共 <b>${body.length}</b> 支，完整清單見附件 CSV。</p>
    <ul>${catLines}</ul>
    <p>部分新品：</p><ul>${picks}</ul>
    ${warn}
    ${dash ? `<p><a href="${dash}">開啟儀表板看趨勢</a></p>` : ''}
    <p style="color:#888;font-size:12px">資料更新：${meta.updated_at}｜DSR 智慧數據整合研究部（內部研究用）</p>`;

  recipients.forEach(email => {
    MailApp.sendEmail({
      to: email,
      subject: `【日韓超商新品】${meta.this_week}｜${body.length} 支新品`,
      htmlBody: html,
      attachments: [blob],
      name: 'DSR 日韓超商情報',
    });
  });
  log_(frequency, recipients.length, body.length);
}

function readRecipients_(frequency) {
  const sh = SpreadsheetApp.getActive().getSheetByName(SHEET_MAIL);
  if (!sh) return [];
  const [header, ...rows] = sh.getDataRange().getValues();
  const idx = name => header.indexOf(name);
  return rows
    .filter(r => String(r[idx('enabled')]) === '1' && String(r[idx('frequency')] || 'weekly') === frequency)
    .map(r => String(r[idx('email')]).trim())
    .filter(e => e.includes('@'));
}

function log_(frequency, nRecipients, nRows) {
  const ss = SpreadsheetApp.getActive();
  const sh = ss.getSheetByName('mail_log') || ss.insertSheet('mail_log');
  if (sh.getLastRow() === 0) sh.appendRow(['sent_at', 'frequency', 'recipients', 'rows']);
  sh.appendRow([new Date(), frequency, nRecipients, nRows]);
}
