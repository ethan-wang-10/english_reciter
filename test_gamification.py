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

    def test_daily_soft_cap_discounts_until_hard_cap(self):
        self.assertEqual(gm.DAILY_XP_SOFT_CAP, 400)
        self.assertEqual(gm.DAILY_XP_HARD_CAP, 700)
        self.assertEqual(gm._apply_daily_cap(gm.DAILY_XP_SOFT_CAP, 55), 11)
        self.assertEqual(gm._apply_daily_cap(gm.DAILY_XP_HARD_CAP - 5, 55), 5)
        self.assertEqual(gm._apply_daily_cap(gm.DAILY_XP_HARD_CAP, 55), 0)

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
            goal = gm.days_inclusive_today_through_month_end(china_today())
            gm.patch_settings(
                d,
                u,
                monthly_checkin_goal=goal,
                clear_monthly_goal=False,
            )
            with self.assertRaises(ValueError) as ctx:
                gm.patch_settings(
                    d,
                    u,
                    monthly_checkin_goal=goal + 1,
                    clear_monthly_goal=False,
                )
            self.assertIn("本月已修改过", str(ctx.exception))

    def test_idempotent_same_value_ok(self):
        with temp_data_dir() as d:
            u = "tuser2"
            goal = gm.days_inclusive_today_through_month_end(china_today())
            gm.patch_settings(d, u, monthly_checkin_goal=goal, clear_monthly_goal=False)
            gm.patch_settings(d, u, monthly_checkin_goal=goal, clear_monthly_goal=False)
            st = gm.load_state(d, u)
            self.assertEqual(st.get("mcheckin_goal"), goal)

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


class TestPracticeAwardIdempotency(unittest.TestCase):
    def test_raw_scope_cannot_alias_an_encoded_scope(self):
        encoded_apple = gm._practice_event_scope_key('apple')
        self.assertNotEqual(
            gm._practice_event_scope_key(encoded_apple),
            encoded_apple,
        )

    def test_legacy_plaintext_scope_still_replays(self):
        with temp_data_dir() as data_dir:
            state = gm.default_state()
            legacy_payload = {'xp_gained': 7, 'total_xp': 7, 'legacy': True}
            state['practice_events'] = [
                {
                    'event_id': 'legacy-scoped-event',
                    'scope': 'Legacy-Word',
                    'payload': legacy_payload,
                }
            ]
            gm.save_state(data_dir, 'legacy-scope-user', state)

            replay = gm.award_correct_answer(
                data_dir,
                'legacy-scope-user',
                bonus_practice=False,
                remedial=False,
                old_success_count=0,
                new_success_count=1,
                mastered_now=False,
                mastered_words=0,
                event_id='legacy-scoped-event',
                event_scope='legacy-word',
            )

            self.assertEqual(replay, legacy_payload)
            self.assertEqual(gm.load_state(data_dir, 'legacy-scope-user')['total_correct'], 0)

    def test_legacy_sha_looking_plaintext_scope_still_replays(self):
        with temp_data_dir() as data_dir:
            raw_scope = gm._practice_event_scope_key('apple')
            state = gm.default_state()
            legacy_payload = {'xp_gained': 5, 'legacy_sha_looking': True}
            state['practice_events'] = [
                {
                    'event_id': 'legacy-sha-looking-event',
                    'scope': raw_scope,
                    'payload': legacy_payload,
                }
            ]
            gm.save_state(data_dir, 'legacy-sha-looking-user', state)

            replay = gm.award_correct_answer(
                data_dir,
                'legacy-sha-looking-user',
                bonus_practice=False,
                remedial=False,
                old_success_count=0,
                new_success_count=1,
                mastered_now=False,
                mastered_words=0,
                event_id='legacy-sha-looking-event',
                event_scope=raw_scope,
            )

            self.assertEqual(replay, legacy_payload)
            self.assertEqual(
                gm.load_state(data_dir, 'legacy-sha-looking-user')['total_correct'],
                0,
            )

    def test_same_review_event_awards_xp_once(self):
        with temp_data_dir() as data_dir:
            kwargs = {
                'bonus_practice': False,
                'remedial': False,
                'old_success_count': 0,
                'new_success_count': 1,
                'mastered_now': False,
                'mastered_words': 0,
                'event_id': 'review-event-1',
            }
            first = gm.award_correct_answer(data_dir, 'idempotent-user', **kwargs)
            second = gm.award_correct_answer(data_dir, 'idempotent-user', **kwargs)
            state = gm.load_state(data_dir, 'idempotent-user')
            self.assertEqual(second, first)
            self.assertEqual(state['total_correct'], 1)
            self.assertEqual(state['total_xp'], first['total_xp'])

    def test_bonus_event_replays_original_payload_after_reload(self):
        with temp_data_dir() as data_dir:
            kwargs = {
                'bonus_practice': True,
                'remedial': False,
                'old_success_count': 0,
                'new_success_count': 0,
                'mastered_now': False,
                'mastered_words': 0,
                'event_id': 'bonus-reload-event',
            }
            first = gm.award_correct_answer(data_dir, 'bonus-reload-user', **kwargs)
            reloaded = gm.load_state(data_dir, 'bonus-reload-user')
            second = gm.award_correct_answer(data_dir, 'bonus-reload-user', **kwargs)
            final = gm.load_state(data_dir, 'bonus-reload-user')

            self.assertEqual(second, first)
            self.assertEqual(reloaded['total_correct'], 1)
            self.assertEqual(final['total_correct'], 1)
            self.assertEqual(final['total_xp'], first['total_xp'])

    def test_earlier_event_replays_its_original_payload(self):
        with temp_data_dir() as data_dir:
            common = {
                'bonus_practice': False,
                'remedial': False,
                'old_success_count': 0,
                'new_success_count': 1,
                'mastered_now': False,
                'mastered_words': 0,
            }
            first = gm.award_correct_answer(
                data_dir,
                'older-event-user',
                event_id='older-event',
                **common,
            )
            latest = gm.award_correct_answer(
                data_dir,
                'older-event-user',
                event_id='newer-event',
                **common,
            )
            replay = gm.award_correct_answer(
                data_dir,
                'older-event-user',
                event_id='older-event',
                **common,
            )
            state = gm.load_state(data_dir, 'older-event-user')

            self.assertEqual(replay, first)
            self.assertGreater(latest['total_xp'], first['total_xp'])
            self.assertEqual(state['total_correct'], 2)

    def test_other_word_events_do_not_evict_replay_payload(self):
        with temp_data_dir() as data_dir:
            first_scope = 'x' * 160 + 'A'
            other_scope = 'x' * 160 + 'B'
            common = {
                'bonus_practice': False,
                'remedial': False,
                'old_success_count': 0,
                'new_success_count': 1,
                'mastered_now': False,
                'mastered_words': 0,
            }
            first = gm.award_correct_answer(
                data_dir,
                'scoped-event-user',
                event_id='first-word-event',
                event_scope=first_scope,
                **common,
            )
            for index in range(100):
                gm.award_correct_answer(
                    data_dir,
                    'scoped-event-user',
                    event_id=f'other-word-event-{index}',
                    event_scope=other_scope,
                    **common,
                )

            replay = gm.award_correct_answer(
                data_dir,
                'scoped-event-user',
                event_id='first-word-event',
                event_scope=first_scope,
                **common,
            )
            state = gm.load_state(data_dir, 'scoped-event-user')

            self.assertEqual(replay, first)
            self.assertEqual(state['total_correct'], 101)

    def test_practice_event_results_are_bounded_per_word_scope(self):
        with temp_data_dir() as data_dir:
            common = {
                'bonus_practice': True,
                'remedial': False,
                'old_success_count': 0,
                'new_success_count': 0,
                'mastered_now': False,
                'mastered_words': 0,
                'event_scope': 'bounded-word',
            }
            for index in range(gm.PRACTICE_EVENT_LIMIT + 1):
                gm.award_correct_answer(
                    data_dir,
                    'bounded-event-user',
                    event_id=f'bounded-event-{index}',
                    **common,
                )

            state = gm.load_state(data_dir, 'bounded-event-user')
            scoped = [
                event
                for event in state['practice_events']
                if event.get('scope') == gm._practice_event_scope_key('bounded-word')
            ]
            self.assertEqual(len(scoped), gm.PRACTICE_EVENT_LIMIT)
            self.assertNotIn('bounded-event-0', {event['event_id'] for event in scoped})


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
        st["lifetime_xp"] = 100
        st["xp_balance"] = 100
        st["total_xp"] = 100
        st["streak"] = 3
        st["streak_max"] = 7
        st["last_streak_date"] = today.isoformat()

        rows = gm.build_leaderboard_from_states({"alice": st}, ["alice"], viewer="alice")

        self.assertEqual(rows[0]["streak"], 3)
        self.assertEqual(rows[0]["streak_max_record"], 7)


    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_current_streak_recomputed_from_history_when_stored_value_is_stale(self):
        today = date(2026, 5, 13)
        st = gm.default_state()
        st["streak"] = 49
        st["streak_max"] = 51
        st["last_streak_date"] = today.isoformat()
        for i in range(51):
            st["streak_correct_by_day"][(today - timedelta(days=i)).isoformat()] = gm.CHECKIN_MIN_CORRECT

        self.assertEqual(gm.display_streak(st, today), 51)
        self.assertEqual(gm.streak_max_record_display(st, today), 51)

    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_streak_diagnostics_reports_gap_before_current_run(self):
        today = date(2026, 5, 13)
        st = gm.default_state()
        st["streak"] = 49
        st["streak_max"] = 51
        st["last_streak_date"] = today.isoformat()
        for i in range(49):
            st["streak_correct_by_day"][(today - timedelta(days=i)).isoformat()] = gm.CHECKIN_MIN_CORRECT
        gap_day = today - timedelta(days=49)
        st["streak_correct_by_day"][gap_day.isoformat()] = gm.CHECKIN_MIN_CORRECT - 1
        st["daily_xp"][gap_day.isoformat()] = 12

        diag = gm.streak_diagnostics(st, today)

        self.assertEqual(diag["current_streak"], 49)
        self.assertEqual(diag["gap_before_current_date"], gap_day.isoformat())
        self.assertEqual(diag["gap_before_current_correct_count"], gm.CHECKIN_MIN_CORRECT - 1)
        self.assertEqual(diag["gap_before_current_daily_xp"], 12)

    @patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1))
    def test_legacy_high_daily_xp_counts_as_valid_checkin(self):
        today = date(2026, 5, 13)
        gap_day = today - timedelta(days=49)
        st = gm.default_state()
        st["streak"] = 49
        st["streak_max"] = 51
        st["last_streak_date"] = today.isoformat()
        for i in range(49):
            st["streak_correct_by_day"][(today - timedelta(days=i)).isoformat()] = gm.CHECKIN_MIN_CORRECT
        st["streak_correct_by_day"][gap_day.isoformat()] = 1
        st["daily_xp"][gap_day.isoformat()] = gm.LEGACY_CHECKIN_MIN_DAILY_XP
        st["streak_correct_by_day"][(gap_day - timedelta(days=1)).isoformat()] = gm.CHECKIN_MIN_CORRECT

        self.assertEqual(gm.display_streak(st, today), 51)
        self.assertEqual(gm.longest_valid_streak_from_history(st), 51)


class TestMakeupCheckin(unittest.TestCase):
    def test_offer_available_for_yesterday_gap(self):
        today = date(2026, 5, 11)
        with patch.object(gm, "STREAK_V2_EFFECTIVE_DATE", date(2000, 1, 1)):
            st = gm.default_state()
            st["lifetime_xp"] = 500
            st["xp_balance"] = 500
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
            st["lifetime_xp"] = 500
            st["xp_balance"] = 500
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
            self.assertEqual(out["total_xp"], 500)
            self.assertEqual(out["xp_balance"], 500 - out["cost_xp"])
            self.assertIn((today - timedelta(days=1)).isoformat(), saved[gm.MAKEUP_CHECKINS_KEY])
            self.assertEqual(gm.valid_checkin_days_in_month(saved, today.strftime("%Y-%m")), 1)
            self.assertEqual(gm.display_streak(saved, today), 2)

    def test_purchase_after_today_checkin_rejoins_split_streak(self):
        today = date(2026, 5, 11)
        with temp_data_dir() as d:
            u = "makeup-today-user"
            st = gm.default_state()
            st["lifetime_xp"] = 500
            st["xp_balance"] = 500
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
            st["lifetime_xp"] = 2000
            st["xp_balance"] = 2000
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
            "2024-05-07": {"practice": 5, "monthly_goal_bonus": 30, "makeup_checkin": -12},
            "2024-04-01": {"weekly_reward": 20},
        }

        out = gm.xp_history_from_state(st, today=today, days=62)

        self.assertEqual(out["start_date"], "2024-03-07")
        self.assertEqual(out["end_date"], "2024-05-07")
        self.assertEqual(out["active_days"], 2)
        self.assertEqual(out["total_xp"], 75)
        self.assertEqual(out["total_income_xp"], 75)
        self.assertEqual(out["total_expense_xp"], 12)
        self.assertEqual(out["net_xp"], 63)
        self.assertEqual(out["entries"][0]["date"], "2024-05-07")
        self.assertEqual(out["entries"][0]["income_xp"], 45)
        self.assertEqual(out["entries"][0]["expense_xp"], 12)
        self.assertEqual(out["entries"][0]["xp"], 33)
        self.assertEqual(out["entries"][1]["xp"], 30)

    def test_apply_xp_delta_records_income_and_expense(self):
        with temp_data_dir() as d:
            ok, msg, total = gm.apply_xp_delta(d, "alice", 50, source="weekly_reward")
            self.assertTrue(ok, msg)
            self.assertEqual(total, 50)
            ok, _, total = gm.apply_xp_delta(d, "alice", -20)
            self.assertTrue(ok)
            self.assertEqual(total, 30)

            out = gm.xp_history_recent(d, "alice")

            self.assertEqual(out["total_xp"], 50)
            self.assertEqual(out["total_income_xp"], 50)
            self.assertEqual(out["total_expense_xp"], 20)
            self.assertEqual(out["net_xp"], 30)
            self.assertEqual(out["entries"][0]["xp"], 30)
            sources = {row["source"]: row["xp"] for row in out["entries"][0]["sources"]}
            self.assertEqual(sources["weekly_reward"], 50)
            self.assertEqual(sources["manual_deduct"], -20)

    def test_spend_xp_with_reserve_records_expense_source(self):
        with temp_data_dir() as d:
            gm.apply_xp_delta(d, "alice", 300, source="manual")
            ok, msg, total = gm.spend_xp_with_reserve(
                d,
                "alice",
                120,
                50,
                source="monthly_pool_fee",
            )
            self.assertTrue(ok, msg)
            self.assertEqual(total, 180)

            out = gm.xp_history_recent(d, "alice")
            sources = {row["source"]: row["xp"] for row in out["entries"][0]["sources"]}

            self.assertEqual(out["total_expense_xp"], 120)
            self.assertEqual(sources["monthly_pool_fee"], -120)

    def test_history_infers_legacy_makeup_checkin_expense(self):
        today = date(2024, 5, 7)
        st = gm.default_state()
        st[gm.MAKEUP_CHECKINS_KEY] = {
            "2024-05-06": {
                "created_at": "2024-05-07T08:30:00",
                "cost_xp": 130,
            }
        }

        out = gm.xp_history_from_state(st, today=today, days=2)

        self.assertEqual(out["total_income_xp"], 0)
        self.assertEqual(out["total_expense_xp"], 130)
        self.assertEqual(out["net_xp"], -130)
        self.assertEqual(out["entries"][0]["sources"][0]["source"], "makeup_checkin")
        self.assertEqual(out["entries"][0]["sources"][0]["xp"], -130)


if __name__ == "__main__":
    unittest.main()
