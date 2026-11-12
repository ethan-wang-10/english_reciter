"""Equivalence checks for the spelling-order performance optimization."""

import itertools
import random
import re
import string

import pytest
import reciter

from reciter import _spelling_edit_distance, order_by_spelling_similarity, spelling_similarity


def _reference_distance(left, right):
    # Keep the original full-matrix metric as an independent regression oracle.
    matrix = [list(range(len(right) + 1))] + [[index] + [0] * len(right) for index in range(1, len(left) + 1)]
    for i in range(1, len(left) + 1):
        for j in range(1, len(right) + 1):
            matrix[i][j] = min(matrix[i][j - 1] + 1, matrix[i - 1][j] + 1, matrix[i - 1][j - 1] + (left[i - 1] != right[j - 1]))
            if i > 1 and j > 1 and left[i - 1] == right[j - 2] and left[i - 2] == right[j - 1]:
                matrix[i][j] = min(matrix[i][j], matrix[i - 2][j - 2] + 1)
    return matrix[-1][-1]


def _reference_score(left, right):
    first = " ".join(re.findall(r"[a-z]+", str(left or "").casefold()))
    second = " ".join(re.findall(r"[a-z]+", str(right or "").casefold()))
    if not first or not second or first == second:
        return 0.0
    first, second = first.replace(" ", ""), second.replace(" ", "")
    shortest, longest = min(len(first), len(second)), max(len(first), len(second))
    if shortest < 3:
        return 0.0
    score = 1.0 - _reference_distance(first, second) / longest
    if score >= 2 / 3:
        return score
    if longest - shortest <= 5 and (first.startswith(second) or second.startswith(first)):
        return 0.8
    return 0.0


@pytest.mark.parametrize("native", [False, True])
def test_rolling_distance_matches_original_matrix_exhaustively(native, monkeypatch):
    if native and reciter._spelling_native_distance is None:
        pytest.skip("RapidFuzz is not installed")
    if not native:
        monkeypatch.setattr(reciter, "_spelling_native_distance", None)
    words = ["".join(chars) for size in range(5) for chars in itertools.product("abc", repeat=size)]
    for first in words:
        for second in words:
            assert _spelling_edit_distance(first, second) == _reference_distance(first, second), (first, second)


def test_filtered_similarity_matches_original_scores():
    rng = random.Random(20261029)
    pairs = [("act", "action"), ("form", "from"), ("ice-cream", "ice cream"), ("ab c", "a bc"), (None, "abc")]
    for _ in range(2000):
        first = "".join(rng.choices(string.ascii_lowercase + " -'", k=rng.randint(1, 45)))
        if rng.random() < 0.5:
            second = first + "".join(rng.choices(string.ascii_lowercase, k=rng.randint(0, 8)))
        else:
            second = "".join(rng.choices(string.ascii_lowercase + " -'", k=rng.randint(1, 45)))
        pairs.append((first, second))
    for first, second in pairs:
        assert spelling_similarity(first, second) == _reference_score(first, second), (first, second)
        assert spelling_similarity(second, first) == _reference_score(first, second), (first, second)


def test_spelling_order_retains_duplicates_and_deterministic_ties():
    rows = ["affect", "banana", "effect", "affect", "effects", "zebra", "effect"]
    assert order_by_spelling_similarity(rows, str) == ["affect", "effect", "effects", "effect", "affect", "banana", "zebra"]
