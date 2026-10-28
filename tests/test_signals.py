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


class FakeSheet:
    def __init__(self):
        self.tabs = {}
    def worksheet(self, name):
        if name not in self.tabs:
            raise KeyError(name)
        return self.tabs[name]
    def add_worksheet(self, name, **k):
        self.tabs[name] = FakeWS()
        return self.tabs[name]


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
