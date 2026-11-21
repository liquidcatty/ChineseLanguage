import json

import httpx
import pytest

import supabase_store


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Keep the developer's real .env out of the tests."""
    for name in (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_STORIES_TABLE",
        "SUPABASE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(supabase_store, "_client", None)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://demo.supabase.co/")
    monkeypatch.setenv("SUPABASE_KEY", "sb_secret_test")
    return monkeypatch


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)


def test_configured_requires_url_and_key():
    assert not supabase_store.configured()


def test_template_placeholders_are_not_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://your-project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_secret_your-secret-key")

    assert not supabase_store.configured()
    with pytest.raises(supabase_store.SupabaseError):
        supabase_store.url()
    with pytest.raises(supabase_store.SupabaseError):
        supabase_store.api_key()


def test_configured_with_url_and_key(env):
    assert supabase_store.configured()
    assert supabase_store.url() == "https://demo.supabase.co"
    assert supabase_store.api_key() == "sb_secret_test"
    assert supabase_store.table() == "stories"


def test_level_and_created_are_normalized():
    assert supabase_store._level_text(3) == "3"
    assert supabase_store._level_text("7-9") == "7-9"
    assert supabase_store._level_value("7-9") == "7-9"
    assert supabase_store._created_value("2026-11-21T05:00:00Z") == (
        "2026-11-21T05:00:00+00:00"
    )
    assert supabase_store._created_value("not a date") is None


def test_story_row_derives_summary_columns():
    story = {
        "id": "bank-l1-abc",
        "title": "过河",
        "level": 1,
        "seed_noun": "河",
        "created": "2026-11-21T05:00:00Z",
        "bank": True,
        "finished": True,
        "metrics": {"n_han": 150},
        "questions": [{"a": 1}, {"a": 2}],
        "sentences": ["你好。"],
    }
    row = supabase_store.story_row(story)
    assert row["id"] == "bank-l1-abc"
    assert row["title"] == "过河"
    assert row["level"] == "1"
    assert row["created"] == "2026-11-21T05:00:00+00:00"
    assert row["seed_noun"] == "河"
    assert row["bank"] is True
    assert row["finished"] is True
    assert row["n_han"] == 150
    assert row["n_questions"] == 2
    assert row["data"]["id"] == "bank-l1-abc"
    assert row["data"]["sentences"] == ["你好。"]


def test_story_row_rejects_invalid_ids():
    with pytest.raises(supabase_store.SupabaseError):
        supabase_store.story_row({"id": "../etc/passwd", "title": "x"})


def test_list_and_get_use_postgrest(env):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["apikey"] == "sb_secret_test"
        assert request.headers["authorization"] == "Bearer sb_secret_test"
        select = request.url.params.get("select", "")
        if request.method == "GET" and select == supabase_store.SUMMARY_COLUMNS:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "sample",
                        "title": "过河看奶奶",
                        "level": "1",
                        "created": "2026-11-20T20:56:53+00:00",
                        "seed_noun": "河",
                        "bank": False,
                        "finished": False,
                        "n_han": 120,
                        "n_questions": 3,
                    }
                ],
            )
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"data": {"id": "sample", "title": "过河看奶奶"}}]
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    http = make_client(handler)
    items = supabase_store.list_stories(http=http)
    assert items == [
        {
            "id": "sample",
            "title": "过河看奶奶",
            "level": 1,
            "created": "2026-11-20T20:56:53+00:00",
            "seed_noun": "河",
            "bank": False,
            "finished": False,
            "n_han": 120,
            "n_questions": 3,
        }
    ]

    story = supabase_store.get_story("sample", http=http)
    assert story == {"id": "sample", "title": "过河看奶奶"}
    assert seen[1].url.params["id"] == "eq.sample"
    assert str(seen[0].url).startswith("https://demo.supabase.co/rest/v1/stories?")


def test_get_missing_story_returns_none(env):
    http = make_client(lambda request: httpx.Response(200, json=[]))
    assert supabase_store.get_story("nope", http=http) is None


def test_save_stories_upserts_in_batches(env):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.params["on_conflict"] == "id"
        assert request.headers["prefer"] == "resolution=merge-duplicates,return=minimal"
        return httpx.Response(201)

    http = make_client(handler)
    stories = [
        {"id": f"s{index}", "title": "t", "created": "2026-11-21T00:00:00Z"}
        for index in range(5)
    ]
    assert supabase_store.save_stories(stories, batch_size=2, http=http) == 5
    assert [len(json.loads(request.content)) for request in requests] == [2, 2, 1]


def test_http_errors_become_supabase_errors(env):
    http = make_client(lambda request: httpx.Response(401, json={"message": "bad key"}))
    with pytest.raises(supabase_store.SupabaseError) as info:
        supabase_store.list_stories(http=http)
    assert "401" in str(info.value)
    assert "bad key" in str(info.value)


def test_fetch_stories_paginates(env):
    pages = {
        "0": [{"data": {"id": "a"}}, {"data": {"id": "b"}}],
        "2": [{"data": {"id": "c"}}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params["offset"]])

    http = make_client(handler)
    stories = supabase_store.fetch_stories(page_size=2, http=http)
    assert [story["id"] for story in stories] == ["a", "b", "c"]


def test_story_ids_paginates(env):
    pages = {"0": [{"id": "a"}, {"id": "b"}], "2": [{"id": "c"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params["offset"]])

    http = make_client(handler)
    assert supabase_store.story_ids(page_size=2, http=http) == {"a", "b", "c"}

