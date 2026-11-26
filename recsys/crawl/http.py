"""Вежливый HTTP-клиент: один поток, пауза, повторы, без обхода защит."""
import time

import requests

from recsys.config import REQUEST_DELAY_SEC, REQUEST_TIMEOUT_SEC, USER_AGENT


class PoliteSession:
    def __init__(self, delay: float = REQUEST_DELAY_SEC):
        self.delay = delay
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })

    def get(self, url: str, retries: int = 3) -> requests.Response | None:
        """Возвращает ответ или None, если страница недоступна после повторов."""
        for attempt in range(retries):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.get(url, timeout=REQUEST_TIMEOUT_SEC)
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404, 410):
                return r  # не настаиваем: запрет или страницы нет
            time.sleep(5 * (attempt + 1))  # 429/5xx: подождать и повторить
        return None
