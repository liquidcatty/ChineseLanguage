import pytest

import grader

SENTENCE = "我们明天打算去北京"

SEGMENTATIONS = {
    SENTENCE: {
        "words": [
            {"w": "我们", "proper": False},
            {"w": "明天", "proper": False},
            {"w": "打算", "proper": False},
            {"w": "去", "proper": False},
            {"w": "北京", "proper": True},
        ]
    },
    "你好，世界！": {
        "words": [
            {"w": "你好", "proper": False},
            {"w": "，", "proper": False},
            {"w": "世界", "proper": False},
            {"w": "！", "proper": False},
        ]
    },
    "我们去北京。": {
        "words": [
            {"w": "我们", "proper": False},
            {"w": "去", "proper": False},
            {"w": "北京", "proper": True},
            {"w": "。", "proper": False},
        ]
    },
    "打算打算。": {
        "words": [
            {"w": "打算", "proper": False},
            {"w": "打算", "proper": False},
            {"w": "。", "proper": False},
        ]
    },
}


def sentence_from_prompt(user: str) -> str:
    prefix = "句子：\n"
    suffix = "\n只输出 JSON。"
    assert user.startswith(prefix), user
    body = user[len(prefix) :]
    sentence, separator, tail = body.rpartition(suffix)
    assert separator and tail == "", user
    return sentence


def segment_chat(mapping=None, calls=None):
    """A fake chat function that only handles segmentation requests."""
    mapping = SEGMENTATIONS if mapping is None else mapping

    def chat(system, user, temperature=0.0, **kwargs):
        assert "分词" in system
        assert temperature == 0.0
        sentence = sentence_from_prompt(user)
        if calls is not None:
            calls.append(sentence)
        result = mapping[sentence]
        return result() if callable(result) else result

    return chat


@pytest.fixture(autouse=True)
def _clear_segmentation_cache():
    grader.clear_cache()
    yield
    grader.clear_cache()


def test_level_1_flags_above_level_word_but_not_proper_noun():
    report = grader.grade([SENTENCE], 1, chat=segment_chat())

    assert [item["word"] for item in report["oov_words"]] == ["打算"]
    assert report["oov_words"][0]["level"] == 3
    assert report["passes"] is False

    by_word = {token["w"]: token for token in report["tokens"][0]}
    assert by_word["北京"]["status"] == "proper"
    assert by_word["打算"]["status"] == "oov"


def test_level_3_has_no_oov_words():
    report = grader.grade([SENTENCE], 3, chat=segment_chat())

    assert report["oov_words"] == []
    assert report["passes"] is True


def test_punctuation_is_not_counted_in_length():
    report = grader.grade(
        ["你好，世界！"], 1, target_length=4, chat=segment_chat()
    )

    assert report["n_han"] == 4
    assert report["length_ok"] is True
    assert grader.count_han("你好，世界！") == 4


def test_length_tolerance_rejects_short_text():
    report = grader.grade(
        ["你好，世界！"], 1, target_length=10, chat=segment_chat()
    )
    assert report["n_han"] == 4
    assert report["length_ok"] is False
    assert report["passes"] is False


def test_oov_words_are_unique():
    report = grader.grade(["打算打算。"], 1, chat=segment_chat())
    assert [item["word"] for item in report["oov_words"]] == ["打算"]


def test_max_new_words_is_a_tolerance():
    report = grader.grade(
        [SENTENCE],
        1,
        target_length=9,
        seed="北京",
        max_new_words=1,
        chat=segment_chat(),
    )
    assert report["length_ok"] is True
    assert report["seed_present"] is True
    assert len(report["oov_words"]) == 1
    assert report["passes"] is True


def test_falls_back_to_characters_when_round_trip_fails():
    calls = []

    def bad_chat(system, user, temperature=0.0, **kwargs):
        calls.append(user)
        return {"words": [{"w": "我们", "proper": False}]}

    words = grader.segment(SENTENCE, chat=bad_chat)

    assert len(calls) == 2  # one retry, then fallback
    assert "".join(item["w"] for item in words) == SENTENCE
    assert len(words) == len(SENTENCE)

    # The fallback is cached as well.
    grader.segment(SENTENCE, chat=bad_chat)
    assert len(calls) == 2


def test_segmentation_is_cached_by_sentence():
    calls = []
    chat = segment_chat(calls=calls)

    grader.grade([SENTENCE], 1, chat=chat)
    grader.grade([SENTENCE], 1, chat=chat)

    assert calls == [SENTENCE]


def test_regrade_only_touches_changed_sentences():
    calls = []
    chat = segment_chat(calls=calls)
    first = grader.grade([SENTENCE, "你好，世界！"], 3, chat=chat)
    assert len(calls) == 2

    updated = grader.regrade(
        first, ["我们去北京。", "你好，世界！"], [0], 3, chat=chat
    )

    assert calls == [SENTENCE, "你好，世界！", "我们去北京。"]
    assert updated["sentences"][0] == "我们去北京。"
    assert updated["tokens"][1] == first["tokens"][1]
    assert [token["w"] for token in updated["tokens"][0]] == [
        "我们",
        "去",
        "北京",
        "。",
    ]
    assert updated["oov_words"] == []
