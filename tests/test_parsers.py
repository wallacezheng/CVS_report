"""解析器測試：HTML 片段依 2026-10-28 實際頁面結構重建（圖片連結＋名稱連結＋文字）。

注意：這些是結構重建的片段，不是官網原始 HTML。第一次在 GitHub Actions
連網執行後，請把實際頁面存成 tests/fixtures/*.html 補上回歸測試。
"""
import unittest

from scraper.common import is_regional_jp, parse_jp_date, parse_price
from scraper.sources import familymart_jp, mognavi, seven_jp

SEJ_HTML = """
<p>152件中 1-15件表示</p>
<ul>
<li><a href="https://www.sej.co.jp/products/a/item/043849/kanto/"><img src="x.jpg"></a>
<div><a href="https://www.sej.co.jp/products/a/item/043849/kanto/">手巻おにぎり　炭火焼牛カルビ</a></div>
<div class="price">215円（税込232.20円）</div>
<div>2026年10月27日（火）以降順次発売</div>
<div>販売地域：千葉県</div></li>
<li><a href="https://www.sej.co.jp/products/a/item/041032/kanto/"><img src="y.jpg"></a>
<div><a href="https://www.sej.co.jp/products/a/item/041032/kanto/">鶏めし御飯幕の内</a></div>
<div class="price">598円（税込645.84円）</div>
<div>2026年10月27日（火）以降順次発売</div>
<div>販売地域：北海道、東北、関東、甲信越、北陸、東海、近畿、中国、四国、九州</div></li>
</ul>
<a href="https://www.sej.co.jp/products/a/thisweek/area/kanto/2/l15/">2</a>
"""

FM_HTML = """
<h2>今週の新商品 (10/27～11/02)</h2>
<ul>
<li><a href="/goods/omusubi/0412056.html"><img src="a.jpg">
<span>おむすび</span> <span>リュウジの至高のレシピ　ペッパーツナマヨネーズおむすび</span>
<span>184円（税込198円）</span></a>
<p>発売地域：北海道、東北、関東、東海、北陸、関西、中国・四国、九州、沖縄</p></li>
<li><a href="/goods/omusubi/0415606.html"><img src="b.jpg">
<span>北陸限定</span> <span>おむすび</span> <span>【北陸】青磯海苔　漬けサーモン</span>
<span>221円（税込238円）</span></a>
<p>発売地域：北陸</p></li>
</ul>
"""


class TestCommon(unittest.TestCase):
    def test_price(self):
        self.assertEqual(parse_price("215円（税込232.20円）"), 232.2)
        self.assertEqual(parse_price("4,500원", prefer_tax_incl=False), 4500.0)

    def test_date(self):
        self.assertEqual(parse_jp_date("2026年10月27日（火）以降順次発売"), "2026-10-27")

    def test_regional(self):
        self.assertTrue(is_regional_jp("千葉県"))
        self.assertFalse(is_regional_jp("北海道、東北、関東、甲信越、北陸、東海、近畿、中国、四国、九州"))
        self.assertFalse(is_regional_jp("全国"))


class TestSeven(unittest.TestCase):
    def test_parse(self):
        rows = seven_jp.parse_list(SEJ_HTML, "thisweek")
        self.assertEqual(len(rows), 2)
        r = rows[0]
        self.assertEqual(r["item_id"], "043849")
        self.assertEqual(r["name_original"], "手巻おにぎり 炭火焼牛カルビ")
        self.assertEqual(r["price_tax_incl"], 232.2)
        self.assertEqual(r["release_date"], "2026-10-27")
        self.assertEqual(r["regions"], "千葉県")
        self.assertIn("九州", rows[1]["regions"])


class TestFamilyMart(unittest.TestCase):
    def test_parse(self):
        rows = familymart_jp.parse_list(FM_HTML, "thisweek")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["item_id"], "0412056")
        self.assertEqual(rows[0]["name_original"], "リュウジの至高のレシピ ペッパーツナマヨネーズおむすび")
        self.assertEqual(rows[0]["price_tax_incl"], 198.0)
        self.assertTrue(rows[0]["release_date"].endswith("-10-27"))
        self.assertEqual(rows[1]["name_original"], "【北陸】青磯海苔 漬けサーモン")
        self.assertEqual(rows[1]["regions"], "北陸")
        self.assertEqual(rows[1]["source_category"], "おむすび")


MOG_RANK_HTML = """
<p>情報更新：2026年10月23日</p>
<h2>コンビニおにぎり・コンビニ手巻寿司部門 週間おすすめランキング</h2>
<ul>
<li><a href="/product/1299093"><img src="a.jpg"></a><span>1位</span>
<h3><a href="/product/1299093">セブン-イレブン 銀しゃりむすび ツナマヨネーズ</a></h3>
<span>4.0</span><span>クチコミ 5件</span><span>159円</span></li>
<li><a href="/product/688409"><img src="b.jpg"></a><span>2位</span>
<h3><a href="/product/688409">第2位 セブン-イレブン 阿部商店監修 いかめしおむすび</a></h3>
<span>4.9</span><span>クチコミ 20件</span></li>
</ul>
<h2>コンビニ弁当部門</h2>
<ul><li><a href="/product/555">セブン-イレブン 海苔弁当</a><span>3.8</span><span>12件</span></li></ul>
"""

MOG_PRODUCT_HTML = """
<h1>「セブン-イレブン 阿部商店監修 いかめしおむすび」の商品情報</h1>
<div><span>画像提供者：製造者/販売者</span><a href="/assessment/2026">2026</a></div>
<p>発売日：2026/9/29</p><p>食べたい 106</p><p>4.9 点</p><p>クチコミ 20件</p>
<div class="review"><a href="/assessment/11364851">「いかめしおむすび」のクチコミ</a></div>
<div class="review"><span>ピックアップクチコミ</span><span>Cinnamo-n さん</span><span>5</span><span>2026/10/28 04:39</span>
<p>テレビのおいしい給食を観て、いかめしが食べたい〜と家族に言われ購入。</p>
<a href="/assessment/11364851">続きを読む</a></div>
<div class="review"><span>やなぎん さん</span><span>3</span><span>2026/10/07 11:51</span>
<p>店頭にあったので興味本位で購入 いかめしの風味は楽しめる</p>
<a href="/assessment/11330861">続きを読む</a></div>
"""


class TestMognavi(unittest.TestCase):
    def test_ranking(self):
        rows = mognavi.parse_ranking(MOG_RANK_HTML, "seven_jp", "1")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["mognavi_id"], "1299093")
        self.assertEqual(rows[1]["rank"], 2)
        self.assertEqual(rows[1]["review_count"], "20")
        self.assertEqual(rows[1]["score"], "4.9")
        self.assertIn("おにぎり", rows[0]["section"])
        self.assertIn("弁当", rows[2]["section"])
        self.assertEqual(rows[1]["section"], "コンビニおにぎり・コンビニ手巻寿司部門")
        self.assertEqual(rows[2]["rank"], 1)
        self.assertEqual(rows[0]["ranking_type"], "おすすめ")
        self.assertEqual(rows[0]["site_updated"], "2026年10月23日")

    def test_product(self):
        d = mognavi.parse_product(MOG_PRODUCT_HTML, "688409")
        self.assertEqual(d["release_date"], "2026-09-29")
        self.assertEqual(d["tabetai"], "106")
        self.assertEqual(d["review_count"], "20")
        self.assertEqual(len(d["reviews"]), 2)
        self.assertEqual(d["reviews"][0]["date"], "2026/10/28")
        self.assertTrue(d["reviews"][0]["text"].startswith("テレビ"), d["reviews"][0]["text"])
        self.assertTrue(d["reviews"][1]["text"].startswith("店頭"), d["reviews"][1]["text"])
        self.assertEqual(d["name_ja"], "セブン-イレブン 阿部商店監修 いかめしおむすび")
        self.assertNotIn("2026", [r["review_id"] for r in d["reviews"]])
        self.assertNotIn("続きを読む", d["reviews"][0]["text"])


if __name__ == "__main__":
    unittest.main()
