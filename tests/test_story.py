import random

import pytest

import grader
import story

# A tiny hand-made vocabulary so the test does not depend on real HSK levels.
VOCAB = {
    "我": 1,
    "想": 1,
    "去": 1,
    "公园": 1,
    "高兴": 1,
    "明天": 1,
    "见": 1,
    "但是": 1,
    "下雨": 1,
    "很": 1,
    "打算": 2,  # OOV at level 1
    "虽然": 3,  # OOV at level 1
    "打扫": 1,  # candidate replacement for 打算
    "计算": 1,  # candidate replacement for 打算
}

S1 = "我打算去公园。"
S2 = "虽然下雨，我很高兴。"
S3 = "明天见。"
FIXED_S1 = "我很想去公园。"
FIXED_S2 = "但是下雨，我很高兴。"
TARGET_LENGTH = 17  # 6 + 8 + 3 Han characters

SEGMENTS = {
    S1: [("我", False), ("打算", False), ("去", False), ("公园", False), ("。", False)],
    S2: [
        ("虽然", False), ("下雨", False), ("，", False),
        ("我", False), ("很", False), ("高兴", False), ("。", False),
    ],
    S3: [("明天", False), ("见", False), ("。", False)],
    FIXED_S1: [
        ("我", False),
        ("很", False),
        ("想", False),
        ("去", False),
        ("公园", False),
        ("。", False),
    ],
    FIXED_S2: [
        ("但是", False), ("下雨", False), ("，", False),
        ("我", False), ("很", False), ("高兴", False), ("。", False),
    ],
}


def _sentence_from_prompt(user):
    return user.split("句子：\n", 1)[1].rsplit("\n只输出 JSON。", 1)[0]


def make_chat(edits=None):
    """Fake DeepSeek: one story draft, segmentation, and one repair answer."""
    record = {"generation": [], "repairs": [], "segmented": []}
    repair_answer = edits if edits is not None else {
        "edits": [
            {"index": 1, "sentence": FIXED_S1},
            {"index": 2, "sentence": FIXED_S2},
        ],
        "append": [],
    }

    def chat(system, user, temperature=1.0, **kwargs):
        if "分词器" in system:
            sentence = _sentence_from_prompt(user)
            record["segmented"].append(sentence)
            return {
                "words": [
                    {"w": word, "proper": proper}
                    for word, proper in SEGMENTS[sentence]
                ]
            }
        if "分级读物的作者" in system:
            record["generation"].append(
                {"system": system, "user": user, "temperature": temperature}
            )
            return {"title": "公园里的下午", "sentences": [S1, S2, S3]}
        if "分级读物的编辑" in system:
            record["repairs"].append(
                {"system": system, "user": user, "temperature": temperature}
            )
            return repair_answer
        raise AssertionError(f"unexpected system prompt: {system[:80]}")

    return chat, record


@pytest.fixture(autouse=True)
def _clear_cache():
    grader.clear_cache()
    yield
    grader.clear_cache()


def test_loop_repairs_only_flagged_sentences_and_stops():
    chat, record = make_chat()

    result = story.generate_story(
        1, TARGET_LENGTH, seed_noun="公园", vocab=VOCAB, chat=chat
    )

    # One repair round fixed both OOV words, so the loop stops immediately.
    assert result["metrics"]["converged"] is True
    assert result["metrics"]["rounds"] == 1
    assert result["metrics"]["oov_count"] == 0
    assert len(record["repairs"]) == 1
    assert result["log"][0]["oov"] == ["打算", "虽然"]
    assert result["log"][-1]["oov"] == []
    assert result["log"][-1]["changed"] == [1, 2]

    # The seed is present and the story is the repaired version.
    assert result["seed_noun"] == "公园"
    assert "公园" in result["text"]
    assert result["text"] == "".join(result["sentences"])
    assert result["sentences"] == [FIXED_S1, FIXED_S2, S3]

    # Only flagged sentences appear in the rewrite list (not the clean one).
    request = record["repairs"][0]
    assert request["temperature"] == 0.3
    flagged_part = request["user"].split("需要改写的句子：", 1)[1]
    assert flagged_part.count("- 第 ") == 2
    assert S1 in flagged_part
    assert S2 in flagged_part
    assert S3 not in flagged_part
    assert "打扫" in flagged_part  # replacement candidate for 打算

    # Re-grading only touched the two edited sentences.
    assert record["segmented"].count(S3) == 1
    assert len(record["segmented"]) == 5

    # Generation prompt carries the constraints but no word list.
    generation = record["generation"][0]
    assert generation["temperature"] == 1.0
    assert "HSK 1" in generation["system"]
    assert "公园" in generation["system"]
    assert str(TARGET_LENGTH) in generation["system"]


def test_output_schema():
    chat, _record = make_chat()
    result = story.generate_story(
        1, TARGET_LENGTH, seed_noun="公园", vocab=VOCAB, chat=chat
    )

    assert set(result) == {
        "level",
        "seed_noun",
        "title",
        "text",
        "sentences",
        "tokens",
        "new_words",
        "metrics",
        "log",
    }
    assert set(result["metrics"]) == {
        "n_han",
        "oov_count",
        "rounds",
        "converged",
        "usage",
    }
    assert result["level"] == 1
    assert result["metrics"]["n_han"] == TARGET_LENGTH
    assert result["metrics"]["usage"]["calls"] == 0  # mocked chat, no API usage
    assert all(isinstance(sentence, str) for sentence in result["sentences"])
    assert len(result["tokens"]) == len(result["sentences"])


def test_stalled_repairs_return_the_best_version():
    chat, record = make_chat(edits={"edits": [], "append": []})

    result = story.generate_story(
        1, TARGET_LENGTH, seed_noun="公园", vocab=VOCAB, chat=chat, max_rounds=3
    )

    assert result["metrics"]["converged"] is False
    assert result["metrics"]["oov_count"] == 2
    assert result["metrics"]["rounds"] == 1
    assert [item["word"] for item in result["new_words"]] == ["打算", "虽然"]
    assert "no usable edits" in result["log"][-1]["note"]
    assert len(record["repairs"]) == 1


def test_build_noun_list_keeps_only_batch_words_and_caches(tmp_path):
    cache_path = tmp_path / "nouns_cache.json"
    table = {"猫": 1, "书": 1, "学校": 2}
    calls = []

    def noun_chat(system, user, temperature=0.0, **kwargs):
        assert "json" in system.lower()
        calls.append(user)
        return {"nouns": ["猫", "石头", "书"]}  # 石头 is not on the list

    nouns = story.build_noun_list(
        1, vocab=table, chat=noun_chat, cache_path=cache_path
    )
    assert nouns == ["猫", "书"]
    assert len(calls) == 1

    def boom(*args, **kwargs):
        raise AssertionError("cache should have been used")

    assert story.build_noun_list(
        1, vocab=table, chat=boom, cache_path=cache_path
    ) == ["猫", "书"]


def test_seed_selection_skips_recent_nouns(tmp_path):
    seeds_path = tmp_path / "seeds.json"
    nouns = ["书", "猫", "学校"]

    first = story.pick_seed_noun(
        1, nouns, rng=random.Random(0), seeds_path=seeds_path, recent=1
    )
    second = story.pick_seed_noun(
        1, nouns, rng=random.Random(0), seeds_path=seeds_path, recent=1
    )

    assert first in nouns
    assert second in nouns
    assert second != first


def test_invalid_level_is_rejected():
    with pytest.raises(ValueError):
        story.generate_story(
            9, 100, seed_noun="书", vocab=VOCAB, chat=make_chat()[0]
        )
