"""Рекомендации для личного кабинета: граф связей по семействам товаров."""
import sys

# Консоль Windows по умолчанию не в UTF-8: без этого русский вывод падает.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
