"""游戏化挑战的安全余量与 PK 鼓励规则。"""
import shutil
import unittest
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import challenges as ch
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


def _save_xp(data_dir: Path, username: str, xp: int) -> None:
    st = gm.default_state()
    st["total_xp"] = xp
    gm.save_state(data_dir, username, st)


class TestChallengeBalanceGuards(unittest.TestCase):
    def test_monthly_pool_requires_safety_reserve(self):
        with temp_data_dir() as d:
            u = "pool-user"
            _save_xp(d, u, ch.MONTHLY_POOL_FEE_XP)

            with patch.object(ch, "china_today", return_value=date(2026, 5, 2)):
                ok, msg, _ = ch.join_monthly_pool(d, u)

            self.assertFalse(ok)
            self.assertIn("安全余量", msg)

    def test_duplicate_open_duel_between_same_users_is_blocked(self):
        with temp_data_dir() as d:
            ok, msg, row = ch.create_duel(d, "alice", "bob", wager_xp=0)
            self.assertTrue(ok, msg)
            self.assertIsNotNone(row)

            ok, msg, row = ch.create_duel(d, "bob", "alice", wager_xp=0)

            self.assertFalse(ok)
            self.assertIn("已有", msg)
            self.assertIsNone(row)

    def test_multiple_wagered_duels_are_allowed(self):
        with temp_data_dir() as d:
            _save_xp(d, "alice", 1000)
            for challenger in ("bob", "carl", "dina", "erin"):
                _save_xp(d, challenger, 1000)

            for challenger in ("bob", "carl", "dina", "erin"):
                ok, msg, row = ch.create_duel(d, challenger, "alice", wager_xp=100)
                self.assertTrue(ok, msg)
                ok, msg, _ = ch.respond_duel(d, str(row["id"]), "alice", accept=True)
                self.assertTrue(ok, msg)

            self.assertEqual(gm.load_state(d, "alice")["total_xp"], 600)


if __name__ == "__main__":
    unittest.main()
