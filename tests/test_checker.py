import checker

STORY = {"sentences": ["小美喜欢书。", "她在家看书。", "妈妈叫她吃饭。"]}

QUESTIONS = [
    {"type": "tf", "statement": "小美喜欢书。", "answer": "对", "evidence": [0]},
    {"type": "tf", "statement": "妈妈叫小美看书。", "answer": "错", "evidence": [2]},
    {
        "type": "mc",
        "question": "小美在哪儿看书？",
        "options": ["家", "学校", "商店"],
        "answer": "A",
        "evidence": [1],
    },
]


def test_tf_true_forms_normalize_to_dui():
    for value in ("对", "T", "t", "true", "TRUE", " 对。 "):
        assert checker.normalize_answer(value, "tf") == "对"


def test_tf_false_forms_normalize_to_cuo():
    for value in ("错", "F", "false", "False", "错！"):
        assert checker.normalize_answer(value, "tf") == "错"


def test_mc_answers_are_case_insensitive():
    for value in ("A", "a", "A.", "a、", "Ａ"):
        assert checker.normalize_answer(value, "mc") == "A"
    for value in ("D", "E", "", "对"):
        assert checker.normalize_answer(value, "mc") == ""


def test_scoring_and_evidence_quoting():
    report = checker.check_answers(QUESTIONS, ["T", "false", "B"], STORY)

    assert report["score"] == 2
    assert report["total"] == 3
    assert [item["index"] for item in report["results"]] == [1, 2, 3]
    assert report["results"][0]["is_correct"] is True
    assert report["results"][1]["is_correct"] is True
    assert report["results"][2]["is_correct"] is False
    assert report["results"][2]["user_answer"] == "B"
    assert report["results"][2]["correct_answer"] == "A"
    assert report["results"][2]["evidence_sentences"] == ["她在家看书。"]


def test_wrong_tf_answer_quotes_its_evidence():
    report = checker.check_answers(QUESTIONS, ["对", "对", "A"], STORY)

    wrong = report["results"][1]
    assert wrong["is_correct"] is False
    assert wrong["user_answer"] == "对"
    assert wrong["correct_answer"] == "错"
    assert wrong["evidence_sentences"] == ["妈妈叫她吃饭。"]


def test_blank_and_unrecognised_answers_are_wrong():
    report = checker.check_answers(QUESTIONS, ["", "maybe", "E"], STORY)

    assert report["score"] == 0
    assert [item["is_correct"] for item in report["results"]] == [False, False, False]
    assert report["results"][1]["user_answer"] == "maybe"


def test_missing_answers_are_treated_as_blank():
    report = checker.check_answers(QUESTIONS, ["对"], STORY)

    assert report["score"] == 1
    assert report["results"][1]["user_answer"] == ""
    assert report["results"][2]["user_answer"] == ""


def test_story_can_be_a_plain_sentence_list():
    report = checker.check_answers(
        QUESTIONS[:1], ["对"], STORY["sentences"]
    )
    assert report["score"] == 1
    assert report["results"][0]["evidence_sentences"] == ["小美喜欢书。"]


def test_invalid_evidence_indices_are_ignored():
    question = {
        "type": "tf",
        "statement": "小美喜欢书。",
        "answer": "对",
        "evidence": [99, -1, True, 0],
    }
    report = checker.check_answers([question], ["对"], STORY)
    assert report["results"][0]["evidence_sentences"] == ["小美喜欢书。"]
