/**
 * もぐナビ口碑排行｜內部頁面（Google Apps Script 網頁應用程式）
 *
 * 讀取本 Sheet 的「mognavi_cache」「もぐナビ排行」「もぐナビ口碑」分頁，產生內部討論用頁面。
 * もぐナビ資料僅供 DSR 內部研究：不放公開儀表板、不寄信、不外流。
 *
 * 部署（只需一次）：
 *   1. 本 Sheet → 擴充功能 → Apps Script，新增檔案 Mognavi.gs 與 MognaviPage.html（內容貼上）
 *   2. 部署 → 新增部署作業 → 類型「網頁應用程式」
 *        執行身分：存取網頁應用程式的使用者
 *        誰可以存取：擁有 Google 帳戶的任何使用者（若 Sheet 屬於公司網域，可選「網域內的使用者」）
 *      → 因為以「瀏覽者本人」身分讀取 Sheet，只有被共用這份 Sheet 的人看得到資料。
 *   3. 複製網頁應用程式網址，貼到 sources 分頁 mognavi_page 那列的 url，
 *      下次 crawl 後公開儀表板會出現「內部：もぐナビ口碑」按鈕（按鈕本身不含任何資料）。
 */

function doGet() {
  return HtmlService.createTemplateFromFile('MognaviPage')
    .evaluate()
    .setTitle('もぐナビ口碑排行｜內部')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** 前端以 google.script.run 呼叫。 */
function getMognaviData() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var cache = readTab_(ss, 'mognavi_cache');
  var ranks = readTab_(ss, 'もぐナビ排行');
  var reviewsTab = readTab_(ss, 'もぐナビ口碑');

  var reviewsById = {};
  reviewsTab.forEach(function (r) {
    var pid = String(r['mognavi_id'] || '');
    var rid = String(r['review_id'] || '');
    var text = cleanReview_(String(r['內容（原文摘錄）'] || ''));
    if (!pid || rid.length < 5 || text.length < 8 || /^(画像提供者|メーカー|製造者)/.test(text)) return;
    (reviewsById[pid] = reviewsById[pid] || []).push({ date: fmtDate_(r['日期']), text: text });
  });

  var latestRank = {};  // 最新一週的排行，依商品 ID
  var latestWeek = '';
  ranks.forEach(function (r) { if (String(r['週次']) > latestWeek) latestWeek = String(r['週次']); });
  ranks.filter(function (r) { return String(r['週次']) === latestWeek; }).forEach(function (r) {
    var pid = String(r['網址'] || '').replace(/.*\/product\//, '');
    (latestRank[pid] = latestRank[pid] || []).push({
      type: r['排行類型'], section: r['品類'], rank: Number(r['名次']) || null
    });
  });

  var products = cache.map(function (c) {
    var pid = String(c['mognavi_id']);
    var reviews = reviewsById[pid];
    if (!reviews || !reviews.length) {
      try {
        reviews = JSON.parse(c['reviews_json'] || '[]').map(function (x) {
          return { date: fmtDate_(x.date), text: cleanReview_(String(x.text || '')) };
        }).filter(function (x) { return x.text.length >= 8 && !/^(画像提供者|メーカー|製造者)/.test(x.text); });
      } catch (e) { reviews = []; }
    }
    reviews.sort(function (a, b) { return a.date < b.date ? 1 : -1; });
    return {
      id: pid,
      chain: String(c['chain'] || ''),
      name_ja: cleanName_(String(c['name_ja'] || '')),
      name_zh: String(c['name_zh'] || ''),
      release_date: fmtDate_(c['release_date']),
      tabetai: Number(c['tabetai']) || 0,
      score: Number(c['avg_score']) || null,
      review_count: Number(c['review_count']) || 0,
      summary: String(c['summary_zh'] || ''),
      sentiment: c['sentiment'] === '' || c['sentiment'] === undefined ? null : Number(c['sentiment']),
      matched_key: String(c['matched_key'] || ''),
      fetched_at: fmtDate_(c['fetched_at']),
      ranks: latestRank[pid] || [],
      reviews: reviews.slice(0, 5),
      url: 'https://mognavi.jp/product/' + pid
    };
  }).filter(function (p) { return p.id && p.name_ja; });

  var fetched = products.map(function (p) { return p.fetched_at; }).filter(String).sort();
  return {
    products: products,
    rankWeek: latestWeek,
    fetchedFrom: fetched[0] || '',
    fetchedTo: fetched[fetched.length - 1] || '',
    generatedAt: Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm')
  };
}

function readTab_(ss, name) {
  var sh = ss.getSheetByName(name);
  if (!sh || sh.getLastRow() < 2) return [];
  var values = sh.getDataRange().getValues();
  var header = values.shift().map(String);
  return values.map(function (row) {
    var o = {};
    header.forEach(function (h, i) { o[h] = row[i]; });
    return o;
  });
}

function fmtDate_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, 'Asia/Taipei', 'yyyy-MM-dd');
  return String(v || '').replace(/\//g, '-');
}

/** 與 scraper/sources/mognavi.py 的 clean_review 相同規則：去掉日期、「續きを読む」、精選標記、投稿者名稱與評分。 */
function cleanReview_(t) {
  t = t.replace(/続きを読む.*$/, '');
  t = t.replace(/\d{4}\/\d{1,2}\/\d{1,2}(\s*\d{1,2}:\d{2})?/g, ' ');
  t = t.replace(/[\s　]+/g, ' ').trim();
  t = t.replace(/^(?:ピックアップクチコミ\s*)?(?:\S{1,30}?\s*さん)\s*(?:\d{1,2}\s+)?/, '');
  return t.trim();
}

function cleanName_(n) {
  var m = n.trim().match(/^「(.+)」の商品情報$/);
  return m ? m[1] : n.trim();
}
