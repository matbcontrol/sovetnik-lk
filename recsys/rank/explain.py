"""Объяснение к совету: из чего он выведен — простыми словами и с числами.

Языковая модель здесь не используется: объяснение собирается из тех же сведений, по которым
совет был выбран. У каждого совета есть главное основание и до двух дополнительных; каждое
основание привязано к конкретной покупке клиента — из них рисуется схема «почему эти товары».

Виды оснований, от самого сильного:
  линейка   — товар из той же линейки, что уже купленный: продление, другой размер, дистрибутив…
              (знание каталога; работает и для товара без истории продаж)
  следом    — что брали потом клиенты с такой же покупкой
  семейство — что брали потом клиенты с продуктами той же линейки
  класс     — что брали потом клиенты с продуктами того же класса
  сегмент   — частые следующие покупки клиентов того же типа, отрасли или размера
  общее     — частые следующие покупки вообще
"""
from __future__ import annotations

import re
from datetime import date

from recsys.rank.data import Task, months_between
from recsys.rank.features import EMPTY, Attr, FoldStats, seg_keys

KIND_RU = {"base": "базовая лицензия", "renewal": "продление", "upgrade": "переход на новую версию",
           "migration": "миграция", "extension": "расширение лицензии", "support": "техподдержка",
           "media": "дистрибутив"}
MONTH_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
            "октябре", "ноябре", "декабре"]
# единицы размера лицензии в трёх формах: «1 узел», «3 узла», «25–49 узлов»
_NODE = ("узел", "узла", "узлов")
_USER = ("пользователь", "пользователя", "пользователей")
_DEV = ("устройство", "устройства", "устройств")
_BOX = ("почтовый ящик", "почтовых ящика", "почтовых ящиков")
_WORK = ("рабочее место", "рабочих места", "рабочих мест")
UNITS = {"node": _NODE, "узел": _NODE, "user": _USER, "пользователь": _USER, "пользовател": _USER,
         "device": _DEV, "устройство": _DEV, "устройств": _DEV, "workstation": _WORK, "рабочих мест": _WORK,
         "pc": ("ПК", "ПК", "ПК"), "desktop": ("ПК", "ПК", "ПК"), "server": ("сервер", "сервера", "серверов"),
         "mailbox": _BOX, "mailboxe": _BOX, "mailaddress": ("почтовый адрес", "почтовых адреса", "почтовых адресов"),
         "mobile device": ("мобильное устройство", "мобильных устройства", "мобильных устройств"),
         "seat": ("место", "места", "мест")}
# артикул Dr.Web: L<B|H><W|S>-<состав>-<срок>M-<число объектов>-<вид><льгота>; W — рабочие станции, S — серверы
DRWEB = re.compile(r"^L[A-Z]([A-Z])-[A-Z]{2}-\d+M-(\d+)-[A-Z][A-Z0-9]$")
# служебное начало названий из продаж и хвост с кодом изделия — во фразе для клиента они лишние
LEGAL = re.compile(
    r"^(?:(?:простая\s+)?(?:не)?исключительн\w+\s+)?"
    r"(?:право\s+(?:на\s+)?использовани[ея](?:\s+(?:программы\s+для\s+эвм|программного\s+обеспечения|по))?"
    r"|лицензия\s+на\s+право\s+использования(?:\s+(?:программы\s+для\s+эвм|по))?"
    r"|программа\s+для\s+эвм|программа)\s+", re.I)
SKU_HEAD = re.compile(r"^(?:[A-Z0-9]{2,}(?:-[A-Z0-9]+)+,\s*|[A-Z0-9]{2,}(?:-[A-Z0-9]+){2,}\s+)")
CODE_TAIL = re.compile(r"[.,]?\s+[А-ЯA-Z]{2,6}\.\d{3,}[\d.\-]*.*$")
SLOT_TITLE = {"renew": "Что истекает", "next": "Следующая ступень", "missing": "Чего не хватает",
              "popular": "Часто берут"}
LABEL = {"линейка": "каталог: та же линейка", "следом": "покупки других клиентов",
         "семейство": "покупки по линейке", "класс": "покупки по классу",
         "сегмент": "докупки вашего сегмента", "общее": "частые докупки"}


def when(d: date) -> str:
    return f"в {MONTH_IN[d.month - 1]} {d.year}"


def plural(n: int, one_form: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one_form if n % 10 == 1 else (few if 2 <= n % 10 <= 4 else many)


def one(n: int) -> bool:
    """Число требует глагола в единственном числе: 1, 21, 31… («взял 21 клиент»)."""
    return n % 10 == 1 and n % 100 != 11


def took(n: int) -> str:
    return "взял" if one(n) else "взяли"


def times(n: int) -> str:
    return f"{n} {plural(n, 'раз', 'раза', 'раз')}"


def clients(n: int) -> str:
    return f"{n} {plural(n, 'клиент', 'клиента', 'клиентов')}"


def clean_name(name: str, limit: int) -> str:
    """Название без артикула в начале, юридического вступления и кода изделия в конце."""
    name = re.sub(r"\s+", " ", name).strip(" .,")
    name = re.sub(r'"([^"]+)"', r"«\1»", SKU_HEAD.sub("", name))
    prev = None
    while prev != name:
        prev = name
        name = LEGAL.sub("", name).strip(" .,")
    name = CODE_TAIL.sub("", name).strip(" .,") or prev
    if len(name) > limit:
        head = name[:limit]
        cut = max(head.rfind(", "), head.rfind(". "))          # лучше оборвать на запятой, чем посреди слова
        if cut >= 12 and head[:cut].count("«") == head[:cut].count("»") and head[:cut].count("(") == head[:cut].count(")"):
            return head[:cut]
        name = head.rsplit(" ", 1)[0].rstrip(" ,.–-") + "…"
    return name


def short_name(task: Task, item: str, limit: int = 64) -> str:
    """Короткое имя для фразы: название семейства из каталога, а если его нет — начало названия из продаж."""
    r = task.items.get(item) or {}
    return clean_name((r.get("family") or "").strip() or task.names.get(item, item), limit)


def title_name(task: Task, item: str) -> str:
    """Заголовок карточки: полное название из продаж без служебных слов."""
    return clean_name(task.names.get(item, item), 120)


def q(name: str) -> str:
    """Название в кавычках-«ёлочках»; кавычки внутри названия становятся „лапками“."""
    inner = name.replace("«", "„").replace("»", "“")
    if inner.count("„") > inner.count("“"):                    # название обрезано посреди кавычек
        inner = inner[:-1] + "“…" if inner.endswith("…") else inner + "“"
    return f"«{inner}»"


def size(task: Task, item: str, a: Attr) -> tuple[int, int, tuple[str, str, str]] | None:
    """Размер лицензии: (от, до, формы единицы). Из каталога, а если там пусто — из артикула Dr.Web."""
    if a.band_lo is not None:
        raw = ((task.items.get(item) or {}).get("band_unit") or "").strip().lower()
        forms = UNITS.get(raw) or UNITS.get(raw.rstrip("s")) or ((raw, raw, raw) if raw else UNITS["seat"])
        return int(a.band_lo), int(a.band_hi if a.band_hi is not None else a.band_lo), forms
    m = DRWEB.match(((task.items.get(item) or {}).get("sku") or item).strip())
    if m:
        n = int(m.group(2))
        forms = UNITS["pc"] if m.group(1) == "W" else UNITS["server"] if m.group(1) == "S" else ("объект", "объекта", "объектов")
        return n, n, forms
    return None


def numbers(sz: tuple) -> str:
    return f"{sz[0]}" if sz[0] == sz[1] else f"{sz[0]}–{sz[1]}"


def band(task: Task, item: str, a: Attr) -> str:
    sz = size(task, item, a)
    return f"{numbers(sz)} {plural(sz[1], *sz[2])}" if sz else ""


def describe(task: Task, item: str, a: Attr) -> str:
    """Параметры лицензии одной строкой: «продление, 25–49 узлов, 12 мес.»"""
    parts = [KIND_RU.get(a.kind, "")]
    parts.append(band(task, item, a))
    if a.term is not None:
        parts.append("бессрочно" if a.term == 0 else f"{a.term} мес.")
    m = re.search(r"\((\d+)/(\d+)", task.names.get(item, ""))
    if m:
        parts.append(f"платёж {m.group(1)} из {m.group(2)}")
    return ", ".join(p for p in parts if p)


CONNECT = {"и", "от", "для", "на", "с", "по", "к", "в", "for", "and", "of", "to", "with", "–", "-", "—"}
NOISE = re.compile(r"^(renewal|base|prolong\w*|продлени\w*|лицензи\w*|licen[sc]e\w*|subscription|подписк\w*|years?"
                   r"|год\w*|лет|мес\w*|months?|nodes?|users?|devices?|russian|edition|право|использовани\w*"
                   r"|неисключительн\w*|по)$", re.I)


def difference(name_a: str, name_b: str, limit: int = 60) -> str:
    """Чем лицензия отличается от уже купленной, словами из её названия.

    Берётся самая длинная цепочка слов, которых нет в названии купленной. Числа, артикулы и служебные
    слова в цепочку не идут, а сама цепочка должна начинаться с заглавной буквы — так остаются названия
    редакций и модулей («Standard», «Модуль от НСД и контроля устройств») и уходят обрывки («сервере», «2»).
    """
    def split(t: str) -> list[str]:
        return [w.strip(".«»\"'") for w in re.split(r"[\s,;()]+", t)]

    def weight(chain: list[str]) -> int:
        return sum(w.casefold() not in CONNECT for w in chain)

    have = {w.casefold() for w in split(name_a) if w}
    best: list[str] = []
    cur: list[str] = []
    for w in split(name_b) + ["1"]:                    # число в конце закрывает последнюю цепочку
        if not w:
            continue
        low = w.casefold()
        if low in CONNECT:
            if cur:
                cur.append(w)
            continue
        if low not in have and not any(ch.isdigit() for ch in w) and not NOISE.match(w):
            cur.append(w)
            continue
        while cur and cur[-1].casefold() in CONNECT:
            cur.pop()
        if weight(cur) > weight(best):
            best = cur
        cur = []
    text = " ".join(best)
    ok = best and best[0][:1].isupper() and any(len(re.sub(r"\W", "", w)) >= 4 for w in best) and len(text) <= limit
    return text if ok else ""


def relation(task: Task, a: str, aa: Attr, b: str, ab: Attr) -> tuple[str, str]:
    """Кем приходится товар b уже купленному товару a той же линейки: (вид совета, фраза)."""
    if aa.period is not None and ab.period is not None and aa.pkey == ab.pkey and ab.period > aa.period:
        return "renew", "следующий платёж по той же подписке"
    sa, sb = size(task, a, aa), size(task, b, ab)
    differ = bool(sa and sb and sa[:2] != sb[:2])
    scale = ""
    if sa and sb:
        scale = f" на {band(task, b, ab)} вместо {numbers(sa)}" if differ else " на тот же размер"
    term = f", на {ab.term} мес. вместо {aa.term}" if aa.term and ab.term and aa.term != ab.term else ""
    if ab.kind == "renewal":
        return "renew", f"продление этой лицензии{scale}{term}"
    if ab.kind == "media":
        return "next", "дистрибутив (носитель) к вашей лицензии"
    if ab.kind == "support":
        return "next", "техподдержка к вашему продукту"
    if ab.kind == "extension":
        return "next", "расширение вашей лицензии"
    if ab.kind in ("upgrade", "migration") or (aa.version is not None and ab.version is not None and ab.version > aa.version):
        return "next", "переход на более новую версию"
    if differ:
        more = "большее" if sb[0] > sa[0] else "другое"
        return "next", f"та же лицензия на {more} число мест: {band(task, b, ab)} вместо {numbers(sa)}{term}"
    if aa.kind == "renewal" and ab.kind == "base":
        return "renew", f"новая лицензия той же линейки{scale}{term}"
    diff = difference(task.names.get(a, ""), task.names.get(b, ""))
    return "next", "другая лицензия той же линейки" + (f": {q(diff)}" if diff else "") + term


def ordinal(k: int) -> str:
    """Место словами; дальше третьего места порядковый номер звучит как довод против, поэтому его нет."""
    return {1: "самая частая", 2: "вторая по частоте", 3: "третья по частоте"}.get(k, "")


def after(n: int, m: int, where: str, what: str) -> str:
    """Что брали после линейки или класса. Долю называем, только когда она заметна: «4 из 849» —
    верное число, но читается как довод против; тогда остаётся счёт клиентов."""
    if n / max(m, 1) >= 0.05:
        return f"{n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')} с продуктами {where} затем {took(n)} {what}."
    return f"{what} затем {took(n)} {clients(n)} с продуктами {where}."


def who(key: tuple) -> str:
    kind = key[0]
    if kind == "gi":
        return f"клиентов отрасли «{key[2]}»"
    if kind == "gs":
        return {"small": "небольших компаний", "medium": "средних компаний", "corporate": "крупных компаний",
                "enterprise": "крупнейших компаний"}.get(key[2], "компаний вашего размера")
    if kind == "gt":
        return {"ЮЛ": "юридических лиц", "ИП": "индивидуальных предпринимателей", "ФЛ": "частных покупателей"}.get(key[2], "таких клиентов")
    return "организаций" if key[1] == "B2B" else "частных покупателей"


def reasons(st: FoldStats, c: str, b: str) -> list[dict]:
    """Основания совета b клиенту c, от сильного к слабому. Вызывать внутри нужного режима (in_regime)."""
    task, fold, at = st.task, st.fold, st.attrs
    h = fold.history[c]
    ab = at[b]
    qb = q(short_name(task, b))
    since = {a: months_between(fold.last[c][a], fold.test_from) for a in h}
    out: list[dict] = []

    # 1) та же линейка — знание каталога
    near = [a for a in h if at[a].line == ab.line] or [a for a in h if at[a].nline and at[a].nline == ab.nline]
    if near:
        a = min(near, key=lambda x: since[x])
        aa = at[a]
        slot, phrase = relation(task, a, aa, b, ab)
        text = f"У вас есть {q(short_name(task, a))}"
        da = describe(task, a, aa)
        text += f" ({da})" if da else ""
        text += f", куплено {when(fold.last[c][a])}. Этот товар — {phrase}."
        n, m = st.pair.get(a, EMPTY).get(b, 0), st.owners[a]
        nf = st.fam.get(aa.line, EMPTY).get(b, 0)
        if n >= 2 and n / max(m, 1) >= 0.05:
            text += f" Так поступили {n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')} с такой же покупкой."
        elif n >= 2:
            text += f" Его потом {took(n)} {clients(n)} с такой же покупкой."
        elif nf >= 2:
            text += f" Его потом {took(nf)} {clients(nf)} с продуктами этой линейки."
        k = int(round(since[a]))
        if slot == "renew" and aa.term:
            left = aa.term - k
            text += (f" Срок лицензии — {aa.term} мес., с покупки прошло {k} мес."
                     if left >= 0 else f" Срок лицензии ({aa.term} мес.) уже истёк: с покупки прошло {k} мес.")
        out.append({"type": "линейка", "slot": slot, "src": a, "edge": phrase.split(":")[0].split(",")[0],
                    "text": text, "strength": 3.0 + min(n, 20) / 100})

    # 2) что брали следом за тем же товаром
    best = None
    for a in h:
        n = st.pair.get(a, EMPTY).get(b, 0)
        if n >= 1 and a not in near:
            p = n / max(st.owners[a], 1)
            if best is None or (n >= 2, p) > (best[1] >= 2, best[2]):
                best = (a, n, p)
    if best:
        a, n, _ = best
        m = st.owners[a]
        order = [i for i, _ in st.pair.get(a, EMPTY).most_common() if at[i].line != at[a].line]
        place = order.index(b) + 1 if b in order else 0
        qa = q(short_name(task, a))
        bought = "купил" if one(n) else "купили"
        if n == 1:
            text = f"Такая пара уже встречалась: клиент с {qa} позже купил и {qb}."
        elif n / max(m, 1) >= 0.05:
            text = f"{n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')}, купивших {qa}, позже {bought} и {qb}."
        elif 1 <= place <= 5 and n >= 3:
            text = (f"{qb} — {ordinal(place)} следующая покупка после {qa} среди других продуктов ({clients(n)})."
                    if ordinal(place) else
                    f"{qb} — в числе пяти самых частых следующих покупок после {qa} ({clients(n)}).")
        else:
            text = f"{clients(n).capitalize()}, {'купивший' if one(n) else 'купивших'} {qa}, позже {bought} и {qb}."
        out.append({"type": "следом", "slot": "missing", "src": a, "edge": "брали следом",
                    "text": text, "strength": 2.0 if n >= 3 else (1.5 if n == 2 else 0.9)})

    # 3) что брали после продуктов той же линейки и того же класса
    seen_l, seen_c = set(), set()
    for a in sorted(h, key=lambda x: since[x]):
        aa = at[a]
        if aa.line not in seen_l and aa.line != ab.line:
            seen_l.add(aa.line)
            n, m = st.fam.get(aa.line, EMPTY).get(b, 0), st.line_owners[aa.line]
            if n >= 2:
                out.append({"type": "семейство", "slot": "missing", "src": a, "edge": "брали после этой линейки",
                            "text": after(n, m, f"линейки {q(short_name(task, a))}", qb),
                            "strength": 1.2 + min(n, 30) / 100})
        if aa.cls and aa.cls not in seen_c:
            seen_c.add(aa.cls)
            n, m = st.cls.get(aa.cls, EMPTY).get(b, 0), st.cls_owners[aa.cls]
            cname = (task.items.get(a) or {}).get("subcategory") or ""
            if n >= 3:
                out.append({"type": "класс", "slot": "missing", "src": a, "edge": "брали после этого класса",
                            "text": after(n, m, f"класса {q(cname)}" if cname else "того же класса", qb),
                            "strength": 1.0 + min(n, 30) / 100})

    # 4) частые следующие покупки сегмента и вообще
    info = task.clients.get(c, {})
    for key in reversed(seg_keys(info)):
        cnt = st.follow_seg.get(key)
        if cnt and cnt.get(b, 0) >= 3:
            rank = [i for i, _ in cnt.most_common(10)]
            if b in rank:
                place = ordinal(rank.index(b) + 1)
                out.append({"type": "сегмент", "slot": "popular", "src": "", "edge": "",
                            "text": (f"{qb} — {place} следующая покупка у {who(key)}: {times(cnt[b])}." if place else
                                     f"{qb} входит в десятку частых следующих покупок у {who(key)}: {times(cnt[b])}."),
                            "strength": 0.6})
                break
    if st.follow.get(b, 0) >= 3:
        out.append({"type": "общее", "slot": "popular", "src": "", "edge": "",
                    "text": f"{qb} часто берут вторым заказом: {times(st.follow[b])} за период.",
                    "strength": 0.3})
    out.sort(key=lambda r: -r["strength"])
    return out


def advice(st: FoldStats, c: str, b: str) -> dict:
    """Совет в виде, готовом для стенда: вид, главное объяснение, источник и пути для схемы."""
    rs = reasons(st, c, b)
    task = st.task
    if not rs:
        return {"item": b, "slot": "popular", "title": SLOT_TITLE["popular"],
                "why": "Этот товар часто берут клиенты того же типа.", "label": LABEL["общее"], "paths": []}
    main = rs[0]
    label = LABEL[main["type"]]
    if main["type"] == "линейка" and ("Так поступили" in main["text"] or " потом взял" in main["text"]):
        label = "каталог и покупки других клиентов"
    extra = [r["text"] for r in rs[1:3] if r["type"] in ("следом", "семейство", "класс")]
    return {
        "item": b, "slot": main["slot"], "title": SLOT_TITLE[main["slot"]],
        "why": main["text"], "also": " ".join(extra[:1]), "label": label,
        "desc": describe(task, b, st.attrs[b]),
        "paths": [{"src": r["src"], "type": r["edge"], "kind": r["type"], "text": r["text"]} for r in rs[:3] if r["src"]],
    }
