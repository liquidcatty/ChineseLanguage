#!/usr/bin/env python
"""Run an interactive comprehension quiz on a generated story.

    python quiz.py story.json
    python quiz.py story.json --answers "对,错,对"   # non-interactive

The questions live in the story JSON produced by
``python generate.py --questions N ...``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import checker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask the comprehension questions stored in a story JSON."
    )
    parser.add_argument("story", help="story JSON with a 'questions' list")
    parser.add_argument(
        "--answers",
        default=None,
        help="comma/space separated answers; skips the interactive prompts",
    )
    return parser


def _read_story(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("story JSON must be an object")
    return data


def _format_question(number: int, question: dict[str, Any]) -> str:
    if question.get("type") == "tf":
        return (
            f"{number}. 判断对错：{question.get('statement', '')}\n"
            "   （输入 对 或 错）"
        )
    lines = [f"{number}. {question.get('question', '')}"]
    for letter, option in zip("ABC", question.get("options") or []):
        lines.append(f"   {letter}. {option}")
    return "\n".join(lines)


def _ask(question: dict[str, Any]) -> str:
    hint = "对/错" if question.get("type") == "tf" else "A/B/C"
    for _attempt in range(3):
        raw = input("你的答案：")
        if checker.normalize_answer(raw, question.get("type")):
            return raw
        print(f"请输入 {hint}。")
    return ""


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover - exotic streams
            pass

    args = build_parser().parse_args(argv)
    try:
        story_data = _read_story(args.story)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.story}: {exc}", file=sys.stderr)
        return 2

    questions = story_data.get("questions")
    if not isinstance(questions, list) or not questions:
        print(
            "error: this story has no questions; regenerate it with "
            "--questions N",
            file=sys.stderr,
        )
        return 2

    provided: list[str] | None = None
    if args.answers is not None:
        provided = [
            part for part in re.split(r"[,，;；\s]+", args.answers.strip()) if part
        ]

    print(f"{story_data.get('title', '故事')}（HSK {story_data.get('level', '?')}）\n")

    answers: list[str] = []
    for number, question in enumerate(questions, 1):
        print(_format_question(number, question))
        if provided is not None:
            answers.append(provided[number - 1] if number - 1 < len(provided) else "")
        else:
            try:
                answers.append(_ask(question))
            except EOFError:
                print(
                    "\nerror: no input available; use --answers for a "
                    "non-interactive run",
                    file=sys.stderr,
                )
                return 2
        print()

    report = checker.check_answers(questions, answers, story_data)
    print(f"得分：{report['score']}/{report['total']}\n")

    for item in report["results"]:
        if item["is_correct"]:
            print(f"✓ 第 {item['index']} 题")
            continue
        print(
            f"✗ 第 {item['index']} 题：你的答案 {item['user_answer'] or '（空）'}，"
            f"正确答案 {item['correct_answer']}"
        )
        for sentence in item["evidence_sentences"]:
            print(f"   依据：{sentence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
