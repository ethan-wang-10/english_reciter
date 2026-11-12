"""Compare spelling order against a Git baseline using deterministic 300-word sets."""

import argparse
import ast
import json
import platform
import random
import re
import statistics
import string
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional

try:
    from rapidfuzz.distance.OSA import distance as _spelling_native_distance
except ImportError:
    _spelling_native_distance = None


def load_order_function(source, filename):
    tree = ast.parse(source, filename)
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name.startswith("_spelling_") or node.name in {"spelling_similarity", "order_by_spelling_similarity"})
    ]
    namespace = {"re": re, "Any": Any, "List": List, "Optional": Optional, "Counter": Counter, "_spelling_native_distance": _spelling_native_distance}
    exec(compile(ast.Module(body=functions, type_ignores=[]), filename, "exec"), namespace)
    return namespace["order_by_spelling_similarity"]


def datasets():
    rng = random.Random(20261029)
    unrelated = ["".join(rng.choices(string.ascii_lowercase, k=rng.randint(5, 15))) for _ in range(300)]
    roots = (
        "accept achieve act add adjust advise agree allow answer appear apply arrange arrive ask avoid "
        "bake balance believe belong borrow bring build call care carry change check choose clean "
        "climb close collect compare complete consider continue cook copy count create dance decide "
        "deliver depend describe design develop discover discuss divide draw dream drive drop earn "
        "enjoy enter explain express face fail feed feel fill finish fix follow forget form gain "
        "gather give grow guess happen help hide hold hope imagine improve include increase invite "
        "join keep know learn leave lend listen live look lose love make manage mean meet move need"
    ).split()[:100]
    families = [word + suffix for word in roots for suffix in ("", "s", "ing")]
    rng.shuffle(families)
    phrases = [f"{word} the international development" for word in roots for _ in range(3)]
    rng.shuffle(phrases)
    return {"unrelated": unrelated, "word_families": families, "long_phrases": phrases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--datasets", nargs="+", choices=list(datasets()), default=list(datasets()))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    baseline_source = subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:reciter.py"], cwd=root, encoding="utf-8"
    )
    baseline = load_order_function(baseline_source, "baseline_reciter.py")
    current = load_order_function((root / "reciter.py").read_text(encoding="utf-8"), "reciter.py")
    report = {
        "python": platform.python_version(),
        "baseline_commit": subprocess.check_output(["git", "rev-parse", args.baseline_ref], cwd=root, text=True).strip(),
        "rounds": args.rounds,
        "native_osa_available": _spelling_native_distance is not None,
        "datasets": {},
    }
    for name, words in datasets().items():
        if name not in args.datasets:
            continue
        expected = baseline(words, str)
        assert current(words, str) == expected, name
        samples = {"baseline_ms": [], "current_ms": []}
        for round_index in range(args.rounds):
            variants = [("baseline_ms", baseline), ("current_ms", current)]
            if round_index % 2:
                variants.reverse()
            for label, function in variants:
                start = time.perf_counter()
                result = function(words, str)
                elapsed_ms = (time.perf_counter() - start) * 1000
                assert result == expected, (name, label)
                samples[label].append(elapsed_ms)
        report["datasets"][name] = {
            "count": len(words),
            "exact_order_equal": True,
            **samples,
            "baseline_median_ms": statistics.median(samples["baseline_ms"]),
            "current_median_ms": statistics.median(samples["current_ms"]),
        }
        print(name, json.dumps(report["datasets"][name]), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
