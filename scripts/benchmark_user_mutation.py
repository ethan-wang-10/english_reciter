"""Measure single-account transactions against the original whole-table store."""

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="+", type=int, default=[100, 10000])
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    import user_store as current

    source = subprocess.check_output(["git", "show", f"{args.baseline_ref}:user_store.py"], cwd=root, text=True, encoding="utf-8")
    baseline = types.ModuleType("baseline_user_store")
    exec(compile(source, "baseline_user_store.py", "exec"), baseline.__dict__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "python": platform.python_version(),
        "baseline_commit": subprocess.check_output(["git", "rev-parse", args.baseline_ref], cwd=root, text=True).strip(),
        "rounds": args.rounds,
        "sizes": {},
    }
    with tempfile.TemporaryDirectory(prefix="user-benchmark-", dir=args.output.parent) as temporary:
        for count in args.counts:
            users = {f"user{index:05}": {"password_hash": "benchmark", "created_at": "2026-10-29"} for index in range(count)}
            samples = {"baseline_ms": [], "current_ms": []}
            try:
                for label, store in [("baseline", baseline), ("current", current)]:
                    store.init_user_store(Path(temporary) / f"{label}-{count}")
                    store.save_users(users)
                for iteration in range(args.rounds + 1):
                    variants = [("baseline_ms", baseline), ("current_ms", current)]
                    if iteration % 2:
                        variants.reverse()
                    plan = "paid" if iteration % 2 == 0 else "free"
                    for label, store in variants:
                        started = time.perf_counter()
                        kwargs = {"usernames": ["user00000"]} if store is current else {}
                        store.mutate_users(lambda rows: rows["user00000"].update(plan=plan), **kwargs)
                        elapsed_ms = (time.perf_counter() - started) * 1000
                        if iteration:
                            samples[label].append(elapsed_ms)
                assert current.load_users() == baseline.load_users()
            finally:
                baseline.close_connection()
                current.close_connection()
            report["sizes"][str(count)] = {
                **samples,
                "baseline_median_ms": statistics.median(samples["baseline_ms"]),
                "current_median_ms": statistics.median(samples["current_ms"]),
                "equal_final_state": True,
            }
            print(count, json.dumps(report["sizes"][str(count)]), flush=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
