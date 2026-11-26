"""Нормализация названий: токены, редакции, «основа» названия линейки."""
import re

STOP = {
    "для", "и", "в", "на", "с", "по", "от", "к", "the", "of", "for", "by", "and",
    "версия", "version", "лицензия", "license", "выпуск", "edition", "ред", "release",
    "по", "программа", "программное", "обеспечение", "software",
}

# Порядок редакций от младшей к старшей. Значение — ступень лестницы.
EDITION_RANK = {
    "home": 1, "домашняя": 1, "домашний": 1, "personal": 1, "персональная": 1, "персональный": 1,
    "lite": 1, "лайт": 1, "light": 1, "start": 1, "starter": 1, "стартовый": 1, "стартовая": 1,
    "basic": 1, "base": 1, "базовый": 1, "базовая": 1, "essentials": 1, "essential": 1,
    "standard": 2, "std": 2, "стандартный": 2, "стандартная": 2, "стандарт": 2,
    "plus": 3, "плюс": 3,
    "professional": 4, "pro": 4, "prof": 4, "профессиональный": 4, "профессиональная": 4, "проф": 4,
    "business": 4,
    "advanced": 5, "расширенный": 5, "расширенная": 5, "premium": 5, "премиум": 5, "оптимальный": 5,
    "enterprise": 6, "корпоративный": 6, "корпоративная": 6, "corporate": 6, "корп": 6,
    "ultimate": 7, "total": 7, "максимальный": 7, "максимальная": 7, "unlimited": 7, "datacenter": 7,
}

# Сокращения, которые в названиях товаров означают то же, что полное слово.
CANON = {
    "pro": "professional", "prof": "professional", "проф": "профессиональный",
    "std": "standard", "стд": "стандартный", "ent": "enterprise", "adv": "advanced",
    "корп": "корпоративный", "srv": "server", "svr": "server", "ws": "workstation",
}

_PHRASES = [
    (r"для\s+бизнеса", " "),      # «для бизнеса» — часть названия линейки, а не редакция
    (r"small\s+business", " smb "),
    (r"малого\s+бизнеса", " smb "),
]


def tokens(name: str) -> list[str]:
    s = (name or "").lower().replace("ё", "е").replace("﻿", " ")
    for pat, rep in _PHRASES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^0-9a-zа-я+#]+", " ", s)
    out = []
    for t in s.split():
        if t in STOP:
            continue
        if re.fullmatch(r"(19|20)\d\d", t) or (t.isdigit() and int(t) < 100):
            continue  # номера версий и годы для сопоставления линейки не нужны; «365» и «360» — часть имени
        out.append(CANON.get(t, t))
    return out


PLATFORM_WORDS = {
    "windows", "win", "mac", "macos", "osx", "linux", "unix", "android", "ios",
    "desktop", "server", "сервер", "x64", "x86", "32", "64", "bit",
}

_PLUS = {"plus", "плюс"}


def split_edition(name: str) -> tuple[tuple[str, ...], float | None, str]:
    """Возвращает (основа названия, ступень редакции, слова редакции).

    Редакцией считается последнее слово-редакция в названии. Слова-редакции, стоящие раньше,
    относятся к названию линейки: в «Microsoft 365 Business Standard» линейка — «Business»,
    редакция — «Standard». «Плюс» после редакции поднимает её на полступени.
    """
    toks = tokens(name)
    ed_pos = [i for i, t in enumerate(toks) if t in EDITION_RANK]
    if not ed_pos:
        return tuple(toks), None, ""
    last = ed_pos[-1]
    ed_idx = {last}
    rank = float(EDITION_RANK[toks[last]])
    if toks[last] in _PLUS and len(ed_pos) > 1 and ed_pos[-2] == last - 1:
        prev = ed_pos[-2]
        rank = EDITION_RANK[toks[prev]] + 0.5
        ed_idx.add(prev)
    base = tuple(t for i, t in enumerate(toks) if i not in ed_idx)
    ed = " ".join(toks[i] for i in sorted(ed_idx))
    return base, rank, ed


def version_of(name: str) -> str:
    """Номер версии или год из названия: «ContentReader PDF 15 Standard» -> «15».

    Число в начале названия считается частью марки («3-Heights», «10-Страйк»).
    """
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я+#.]+", " ", s)
    out = []
    for i, p in enumerate(s.split()):
        p = p.strip(".")
        if i > 0 and re.fullmatch(r"\d+(?:[.]\d+)*", p):
            out.append(p)
    return " ".join(out)


def version_key(version: str) -> tuple[bool, tuple[int, ...]] | None:
    """Ключ сравнения версий: (это год, числа). Год и номер версии между собой не сравниваются."""
    if not version:
        return None
    first = version.split()[0]
    nums = tuple(int(x) for x in first.split(".") if x.isdigit())
    if not nums:
        return None
    is_year = len(nums) == 1 and 1990 <= nums[0] <= 2100
    if not is_year and nums[0] >= 100:
        return None      # 365, 750, 3600 — это тариф, ёмкость или номер модели, а не версия
    return is_year, nums


def newer(a: str, b: str) -> bool:
    """Верно, если версия b новее версии a и их можно сравнивать."""
    ka, kb = version_key(a), version_key(b)
    return bool(ka and kb and ka[0] == kb[0] and kb[1] > ka[1])


def overlap(a: list[str] | tuple[str, ...], b: list[str] | tuple[str, ...]) -> float:
    """Доля токенов a, найденных в b."""
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa) if sa else 0.0


def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0
