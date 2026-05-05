"""游戏化：月度打卡目标每月仅可改一次。"""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app_time import china_today
import gamification as gm


class TestAchievementUnlock(unittest.TestCase):
    def test_daily_xp_cap_achievement(self):
        st = gm.default_state()
        st["daily_xp"] = {"2020-01-01": gm.DAILY_XP_SOFT_CAP}
        new = gm._unlock_achievements(st, mastered_words=0)
        self.assertTrue(any(x.get("id") == "daily_xp_cap" for x in new))

    def test_monthly_goal_met_achievement(self):
        today = china_today()
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


class TestStreakDisplayV2(unittest.TestCase):
    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_effective_zero_when_gap_before_today(self):
        today = date(2024, 6, 15)
        st = gm.default_state()
        st["streak"] = 26
        st["last_streak_date"] = "2024-06-12"
        self.assertEqual(gm.display_streak(st, today), 0)

    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_effective_keeps_yesterday_without_today_checkin(self):
        today = date(2024, 6, 15)
        st = gm.default_state()
        st["streak"] = 5
        st["last_streak_date"] = "2024-06-14"
        self.assertEqual(gm.display_streak(st, today), 5)

    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2030, 1, 1))
    def test_legacy_uses_raw_streak_before_effective_date(self):
        today = date(2024, 6, 15)
        st = gm.default_state()
        st["streak"] = 26
        st["last_streak_date"] = "2024-06-12"
        self.assertEqual(gm.display_streak(st, today), 26)

    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_longest_valid_streak_from_history(self):
        st = gm.default_state()
        m = 5
        base = date(2024, 1, 10)
        for i in range(3):
            d = base + timedelta(days=i)
            st["streak_correct_by_day"][d.isoformat()] = m
        st["streak_correct_by_day"][(base + timedelta(days=5)).isoformat()] = m
        self.assertEqual(gm.longest_valid_streak_from_history(st), 3)


if __name__ == "__main__":
    unittest.main()
