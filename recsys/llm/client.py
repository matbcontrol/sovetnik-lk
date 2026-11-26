"""Обращение к модели для разовой разметки каталога. На запрос клиента модель не вызывается.

Поставщик задаётся в .env и меняется без правки кода:
  LLM_PROVIDER=google             — Google AI Studio (GOOGLE_API_KEY, LLM_MODEL; запасные модели
                                    через запятую в LLM_MODELS — на бесплатном тарифе суточный лимит
                                    считается на каждую модель отдельно)
  LLM_PROVIDER=openai_compatible  — любой совместимый сервис, например Yandex AI Studio
                                    (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)

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
import sys
import time
from pathlib import Path

import requests

from recsys.config import INTERIM, ROOT

CALLS = INTERIM / "llm_calls.jsonl"
CACHE = INTERIM / "llm_cache"
EXHAUSTED = INTERIM / "llm_exhausted.json"      # модели, у которых на сегодня исчерпан суточный лимит


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
        if not self.key:
            raise LLMError("Ключ модели не задан: скопируйте .env.example в .env и впишите ключ.")
        self.http = requests.Session()

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
        CALLS.parent.mkdir(parents=True, exist_ok=True)
        with CALLS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "purpose": purpose, "provider": self.provider,
                "model": self.model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                "seconds": round(seconds, 2), "cached": cached,
            }, ensure_ascii=False) + "\n")

    # ---- вызов ---------------------------------------------------------------------------

    def _digest(self, model: str, system: str, user: str, temperature: float) -> str:
        return hashlib.sha256(json.dumps([self.provider, model, system, user, temperature],
                                         ensure_ascii=False).encode("utf-8")).hexdigest()[:32]

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
        last = ""
        for attempt in range(retries):
            if not queue:
                raise LLMError(f"Суточный лимит исчерпан у всех моделей из списка ({purpose}). Последняя ошибка: {last}")
            self.model = queue[0]
            t0 = time.time()
            try:
                text, t_in, t_out = self._call(system, user, temperature)
                data = json.loads(_strip_fence(text))
            except (requests.RequestException, LLMError, json.JSONDecodeError, KeyError, IndexError) as e:
                last = f"{type(e).__name__}: {str(e)[:900]}"
                if os.environ.get("LLM_DEBUG"):
                    print(f"  [{self.model}, попытка {attempt + 1}] {last[:260]}", file=sys.stderr, flush=True)
                if "HTTP 429" in last and ("per day" in last or "PerDay" in last):
                    self._mark_exhausted(self.model)          # суточный лимит: до завтра эту модель не трогаем
                    queue.pop(0)
                    continue
                if len(queue) > 1:
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
                raise LLMError(f"HTTP {r.status_code}: {r.text[:900]}")
            body = r.json()
            text = "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"])
            usage = body.get("usageMetadata", {})
            return text, int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0))
        r = self.http.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json={
                "model": self.model, "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }, timeout=120)
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        usage = body.get("usage", {})
        return (body["choices"][0]["message"]["content"],
                int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))


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
