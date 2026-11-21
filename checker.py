"""Deterministic answer checking for HSK story comprehension questions.

No LLM calls live here: answers are normalised, compared with the stored key,
and the evidence indices are turned into quoted story sentences so a learner can
see why an answer is right or wrong.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

TF_TRUE = {"对", "t", "true", "yes", "y", "1"}
TF_FALSE = {"错", "f", "false", "no", "n", "0"}
MC_LETTERS = {"A", "B", "C"}

_FULLWIDTH = str.maketrans("ＡＢＣＤａｂｃｄ", "ABCDabcd")
_TRAILING = "。.!！?？ "


def normalize_answer(answer: Any, question_type: str | None = None) -> str:
    """Return a canonical answer: ``对``/``错``, ``A``-``C``, or ``""``.

    ``question_type`` is ``"tf"`` or ``"mc"``.  When it is omitted the type is
    inferred from the value (Chinese true/false, T/F, true/false, or a letter).
    """
    if answer is None:
        return ""
    text = str(answer).translate(_FULLWIDTH).strip()
    if not text:
        return ""
    qtype = (question_type or "").strip().lower()

    if qtype in {"", "tf"}:
        folded = text.casefold().strip(_TRAILING)
        if folded in TF_TRUE:
            return "对"
        if folded in TF_FALSE:
            return "错"
        if qtype == "tf":
            return ""

    if qtype in {"", "mc"}:
        folded = text.upper().strip(_TRAILING)
        if folded and folded[0] in MC_LETTERS:
            rest = folded[1:].strip()
            if not rest or rest[0] in ".、:：)）]】":
                return folded[0]
    return ""


def _sentences(story: Mapping[str, Any] | Sequence[str]) -> list[str]:
    if isinstance(story, Mapping):
        raw = story.get("sentences") or []
    else:
        raw = story
    return [str(sentence) for sentence in raw]


def check_answers(
    questions: Sequence[Mapping[str, Any]],
    user_answers: Sequence[Any] | Any,
    story: Mapping[str, Any] | Sequence[str],
) -> dict[str, Any]:
    """Score answers and quote the evidence for every question.

    ``questions`` is the list stored in the story JSON.  ``user_answers`` is
    positional (one entry per question); a plain string is treated as a single
    answer.  Result indices are 1-based for display.
    """
    sentences = _sentences(story)
    if isinstance(user_answers, str) or not isinstance(user_answers, Sequence):
        answers: list[Any] = [user_answers]
    else:
        answers = list(user_answers)

    results: list[dict[str, Any]] = []
    score = 0
    for index, question in enumerate(questions):
        qtype = str(question.get("type", "")).strip().lower()
        raw_answer = answers[index] if index < len(answers) else ""
        user_answer = normalize_answer(raw_answer, qtype)
        correct_answer = normalize_answer(question.get("answer"), qtype)
        is_correct = bool(correct_answer) and user_answer == correct_answer

        evidence_indices = question.get("evidence") or []
        evidence_sentences: list[str] = []
        for position in evidence_indices:
            if isinstance(position, bool) or not isinstance(position, int):
                continue
            if 0 <= position < len(sentences):
                evidence_sentences.append(sentences[position])

        if is_correct:
            score += 1
        results.append(
            {
                "index": index + 1,
                "user_answer": user_answer
                if user_answer
                else ("" if raw_answer is None else str(raw_answer).strip()),
                "correct_answer": correct_answer
                if correct_answer
                else str(question.get("answer", "")).strip(),
                "is_correct": is_correct,
                "evidence_sentences": evidence_sentences,
            }
        )

    return {"score": score, "total": len(questions), "results": results}
