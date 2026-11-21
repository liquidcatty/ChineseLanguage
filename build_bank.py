"""Generate a resumable HSK 1-3 reading bank: one story per noun.

Lengths follow the requested 0/85/15 distribution over 60/150/500 Han
characters.  Each story is saved under ``stories/<id>.json`` exactly like a
normal generated story, plus bank metadata.  Existing bank files are skipped,
so the builder can be stopped and restarted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import llm
import questions
import story
import vocab as vocab_mod

ROOT = Path(__file__).resolve().parent
STORIES_DIR = ROOT / "stories"

LENGTHS = (60, 150, 500)
WEIGHTS = (0, 85, 15)  # 0% / 85% / 15%
DEFAULT_MAX_NEW_WORDS = 3
DEFAULT_WORKERS = 4


def choose_length(level: int | str, noun: str) -> int:
    """Deterministic 0/85/15 choice for one noun."""
    rng = random.Random(f"hsk-bank:{level}:{noun}")
    return rng.choices(LENGTHS, weights=WEIGHTS, k=1)[0]


def bank_id(level: int | str, noun: str) -> str:
    digest = hashlib.sha1(f"{level}:{noun}".encode("utf-8")).hexdigest()[:8]
    return f"bank-l{level}-{digest}"


def _write_story(story_id: str, data: dict[str, Any]) -> Path:
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    path = STORIES_DIR / f"{story_id}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def generate_one(
    level: int | str,
    noun: str,
    *,
    n_questions: int,
    max_new_words: int,
) -> tuple[str, str]:
    story_id = bank_id(level, noun)
    path = STORIES_DIR / f"{story_id}.json"
    if path.is_file():
        return story_id, "skip"

    length = choose_length(level, noun)
    result = story.generate_story(
        level,
        length,
        seed_noun=noun,
        max_new_words=max_new_words,
    )

    if n_questions:
        quiz = questions.generate_quiz(result, n_questions)
        result["questions"] = quiz["questions"]
        result["quiz_metrics"] = quiz["metrics"]
        result["quiz_complete"] = quiz["complete"]
    else:
        result["questions"] = []
        result["quiz_metrics"] = {
            "rounds": 0,
            "failed_by_reason": {},
            "usage": llm.usage_snapshot(),
        }
        result["quiz_complete"] = False

    now = datetime.now(timezone.utc)
    result["id"] = story_id
    result["created"] = now.isoformat(timespec="seconds")
    result["bank"] = True
    result["bank_level"] = level
    result["bank_noun"] = noun
    result["target_length"] = length
    result["finished"] = False
    _write_story(story_id, result)
    return story_id, f"ok {length}"


def _parse_levels(value: str) -> list[int | str]:
    levels: list[int | str] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            level = vocab_mod.normalize_level(part)
            if vocab_mod.level_rank(level) > 3:
                raise SystemExit("the bank covers HSK 1, 2 and 3 only")
            levels.append(level)
    return levels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", default="1,2,3")
    parser.add_argument("--questions", type=int, default=0)
    parser.add_argument("--max-new-words", type=int, default=DEFAULT_MAX_NEW_WORDS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    levels = _parse_levels(args.levels)
    vocab_table = vocab_mod.load_vocab()

    tasks: list[tuple[int | str, str]] = []
    for level in levels:
        nouns = story.build_noun_list(level, vocab=vocab_table)
        print(f"HSK {level}: {len(nouns)} nouns", flush=True)
        tasks.extend((level, noun) for noun in sorted(nouns))

    distribution = Counter(choose_length(level, noun) for level, noun in tasks)
    print(
        "length distribution:",
        {length: distribution.get(length, 0) for length in LENGTHS},
        flush=True,
    )
    if args.dry_run:
        for level in levels:
            subset = [noun for lvl, noun in tasks if lvl == level]
            counts = Counter(choose_length(level, noun) for noun in subset)
            print(f"  HSK {level}: {len(subset)} nouns, lengths {dict(counts)}", flush=True)
        return 0

    pending = [
        (level, noun)
        for level, noun in tasks
        if not (STORIES_DIR / f"{bank_id(level, noun)}.json").is_file()
    ]
    print(
        f"total: {len(tasks)}, already done: {len(tasks) - len(pending)}, "
        f"pending: {len(pending)}",
        flush=True,
    )
    if args.limit:
        pending = pending[: args.limit]

    lock = threading.Lock()
    done = 0
    errors = 0

    def work(task: tuple[int | str, str]) -> tuple[int | str, str, str]:
        level, noun = task
        try:
            _story_id, status = generate_one(
                level,
                noun,
                n_questions=args.questions,
                max_new_words=args.max_new_words,
            )
            return level, noun, status
        except Exception as exc:  # keep the batch running
            return level, noun, f"error: {exc}"

    if args.workers <= 1:
        for task in pending:
            level, noun, status = work(task)
            done += 1
            print(f"[{done}/{len(pending)}] HSK {level} {noun}: {status}", flush=True)
            if status.startswith("error"):
                errors += 1
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(work, task) for task in pending]
            for future in as_completed(futures):
                level, noun, status = future.result()
                with lock:
                    done += 1
                    print(f"[{done}/{len(pending)}] HSK {level} {noun}: {status}", flush=True)
                if status.startswith("error"):
                    errors += 1

    print(f"finished: {done - errors} ok, {errors} errors", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
