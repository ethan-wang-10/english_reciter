"""游戏化：月度打卡目标每月仅可改一次。"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import gamification as gm


class TestAchievementUnlock(unittest.TestCase):
    def test_daily_xp_cap_achievement(self):
        st = gm.default_state()
        st["daily_xp"] = {"2020-01-01": gm.DAILY_XP_SOFT_CAP}
        new = gm._unlock_achievements(st, mastered_words=0)
        self.assertTrue(any(x.get("id") == "daily_xp_cap" for x in new))

    def test_monthly_goal_met_achievement(self):
        today = date.today()
        ym = today.strftime("%Y-%m")
        st = gm.default_state()
        st["mcheckin_goal_month"] = ym
        st["mcheckin_goal"] = 1
        st["streak_correct_by_day"] = {today.isoformat(): gm.CHECKIN_MIN_CORRECT}
        new = gm._unlock_achievements(st, mastered_words=0)
        self.assertTrue(any(x.get("id") == "monthly_goal_met" for x in new))


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
