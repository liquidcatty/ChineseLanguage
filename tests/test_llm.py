from types import SimpleNamespace

import pytest

import llm


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
        )


def make_client(replies):
    completions = FakeCompletions(replies)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


@pytest.fixture(autouse=True)
def _reset_usage():
    llm.reset_usage()
    yield
    llm.reset_usage()


def test_chat_json_returns_an_object_and_uses_json_mode():
    client, completions = make_client(['{"title": "x"}'])

    assert llm.chat_json("system", "user", 0.0, client=client) == {"title": "x"}

    call = completions.calls[0]
    assert call["model"] == "deepseek-flash"
    assert call["response_format"] == {"type": "json_object"}
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert call["temperature"] == 0.0
    assert call["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert llm.usage_snapshot() == {
        "calls": 1,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_invalid_json_is_retried_once():
    client, completions = make_client(["not json", '{"ok": true}'])

    assert llm.chat_json("system", "user", 0.0, client=client) == {"ok": True}
    assert len(completions.calls) == 2


def test_two_invalid_json_replies_raise():
    client, completions = make_client(["not json", "still not json"])

    with pytest.raises(llm.LLMError):
        llm.chat_json("system", "user", 0.0, client=client)
    assert len(completions.calls) == 2


def test_api_errors_back_off_and_succeed():
    client, completions = make_client([RuntimeError("boom"), '{"ok": true}'])
    sleeps = []

    result = llm.chat_json(
        "system", "user", 0.0, client=client, sleep=sleeps.append
    )

    assert result == {"ok": True}
    assert len(completions.calls) == 2
    assert sleeps == [2.0]


def test_api_errors_give_up_after_three_retries():
    client, completions = make_client([RuntimeError("boom")] * 4)
    sleeps = []

    with pytest.raises(llm.LLMError):
        llm.chat_json("system", "user", 0.0, client=client, sleep=sleeps.append)

    assert len(completions.calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_model_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    client, completions = make_client(['{"ok": true}'])

    llm.chat_json("system", "user", 0.0, client=client)

    assert completions.calls[0]["model"] == "deepseek-v4-pro"


def test_missing_api_key_raises_a_clear_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(llm.LLMError):
        llm.api_key()
