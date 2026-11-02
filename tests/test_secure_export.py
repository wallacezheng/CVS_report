import unittest

from scraper.mognavi_job import build_export
from scraper.secure_export import decrypt_json, encrypt_json


class SecureExportTest(unittest.TestCase):
    def test_roundtrip_and_wrong_password(self):
        obj = {"products": [{"name_ja": "抹茶クッキー", "score": 5.2}]}
        b = encrypt_json(obj, "correct-horse", "2026-11-02")
        self.assertNotIn("抹茶", str(b))
        self.assertEqual(decrypt_json(b, "correct-horse"), obj)
        with self.assertRaises(Exception):
            decrypt_json(b, "wrong-password")

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            encrypt_json({}, "short")

    def test_build_export(self):
        cache = [{"mognavi_id": "123", "chain": "seven_jp", "name_ja": "宇治抹茶クッキー", "name_zh": "宇治抹茶餅乾",
                  "avg_score": "5.2", "tabetai": "201", "review_count": "29", "sentiment": "0.6",
                  "summary_zh": "好評：酥", "fetched_at": "2026/11/02", "release_date": "", "matched_key": "",
                  "reviews_json": '[{"date":"2026-10-30","text":"とてもおいしい抹茶でした"}]'},
                 {"mognavi_id": "", "name_ja": "x"}]
        ranks = [{"週次": "2026-W44", "網址": "https://mognavi.jp/product/123", "排行類型": "おすすめ", "品類": "A", "名次": "9"},
                 {"週次": "2026-W45", "網址": "https://mognavi.jp/product/123", "排行類型": "食べたい", "品類": "クッキー", "名次": "2"}]
        out = build_export(cache, ranks, [])
        self.assertEqual(len(out["products"]), 1)
        p = out["products"][0]
        self.assertEqual(out["rankWeek"], "2026-W45")
        self.assertEqual(p["ranks"], [{"type": "食べたい", "section": "クッキー", "rank": 2}])
        self.assertEqual(p["tabetai"], 201)
        self.assertEqual(p["fetched_at"], "2026-11-02")
        self.assertEqual(len(p["reviews"]), 1)  # 口碑分頁沒有時，退回快取中的口碑


if __name__ == "__main__":
    unittest.main()
