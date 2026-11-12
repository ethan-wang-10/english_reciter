#!/usr/bin/env python3
"""Build normally paced fixed listening audio for one or more catalog papers.

Uses the same offline voice and two-read format as the existing grade-four
papers. New papers must use their own audio folder; changing published scripts
requires a new folder so existing attempts keep their matching recording.
"""

import argparse
from pathlib import Path

from build_grade4_final_audio import build, listening_questions, work_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', type=Path, required=True)
    parser.add_argument('--voice', default='Samantha')
    parser.add_argument('--rate', type=int, default=100)
    args = parser.parse_args()
    if not 80 <= args.rate <= 180:
        parser.error('--rate must be between 80 and 180')
    for source in args.source:
        source = source.resolve()
        build(voice=args.voice, rate=args.rate, source=source)
        _, folder = listening_questions(source)
        print(f'Verified ten normal-speed recordings: {work_directory(folder) / "quality-report.json"}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
