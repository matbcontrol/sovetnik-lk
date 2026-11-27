"""Обращение к модели для разовой разметки каталога. На запрос клиента модель не вызывается.

Поставщик задаётся в .env и меняется без правки кода:
  LLM_PROVIDER=google             — Google AI Studio (GOOGLE_API_KEY, LLM_MODEL; запасные модели
                                    через запятую в LLM_MODELS — на бесплатном тарифе суточный лимит
                                    считается на каждую модель отдельно)
  LLM_PROVIDER=openai_compatible  — любой совместимый сервис: DeepSeek, Alibaba Model Studio, Yandex AI Studio
                                    (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL). Режим рассуждений задаёт
                                    LLM_THINKING: off (по умолчанию), on или auto; усилие — LLM_EFFORT.

Каждый вызов записывается в data/interim/llm_calls.jsonl (назначение, модель, токены, время) —
из этого журнала считается стоимость. Ответы кешируются по содержимому запроса: повторный
запуск разметки не тратит деньги и даёт тот же результат.

  python -m recsys.llm.client --list     показать модели, доступные по ключу
  python -m recsys.llm.client --ping     проверить, что ключ и модель работают
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from recsys.config import INTERIM, ROOT

CALLS = INTERIM / "llm_calls.jsonl"
CACHE = INTERIM / "llm_cache"
EXHAUSTED = INTERIM / "llm_exhausted.json"      # модели, у которых на сегодня исчерпан суточный лимит
QUIRKS: dict[str, dict] = {}                    # что совместимый сервис отверг у модели: «адрес|модель» -> настройки запроса


def load_env(path: Path = ROOT / ".env") -> None:
    """Читает .env в переменные окружения (уже заданные переменные не трогает)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self) -> None:
        load_env()
        self.provider = os.environ.get("LLM_PROVIDER", "google").strip()
        self.model = os.environ.get("LLM_MODEL", "").strip()
        # запасные модели: у бесплатного тарифа суточный лимит считается на каждую модель отдельно
        self.models = [m.strip() for m in os.environ.get("LLM_MODELS", "").split(",") if m.strip()]
        if self.model and self.model not in self.models:
            self.models.insert(0, self.model)
        self.exhausted: dict[str, str] = {}
        if EXHAUSTED.exists():
            try:
                self.exhausted = json.loads(EXHAUSTED.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.exhausted = {}
        if self.provider == "google":
            self.key = os.environ.get("GOOGLE_API_KEY", "").strip()
            self.base = "https://generativelanguage.googleapis.com/v1beta"
        else:
            self.key = os.environ.get("LLM_API_KEY", "").strip()
            self.base = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
            # запасной список мог остаться от Google: чужие модели на этом адресе не ответят, а очередь пойдёт их перебирать
            self.models = [m for m in self.models if not m.lower().startswith(("gemini", "gemma"))]
            if not self.base:
                raise LLMError("Адрес сервиса не задан: впишите LLM_BASE_URL в .env.")
        if not self.key:
            raise LLMError("Ключ модели не задан: скопируйте .env.example в .env и впишите ключ.")
        # Режим рассуждений у совместимого сервиса: off (по умолчанию) — выключить, on — включить, auto — как решит
        # сервис. С рассуждением ответ идёт в разы дольше и дороже (оно оплачивается как выход), поэтому по умолчанию
        # выключено; LLM_EFFORT — усилие рассуждений (low, high, max), если сервис его знает.
        self.thinking = os.environ.get("LLM_THINKING", "off").strip().lower() or "off"
        self.effort = os.environ.get("LLM_EFFORT", "").strip().lower()
        self.http = requests.Session()

    def _thinking_fields(self) -> dict:
        """Поля тела запроса, которыми совместимый сервис включает или выключает режим рассуждений.

        У сервисов они разные. Незнакомому сервису ничего не шлём — он решает сам; так же ведёт себя LLM_THINKING=auto.
        """
        if self.provider == "google" or self.thinking not in ("on", "off"):
            return {}
        on = self.thinking == "on"
        if "deepseek" in self.base:                                 # api-docs.deepseek.com, раздел Thinking Mode
            fields: dict = {"thinking": {"type": "enabled" if on else "disabled"}}
            if on and self.effort in ("low", "high", "max"):
                fields["reasoning_effort"] = self.effort
            return fields
        if "dashscope" in self.base or "aliyuncs" in self.base:     # Alibaba Model Studio
            return {"enable_thinking": on}
        return {}

    def _scrub(self, text: str) -> str:
        """Текст ошибки сервиса без ключа: некоторые сервисы повторяют его в ответе, а ошибка уходит в журнал и на стенд."""
        if self.key:
            text = text.replace(self.key, "<ключ>")
        return re.sub(r"\b(sk|ak|key)-[A-Za-z0-9*_\-]{6,}", "<ключ>", text)

    # ---- служебное -----------------------------------------------------------------------

    def list_models(self) -> list[str]:
        if self.provider == "google":
            r = self.http.get(f"{self.base}/models", headers={"x-goog-api-key": self.key},
                              params={"pageSize": 200}, timeout=60)
            r.raise_for_status()
            return [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])]
        r = self.http.get(f"{self.base}/models", headers={"Authorization": f"Bearer {self.key}"}, timeout=60)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    def _log(self, purpose: str, tokens_in: int, tokens_out: int, seconds: float, cached: bool) -> None:
        spent = self.__dict__.setdefault("spent", [0, 0, 0])    # счётчик этого объекта: токены на вход, на выход, вызовы
        if not cached:
            spent[0] += tokens_in
            spent[1] += tokens_out
            spent[2] += 1
        CALLS.parent.mkdir(parents=True, exist_ok=True)
        with CALLS.open("a", encoding="utf-8") as f:
            row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "purpose": purpose, "provider": self.provider,
                   "model": self.model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                   "seconds": round(seconds, 2), "cached": cached}
            if self._thinking_fields():
                row["thinking"] = self.thinking                 # с рассуждением выход дороже: расход считается отдельно
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---- вызов ---------------------------------------------------------------------------

    def _digest(self, model: str, system: str, user: str, temperature: float) -> str:
        key = [self.provider, model, system, user, temperature]
        if self.thinking == "on" and self._thinking_fields():
            key.append("thinking:" + (self.effort or "on"))    # ответ с рассуждением — другой ответ: кеш у него свой
        return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]

    def _mark_exhausted(self, model: str) -> None:
        self.exhausted[model] = time.strftime("%Y-%m-%d")
        EXHAUSTED.parent.mkdir(parents=True, exist_ok=True)
        EXHAUSTED.write_text(json.dumps(self.exhausted, ensure_ascii=False), encoding="utf-8")

    def json(self, system: str, user: str, purpose: str, temperature: float = 0.0, retries: int = 8) -> dict | list:
        """Один вызов модели с ответом в виде JSON.

        Одинаковый запрос второй раз берётся из кеша (ответ любой модели из списка годится).
        Если у модели исчерпан суточный лимит — она пропускается до завтра и берётся следующая;
        при временной перегрузке модель уходит в конец очереди.
        """
        if not self.models:
            raise LLMError("Модель не выбрана: впишите LLM_MODEL в .env (список: python -m recsys.llm.client --list).")
        for m in self.models:
            cache_file = CACHE / f"{self._digest(m, system, user, temperature)}.json"
            if cache_file.exists():
                self.model = m
                self._log(purpose, 0, 0, 0.0, True)
                return json.loads(cache_file.read_text(encoding="utf-8"))
        today = time.strftime("%Y-%m-%d")
        queue = [m for m in self.models if self.exhausted.get(m) != today]
        last, not_json = "", 0
        for attempt in range(retries):
            if not queue:
                raise LLMError(f"Суточный лимит исчерпан у всех моделей из списка ({purpose}). Последняя ошибка: {last}")
            self.model = queue[0]
            t0 = time.time()
            try:
                # ответ пришёл, но не JSON (у части сервисов — одни пробелы): тот же запрос при той же температуре
                # вернул бы то же самое, поэтому повтор идёт чуть «теплее»
                text, t_in, t_out = self._call(system, user, min(0.6, temperature + 0.2 * not_json))
                data = json.loads(_strip_fence(text))
            except (requests.RequestException, LLMError, json.JSONDecodeError, KeyError, IndexError) as e:
                last = f"{type(e).__name__}: {str(e)[:900]}"
                if os.environ.get("LLM_DEBUG"):
                    print(f"  [{self.model}, попытка {attempt + 1}] {last[:260]}", file=sys.stderr, flush=True)
                if "HTTP 429" in last and ("per day" in last or "PerDay" in last):
                    self._mark_exhausted(self.model)          # суточный лимит: до завтра эту модель не трогаем
                    queue.pop(0)
                    continue
                if isinstance(e, json.JSONDecodeError):
                    not_json += 1
                    time.sleep(1)                             # сервис жив, ждать нечего
                elif len(queue) > 1:
                    queue.append(queue.pop(0))                # перегрузка или сбой: пробуем следующую модель
                    time.sleep(min(20, 2 * (attempt + 1)))
                else:
                    time.sleep(min(90, 8 * 2 ** attempt))
                continue
            self._log(purpose, t_in, t_out, time.time() - t0, False)
            CACHE.mkdir(parents=True, exist_ok=True)
            (CACHE / f"{self._digest(self.model, system, user, temperature)}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        raise LLMError(f"Модель не ответила после {retries} попыток ({purpose}): {last}")

    def _call(self, system: str, user: str, temperature: float) -> tuple[str, int, int]:
        if self.provider == "google":
            r = self.http.post(
                f"{self.base}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
                }, timeout=120)
            if r.status_code != 200:
                raise LLMError(f"HTTP {r.status_code}: {self._scrub(r.text[:900])}")
            body = r.json()
            text = "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"])
            usage = body.get("usageMetadata", {})
            return text, int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0))
        # Совместимые сервисы расходятся в мелочах: одни не знают response_format, другие требуют выключить режим
        # рассуждений у ответа без потоковой выдачи. Что сервис отверг у модели — запоминаем и больше не шлём.
        quirks = QUIRKS.setdefault(f"{self.base}|{self.model}", {})
        if "json" not in (system + user).lower():
            system += " Ответ — только JSON."                    # режим JSON-ответа требует слова «json» в запросе
        for _ in range(4):
            body = {"model": self.model, "temperature": temperature,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if not quirks.get("plain"):
                body["response_format"] = {"type": "json_object"}
            thinking = {} if quirks.get("no_thinking") else self._thinking_fields()
            body.update(thinking)
            if "enable_thinking" in quirks:
                body["enable_thinking"] = quirks["enable_thinking"]
            r = self.http.post(f"{self.base}/chat/completions",
                               headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                               json=body, timeout=300 if self.thinking == "on" else 180)
            if r.status_code == 200:
                break
            text = r.text[:600]
            if r.status_code == 400 and "enable_thinking" in text and quirks.get("enable_thinking") is not False:
                quirks["enable_thinking"] = False                # ответ целиком, без потока: рассуждения выключаем
                continue
            if r.status_code == 400 and not quirks.get("plain") and ("response_format" in text or "json_object" in text):
                quirks["plain"] = True                           # формат ответа задаём только словами запроса
                continue
            if r.status_code == 400 and thinking and ("thinking" in text or "reasoning_effort" in text):
                quirks["no_thinking"] = True                     # сервис не знает наших полей режима: решает сам
                continue
            raise LLMError(f"HTTP {r.status_code}: {self._scrub(text[:300])}")
        else:
            raise LLMError(f"HTTP {r.status_code}: {self._scrub(r.text[:300])}")
        data = r.json()
        usage = data.get("usage") or {}
        content = data["choices"][0]["message"].get("content") or ""
        if isinstance(content, list):                            # ответ частями: берём только текст
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def usage_summary() -> dict:
    """Сколько токенов ушло на разметку — по назначениям. Основа для расчёта стоимости."""
    out: dict[str, dict] = {}
    if not CALLS.exists():
        return out
    for line in CALLS.read_text(encoding="utf-8").splitlines():
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        row = out.setdefault(c["purpose"], {"calls": 0, "cached": 0, "tokens_in": 0, "tokens_out": 0, "model": c["model"]})
        row["calls"] += 0 if c["cached"] else 1
        row["cached"] += 1 if c["cached"] else 0
        row["tokens_in"] += c["tokens_in"]
        row["tokens_out"] += c["tokens_out"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ping", action="store_true")
    args = ap.parse_args()
    llm = LLM()
    if args.list:
        for m in llm.list_models():
            print(m)
    if args.ping:
        print(llm.json("Отвечай только JSON.", 'Верни {"ok": true}', purpose="проверка связи"))


if __name__ == "__main__":
    main()
