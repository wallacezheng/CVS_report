import unittest
from unittest import mock

import scraper.mognavi_job as m


class CleanupTest(unittest.TestCase):
    def test_cleanup_is_idempotent(self):
        """已整理過的簡短品類名與空白品類，再跑一次 cleanup 不能被刪掉（2026-11-02 事故）。"""
        rank = [{"週次": "2026-W44", "品類": "コンビニドリンク・コンビニカップ飲料", "名次": "5"},
                {"週次": "2026-W44", "品類": "", "名次": "4"},
                {"週次": "2026-W45", "品類": "今週新発売のセブンまとめ ランキング", "名次": "1"}]
        tabs = {"もぐナビ排行": rank, "もぐナビ口碑": [], "mognavi_cache": []}
        out = {}
        with mock.patch.multiple(m, read_tab=lambda sh, n: [dict(r) for r in tabs[n]],
                                 backup_tabs=lambda sh: None,
                                 write_tab=lambda sh, n, h, rows, mode="replace": out.__setitem__(n, rows)):
            m.cleanup(None)
        self.assertEqual(len(out["もぐナビ排行"]), 2)


if __name__ == "__main__":
    unittest.main()
