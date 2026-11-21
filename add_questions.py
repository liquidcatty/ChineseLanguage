"""Add comprehension questions to existing stories (resumable).

The first bank pass deliberately generated stories only.  This script walks
``stories/*.json``, skips stories that already have the requested number of
questions, generates and validates three A/B/C questions for the rest, and
writes the enriched story back in place.  Story text and tokens are untouched.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import questions

ROOT = Path(__file__).resolve().parent
STORIES_DIR = ROOT / "stories"


def _write_story(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def add_one(path: Path, n_questions: int) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return path.name, f"error: {exc}"
    if not isinstance(data, dict):
        return path.name, "skip: not an object"

    current = data.get("questions") or []
    if len(current) >= n_questions:
        return path.name, "skip"

    try:
        quiz = questions.generate_quiz(data, n_questions)
    except Exception as exc:  # keep the batch running
        return path.name, f"error: {exc}"

    data["questions"] = quiz["questions"]
    data["quiz_metrics"] = quiz["metrics"]
    data["quiz_complete"] = quiz["complete"]
    _write_story(path, data)
    return path.name, f"{len(quiz['questions'])}/{n_questions} questions"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=int, default=3)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    paths = sorted(STORIES_DIR.glob("*.json"))
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("no stories found", flush=True)
        return 0

    lock = threading.Lock()
    done = 0
    errors = 0

    def work(path: Path) -> tuple[str, str]:
        return add_one(path, args.questions)

    if args.workers <= 1:
        for path in paths:
            name, status = work(path)
            done += 1
            print(f"[{done}/{len(paths)}] {name}: {status}", flush=True)
            if status.startswith("error"):
                errors += 1
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(work, path) for path in paths]
            for future in as_completed(futures):
                name, status = future.result()
                with lock:
                    done += 1
                    print(f"[{done}/{len(paths)}] {name}: {status}", flush=True)
                if status.startswith("error"):
                    errors += 1

    print(f"finished: {done - errors} ok, {errors} errors", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
