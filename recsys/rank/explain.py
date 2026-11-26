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
UNIT_RU = {"node": "узлов", "user": "пользователей", "device": "устройств", "workstation": "рабочих мест",
           "pc": "ПК", "server": "серверов", "mailbox": "почтовых ящиков", "seat": "мест", "desktop": "ПК",
           "узел": "узлов", "пользователь": "пользователей", "устройство": "устройств",
           "mobile device": "мобильных устройств", "mailaddress": "почтовых адресов", "mailboxe": "почтовых ящиков"}
SLOT_TITLE = {"renew": "Что истекает", "next": "Следующая ступень", "missing": "Чего не хватает",
              "popular": "Часто берут"}
LABEL = {"линейка": "каталог: та же линейка", "следом": "покупки других клиентов",
         "семейство": "покупки по линейке", "класс": "покупки по классу",
         "сегмент": "докупки вашего сегмента", "общее": "частые докупки"}


def when(d: date) -> str:
    return f"в {MONTH_IN[d.month - 1]} {d.year}"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else (few if 2 <= n % 10 <= 4 else many)


def short_name(task: Task, item: str, limit: int = 64) -> str:
    """Короткое имя для фразы: название семейства из каталога, а если его нет — начало названия из продаж."""
    r = task.items.get(item) or {}
    name = (r.get("family") or "").strip() or task.names.get(item, item)
    name = re.sub(r"\s+", " ", name).strip(" .,")
    if len(name) > limit:
        name = name[:limit].rsplit(" ", 1)[0].rstrip(" ,.–-") + "…"
    return name


def band(task: Task, item: str, a: Attr) -> str:
    if a.band_lo is None:
        return ""
    unit = ((task.items.get(item) or {}).get("band_unit") or "").strip().lower()
    unit = UNIT_RU.get(unit.rstrip("s"), unit) or "мест"
    return f"{a.band_lo} {unit}" if a.band_lo == a.band_hi or a.band_hi is None else f"{a.band_lo}–{a.band_hi} {unit}"


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


def relation(task: Task, a: str, aa: Attr, b: str, ab: Attr) -> tuple[str, str]:
    """Кем приходится товар b уже купленному товару a той же линейки: (вид совета, фраза)."""
    if aa.period is not None and ab.period is not None and aa.pkey == ab.pkey and ab.period > aa.period:
        return "renew", "следующий платёж по той же подписке"
    ba, bb = band(task, a, aa), band(task, b, ab)
    size = ""
    if ba and bb:
        size = " на тот же размер" if ba == bb else f" на {bb} вместо {ba}"
    term = f", на {ab.term} мес. вместо {aa.term}" if aa.term and ab.term and aa.term != ab.term else ""
    if ab.kind == "renewal":
        return "renew", f"продление этой лицензии{size}{term}"
    if ab.kind == "media":
        return "next", "дистрибутив (носитель) к вашей лицензии"
    if ab.kind == "support":
        return "next", "техподдержка к вашему продукту"
    if ab.kind == "extension":
        return "next", "расширение вашей лицензии"
    if ab.kind in ("upgrade", "migration") or (aa.version is not None and ab.version is not None and ab.version > aa.version):
        return "next", "переход на более новую версию"
    if ba and bb and ba != bb:
        more = ab.band_lo is not None and aa.band_lo is not None and ab.band_lo > aa.band_lo
        return "next", f"та же лицензия на {'большее' if more else 'другое'} число мест: {bb} вместо {ba}"
    if aa.kind == "renewal" and ab.kind == "base":
        return "renew", f"новая лицензия той же линейки{size}{term}"
    return "next", f"другая лицензия той же линейки{term}"


def ordinal(k: int) -> str:
    return {1: "самая частая", 2: "вторая по частоте", 3: "третья по частоте"}.get(k, f"{k}-я по частоте")


def clients(n: int) -> str:
    return f"{n} {plural(n, 'клиент', 'клиента', 'клиентов')}"


def difference(name_a: str, name_b: str, limit: int = 60) -> str:
    """Чем название лицензии отличается от уже купленной: слова, которых в купленной нет."""
    split = lambda t: [w for w in re.split(r"[\s,;()]+", t) if w]          # noqa: E731
    have = {w.casefold().strip(".") for w in split(name_a)}
    extra = [w.strip(".") for w in split(name_b) if w.casefold().strip(".") not in have]
    text = " ".join(extra[:7])
    return text if 0 < len(text) <= limit else ""


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
    nb = short_name(task, b)
    since = {a: months_between(fold.last[c][a], fold.test_from) for a in h}
    out: list[dict] = []

    # 1) та же линейка — знание каталога
    near = [a for a in h if at[a].line == ab.line] or [a for a in h if at[a].nline and at[a].nline == ab.nline]
    if near:
        a = min(near, key=lambda x: since[x])
        aa = at[a]
        slot, phrase = relation(task, a, aa, b, ab)
        text = f"У вас есть «{short_name(task, a)}»"
        da = describe(task, a, aa)
        text += f" ({da})" if da else ""
        text += f", куплено {when(fold.last[c][a])}. Этот товар — {phrase}."
        diff = difference(task.names.get(a, ""), task.names.get(b, ""))
        if diff:
            text += f" Отличие от вашей: {diff}."
        n, m = st.pair.get(a, EMPTY).get(b, 0), st.owners[a]
        nf, mf = st.fam.get(aa.line, EMPTY).get(b, 0), st.line_owners[aa.line]
        if n >= 2 and n / max(m, 1) >= 0.05:
            text += f" Так поступили {n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')} с такой же покупкой."
        elif n >= 2:
            text += f" Её потом взяли {clients(n)} с такой же покупкой."
        elif nf >= 2:
            text += f" Её потом взяли {clients(nf)} с продуктами этой линейки."
        k = int(round(since[a]))
        if slot == "renew" and aa.term:
            left = aa.term - k
            text += (f" Срок лицензии {aa.term} мес., с покупки прошло {k}."
                     if left >= 0 else f" Срок лицензии {aa.term} мес. уже истёк: с покупки прошло {k}.")
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
        if n / max(m, 1) >= 0.05:
            text = f"{n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')}, купивших «{short_name(task, a)}», позже купили и «{nb}»."
        elif 1 <= place <= 5 and n >= 3:
            text = (f"«{nb}» — {ordinal(place)} следующая покупка после «{short_name(task, a)}» "
                    f"среди других продуктов ({clients(n)}).")
        else:
            text = f"{clients(n).capitalize()}, купивших «{short_name(task, a)}», позже купили и «{nb}»."
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
                            "text": f"{n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')} с продуктами линейки «{short_name(task, a)}» затем взяли «{nb}».",
                            "strength": 1.2 + min(n, 30) / 100})
        if aa.cls and aa.cls not in seen_c:
            seen_c.add(aa.cls)
            n, m = st.cls.get(aa.cls, EMPTY).get(b, 0), st.cls_owners[aa.cls]
            cname = (task.items.get(a) or {}).get("subcategory") or "того же класса"
            if n >= 3:
                out.append({"type": "класс", "slot": "missing", "src": a, "edge": "брали после этого класса",
                            "text": f"{n} из {m} {plural(m, 'клиента', 'клиентов', 'клиентов')} с продуктами класса «{cname}» затем взяли «{nb}».",
                            "strength": 1.0 + min(n, 30) / 100})

    # 4) частые следующие покупки сегмента и вообще
    info = task.clients.get(c, {})
    for key in reversed(seg_keys(info)):
        cnt = st.follow_seg.get(key)
        if cnt and cnt.get(b, 0) >= 3:
            rank = [i for i, _ in cnt.most_common(10)]
            if b in rank:
                out.append({"type": "сегмент", "slot": "popular", "src": "", "edge": "",
                            "text": f"«{nb}» — {ordinal(rank.index(b) + 1)} следующая покупка у {who(key)}: {cnt[b]} {plural(cnt[b], 'раз', 'раза', 'раз')}.",
                            "strength": 0.6})
                break
    if st.follow.get(b, 0) >= 3:
        out.append({"type": "общее", "slot": "popular", "src": "", "edge": "",
                    "text": f"«{nb}» часто берут вторым заказом: {st.follow[b]} {plural(st.follow[b], 'раз', 'раза', 'раз')} за период.",
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
    if main["type"] == "линейка" and ("Так поступили" in main["text"] or "потом взяли" in main["text"]):
        label = "каталог и покупки других клиентов"
    extra = [r["text"] for r in rs[1:3] if r["type"] in ("следом", "семейство", "класс")]
    return {
        "item": b, "slot": main["slot"], "title": SLOT_TITLE[main["slot"]],
        "why": main["text"], "also": " ".join(extra[:1]), "label": label,
        "desc": describe(task, b, st.attrs[b]),
        "paths": [{"src": r["src"], "type": r["edge"], "kind": r["type"], "text": r["text"]} for r in rs[:3] if r["src"]],
    }
