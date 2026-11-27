"""Локальный сервер стенда: отдаёт страницу и запускает агента каталога по запросу.

Зачем нужен сервер: ключ модели нельзя класть в страницу, поэтому живой прогон агента
(«завели товар — он встал в граф») идёт через этот процесс. Сами советы клиенту по-прежнему
считаются в браузере без сервера и без модели.

Запуск:  python -m recsys.server            стенд на http://127.0.0.1:8765
Сервер слушает только этот компьютер.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:                      # запуск файлом, а не модулем
    sys.path.insert(0, str(ROOT))

from recsys import events, pilot  # noqa: E402
from recsys.agent.agent import place  # noqa: E402
from recsys.agent.tools import Catalog  # noqa: E402
from recsys.llm.client import LLM, LLMError  # noqa: E402

LOCK = threading.Lock()                            # агент работает по одному запросу за раз
# адреса, которые кабинет вызывает со своей страницы (другой домен): совет, журнал, отчёт пилота.
# Агента (/api/place, /api/hole) чужим страницам не открываем — он тратит вызовы модели.
OPEN = ("/api/advice", "/api/event", "/api/pilot")


def agent_ready() -> dict:
    """Можно ли прямо сейчас запустить агента: есть ли ключ и модель, у которой не исчерпан суточный лимит."""
    import time

    try:
        llm = LLM()
    except Exception as e:                             # нет файла настроек — агент недоступен, стенд работает без него
        return {"agent": False, "why": f"модель не настроена: {type(e).__name__}"}
    if not getattr(llm, "key", ""):
        return {"agent": False, "why": "нет ключа модели: стенд показывает записанные прогоны агента"}
    today = time.strftime("%Y-%m-%d")
    free = [m for m in llm.models if llm.exhausted.get(m) != today]
    if not free:
        return {"agent": False, "why": "суточный лимит модели исчерпан: стенд показывает записанные прогоны агента"}
    try:
        import recsys.agent.holes  # noqa: F401 — режим «закрыть дыру» есть не в каждой сборке
        holes = True
    except Exception:                                  # модуля нет или он ещё не дописан: стенд работает без него
        holes = False
    return {"agent": True, "model": free[0], "holes": holes}


STATE: dict = {}
PROGRESS: dict = {"family": "", "steps": [], "done": True}   # что агент делает прямо сейчас: страница читает по ходу работы


def catalog() -> Catalog:
    """Каталог для агента. Если подключён режим «закрыть дыру», берём его расширенный каталог: один объект на оба режима."""
    if "cat" not in STATE:
        try:
            from recsys.agent.holes import HoleTools
            STATE["cat"] = HoleTools()
        except Exception:                              # режима нет или его данные не собраны: «вынь и вставь» работает и без него
            STATE["cat"] = Catalog()
    return STATE["cat"]


def brief(result: object) -> str:
    """Одна строка о том, что вернул инструмент: для показа шага на странице, пока агент ещё работает."""
    if isinstance(result, dict) and isinstance(result.get("pairs"), list):
        result = result["pairs"]
    if isinstance(result, list):
        names = [str(x.get("name", ""))[:46] for x in result if isinstance(x, dict) and x.get("name")]
        notes = [str(x.get("note", "")) for x in result if isinstance(x, dict) and x.get("note")]
        return f"найдено {len(names)}: " + "; ".join(names[:4]) if names else (notes[0] if notes else "ничего не найдено")
    if isinstance(result, dict):
        return str(result.get("error") or " · ".join(str(result[k]) for k in ("name", "class", "sale") if result.get(k)))[:160]
    return ""


class Watched:
    """Обёртка над каталогом или моделью: каждое обращение агента сразу попадает в PROGRESS.

    Сам агент об этом не знает — он вызывает те же call и json; остальные поля и методы отдаются как есть.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def call(self, tool: str, args: dict) -> object:
        result = self._inner.call(tool, args)
        PROGRESS["steps"].append({"tool": tool, "args": args, "found": brief(result)})
        return result

    def json(self, *args, **kwargs) -> object:
        critic = "критик" in str(kwargs.get("purpose", ""))
        PROGRESS["steps"].append({"ask": "критик сверяет советы с цитатами" if critic else "модель решает, что искать дальше"})
        return self._inner.json(*args, **kwargs)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:        # в журнал — только обращения к агенту
        line = str(args[0] if args else "")
        if "/api/" in line and "/api/event" not in line and "/api/pilot" not in line:   # журнал и пилот спрашивают часто   # у ответа «файл не найден» первым идёт код, а не строка запроса
            super().log_message(fmt, *args)

    def end_headers(self) -> None:                         # данные стенда пересобираются — кеш браузера не нужен
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if self.path.startswith(OPEN):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:                         # предварительный запрос браузера со страницы кабинета
        if not self.path.startswith(OPEN):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/pilot"):              # тест против контроля по журналу кабинета
            return self._json(200, pilot.report(events.read()))
        if self.path.startswith("/api/ping"):
            return self._json(200, agent_ready())
        if self.path.startswith("/api/events"):             # итог журнала кабинета для панели стенда
            return self._json(200, events.summarize(events.read()))
        if self.path.startswith("/api/hole/progress"):     # без LOCK: страница спрашивает, пока агент занят
            return self._json(200, {"family": PROGRESS["family"], "steps": list(PROGRESS["steps"]), "done": PROGRESS["done"]})
        super().do_GET()

    def _hole(self) -> None:
        """Агент закрывает «дыру»: ищет связь для товара каталога, которому нечего посоветовать."""
        try:
            size = min(int(self.headers.get("Content-Length") or 0), 2000)
            family = str(json.loads(self.rfile.read(size).decode("utf-8") or "{}").get("family_id", "")).strip()
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "ожидается JSON с полем family_id"})
        try:
            from recsys.agent.holes import close_hole     # режим «закрыть дыру» ведёт сессия агента
        except Exception as e:
            return self._json(503, {"error": f"режим «закрыть дыру» в этой сборке не подключён ({type(e).__name__})"})
        with LOCK:
            cat = catalog()
            if not hasattr(cat, "line_veto"):              # обычный каталог: данные режима «закрыть дыру» не собраны
                return self._json(503, {"error": "данные режима «закрыть дыру» не собраны: нужен python -m recsys.export"})
            if family not in cat.nodes:
                return self._json(400, {"error": "такого товара в каталоге нет"})
            PROGRESS.update(family=family, steps=[], done=False)
            try:
                # результат агент сам дописывает в knowledge/agent_links.csv и reports/agent_holes.json
                res = close_hole(Watched(LLM()), Watched(cat), family)
                from recsys.graph import agent_links
                agent_links.publish()                      # копия для страницы: после перезагрузки дыра видна разобранной
            except LLMError as e:
                return self._json(503, {"error": str(e)[:300]})
            except Exception as e:                         # любой сбой агента — понятный ответ странице, а не обрыв
                return self._json(500, {"error": f"сбой агента: {type(e).__name__}: {str(e)[:200]}"})
            finally:
                PROGRESS["done"] = True
        self._json(200, res)

    def _event(self) -> None:
        """Показ, переход или запрос счёта со страницы кабинета — строка в журнал (data/private/events.jsonl)."""
        try:
            size = min(int(self.headers.get("Content-Length") or 0), 4000)
            ev = events.clean(json.loads(self.rfile.read(size).decode("utf-8") or "{}"))
        except (ValueError, json.JSONDecodeError):
            ev = None
        if ev is None:
            return self._json(400, {"error": "ожидается событие: kind show|click|invoice и вид совета slot"})
        events.append(ev)
        self._json(200, {"ok": True})

    def _advice(self) -> None:
        """Встраивание: кабинет шлёт историю покупок — получает блок советника (recsys/recommend/block.py)."""
        from recsys.recommend.block import advice
        try:
            size = min(int(self.headers.get("Content-Length") or 0), 50000)
            res = advice(json.loads(self.rfile.read(size).decode("utf-8") or "{}"))
        except ValueError as e:                            # и испорченный JSON, и ошибка в полях покупки
            return self._json(400, {"error": str(e)[:300]})
        self._json(200, res)

    def do_POST(self) -> None:
        if self.path.startswith("/api/event"):
            return self._event()
        if self.path.startswith("/api/advice"):
            return self._advice()
        if self.path.startswith("/api/hole"):
            return self._hole()
        if not self.path.startswith("/api/place"):
            return self._json(404, {"error": "нет такого адреса"})
        try:
            size = min(int(self.headers.get("Content-Length") or 0), 20000)
            body = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
            card = {"name": str(body.get("name", "")).strip()[:200], "vendor": str(body.get("vendor", "")).strip()[:100],
                    "description": str(body.get("description", "")).strip()[:1500]}
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "ожидается JSON с полями name, vendor, description"})
        if len(card["name"]) < 3:
            return self._json(400, {"error": "нужно название товара"})
        with LOCK:
            cat = catalog()
            cat.hidden = set()
            hide = str(body.get("hide_id", ""))
            if hide in cat.nodes:                          # режим «вынь и вставь»: прячем товар каталога
                cat.hidden = {hide}
            try:
                res = place(LLM(), cat, card)
            except LLMError as e:
                return self._json(503, {"error": str(e)[:300]})
            except Exception as e:                         # любой сбой агента — понятный ответ странице, а не обрыв связи
                return self._json(500, {"error": f"сбой агента: {type(e).__name__}: {str(e)[:200]}"})
            finally:
                cat.hidden = set()
        self._json(200, res)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 — в контейнере; по умолчанию только этот компьютер")
    args = ap.parse_args()
    handler = partial(Handler, directory=str(ROOT / "site"))
    srv = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"стенд: http://{args.host}:{args.port}  (агент каталога включён)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
