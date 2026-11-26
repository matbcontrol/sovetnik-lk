"""Разбор конфигурации и названия товара в признаки: тип лицензии, срок, платформа, тип клиента, число мест.

Модель здесь не нужна: магазин публикует конфигурацию готовой строкой, например
«Срок действия: 12 мес. • Платформа: Windows • Тип лицензии: продление • Тип клиента: юрлицо • К-во узлов от 10 до 14».
"""
import re

LICENSE_PATTERNS = [
    ("renewal", r"продлени|renewal|\bпродл\b"),
    ("upgrade", r"обновлени|апгрейд|upgrade|переход"),
    ("migration", r"миграци|cross.?grade|кроссгрейд"),
    ("extension", r"расширени|доп(олнительн\w*)?\s+(лиценз|узл|мест)|add-?on"),
    ("support", r"техподдержк|техническ\w+ поддержк|сертификат.*поддержк|support"),
    ("new", r"полная версия|базов\w+ лиценз|новая|new\b|base"),
]
CLIENT_PATTERNS = [
    ("b2c", r"физ\.?\s?лиц|для дома|домашн|частн|personal|home"),
    ("b2b", r"юр\.?\s?лиц|организаци|бизнес|корпоратив|business|enterprise"),
    ("edu", r"академич|образоват|учебн|education|academic"),
    ("gov", r"гос\w*\b|государствен"),
]
# В названии лицензии слова «домашняя», «business» часто означают редакцию, а не покупателя,
# поэтому из названия берём тип клиента только по прямому указанию «для кого».
CLIENT_PATTERNS_IN_NAME = [
    ("b2c", r"для физ\w*\.?\s?лиц|для дома\b|для частн\w+ лиц"),
    ("b2b", r"для юр\w*\.?\s?лиц|для организаци|для коммерческ"),
    ("edu", r"для образовательн|для учебн|академическ\w+ лиценз"),
    ("gov", r"для гос\w*\s?(учрежд|орган)|для государствен"),
]
PLATFORMS = ["Windows", "Linux", "macOS", "Mac", "Android", "iOS", "Astra", "РЕД ОС", "Альт", "Unix", "FreeBSD"]


def _first(patterns: list[tuple[str, str]], text: str) -> str:
    low = text.lower()
    for label, pat in patterns:
        if re.search(pat, low):
            return label
    return ""


def term_months(text: str) -> int | None:
    low = text.lower()
    if "бессрочн" in low or "perpetual" in low:
        return 0
    m = re.search(r"(\d+)\s*мес", low)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(год|года|лет|year)", low)
    if m:
        return int(m.group(1)) * 12
    if re.search(r"\bна год\b|1 year|годов", low):
        return 12
    return None


def parse_config(config: str) -> dict:
    """«Ключ: значение • Ключ: значение» -> словарь исходных пар."""
    pairs = {}
    for part in re.split(r"\s*[•|]\s*", config or ""):
        if ":" in part:
            k, v = part.split(":", 1)
            pairs[k.strip()] = v.strip()
    return pairs


def features(name: str, config: str = "", min_qty=None, max_qty=None) -> dict:
    pairs = parse_config(config)
    text = f"{name} {config}"
    lic_src = pairs.get("Тип лицензии", "") or name
    cli_src = pairs.get("Тип клиента", "") or pairs.get("Тип организации", "")
    client_type = _first(CLIENT_PATTERNS, cli_src) if cli_src else _first(CLIENT_PATTERNS_IN_NAME, name)
    platform = pairs.get("Платформа", "")
    if not platform:
        found = [p for p in PLATFORMS if re.search(rf"\b{re.escape(p)}\b", text, re.I)]
        platform = ", ".join(dict.fromkeys(found))
    term = term_months(pairs.get("Срок действия", "") or name)
    unit = ""
    for k in pairs:
        m = re.match(r"К-во\s+(\S+)", k)
        if m:
            unit = m.group(1)
    return {
        "license_type": _first(LICENSE_PATTERNS, lic_src) or "new",
        "client_type": client_type,
        "platform": platform,
        "term_months": term,
        "qty_unit": unit,
        "qty_min": min_qty,
        "qty_max": max_qty,
    }
