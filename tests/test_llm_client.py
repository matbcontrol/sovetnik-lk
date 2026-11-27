"""Клиент модели на совместимом сервисе: подстройка под отказ сервиса и ключ, который не должен попасть в текст ошибки."""
import pytest

from recsys.llm import client


class Reply:
    def __init__(self, status: int, text: str = "", data: dict | None = None) -> None:
        self.status_code, self.text, self._data = status, text, data or {}

    def json(self) -> dict:
        return self._data


class Http:
    """Поддельная сессия: отвечает по очереди и запоминает тела запросов."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies, self.sent = list(replies), []

    def post(self, url: str, headers: dict, json: dict, timeout: int) -> Reply:
        self.sent.append(json)
        return self.replies.pop(0)


OK = Reply(200, data={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(client, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(client, "QUIRKS", {})
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-0123456789abcdef")
    monkeypatch.setenv("LLM_MODEL", "strong-model")
    monkeypatch.setenv("LLM_MODELS", "gemini-3.6-flash, gemini-3.5-flash-lite")
    return client.LLM()


def test_foreign_spare_models_are_dropped(llm):
    assert llm.models == ["strong-model"]
    assert llm.base == "https://example.test/v1"


def test_thinking_is_switched_off_when_service_demands(llm):
    llm.model = "strong-model"
    llm.http = Http([Reply(400, "parameter.enable_thinking must be set to false for non-streaming calls"), OK])
    text, t_in, t_out = llm._call("system", "user", 0.0)
    assert (text, t_in, t_out) == ('{"ok": true}', 7, 3)
    assert "enable_thinking" not in llm.http.sent[0] and llm.http.sent[1]["enable_thinking"] is False
    llm.http = Http([OK])                                  # отказ запомнен: второй запрос сразу верный
    llm._call("system", "user", 0.0)
    assert llm.http.sent[0]["enable_thinking"] is False


def test_response_format_is_dropped_when_unknown(llm):
    llm.model = "strong-model"
    llm.http = Http([Reply(400, "Unknown parameter: response_format"), OK])
    assert llm._call("system", "user", 0.0)[0] == '{"ok": true}'
    assert "response_format" in llm.http.sent[0] and "response_format" not in llm.http.sent[1]


def test_key_never_reaches_error_text(llm):
    llm.model = "strong-model"
    llm.http = Http([Reply(401, "Incorrect API key provided: sk-test-0123456789abcdef. Also sk-other-AAAABBBBCCCC")])
    with pytest.raises(client.LLMError) as e:
        llm._call("system", "user", 0.0)
    assert "sk-" not in str(e.value) and "<ключ>" in str(e.value)
