from datetime import datetime, timezone

import gaokao_backfill


def _utc(weekday_day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-08-24 is a Monday.
    return datetime(2026, 8, weekday_day, hour, minute, tzinfo=timezone.utc)


def test_deepseek_off_peak_weekday_boundaries() -> None:
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 0, 59)) is True
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 1, 0)) is False
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 3, 59)) is False
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 4, 0)) is True
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 6, 0)) is False
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 9, 59)) is False
    assert gaokao_backfill.is_deepseek_off_peak(_utc(24, 10, 0)) is True


def test_deepseek_weekend_is_always_off_peak() -> None:
    assert gaokao_backfill.is_deepseek_off_peak(_utc(29, 2, 0)) is True
    assert gaokao_backfill.is_deepseek_off_peak(_utc(29, 8, 0)) is True


def test_generation_job_lock_is_nonblocking(tmp_path) -> None:
    lock_file = tmp_path / "generation.lock"
    with gaokao_backfill.generation_job_lock(lock_file=lock_file) as first:
        assert first is True
        with gaokao_backfill.generation_job_lock(lock_file=lock_file) as second:
            assert second is False


def test_auto_state_round_trip(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    gaokao_backfill.save_auto_state(
        {"status": "completed", "last_generated": 30},
        state_file,
    )
    assert gaokao_backfill.load_auto_state(state_file) == {
        "status": "completed",
        "last_generated": 30,
    }
