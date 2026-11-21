#!/usr/bin/env python
"""CLI for the HSK 3.0 story generator.

Example:
    python generate.py --level 3 --length 200
    python generate.py --level 1 --length 180 --seed-noun 书 --out story.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import llm
import questions
import story
import vocab as vocab_mod


def _level(value: str) -> int | str:
    try:
        return vocab_mod.normalize_level(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a simplified-Chinese story at a given HSK 3.0 level."
    )
    parser.add_argument(
        "--level",
        required=True,
        type=_level,
        metavar="{1,2,3,4,5,6,7-9}",
        help="target HSK level (7, 8 and 9 share one word list: use 7-9)",
    )
    parser.add_argument(
        "--length",
        required=True,
        type=int,
        help="target length in Han characters, punctuation excluded (+/-15%%)",
    )
    parser.add_argument("--seed-noun", default=None, help="force the story's seed noun")
    parser.add_argument(
        "--max-new-words",
        type=int,
        default=0,
        help="how many out-of-vocabulary words are tolerated (default 0)",
    )
    parser.add_argument("--rng-seed", type=int, default=None, help="seed for noun selection")
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=story.DEFAULT_ROUNDS,
        help="maximum repair rounds (default 6)",
    )
    parser.add_argument(
        "--questions",
        type=_non_negative,
        default=0,
        help="also generate N three-option (A/B/C) comprehension questions (default 0)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="write the JSON report here instead of stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover - exotic streams
            pass

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        result = story.generate_story(
            args.level,
            args.length,
            seed_noun=args.seed_noun,
            max_new_words=args.max_new_words,
            rng_seed=args.rng_seed,
            max_rounds=args.max_rounds,
        )
    except (llm.LLMError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.questions:
        try:
            quiz = questions.generate_quiz(result, args.questions)
        except (llm.LLMError, ValueError) as exc:
            print(f"warning: question generation failed: {exc}", file=sys.stderr)
            quiz = {
                "questions": [],
                "metrics": {
                    "rounds": 0,
                    "failed_by_reason": {"api": 1},
                    "usage": llm.usage_snapshot(),
                },
                "complete": False,
            }
        result["questions"] = quiz["questions"]
        result["quiz_metrics"] = quiz["metrics"]
        result["quiz_complete"] = quiz["complete"]

    result["finished"] = False
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    metrics = result["metrics"]
    summary = (
        f"{result['title']} | HSK {result['level']} | seed {result['seed_noun']} | "
        f"{metrics['n_han']} Han chars | OOV {metrics['oov_count']} | "
        f"rounds {metrics['rounds']} | converged {metrics['converged']}"
    )
    if args.questions:
        summary += (
            f" | questions {len(result['questions'])}/{args.questions}"
            f" (complete={result['quiz_complete']})"
        )

    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(summary, file=sys.stderr)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
        print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
