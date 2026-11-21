"""Generate HSK-style comprehension questions for a story, and validate them.

A question is only kept when it passes three gates:

1. **Schema** - three options (A/B/C), valid answer key and in-range evidence
   indices.
2. **Vocabulary** - the question and options contain no above-level word that
   the reader has not already met in the story.
3. **Blind answer** - a separate reader call sees only the story and the
   question (never the key) and must reach the same answer.

Failed questions are regenerated with feedback for at most four rounds.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Mapping, Sequence

import checker
import grader
import llm
import story as story_mod
import vocab as vocab_mod
from vocab import Level

logger = logging.getLogger(__name__)

ChatFn = Callable[..., dict[str, Any]]
DEFAULT_ROUNDS = 4

QUESTION_SYSTEM = """你是 HSK {level} 阅读理解出题人，根据用户给出的故事出题。
级别说明：{guide}
要求：
1. 全部使用简体中文，语言难度不超过 HSK {level}；可以使用故事里已经出现的词，即使它稍高于该级别。
2. 题干和选项里不要使用故事中没有出现的超纲词。
3. 每道题的答案都必须能从故事中推出来；evidence 给出能证明答案的句子索引（从 0 开始，可以多个）。
4. 题目要考查故事的不同部分，不要都问同一个细节。
5. 所有题目都是三选一：options 必须正好 3 个（A、B、C），answer 只能是 A、B 或 C。
6. 错误选项要听起来合理，但根据故事不能为真。
只输出 JSON，格式：
{{"questions": [{{"type": "mc", "question": "问题", "options": ["选项一", "选项二", "选项三"], "answer": "A", "evidence": [1]}}]}}"""

BLIND_SYSTEM = """你是一个只根据故事回答问题的读者。不要使用故事以外的知识，也不要猜测。
只输出 JSON，格式：{"answer": "答案", "evidence": [句子索引]}"""


def _number_story(sentences: Sequence[str]) -> str:
    return "\n".join(f"{index}. {sentence}" for index, sentence in enumerate(sentences))


def _schema_problem(question: Any, n_sentences: int) -> str | None:
    """Return a human-readable schema error, or ``None`` when valid."""
    if not isinstance(question, Mapping):
        return "题目必须是 JSON 对象"
    if question.get("type") != "mc":
        return "题型必须是 mc（三选一）"

    evidence = question.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "evidence 必须是非空的句子索引列表"
    for position in evidence:
        if isinstance(position, bool) or not isinstance(position, int):
            return f"evidence 索引必须是整数：{position!r}"
        if not 0 <= position < n_sentences:
            return f"evidence 索引超出范围：{position}"

    text = question.get("question")
    if not isinstance(text, str) or not text.strip():
        return "question 必须是非空字符串"
    options = question.get("options")
    if not isinstance(options, list) or len(options) != 3:
        return "options 必须正好 3 个（A/B/C）"
    cleaned = [option.strip() for option in options if isinstance(option, str)]
    if len(cleaned) != 3 or any(not option for option in cleaned):
        return "options 必须都是非空字符串"
    if len(set(cleaned)) != 3:
        return "options 不能重复"
    if checker.normalize_answer(question.get("answer"), "mc") not in {"A", "B", "C"}:
        return "answer 必须是 A/B/C"
    return None


def _normalize_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """Return the question with canonical answer and stripped text."""
    return {
        "type": "mc",
        "question": str(question["question"]).strip(),
        "options": [str(option).strip() for option in question["options"]],
        "answer": checker.normalize_answer(question.get("answer"), "mc"),
        "evidence": [int(position) for position in question["evidence"]],
    }


def _question_text(question: Mapping[str, Any]) -> str:
    return str(question.get("question", "")).strip()


def _describe_question(question: Any) -> str:
    if isinstance(question, Mapping):
        options = question.get("options")
        rendered = (
            " / ".join(str(option) for option in options)
            if isinstance(options, list)
            else ""
        )
        return f"选择题：{question.get('question', '')}（选项：{rendered}）"
    return json.dumps(question, ensure_ascii=False)[:200]


# --- validation helpers ---------------------------------------------------


def story_word_set(
    story_data: Mapping[str, Any],
    *,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> set[str]:
    """All segmented words of the story (proper nouns included)."""
    words: set[str] = set()
    for sentence in story_data.get("sentences") or []:
        for token in grader.segment(str(sentence), chat=chat, cache=cache):
            words.add(str(token["w"]))
    return words


def question_tokens_and_oov(
    question: Mapping[str, Any],
    level: Level,
    story_data: Mapping[str, Any],
    *,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    """Enriched tokens for the question and options, plus disallowed OOV words."""
    texts = [str(question.get("question", ""))] + [
        str(option) for option in question.get("options") or []
    ]
    report = grader.grade(texts, level, vocab=vocab, chat=chat, cache=cache)
    story_text = "".join(str(sentence) for sentence in story_data.get("sentences") or [])
    story_words = {
        str(token.get("w"))
        for sentence in story_data.get("tokens") or []
        for token in sentence
        if token.get("w")
    }
    if not story_words:
        story_words = story_word_set(story_data, chat=chat, cache=cache)
    oov = [
        item
        for item in report["oov_words"]
        if item["word"] not in story_words and item["word"] not in story_text
    ]
    return report["tokens"], oov


def question_oov_words(
    question: Mapping[str, Any],
    level: Level,
    story_data: Mapping[str, Any],
    *,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Above-level words in a question that the reader has not met in the story."""
    _tokens, oov = question_tokens_and_oov(
        question, level, story_data, vocab=vocab, chat=chat, cache=cache
    )
    return oov


def ask_blind(
    question: Mapping[str, Any],
    story_data: Mapping[str, Any],
    *,
    chat: ChatFn | None = None,
) -> str:
    """Answer the question with the story only (no key, no evidence)."""
    chat_fn = chat or llm.chat_json
    numbered = _number_story(
        [str(sentence) for sentence in story_data.get("sentences") or []]
    )
    options = [str(option) for option in question.get("options") or []]
    labeled = "\n".join(
        f"{letter}. {option}" for letter, option in zip("ABC", options)
    )
    user = (
        "故事（每句前的数字是从 0 开始的索引）：\n"
        f"{numbered}\n\n"
        f"问题：{question.get('question', '')}\n"
        f"选项：\n{labeled}\n\n"
        "answer 写 A/B/C 中的一个。只输出 JSON。"
    )

    data = chat_fn(BLIND_SYSTEM, user, 0.0, max_tokens=512)
    raw = data.get("answer") if isinstance(data, Mapping) else None
    return checker.normalize_answer(raw, "mc")


# --- generation loop ------------------------------------------------------


def _generation_request(
    story_data: Mapping[str, Any],
    missing: int,
    level: Level,
    accepted: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, str]],
) -> tuple[str, str]:
    sentences = [str(sentence) for sentence in story_data.get("sentences") or []]
    title = str(story_data.get("title") or "故事")
    system = QUESTION_SYSTEM.format(
        level=level,
        guide=story_mod.LEVEL_GUIDES[vocab_mod.level_rank(level)],
    )
    parts = [
        f"故事《{title}》（每句前的数字是从 0 开始的索引）：\n"
        f"{_number_story(sentences)}"
    ]
    if accepted:
        parts.append(
            "已经合格的题目（不要重复）：\n"
            + "\n".join(f"- {_describe_question(question)}" for question in accepted)
        )
    if failures:
        parts.append(
            "上一轮不合格的题目：\n"
            + "\n".join(
                f"- {failure['text']}\n  失败原因：{failure['detail']}"
                for failure in failures
            )
        )
    parts.append(f"请出 {missing} 道题。只输出 JSON。")
    return system, "\n\n".join(parts)


def _level_text(item: Mapping[str, Any]) -> str:
    level = item.get("level")
    return f"HSK {level}" if level is not None else "词表以外"


def generate_quiz(
    story_data: Mapping[str, Any],
    n_questions: int = 3,
    *,
    chat: ChatFn | None = None,
    vocab: Mapping[str, Level] | None = None,
    max_rounds: int = DEFAULT_ROUNDS,
) -> dict[str, Any]:
    """Generate and validate questions, returning questions plus metrics."""
    if not isinstance(story_data, Mapping):
        raise ValueError("story must be a JSON object")
    if "level" not in story_data:
        raise ValueError("story JSON is missing 'level'")
    sentences = [str(sentence) for sentence in story_data.get("sentences") or []]
    if not sentences:
        raise ValueError("story JSON has no sentences")
    if (
        isinstance(n_questions, bool)
        or not isinstance(n_questions, int)
        or n_questions < 1
    ):
        raise ValueError("n_questions must be a positive integer")
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")

    level = vocab_mod.normalize_level(story_data["level"])
    chat_fn = chat or llm.chat_json
    vocab_table = vocab if vocab is not None else vocab_mod.load_vocab()
    llm.reset_usage()

    valid: list[dict[str, Any]] = []
    failed_by_reason: dict[str, int] = {}
    failures: list[dict[str, str]] = []
    rounds = 0

    while rounds < max_rounds and len(valid) < n_questions:
        rounds += 1
        missing = n_questions - len(valid)
        system, user = _generation_request(story_data, missing, level, valid, failures)
        try:
            data = chat_fn(system, user, 0.7, max_tokens=max(1024, missing * 500))
        except llm.LLMError as exc:
            logger.error("question generation round %d failed: %s", rounds, exc)
            failed_by_reason["api"] = failed_by_reason.get("api", 0) + 1
            break

        raw_questions = data.get("questions") if isinstance(data, Mapping) else None
        if not isinstance(raw_questions, list) or not raw_questions:
            failed_by_reason["schema"] = failed_by_reason.get("schema", 0) + 1
            failures = [
                {
                    "text": "（本轮没有返回题目）",
                    "detail": "返回的 JSON 里没有非空的 questions 数组",
                }
            ]
            continue

        failures = []

        def fail(reason: str, question: Any, detail: str) -> None:
            failed_by_reason[reason] = failed_by_reason.get(reason, 0) + 1
            failures.append({"text": _describe_question(question), "detail": detail})
            logger.info(
                "rejected question (%s): %s | %s",
                reason,
                _question_text(question) or _describe_question(question),
                detail,
            )

        for raw in raw_questions[: max(missing, n_questions)]:
            if len(valid) >= n_questions:
                break

            problem = _schema_problem(raw, len(sentences))
            if problem:
                fail("schema", raw, problem)
                continue

            question = _normalize_question(raw)
            label = _question_text(question)
            if label and any(_question_text(other) == label for other in valid):
                fail("duplicate", question, "与已经合格的题目重复")
                continue

            text_tokens, oov_words = question_tokens_and_oov(
                question, level, story_data, vocab=vocab_table, chat=chat_fn
            )
            if oov_words:
                listed = "、".join(
                    f"{item['word']}（{_level_text(item)}）" for item in oov_words
                )
                fail(
                    "vocabulary",
                    question,
                    f"这些词超出 HSK {level}，而且没有在故事里出现：{listed}。"
                    "请换成本级词或故事里的词。",
                )
                continue

            blind = ask_blind(question, story_data, chat=chat_fn)
            key = question["answer"]
            if not blind or blind != key:
                fail(
                    "blind",
                    question,
                    f"盲答读者选择了「{blind or '无法判断'}」，与答案「{key}」不一致；"
                    "题目可能有歧义或答案不对。",
                )
                continue

            question["question_tokens"] = text_tokens[0]
            question["option_tokens"] = text_tokens[1:]
            valid.append(question)

    return {
        "questions": valid[:n_questions],
        "metrics": {
            "rounds": rounds,
            "failed_by_reason": failed_by_reason,
            "usage": llm.usage_snapshot(),
        },
        "complete": len(valid) >= n_questions,
    }


def generate_questions(
    story_data: Mapping[str, Any],
    n_questions: int = 3,
    *,
    chat: ChatFn | None = None,
    vocab: Mapping[str, Level] | None = None,
    max_rounds: int = DEFAULT_ROUNDS,
) -> list[dict[str, Any]]:
    """Convenience wrapper returning only the valid questions."""
    return generate_quiz(
        story_data,
        n_questions,
        chat=chat,
        vocab=vocab,
        max_rounds=max_rounds,
    )["questions"]
