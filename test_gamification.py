"""游戏化：月度打卡目标每月仅可改一次。"""
import shutil
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app_time import china_today
import gamification as gm


@contextmanager
def temp_data_dir():
    root = Path(__file__).with_name(".test_tmp")
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class TestAchievementUnlock(unittest.TestCase):
    def test_daily_xp_cap_achievement(self):
        st = gm.default_state()
        st["daily_xp"] = {"2020-01-01": gm.DAILY_XP_SOFT_CAP}
        new = gm._unlock_achievements(st, mastered_words=0)
        self.assertTrue(any(x.get("id") == "daily_xp_cap" for x in new))

    def test_daily_soft_cap_discounts_without_hard_cap(self):
        self.assertEqual(gm.DAILY_XP_SOFT_CAP, 1000)
        self.assertEqual(gm._apply_daily_cap(gm.DAILY_XP_SOFT_CAP, 55), 11)
        self.assertEqual(gm._apply_daily_cap(gm.DAILY_XP_SOFT_CAP + 5000, 55), 11)
        self.assertEqual(gm._apply_daily_cap(gm.DAILY_XP_SOFT_CAP, 3), 1)

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
        with temp_data_dir() as d:
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
        with temp_data_dir() as d:
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


class TestCheckinCompletionBonus(unittest.TestCase):
    def test_bonus_is_awarded_once_when_crossing_checkin_threshold(self):
        today = date(2026, 5, 11)
        with temp_data_dir() as d:
            u = "bonus-user"
            with patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1)), patch.object(
                gm, "china_today", return_value=today
            ):
                outs = [
                    gm.award_correct_answer(
                        d,
                        u,
                        bonus_practice=False,
                        remedial=False,
                        old_success_count=i,
                        new_success_count=i + 1,
                        mastered_now=False,
                        mastered_words=0,
                    )
                    for i in range(gm.CHECKIN_MIN_CORRECT)
                ]

            self.assertEqual(
                [x["checkin_bonus_xp"] for x in outs[:-1]],
                [0] * (gm.CHECKIN_MIN_CORRECT - 1),
            )
            expected = gm.checkin_completion_bonus_raw(1)
            self.assertEqual(outs[-1]["checkin_bonus_xp"], expected)
            self.assertTrue(outs[-1]["check_in_done_today"])


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

    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_leaderboard_includes_streak_max_record(self):
        today = china_today()
        st = gm.default_state()
        st["total_xp"] = 100
        st["streak"] = 3
        st["streak_max"] = 7
        st["last_streak_date"] = today.isoformat()

        rows = gm.build_leaderboard_from_states({"alice": st}, ["alice"], viewer="alice")

        self.assertEqual(rows[0]["streak"], 3)
        self.assertEqual(rows[0]["streak_max_record"], 7)

class TestMakeupCheckin(unittest.TestCase):
    def test_offer_available_for_yesterday_gap(self):
        today = date(2026, 5, 11)
        with patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1)):
            st = gm.default_state()
            st["total_xp"] = 500
            st["streak_correct_by_day"] = {
                (today - timedelta(days=2)).isoformat(): gm.CHECKIN_MIN_CORRECT,
            }

            offer = gm.makeup_checkin_offer(st, today)

            self.assertTrue(offer["available"])
            self.assertEqual(offer["target_date"], (today - timedelta(days=1)).isoformat())
            self.assertEqual(offer["cost_xp"], gm.MAKEUP_CHECKIN_BASE_XP + gm.MAKEUP_CHECKIN_STREAK_XP_PER_DAY)

    def test_purchase_consumes_xp_and_does_not_count_as_real_month_checkin(self):
        today = date(2026, 5, 11)
        with temp_data_dir() as d:
            u = "makeup-user"
            st = gm.default_state()
            st["total_xp"] = 500
            st["streak"] = 1
            st["last_streak_date"] = (today - timedelta(days=2)).isoformat()
            st["streak_correct_by_day"] = {
                (today - timedelta(days=2)).isoformat(): gm.CHECKIN_MIN_CORRECT,
            }
            gm.save_state(d, u, st)

            with patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1)), patch.object(
                gm, "china_today", return_value=today
            ):
                out = gm.purchase_makeup_checkin(d, u, mastered_words=0)
                saved = gm.load_state(d, u)

            self.assertEqual(out["streak"], 2)
            self.assertEqual(out["total_xp"], 500 - out["cost_xp"])
            self.assertIn((today - timedelta(days=1)).isoformat(), saved[gm.MAKEUP_CHECKINS_KEY])
            self.assertEqual(gm.valid_checkin_days_in_month(saved, today.strftime("%Y-%m")), 1)
            self.assertEqual(gm.display_streak(saved, today), 2)

    def test_purchase_after_today_checkin_rejoins_split_streak(self):
        today = date(2026, 5, 11)
        with temp_data_dir() as d:
            u = "makeup-today-user"
            st = gm.default_state()
            st["total_xp"] = 500
            st["streak"] = 1
            st["last_streak_date"] = today.isoformat()
            st["streak_correct_by_day"] = {
                (today - timedelta(days=2)).isoformat(): gm.CHECKIN_MIN_CORRECT,
                today.isoformat(): gm.CHECKIN_MIN_CORRECT,
            }
            gm.save_state(d, u, st)

            with patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1)), patch.object(
                gm, "china_today", return_value=today
            ):
                out = gm.purchase_makeup_checkin(d, u, mastered_words=0)

            self.assertEqual(out["streak"], 3)
            self.assertEqual(out["streak_max_record"], 3)

    def test_purchase_steps_backward_and_older_targets_cost_more(self):
        today = date(2026, 5, 11)
        with temp_data_dir() as d:
            u = "makeup-sequence-user"
            st = gm.default_state()
            st["total_xp"] = 2000
            st["streak"] = 1
            st["last_streak_date"] = (today - timedelta(days=3)).isoformat()
            st["streak_correct_by_day"] = {
                (today - timedelta(days=3)).isoformat(): gm.CHECKIN_MIN_CORRECT,
            }
            gm.save_state(d, u, st)

            with patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1)), patch.object(
                gm, "china_today", return_value=today
            ):
                first = gm.purchase_makeup_checkin(d, u, mastered_words=0)
                second_offer = first["makeup_checkin"]
                second = gm.purchase_makeup_checkin(d, u, mastered_words=0)
                saved = gm.load_state(d, u)

            self.assertEqual(first["target_date"], (today - timedelta(days=1)).isoformat())
            self.assertEqual(second_offer["target_date"], (today - timedelta(days=2)).isoformat())
            self.assertGreater(second_offer["cost_xp"], first["cost_xp"])
            self.assertEqual(second["streak"], 3)
            self.assertEqual(gm.valid_checkin_days_in_month(saved, today.strftime("%Y-%m")), 1)


class TestXpHistory(unittest.TestCase):
    def test_history_filters_recent_days_and_merges_breakdown(self):
        today = date(2024, 5, 7)
        st = gm.default_state()
        st["daily_xp"] = {
            "2024-05-07": 15,
            "2024-04-01": 10,
            "2024-02-01": 99,
        }
        st["xp_gain_history"] = {
            "2024-05-07": {"practice": 5, "monthly_goal_bonus": 30},
            "2024-04-01": {"weekly_reward": 20},
        }

        out = gm.xp_history_from_state(st, today=today, days=62)

        self.assertEqual(out["start_date"], "2024-03-07")
        self.assertEqual(out["end_date"], "2024-05-07")
        self.assertEqual(out["active_days"], 2)
        self.assertEqual(out["total_xp"], 75)
        self.assertEqual(out["entries"][0]["date"], "2024-05-07")
        self.assertEqual(out["entries"][0]["xp"], 45)
        self.assertEqual(out["entries"][1]["xp"], 30)

    def test_apply_xp_delta_records_positive_source_only(self):
        with temp_data_dir() as d:
            ok, msg, total = gm.apply_xp_delta(d, "alice", 50, source="weekly_reward")
            self.assertTrue(ok, msg)
            self.assertEqual(total, 50)
            ok, _, total = gm.apply_xp_delta(d, "alice", -20)
            self.assertTrue(ok)
            self.assertEqual(total, 30)

            out = gm.xp_history_recent(d, "alice")

            self.assertEqual(out["total_xp"], 50)
            self.assertEqual(out["entries"][0]["sources"][0]["source"], "weekly_reward")


if __name__ == "__main__":
    unittest.main()
