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

from recsys.agent.agent import place  # noqa: E402
from recsys.agent.tools import Catalog  # noqa: E402
from recsys.llm.client import LLM, LLMError  # noqa: E402

LOCK = threading.Lock()                            # агент работает по одному запросу за раз
STATE: dict = {}


def catalog() -> Catalog:
    if "cat" not in STATE:
        STATE["cat"] = Catalog()
    return STATE["cat"]


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:        # в журнал — только обращения к агенту
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.startswith("/api/ping"):
            return self._json(200, {"agent": True})
        super().do_GET()

    def do_POST(self) -> None:
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
            finally:
                cat.hidden = set()
        self._json(200, res)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    handler = partial(Handler, directory=str(ROOT / "site"))
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"стенд: http://127.0.0.1:{args.port}  (агент каталога включён)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
