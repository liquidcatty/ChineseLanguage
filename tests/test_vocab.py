import pytest

import vocab


def test_bundled_syllabus_has_10896_words():
    table = vocab.load_vocab()
    assert len(table) == 10896
    assert table["爱"] == 1
    assert table["打算"] == 3


def test_word_level_returns_none_for_unknown_words():
    assert vocab.word_level("亚历山大图书馆") is None


def test_level_rank_maps_7_9_to_seven():
    assert vocab.level_rank(1) == 1
    assert vocab.level_rank("6") == 6
    assert vocab.level_rank("7-9") == 7
    assert vocab.level_rank("7–9") == 7


def test_level_rank_rejects_bad_levels():
    with pytest.raises(ValueError):
        vocab.level_rank(0)
    with pytest.raises(ValueError):
        vocab.level_rank("7")


def test_load_vocab_from_custom_csv(tmp_path):
    path = tmp_path / "mini.csv"
    path.write_text("word,level\n猫,1\n打扫,3\n学校,7-9\n", encoding="utf-8")
    table = vocab.load_vocab(path)
    assert table == {"猫": 1, "打扫": 3, "学校": "7-9"}
    assert vocab.is_allowed("打扫", 3, table)
    assert not vocab.is_allowed("打扫", 1, table)


def test_candidate_replacements_share_a_character_and_stay_allowed():
    table = {"打算": 2, "打扫": 1, "计算": 1, "虽然": 3, "想": 1}
    candidates = vocab.candidate_replacements("打算", 1, table)
    assert "打扫" in candidates  # shares 打
    assert "计算" in candidates  # shares 算
    assert "打算" not in candidates
    assert "虽然" not in candidates  # above level 1
    assert len(candidates) <= 8


def test_words_at_level_is_exact_not_cumulative():
    table = {"猫": 1, "书": 1, "学校": 2, "公园": "7-9"}
    assert vocab.words_at_level(1, table) == ["书", "猫"]
    assert vocab.words_at_level("7-9", table) == ["公园"]
