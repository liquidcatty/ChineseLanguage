import pytest

import grader

TOKEN_KEYS = {"w", "pinyin", "gloss_pl", "gloss_en", "level", "status", "proper"}
VOCAB = {"妈妈": 1, "买": 1, "东西": 1}


def chat_with(words):
    def chat(system, user, temperature=0.0, **kwargs):
        assert "分词器" in system
        assert temperature == 0.0
        return {"words": words}

    return chat


@pytest.fixture(autouse=True)
def _clean():
    grader.clear_cache()
    grader.reset_pinyin_fallbacks()
    yield
    grader.clear_cache()
    grader.reset_pinyin_fallbacks()


def test_segmentation_returns_pinyin_and_bilingual_glosses():
    words = [
        {
            "w": "妈妈",
            "proper": False,
            "pinyin": ["mā", "ma"],
            "gloss_pl": "mama",
            "gloss_en": "mom",
        },
        {
            "w": "买",
            "proper": False,
            "pinyin": ["mǎi"],
            "gloss_pl": "kupować",
            "gloss_en": "buy",
        },
        {
            "w": "东西",
            "proper": False,
            "pinyin": ["dōng", "xi"],
            "gloss_pl": "rzeczy",
            "gloss_en": "things",
        },
        {"w": "。", "proper": False, "pinyin": [], "gloss_pl": "", "gloss_en": ""},
    ]

    tokens = grader.grade_sentences(
        ["妈妈买东西。"], 1, vocab=VOCAB, chat=chat_with(words)
    )[0]

    assert [token["pinyin"] for token in tokens] == [
        ["mā", "ma"],
        ["mǎi"],
        ["dōng", "xi"],
        [],
    ]
    assert [token["gloss_pl"] for token in tokens] == [
        "mama",
        "kupować",
        "rzeczy",
        "",
    ]
    assert [token["gloss_en"] for token in tokens] == ["mom", "buy", "things", ""]
    assert all(set(token) == TOKEN_KEYS for token in tokens)
    assert [token["status"] for token in tokens] == ["ok", "ok", "ok", "punct"]
    assert tokens[0]["level"] == 1
    assert tokens[3]["level"] is None
    assert grader.pinyin_fallback_count() == 0


def test_pinyin_fallback_when_the_syllable_count_is_wrong():
    words = [
        {
            "w": "妈妈",
            "proper": False,
            "pinyin": ["mā"],  # one syllable for two characters
            "gloss_pl": "mama",
            "gloss_en": "mom",
        },
        {"w": "。", "proper": False, "pinyin": [], "gloss_pl": "", "gloss_en": ""},
    ]

    tokens = grader.segment("妈妈。", chat=chat_with(words))

    assert len(tokens[0]["pinyin"]) == 2
    assert tokens[0]["pinyin"] != ["mā"]
    assert grader.pinyin_fallback_count() == 1
    assert tokens[0]["gloss_pl"] == "mama"  # glosses are kept


def test_missing_pinyin_also_falls_back():
    words = [
        {"w": "妈妈", "proper": False},
        {"w": "。", "proper": False},
    ]

    tokens = grader.segment("妈妈。", chat=chat_with(words))

    assert len(tokens[0]["pinyin"]) == 2
    assert grader.pinyin_fallback_count() == 1


def test_punctuation_digits_and_latin_get_empty_pinyin_and_gloss():
    words = [
        {"w": "OK", "proper": False, "pinyin": ["ok"], "gloss_pl": "x", "gloss_en": "x"},
        {"w": "3", "proper": False, "pinyin": ["sān"], "gloss_pl": "x", "gloss_en": "x"},
        {"w": "，", "proper": False, "pinyin": ["，"], "gloss_pl": "x", "gloss_en": "x"},
        {
            "w": "妈妈",
            "proper": False,
            "pinyin": ["mā", "ma"],
            "gloss_pl": "mama",
            "gloss_en": "mom",
        },
        {"w": "。", "proper": False, "pinyin": [], "gloss_pl": "", "gloss_en": ""},
    ]

    tokens = grader.segment("OK3，妈妈。", chat=chat_with(words))

    for index in (0, 1, 2, 4):
        assert tokens[index]["pinyin"] == []
        assert tokens[index]["gloss_pl"] == ""
        assert tokens[index]["gloss_en"] == ""
    assert grader.pinyin_fallback_count() == 0


def test_glosses_are_clipped_to_five_words():
    words = [
        {
            "w": "买",
            "proper": False,
            "pinyin": ["mǎi"],
            "gloss_pl": "kupować coś bardzo szybko teraz proszę",
            "gloss_en": "buy something very quickly now please",
        }
    ]

    tokens = grader.segment("买", chat=chat_with(words))

    assert tokens[0]["gloss_pl"] == "kupować coś bardzo szybko teraz"
    assert tokens[0]["gloss_en"] == "buy something very quickly now"
