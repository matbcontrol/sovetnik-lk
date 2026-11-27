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
    llm.http = Http([Reply(500, "Internal error for key sk-test-0123456789abcdef. Also sk-other-AAAABBBBCCCC")])
    with pytest.raises(client.LLMError) as e:
        llm._call("system", "user", 0.0)
    assert "sk-" not in str(e.value) and "<ключ>" in str(e.value)


def test_bad_key_or_empty_balance_is_not_retried(llm, monkeypatch, tmp_path):
    """Ключ не принят или кончились деньги: одна попытка и понятная фраза, а не восемь повторов с паузами."""
    monkeypatch.setattr(client, "CALLS", tmp_path / "calls.jsonl")
    monkeypatch.setattr(client, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(client.time, "sleep", lambda s: pytest.fail("повторять нельзя"))
    for status, word in ((401, "ключ"), (402, "деньги")):
        llm.http = Http([Reply(status, "Insufficient Balance for sk-test-0123456789abcdef")])
        with pytest.raises(client.LLMFatal) as e:
            llm.json("Отвечай JSON.", "user", purpose="тест")
        assert word in str(e.value) and "sk-" not in str(e.value) and len(llm.http.sent) == 1


@pytest.fixture
def deepseek(monkeypatch):
    def make(**env):
        monkeypatch.setattr(client, "load_env", lambda *a, **k: None)
        monkeypatch.setattr(client, "QUIRKS", {})
        for k, v in {"LLM_PROVIDER": "openai_compatible", "LLM_BASE_URL": "https://api.deepseek.com",
                     "LLM_API_KEY": "sk-test-0123456789abcdef", "LLM_MODEL": "deepseek-v4-pro",
                     "LLM_MODELS": "deepseek-v4-pro", **env}.items():
            monkeypatch.setenv(k, v)
        for k in ("LLM_THINKING", "LLM_EFFORT"):
            if k not in env:
                monkeypatch.delenv(k, raising=False)
        llm = client.LLM()
        llm.model = "deepseek-v4-pro"
        return llm
    return make


def test_deepseek_thinking_is_off_unless_asked(deepseek):
    llm = deepseek()
    llm.http = Http([OK])
    llm._call("Отвечай JSON.", "user", 0.0)
    assert llm.http.sent[0]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in llm.http.sent[0]
    on = deepseek(LLM_THINKING="on", LLM_EFFORT="max")
    on.http = Http([OK])
    on._call("Отвечай JSON.", "user", 0.0)
    assert on.http.sent[0]["thinking"] == {"type": "enabled"} and on.http.sent[0]["reasoning_effort"] == "max"
    auto = deepseek(LLM_THINKING="auto")
    auto.http = Http([OK])
    auto._call("Отвечай JSON.", "user", 0.0)
    assert "thinking" not in auto.http.sent[0]


def test_answer_with_thinking_has_its_own_cache(deepseek):
    off = deepseek()._digest("deepseek-v4-pro", "s", "u", 0.0)
    assert deepseek(LLM_THINKING="on")._digest("deepseek-v4-pro", "s", "u", 0.0) != off
    assert deepseek(LLM_THINKING="auto")._digest("deepseek-v4-pro", "s", "u", 0.0) == off


def test_unknown_thinking_field_is_dropped(deepseek):
    llm = deepseek()
    llm.http = Http([Reply(400, "Unrecognized request argument supplied: thinking"), OK])
    assert llm._call("Отвечай JSON.", "user", 0.0)[0] == '{"ok": true}'
    assert "thinking" in llm.http.sent[0] and "thinking" not in llm.http.sent[1]


def test_json_word_is_added_when_prompt_lacks_it(deepseek):
    llm = deepseek()
    llm.http = Http([OK])
    llm._call("Ты менеджер.", "Оцени пару.", 0.0)
    assert "json" in llm.http.sent[0]["messages"][0]["content"].lower()


def test_blank_answer_is_retried_warmer(deepseek, monkeypatch, tmp_path):
    """Сервис вернул одни пробелы вместо JSON: повтор идёт сразу, теплее и без режима JSON-ответа."""
    monkeypatch.setattr(client, "CALLS", tmp_path / "calls.jsonl")
    monkeypatch.setattr(client, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    blank = Reply(200, data={"choices": [{"message": {"content": "   "}}], "usage": {}})
    llm = deepseek()
    llm.http = Http([blank, OK])
    assert llm.json("Отвечай JSON.", "user", purpose="тест") == {"ok": True}
    assert [b["temperature"] for b in llm.http.sent] == [0.0, 0.2]
    assert "response_format" in llm.http.sent[0] and "response_format" not in llm.http.sent[1]
    assert '"thinking": "off"' in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
