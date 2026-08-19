import copy
import unittest
from datetime import date

from review_scheduler import (
    RECENT_EVENT_LIMIT,
    ReviewEventConflict,
    claim_review_event,
    choose_exercise_type,
    mark_exercise_unavailable,
    mastery_ready,
    mastery_snapshot,
    normalize_review_state,
    record_mastery_attempt,
    schedule_review,
)


class TestReviewScheduler(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 7, 13)

    def test_legacy_progress_initializes_without_mutating_input(self):
        raw = {}
        state = normalize_review_state(
            raw,
            success_count=4,
            max_success_count=8,
            legacy_interval_days=7,
        )
        self.assertEqual(raw, {})
        self.assertFalse(state['scheduler']['active'])
        self.assertEqual(state['scheduler']['interval_days'], 7)
        self.assertGreater(state['mastery']['spelling']['score'], 0)
        self.assertEqual(state['mastery']['listening']['score'], 0)

    def test_legacy_mastered_state_can_start_maintenance(self):
        state = normalize_review_state({}, success_count=8, legacy_active=True)
        self.assertTrue(state['scheduler']['active'])

    def test_legacy_mastered_state_overrides_inactive_scheduler(self):
        state = normalize_review_state(
            {'scheduler': {'active': False}},
            success_count=8,
            legacy_active=True,
        )
        self.assertTrue(state['scheduler']['active'])

    def test_future_unknown_state_fields_are_preserved(self):
        state = normalize_review_state(
            {
                'future_root': {'value': 1},
                'scheduler': {'future_scheduler': 'kept'},
                'mastery': {'future_dimension': {'score': 0.9}},
            }
        )
        self.assertEqual(state['future_root'], {'value': 1})
        self.assertEqual(state['scheduler']['future_scheduler'], 'kept')
        self.assertEqual(state['mastery']['future_dimension'], {'score': 0.9})

    def test_unknown_spelling_dimension_fields_are_preserved(self):
        future_value = {'confidence': 0.73, 'model': 'future-v2'}
        state = normalize_review_state(
            {
                'mastery': {
                    'spelling': {
                        'score': 0.6,
                        'attempts': 2,
                        'correct': 1,
                        'future_metric': future_value,
                    }
                }
            }
        )
        self.assertEqual(state['mastery']['spelling']['future_metric'], future_value)

    def test_invalid_state_is_bounded_per_field(self):
        state = normalize_review_state(
            {
                'scheduler': {
                    'ease_factor': 99,
                    'interval_days': -8,
                    'lapses': 'bad',
                    'last_review_date': 'not-a-date',
                },
                'mastery': {'spelling': {'score': 4, 'attempts': -2}},
            }
        )
        self.assertEqual(state['scheduler']['ease_factor'], 3.2)
        self.assertEqual(state['scheduler']['interval_days'], 0)
        self.assertIsNone(state['scheduler']['last_review_date'])
        self.assertEqual(state['mastery']['spelling']['score'], 1.0)
        self.assertEqual(state['mastery']['spelling']['attempts'], 0)

    def test_ratings_produce_ordered_intervals(self):
        base = normalize_review_state(
            {
                'scheduler': {
                    'active': True,
                    'ease_factor': 2.5,
                    'interval_days': 6,
                    'repetitions': 2,
                }
            }
        )
        hard = schedule_review(copy.deepcopy(base), 'hard', today=self.today)
        good = schedule_review(copy.deepcopy(base), 'good', today=self.today)
        easy = schedule_review(copy.deepcopy(base), 'easy', today=self.today)
        self.assertLess(hard, good)
        self.assertLess(good, easy)

    def test_again_resets_repetitions_and_counts_lapse(self):
        state = normalize_review_state(
            {'scheduler': {'active': True, 'interval_days': 20, 'repetitions': 4}}
        )
        interval = schedule_review(state, 'again', today=self.today)
        self.assertEqual(interval, 1)
        self.assertEqual(state['scheduler']['repetitions'], 0)
        self.assertEqual(state['scheduler']['lapses'], 1)

    def test_attempt_updates_only_selected_dimension_and_is_idempotent(self):
        state = normalize_review_state({})
        first = record_mastery_attempt(
            state,
            'listening',
            True,
            today=self.today,
            event_id='event-1',
            elapsed_ms=2500,
        )
        duplicate = record_mastery_attempt(
            state,
            'listening',
            True,
            today=self.today,
            event_id='event-1',
            elapsed_ms=2500,
        )
        snapshot = mastery_snapshot(state)
        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual(snapshot['by_type']['listening']['attempts'], 1)
        self.assertEqual(snapshot['by_type']['spelling']['attempts'], 0)

    def test_reused_event_id_with_different_outcome_is_rejected(self):
        state = normalize_review_state({})
        self.assertTrue(
            record_mastery_attempt(
                state,
                'spelling',
                True,
                today=self.today,
                event_id='conflicting-event',
                event_fingerprint='correct:true',
            )
        )
        before = copy.deepcopy(state)

        with self.assertRaises(ReviewEventConflict):
            record_mastery_attempt(
                state,
                'spelling',
                False,
                today=self.today,
                event_id='conflicting-event',
                event_fingerprint='correct:false',
            )

        self.assertEqual(state, before)

    def test_event_results_are_pruned_with_the_event_id_window(self):
        state = normalize_review_state({})
        for index in range(RECENT_EVENT_LIMIT + 2):
            event_id = f'event-{index}'
            self.assertTrue(
                claim_review_event(state, event_id, event_fingerprint=f'fingerprint-{index}')
            )
            state['recent_event_results'][event_id] = {'index': index}
        state['recent_event_fingerprints']['orphan-event'] = 'orphan'
        state['recent_event_results']['orphan-event'] = {'index': -1}

        normalized = normalize_review_state(state)
        self.assertEqual(len(normalized['recent_event_ids']), RECENT_EVENT_LIMIT)
        self.assertEqual(
            set(normalized['recent_event_fingerprints']),
            set(normalized['recent_event_ids']),
        )
        self.assertEqual(len(normalized['recent_event_results']), RECENT_EVENT_LIMIT)
        self.assertEqual(
            set(normalized['recent_event_results']),
            set(normalized['recent_event_ids']),
        )
        self.assertNotIn('event-0', normalized['recent_event_results'])
        self.assertNotIn('event-1', normalized['recent_event_results'])
        self.assertEqual(
            normalized['recent_event_results']['event-2'],
            {'index': 2},
        )

    def test_exercise_selection_moves_to_unseen_dimension(self):
        state = normalize_review_state({})
        self.assertEqual(choose_exercise_type(state), 'recognition')
        record_mastery_attempt(state, 'recognition', True, today=self.today, event_id='a')
        self.assertEqual(choose_exercise_type(state), 'context')

    def test_listening_only_enters_stable_maintenance_when_available(self):
        state = normalize_review_state({'memory_status': 'stable'})
        self.assertEqual(choose_exercise_type(state), 'recognition')
        self.assertEqual(
            choose_exercise_type(state, listening_available=True),
            'recognition',
        )
        for exercise_type in ('recognition', 'context', 'spelling'):
            record_mastery_attempt(
                state,
                exercise_type,
                True,
                today=self.today,
                event_id=f'stable-{exercise_type}',
            )
        self.assertEqual(
            choose_exercise_type(state, listening_available=True),
            'listening',
        )

    def test_core_mastery_percent_excludes_optional_listening(self):
        state = normalize_review_state({})
        for exercise_type in ('recognition', 'context', 'spelling'):
            record_mastery_attempt(
                state,
                exercise_type,
                True,
                today=self.today,
                event_id=f'core-{exercise_type}',
            )
        snapshot = mastery_snapshot(state)
        core_scores = [
            snapshot['by_type'][exercise_type]['score']
            for exercise_type in ('recognition', 'context', 'spelling')
        ]
        self.assertAlmostEqual(snapshot['overall'], sum(core_scores) / 3, places=3)

    def test_missing_semantic_exercises_are_excluded_from_mastery(self):
        state = normalize_review_state({})
        self.assertEqual(choose_exercise_type(state), 'recognition')
        self.assertTrue(mark_exercise_unavailable(state, 'recognition'))
        self.assertEqual(choose_exercise_type(state), 'context')
        self.assertEqual(
            mastery_snapshot(state)['by_type']['spelling']['required_attempts'],
            5,
        )
        self.assertTrue(mark_exercise_unavailable(state, 'context'))
        self.assertEqual(choose_exercise_type(state), 'spelling')

        for index in range(7):
            record_mastery_attempt(
                state,
                'spelling',
                True,
                today=self.today,
                event_id=f'spelling-{index}',
            )

        self.assertFalse(mastery_ready(state))
        record_mastery_attempt(
            state,
            'spelling',
            True,
            today=self.today,
            event_id='spelling-7',
        )

        snapshot = mastery_snapshot(state)
        self.assertFalse(snapshot['by_type']['recognition']['available'])
        self.assertFalse(snapshot['by_type']['recognition']['required'])
        self.assertFalse(snapshot['by_type']['context']['available'])
        self.assertTrue(snapshot['by_type']['spelling']['required'])
        self.assertEqual(snapshot['by_type']['spelling']['required_attempts'], 8)
        self.assertEqual(snapshot['overall_percent'], snapshot['by_type']['spelling']['percent'])
        self.assertTrue(mastery_ready(state))

    def test_spelling_cannot_be_marked_unavailable(self):
        state = normalize_review_state(
            {'unavailable_exercise_types': ['recognition', 'spelling', 'unknown']}
        )
        self.assertEqual(state['unavailable_exercise_types'], ['recognition'])
        self.assertFalse(mark_exercise_unavailable(state, 'spelling'))
        self.assertFalse(mastery_ready(state))


if __name__ == '__main__':
    unittest.main()
