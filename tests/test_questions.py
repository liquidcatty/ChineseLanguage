import pytest

import grader
import questions

# --- fixtures -------------------------------------------------------------

SENTENCES = ["小美喜欢书。", "她在家看书。", "妈妈叫她吃饭。"]
STORY = {"level": 1, "title": "小美的书", "sentences": SENTENCES}

WITH_COMPLEX = {
    "level": 1,
    "title": "小美的书",
    "sentences": ["小美喜欢书。", "这是一本复杂的书。", "妈妈叫她吃饭。"],
}

VOCAB = {
    "小美": 1,
    "喜欢": 1,
    "书": 1,
    "她": 1,
    "在": 1,
    "家": 1,
    "看": 1,
    "妈妈": 1,
    "叫": 1,
    "吃饭": 1,
    "这": 1,
    "是": 1,
    "一": 1,
    "本": 1,
    "的": 1,
    "吗": 1,
    "哪儿": 1,
    "学校": 1,
    "商店": 1,
    "复杂": 5,  # above level 1
    "做": 1,
    "什么": 1,
    "不": 1,
    "知道": 1,
    "睡觉": 1,
}
PROPER = {"小美"}

Q1 = {
    "type": "mc",
    "question": "小美喜欢书吗？",
    "options": ["喜欢", "不喜欢", "不知道"],
    "answer": "A",
    "evidence": [0],
}
Q2 = {
    "type": "mc",
    "question": "妈妈叫小美做什么？",
    "options": ["看书", "吃饭", "睡觉"],
    "answer": "B",
    "evidence": [2],
}
Q3 = {
    "type": "mc",
    "question": "小美在哪儿看书？",
    "options": ["家", "学校", "商店"],
    "answer": "A",
    "evidence": [1],
}


def make_chat(batches, blind_answers, vocab=VOCAB, proper=PROPER):
    """Fake DeepSeek: greedy segmenter, queued drafts, and blind answers."""
    ordered = sorted(vocab, key=len, reverse=True)
    record = {"generations": [], "blinds": [], "segmented": []}

    def segment_text(text):
        tokens = []
        position = 0
        while position < len(text):
            for word in ordered:
                if text.startswith(word, position):
                    tokens.append({"w": word, "proper": word in proper})
                    position += len(word)
                    break
            else:
                tokens.append({"w": text[position], "proper": False})
                position += 1
        return {"words": tokens}

    def chat(system, user, temperature=0.0, **kwargs):
        if "分词器" in system:
            sentence = user.split("句子：\n", 1)[1].rsplit("\n只输出 JSON。", 1)[0]
            record["segmented"].append(sentence)
            return segment_text(sentence)
        if "出题人" in system:
            record["generations"].append(
                {"system": system, "user": user, "temperature": temperature}
            )
            return batches.pop(0) if batches else {"questions": []}
        if "只根据故事回答" in system:
            text = user.split("问题：", 1)[1].split("\n选项：", 1)[0]
            record["blinds"].append(
                {"text": text, "user": user, "temperature": temperature}
            )
            if text not in blind_answers:
                raise AssertionError(f"no blind answer for {text!r}")
            return {"answer": blind_answers[text], "evidence": []}
        raise AssertionError(f"unexpected system prompt: {system[:80]}")

    return chat, record


def assert_core(actual, expected):
    for key in ("type", "question", "options", "answer", "evidence"):
        assert actual[key] == expected[key]


TOKEN_KEYS = {"w", "pinyin", "gloss_pl", "gloss_en", "level", "status", "proper"}


@pytest.fixture(autouse=True)
def _clear_cache():
    grader.clear_cache()
    yield
    grader.clear_cache()


# --- tests ----------------------------------------------------------------


def test_valid_mc_question_passes_all_checks():
    chat, record = make_chat([{"questions": [Q1]}], {Q1["question"]: "A"})

    quiz = questions.generate_quiz(STORY, 1, chat=chat, vocab=VOCAB)

    assert quiz["complete"] is True
    assert len(quiz["questions"]) == 1
    assert_core(quiz["questions"][0], Q1)
    assert quiz["metrics"]["rounds"] == 1
    assert quiz["metrics"]["failed_by_reason"] == {}
    assert quiz["metrics"]["usage"]["calls"] == 0

    stored = quiz["questions"][0]
    assert stored["question_tokens"]
    assert all(set(token) == TOKEN_KEYS for token in stored["question_tokens"])
    assert len(stored["option_tokens"]) == 3
    assert all(set(token) == TOKEN_KEYS for tokens in stored["option_tokens"] for token in tokens)

    generation = record["generations"][0]
    assert generation["temperature"] == 0.7
    assert "三选一" in generation["system"]
    assert "A、B、C" in generation["system"]

    blind = record["blinds"][0]
    assert blind["temperature"] == 0.0
    assert Q1["question"] in blind["user"]
    assert "A. 喜欢" in blind["user"]
    assert '"answer":' not in blind["user"]  # the key is never sent


def test_generate_questions_returns_a_list():
    chat, _record = make_chat([{"questions": [Q1]}], {Q1["question"]: "A"})
    result = questions.generate_questions(STORY, 1, chat=chat, vocab=VOCAB)
    assert isinstance(result, list)
    assert len(result) == 1
    assert_core(result[0], Q1)


def test_mc_with_two_options_fails_schema():
    bad = dict(Q1, options=["喜欢", "不喜欢"])
    chat, record = make_chat([{"questions": [bad]}], {})

    quiz = questions.generate_quiz(STORY, 1, chat=chat, vocab=VOCAB, max_rounds=1)

    assert quiz["complete"] is False
    assert quiz["questions"] == []
    assert quiz["metrics"]["rounds"] == 1
    assert quiz["metrics"]["failed_by_reason"] == {"schema": 1}
    assert record["blinds"] == []
    assert "三选一" in record["generations"][0]["system"]


def test_true_false_questions_are_rejected():
    tf = {
        "type": "tf",
        "statement": "小美喜欢书。",
        "answer": "对",
        "evidence": [0],
    }
    chat, record = make_chat([{"questions": [tf]}], {})

    quiz = questions.generate_quiz(STORY, 1, chat=chat, vocab=VOCAB, max_rounds=1)

    assert quiz["complete"] is False
    assert quiz["questions"] == []
    assert quiz["metrics"]["failed_by_reason"] == {"schema": 1}
    assert record["blinds"] == []


def test_four_options_fail_schema():
    bad = dict(Q1, options=["喜欢", "不喜欢", "不知道", "都喜欢"])
    chat, record = make_chat([{"questions": [bad]}], {})

    quiz = questions.generate_quiz(STORY, 1, chat=chat, vocab=VOCAB, max_rounds=1)

    assert quiz["complete"] is False
    assert quiz["metrics"]["failed_by_reason"] == {"schema": 1}
    assert record["blinds"] == []


COMPLEX_Q = {
    "type": "mc",
    "question": "这是一本复杂的书吗？",
    "options": ["是", "不是", "不知道"],
    "answer": "A",
    "evidence": [1],
}


def test_above_level_word_must_appear_in_the_story():
    chat, _record = make_chat([], {COMPLEX_Q["question"]: "A"})

    missing = questions.question_oov_words(
        COMPLEX_Q, 1, STORY, vocab=VOCAB, chat=chat
    )
    assert [item["word"] for item in missing] == ["复杂"]
    assert missing[0]["level"] == 5

    allowed = questions.question_oov_words(
        COMPLEX_Q, 1, WITH_COMPLEX, vocab=VOCAB, chat=chat
    )
    assert allowed == []


def test_vocabulary_failure_rejects_only_the_offending_question():
    chat, record = make_chat(
        [{"questions": [COMPLEX_Q]}], {COMPLEX_Q["question"]: "A"}
    )

    quiz = questions.generate_quiz(
        STORY, 1, chat=chat, vocab=VOCAB, max_rounds=1
    )

    assert quiz["complete"] is False
    assert quiz["questions"] == []
    assert quiz["metrics"]["failed_by_reason"] == {"vocabulary": 1}
    assert record["blinds"] == []


def test_same_word_passes_when_the_story_contains_it():
    chat, _record = make_chat(
        [{"questions": [COMPLEX_Q]}], {COMPLEX_Q["question"]: "A"}
    )

    quiz = questions.generate_quiz(WITH_COMPLEX, 1, chat=chat, vocab=VOCAB)

    assert quiz["complete"] is True
    assert len(quiz["questions"]) == 1
    assert_core(quiz["questions"][0], COMPLEX_Q)


def test_blind_mismatch_regenerates_only_that_question():
    batches = [{"questions": [Q1, Q2]}, {"questions": [Q3]}]
    chat, record = make_chat(
        batches,
        {
            Q1["question"]: "A",
            Q2["question"]: "A",  # key says B -> blind disagrees
            Q3["question"]: "A",
        },
    )

    quiz = questions.generate_quiz(STORY, 2, chat=chat, vocab=VOCAB)

    assert quiz["complete"] is True
    assert [item["question"] for item in quiz["questions"]] == [
        Q1["question"],
        Q3["question"],
    ]
    assert quiz["metrics"]["rounds"] == 2
    assert quiz["metrics"]["failed_by_reason"] == {"blind": 1}
    assert len(record["generations"]) == 2
    assert [item["text"] for item in record["blinds"]] == [
        Q1["question"],
        Q2["question"],
        Q3["question"],
    ]

    second_request = record["generations"][1]["user"]
    accepted_part = second_request.split("已经合格的题目", 1)[1]
    assert Q1["question"] in accepted_part
    assert Q2["question"] not in accepted_part.split("上一轮不合格的题目", 1)[0]
    assert "盲答读者选择了「A」，与答案「B」不一致" in second_request
    assert "请出 1 道题" in second_request


def test_duplicate_question_is_rejected():
    batches = [{"questions": [Q1, Q1]}, {"questions": [Q3]}]
    chat, _record = make_chat(
        batches, {Q1["question"]: "A", Q3["question"]: "A"}
    )

    quiz = questions.generate_quiz(STORY, 2, chat=chat, vocab=VOCAB)

    assert quiz["complete"] is True
    assert [item["question"] for item in quiz["questions"]] == [
        Q1["question"],
        Q3["question"],
    ]
    assert quiz["metrics"]["failed_by_reason"] == {"duplicate": 1}
