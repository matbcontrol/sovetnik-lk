"""Журнал кабинета: показы советов, переходы в магазин и запросы счёта — кликабельность и конверсия по видам совета.

Пишет только страница кабинета (site/cabinet.html через site/track.js) — то, что видит клиент:
  показ   — карточка совета появилась на экране (одна карточка за один заход на страницу считается один раз);
  переход — клиент открыл товар по ссылке с метками utm (те же метки увидит аналитика магазина);
  запрос счёта — кнопка «Запросить счёт»: это и есть конверсия.
Сервер стенда только дописывает строки; номеров клиентов датасета в журнале нет — вид совета, место, название товара.
Журнал локальный и не публикуется: data/private/events.jsonl. Подсчёт тот же, что в странице (track.js, summarize).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from recsys.config import DATA

LOG = DATA / "private" / "events.jsonl"
KINDS = ("show", "click", "invoice")
SLOTS = ("renew", "next", "missing", "kit", "replace", "popular")
_LOCK = threading.Lock()


def clean(ev) -> dict | None:
    """Событие от страницы -> строка журнала. Чужие виды событий и советов отбрасываем, длину полей режем."""
    if not isinstance(ev, dict):
        return None
    kind, slot = str(ev.get("kind", "")), str(ev.get("slot", ""))
    if kind not in KINDS or slot not in SLOTS:
        return None
    try:
        pos = max(0, min(int(ev.get("pos") or 0), 20))
    except (TypeError, ValueError):
        pos = 0
    return {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "slot": slot, "pos": pos,
            "item": str(ev.get("item", "")).strip()[:200], "group": "control" if ev.get("group") == "control" else "test",
            "view": re.sub(r"[^0-9a-z]", "", str(ev.get("view", "")).lower())[:24]}


def append(ev: dict, path: Path = LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    with _LOCK, path.open("a", encoding="utf-8") as f:
        f.write(line)


def read(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def summarize(events: list[dict]) -> dict:
    """Показы, переходы и запросы счёта — всего и по видам совета. Доли считает страница: дробью и в процентах."""
    seen: set[tuple] = set()
    rows = {s: {"shows": 0, "clicks": 0, "invoices": 0} for s in SLOTS}
    for e in events:
        r = rows.get(e.get("slot"))
        if r is None:
            continue
        if e.get("kind") == "show":
            key = (e.get("view"), e.get("slot"), e.get("item"))
            if key in seen:
                continue
            seen.add(key)
            r["shows"] += 1
        elif e.get("kind") == "click":
            r["clicks"] += 1
        elif e.get("kind") == "invoice":
            r["invoices"] += 1
    total = {k: sum(r[k] for r in rows.values()) for k in ("shows", "clicks", "invoices")}
    return {"total": total, "by_slot": {s: r for s, r in rows.items() if any(r.values())},
            "views": len({e.get("view") for e in events if e.get("kind") == "show"})}


def summarize_groups(events: list[dict]) -> dict:
    """Пилот: тот же подсчёт отдельно для теста и контроля (у старых записей без группы — тест)."""
    out = {}
    for g in ("test", "control"):
        part = [e for e in events if (e.get("group") or "test") == g]
        if part:
            out[g] = summarize(part)
    return out
