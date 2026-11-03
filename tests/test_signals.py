"""熱賣訊號、地區擴張、もぐナビ工作流程的離線測試（Gemini 與 Sheet 皆以假物件代替）。"""
import json
import unittest
from unittest import mock

from scraper.common import new_areas
from scraper.signals import match_products
from scraper.sources import press_fm

FM_LIST = """
<ul>
<li><span>2026.10.27</span><span>商品</span>
<a href="/company/news_releases/2026/20261027_02.html">本気のチョコがけパン 発売</a></li>
<li><a href="/company/news_releases/2026/20261020_01.html">無限クリーム 新作</a></li>
<li><a href="https://hataraku.family.co.jp/pdf/x.pdf">PDF</a></li>
</ul>"""


class TestPress(unittest.TestCase):
    def test_list(self):
        rows = press_fm.parse_list(FM_LIST)
        self.assertEqual([r["release_id"] for r in rows], ["fm_20261027_02", "fm_20261020_01"])
        self.assertEqual(rows[0]["date"], "2026-10-27")
        self.assertTrue(rows[0]["url"].startswith("https://www.family.co.jp/company/news_releases/2026/"))

    def test_release_text(self):
        html = "<html><nav>menu</nav><main><h1>累計販売4億本突破</h1><p>黒糖ロイヤルミルクティー</p></main></html>"
        t = press_fm.parse_release(html)
        self.assertIn("4億本", t)
        self.assertNotIn("menu", t)

    def test_match(self):
        products = [
            {"product_key": "familymart_jp:1", "chain": "familymart_jp",
             "name_original": "Afternoon Tea監修 黒糖ロイヤルミルクティー 370g"},
            {"product_key": "familymart_jp:2", "chain": "familymart_jp",
             "name_original": "Afternoon Tea監修 ミルクに癒されるミルクティー"},
            {"product_key": "seven_jp:3", "chain": "seven_jp", "name_original": "Afternoon Tea監修 X"},
        ]
        self.assertEqual(match_products("Afternoon Tea監修", "familymart_jp", products),
                         ["familymart_jp:1", "familymart_jp:2"])
        self.assertEqual(match_products("黒糖ロイヤルミルクティー", "familymart_jp", products), ["familymart_jp:1"])


class TestAreas(unittest.TestCase):
    def test_new_areas(self):
        self.assertEqual(new_areas("千葉県", "関東"), ["関東"])
        self.assertEqual(new_areas("関東", "東京都"), [])
        self.assertEqual(new_areas("全国", "九州"), [])
        self.assertEqual(new_areas("北陸", "北陸、沖縄"), ["沖縄"])


class FakeWS:
    def __init__(self, rows=None):
        self.rows = rows or []
    def get_all_records(self):
        if not self.rows:
            return []
        h = self.rows[0]
        return [dict(zip(h, r)) for r in self.rows[1:]]
    def clear(self):
        self.rows = []
    def update(self, values, rng, **k):
        self.rows = [list(v) for v in values]
    def row_values(self, i):
        return self.rows[0] if self.rows else []
    def append_rows(self, rows, **k):
        self.rows.extend([list(r) for r in rows])
    def freeze(self, **k):
        pass
    def duplicate(self, new_sheet_name=None, **k):
        self.sheet.tabs[new_sheet_name] = FakeWS([list(r) for r in self.rows])


class FakeSheet:
    def __init__(self):
        self.tabs = {}
        self.fail_read = False
    def worksheet(self, name):
        import gspread
        if name not in self.tabs:
            raise gspread.exceptions.WorksheetNotFound(name)
        if self.fail_read:
            raise RuntimeError("API quota")
        return self.tabs[name]
    def add_worksheet(self, name, **k):
        self.tabs[name] = FakeWS()
        self.tabs[name].sheet = self
        return self.tabs[name]
    def del_worksheet(self, ws):
        self.tabs = {k: v for k, v in self.tabs.items() if v is not ws}


class TestMognaviJob(unittest.TestCase):
    def test_end_to_end(self):
        from scraper import mognavi_job
        ranks = [{"chain": "seven_jp", "ranking_type": "おすすめ", "section": "コンビニおにぎり", "rank": 1,
                  "mognavi_id": "688409", "name_ja": "セブン-イレブン 阿部商店監修 いかめしおむすび",
                  "score": "4.9", "review_count": "20", "tabetai": "", "url": "https://mognavi.jp/product/688409",
                  "site_updated": "", "crawled_at": "2026-10-31"}]
        details = {"688409": {"mognavi_id": "688409", "name_ja": ranks[0]["name_ja"], "release_date": "2026-09-29",
                              "tabetai": "106", "avg_score": "4.9", "review_count": "20", "fetched_at": "2026-10-31",
                              "reviews": [{"review_id": "11364851", "date": "2026/10/28", "text": "おいしい"}]}}
        details["688409"]["parser_version"] = "2"
        details["688409"]["fetched_at"] = __import__("scraper.common", fromlist=["x"]).today_tw().isoformat()
        sheet = FakeSheet()
        cfg = mock.Mock(sheet=sheet)
        cfg.source_enabled.return_value = True

        def fake_gemini(prompt, schema=None):
            items = json.loads(prompt.split("商品（JSON）：")[1])
            return [{"mognavi_id": i["mognavi_id"], "matched_key": i["candidates"][0]["product_key"],
                     "match_confidence": 0.9, "name_zh": "阿部商店監修 烏賊飯飯糰",
                     "summary_zh": "好評：好吃", "sentiment": 0.8} for i in items]

        with mock.patch.object(mognavi_job, "ConfigStore", return_value=cfg), \
                mock.patch.object(mognavi_job.mognavi, "crawl", return_value=(ranks, details)), \
                mock.patch.object(mognavi_job, "call_gemini", side_effect=fake_gemini), \
                mock.patch.object(mognavi_job, "resolve_model", return_value="m"), \
                mock.patch.object(mognavi_job, "gemini_mode", return_value="vertex"):
            self.assertEqual(mognavi_job.main(), 0)
            # 第二次執行：同一週不重複、口碑不重複
            self.assertEqual(mognavi_job.main(), 0)
        rank_rows = sheet.tabs["もぐナビ排行"].get_all_records()
        self.assertEqual(len(rank_rows), 1)
        self.assertEqual(rank_rows[0]["中譯"], "阿部商店監修 烏賊飯飯糰")
        self.assertEqual(rank_rows[0]["備註"], "內部研究・不寄信")
        self.assertTrue(rank_rows[0]["對應商品key"].startswith("seven_jp:"))
        self.assertEqual(len(sheet.tabs["もぐナビ口碑"].get_all_records()), 1)
        self.assertEqual(len(sheet.tabs["mognavi_cache"].get_all_records()), 1)


if __name__ == "__main__":
    unittest.main()


class TestMognaviSafety(unittest.TestCase):
    """抓不到或中途被擋時，不可覆蓋既有資料。"""
    def setUp(self):
        from scraper import mognavi_job
        self.job = mognavi_job
        self.sheet = FakeSheet()
        for name, header in [("もぐナビ排行", mognavi_job.RANK_HEADER), ("もぐナビ口碑", mognavi_job.REVIEW_HEADER),
                             ("mognavi_cache", mognavi_job.CACHE_FIELDS)]:
            self.sheet.add_worksheet(name)
            self.sheet.tabs[name].rows = [list(header)]
        rank = dict.fromkeys(mognavi_job.RANK_HEADER, "")
        rank.update({"週次": "2026-W44", "超商": "seven_jp", "排行類型": "おすすめ", "名次": 1,
                     "網址": "https://mognavi.jp/product/111"})
        self.sheet.tabs["もぐナビ排行"].rows.append([rank[h] for h in mognavi_job.RANK_HEADER])
        self.sheet.tabs["もぐナビ口碑"].rows.append(["99999", "111", "舊商品", "2026-10-01", "舊的口碑內容很好吃", "2026-10-28"])
        c = dict.fromkeys(mognavi_job.CACHE_FIELDS, "")
        c.update({"mognavi_id": "111", "name_ja": "舊商品", "review_count": "9",
                  "reviews_json": '[{"review_id":"99999","date":"2026/10/01","text":"舊的口碑內容很好吃"}]',
                  "summary_zh": "好評：舊摘要", "fetched_at": "2026-10-28"})
        self.sheet.tabs["mognavi_cache"].rows.append([c[h] for h in mognavi_job.CACHE_FIELDS])
        self.snapshot = {k: [list(r) for r in v.rows] for k, v in self.sheet.tabs.items()}

    def run_job(self, ranks, details, blocked=()):
        cfg = mock.Mock(sheet=self.sheet)
        cfg.source_enabled.return_value = True
        def fake_crawl(**k):
            self.job.mognavi.BLOCKED[:] = list(blocked)
            return ranks, details
        with mock.patch.object(self.job, "ConfigStore", return_value=cfg), \
                mock.patch.object(self.job.mognavi, "crawl", side_effect=fake_crawl), \
                mock.patch.object(self.job, "gemini_mode", return_value=""):
            return self.job.main()

    def test_nothing_fetched_leaves_sheet_untouched(self):
        rc = self.run_job([], {"111": {"fetched_at": "2026-10-28", "reviews": []}}, ["403"])
        self.assertEqual(rc, 1)
        self.assertEqual({k: v.rows for k, v in self.sheet.tabs.items()}, self.snapshot)

    def test_sheet_read_error_aborts(self):
        self.sheet.fail_read = True
        rc = self.run_job([{"chain": "seven_jp"}], {})
        self.assertEqual(rc, 1)
        self.sheet.fail_read = False
        self.assertEqual({k: v.rows for k, v in self.sheet.tabs.items()}, self.snapshot)

    def test_blank_refetch_keeps_old_reviews_and_backs_up(self):
        today = __import__("scraper.common", fromlist=["x"]).today_tw().isoformat()
        details = {"111": {"mognavi_id": "111", "name_ja": "", "reviews": [], "review_count": "",
                           "fetched_at": today, "parser_version": self.job.mognavi.PARSER_VERSION}}
        rc = self.run_job([], details, ["商品頁 222 回應 403"])
        self.assertEqual(rc, 0)
        reviews = self.sheet.tabs["もぐナビ口碑"].get_all_records()
        self.assertEqual([str(r["review_id"]) for r in reviews], ["99999"])
        cache = self.sheet.tabs["mognavi_cache"].get_all_records()[0]
        self.assertEqual(cache["name_ja"], "舊商品")
        self.assertIn("舊的口碑", cache["reviews_json"])
        self.assertEqual(len(self.sheet.tabs["もぐナビ排行"].get_all_records()), 1)  # 舊排行保留
        for n in ("もぐナビ排行", "もぐナビ口碑", "mognavi_cache"):
            self.assertIn(f"備份_{n}", self.sheet.tabs)


class TagTest(unittest.TestCase):
    TAGS = [{"tag_zh": "水果", "tag_type": "素材", "parent": ""},
            {"tag_zh": "草莓", "tag_type": "素材", "parent": "水果"},
            {"tag_zh": "酥脆", "tag_type": "口感", "parent": ""}]

    def test_expand_parent_and_filter(self):
        from scraper.enrich import expand_tags
        self.assertEqual(expand_tags(["草莓", "酥脆", "不存在"], self.TAGS), "草莓|水果|酥脆")
        self.assertEqual(expand_tags(["水果", "草莓"], self.TAGS), "水果|草莓")

    def test_retag_only_changes_tags(self):
        from scraper import enrich as E
        cache = {"a:1": {"product_key": "a:1", "name_zh": "草莓蛋糕", "category_code": "DESSERT",
                         "tags": "水果", "model": "m", "tag_version": "old"},
                 "a:2": {"product_key": "a:2", "category_code": "NON_FOOD", "model": "rule", "tags": ""}}
        items = [{"product_key": "a:1", "name_original": "いちごケーキ"}, {"product_key": "a:2"}]
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(E, "TAG_HISTORY", Path(tmp.name) / "h.csv"), \
                mock.patch.object(E, "call_gemini", return_value=[{"product_key": "a:1", "tags": ["草莓"]}]), \
                mock.patch.object(E, "resolve_model", return_value="m"), \
                mock.patch.object(E, "gemini_mode", return_value="vertex"), \
                mock.patch.object(E, "log_usage"):
            E.retag(items, self.TAGS, cache, "2026-11-02")
        self.assertEqual(cache["a:1"]["tags"], "草莓|水果")
        self.assertEqual(cache["a:1"]["name_zh"], "草莓蛋糕")
        self.assertEqual(cache["a:1"]["tag_version"], E.tag_version(self.TAGS))
        self.assertEqual(cache["a:2"]["tags"], "")


class SameCountryStyleTest(unittest.TestCase):
    def test_drop_own_country_style(self):
        from scraper.enrich import expand_tags
        tags = [{"tag_zh": "日式", "parent": ""}, {"tag_zh": "韓式", "parent": ""}]
        self.assertEqual(expand_tags(["日式", "韓式"], tags, "JP"), "韓式")
        self.assertEqual(expand_tags(["日式", "韓式"], tags, "KR"), "日式")


class ProvenanceTest(unittest.TestCase):
    def test_prompt_version_stable_and_distinct(self):
        from scraper import enrich as E
        self.assertEqual(E.prompt_version(E.build_prompt), E.prompt_version(E.build_prompt))
        self.assertNotEqual(E.prompt_version(E.build_prompt), E.prompt_version(E._retag_prompt))

    def test_retag_writes_history(self):
        import tempfile
        from pathlib import Path
        from scraper import enrich as E
        tags = [{"tag_zh": "草莓", "parent": "水果"}, {"tag_zh": "水果", "parent": ""}]
        cache = {"a:1": {"product_key": "a:1", "category_code": "DESSERT", "tags": "水果", "model": "m",
                         "tag_version": "old"}}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(E, "TAG_HISTORY", Path(d) / "h.csv"), \
                mock.patch.object(E, "call_gemini", return_value=[{"product_key": "a:1", "tags": ["草莓"]}]), \
                mock.patch.object(E, "resolve_model", return_value="m"), \
                mock.patch.object(E, "gemini_mode", return_value="vertex"), \
                mock.patch.object(E, "log_usage"):
            E.retag([{"product_key": "a:1"}], tags, cache, "2026-11-03")
            text = (Path(d) / "h.csv").read_text(encoding="utf-8")
        self.assertIn("a:1,水果,草莓|水果,old", text)
        self.assertEqual(cache["a:1"]["tag_prompt_version"], E.prompt_version(E._retag_prompt))


class InsightsTest(unittest.TestCase):
    def _p(self, key, chain, cat, week, tags, price=100, cc="JP"):
        return {"product_key": key, "chain": chain, "country": cc, "category_code": cat, "release_week": week,
                "tags": tags, "price_tax_incl": str(price), "hit_signals": ""}

    def test_cross_chain_and_new_combo(self):
        from scraper.insights import category_tags
        ps = [self._p("a", "seven_jp", "DESSERT", "2026-W44", "巧克力"),
              self._p("b", "seven_jp", "DESSERT", "2026-W45", "Q彈・Q軟"),
              self._p("c", "lawson_jp", "DESSERT", "2026-W45", "Q彈・Q軟"),
              self._p("d", "lawson_jp", "DESSERT", "2026-W45", "巧克力")]
        rows = {r["tag"]: r for r in category_tags(ps, "JP", "2026-W45", {})}
        self.assertEqual(rows["Q彈・Q軟"]["cross_chain_rising"], ["lawson_jp", "seven_jp"])
        self.assertTrue(rows["Q彈・Q軟"]["new_combo"])
        self.assertFalse(rows["巧克力"]["new_combo"])
        self.assertEqual(rows["巧克力"]["delta"], 0)
