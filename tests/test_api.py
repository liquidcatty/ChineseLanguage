import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "stories" / "sample.json"
TOKEN_KEYS = {"w", "pinyin", "gloss_pl", "gloss_en", "level", "status", "proper"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app_module.app)


def test_index_and_static_files(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "HSK Story Library" in index.text
    reader = client.get("/read/sample")
    assert reader.status_code == 200
    assert "HSK Reader" in reader.text
    listener = client.get("/listen/sample")
    assert listener.status_code == 200
    assert "HSK Listening" in listener.text
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/library.js").status_code == 200
    assert client.get("/static/reader.js").status_code == 200
    assert client.get("/static/listening.js").status_code == 200


def test_finished_flag_toggle(client):
    original = bool(client.get("/api/stories/sample").json().get("finished", False))
    try:
        response = client.post(
            "/api/stories/sample/finished", json={"finished": not original}
        )
        assert response.status_code == 200
        assert response.json()["finished"] is (not original)
        assert (
            client.get("/api/stories/sample").json()["finished"] is (not original)
        )
        listed = {item["id"]: item for item in client.get("/api/stories").json()}
        assert listed["sample"]["finished"] is (not original)
    finally:
        client.post("/api/stories/sample/finished", json={"finished": original})


def test_list_and_load_the_sample_story(client):
    items = client.get("/api/stories").json()
    assert "sample" in {item["id"] for item in items}

    story = client.get("/api/stories/sample").json()
    assert story["title"] == "过河看奶奶"
    assert len(story["tokens"]) == len(story["sentences"])
    assert set(story["tokens"][0][0]) == TOKEN_KEYS
    assert story["questions"]
    assert story["questions"][0]["question_tokens"]
    assert len(story["questions"][0]["option_tokens"]) == 3


def test_check_endpoint_scores_the_sample(client):
    story = client.get("/api/stories/sample").json()
    answers = [question["answer"] for question in story["questions"]]

    result = client.post(
        "/api/check", json={"story_id": "sample", "answers": answers}
    ).json()

    assert result["score"] == len(answers) == result["total"]
    assert all(item["is_correct"] for item in result["results"])
    assert result["results"][0]["evidence_sentences"]


def test_missing_story_is_404(client):
    assert client.get("/api/stories/nope").status_code == 404
    assert client.get("/api/stories/..%2Fapp").status_code == 404
    assert (
        client.post("/api/check", json={"story_id": "nope", "answers": []}).status_code
        == 404
    )


def test_create_story_saves_a_json_file(monkeypatch, tmp_path, client):
    import questions as questions_module
    import story as story_module

    monkeypatch.setattr(app_module, "STORIES_DIR", tmp_path)
    fake_story = {
        "level": 3,
        "seed_noun": "书",
        "title": "测试故事",
        "text": "我在家看书。",
        "sentences": ["我在家看书。"],
        "tokens": [
            [
                {
                    "w": "我",
                    "pinyin": ["wǒ"],
                    "gloss_pl": "ja",
                    "gloss_en": "I",
                    "level": 1,
                    "status": "ok",
                    "proper": False,
                }
            ]
        ],
        "new_words": [],
        "metrics": {
            "n_han": 6,
            "oov_count": 0,
            "rounds": 0,
            "converged": True,
            "usage": {},
        },
        "log": [],
    }
    fake_quiz = {
        "questions": [],
        "metrics": {"rounds": 0, "failed_by_reason": {}, "usage": {}},
        "complete": False,
    }
    monkeypatch.setattr(story_module, "generate_story", lambda *a, **k: fake_story)
    monkeypatch.setattr(questions_module, "generate_quiz", lambda *a, **k: fake_quiz)

    response = client.post(
        "/api/story", json={"level": 3, "length": 100, "n_questions": 1}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "测试故事"
    assert data["questions"] == []
    assert data["quiz_complete"] is False
    assert data["id"]
    saved = json.loads((tmp_path / f"{data['id']}.json").read_text(encoding="utf-8"))
    assert saved["title"] == "测试故事"
