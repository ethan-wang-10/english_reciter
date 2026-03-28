"""游戏化：月度打卡目标每月仅可改一次。"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import gamification as gm


class TestMonthlyGoalEditLock(unittest.TestCase):
    def test_second_edit_same_month_raises(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            u = "tuser"
            gm.patch_settings(
                d,
                u,
                monthly_checkin_goal=10,
                clear_monthly_goal=False,
            )
            with self.assertRaises(ValueError) as ctx:
                gm.patch_settings(
                    d,
                    u,
                    monthly_checkin_goal=12,
                    clear_monthly_goal=False,
                )
            self.assertIn("本月已修改过", str(ctx.exception))

    def test_idempotent_same_value_ok(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            u = "tuser2"
            gm.patch_settings(d, u, monthly_checkin_goal=8, clear_monthly_goal=False)
            gm.patch_settings(d, u, monthly_checkin_goal=8, clear_monthly_goal=False)
            st = gm.load_state(d, u)
            self.assertEqual(st.get("mcheckin_goal"), 8)

    def test_days_inclusive_last_day_of_month(self):
        n = gm.days_inclusive_today_through_month_end(date(2026, 3, 31))
        self.assertEqual(n, 1)

    def test_days_inclusive_mid_month(self):
        n = gm.days_inclusive_today_through_month_end(date(2026, 3, 28))
        self.assertEqual(n, 4)


if __name__ == "__main__":
    unittest.main()
