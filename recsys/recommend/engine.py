"""Рекомендатель: три совета клиенту по готовому графу связей.

На запрос модель не вызывается: это поиск по таблицам в памяти. Каждый совет несёт путь по графу —
из чего он выведен и каким источником подтверждён. Этот путь показывается клиенту как объяснение
и рисуется в визуализации.

Три вида советов (слоты):
  renew   — что истекает: продление того, что уже куплено
  next    — следующая ступень: старшая редакция или новая версия
  missing — чего не хватает: дополняющие и требуемые продукты
  replace — замена: когда купленного зарубежного продукта больше нет в продаже — его российский аналог
            из таблицы замещения, а если прямого аналога нет — российский продукт того же класса из реестра.
            Если при этом срок лицензии подошёл, замена идёт первой: продлить такую лицензию нельзя.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from recsys.config import INTERIM

SLOT = {
    "renewal": "renew",
    "upgrade": "next", "new_version": "next",
    "requires": "missing", "complements": "missing", "compatible": "missing",
    "copurchase": "missing", "next_purchase": "missing", "mentions": "missing",
    "analog_ru": "replace", "analog_class": "replace",
}
SLOT_TITLE = {
    "renew": "Что истекает", "next": "Следующая ступень",
    "missing": "Чего не хватает", "replace": "Замена", "popular": "Часто берут",
}
# Вес вида связи. Начальные значения; после получения продаж подбираются по истории (calibrate).
WEIGHT = {
    "renewal": 1.0, "requires": 0.85, "upgrade": 0.7, "new_version": 0.6,
    "copurchase": 0.65, "next_purchase": 0.65, "complements": 0.55, "compatible": 0.5,
    "analog_ru": 0.45, "analog_class": 0.45, "mentions": 0.25,
}
# Замена по классу — вывод слабее прямого аналога: тот же класс ещё не значит «то же самое».
CLASS_ANALOG_CONFIDENCE = 0.5
CLASS_ANALOG_TOP = 5         # сколько российских продуктов класса рассматриваем
# Срок лицензии подошёл, а продлить её нельзя: замена так же срочна, как продление.
LAPSE_ANALOG = 0.9           # нижняя граница оценки прямого аналога, умножается на уверенность связи
LAPSE_CLASS = 0.75           # нижняя граница оценки замены по классу
LABEL = {
    "таблица сравнения магазина": "подтверждено магазином",
    "типы лицензий на странице": "подтверждено магазином",
    "срок лицензии": "срок вашей покупки",
    "ссылка на странице магазина": "упоминание на странице магазина",
    "названия редакций": "выведено из названий",
    "номера версий в названиях": "выведено из названий",
    "названия": "выведено из названий",
    "название товара": "сказано в названии товара",
    "таблица замещения АРПП": "реестр российского ПО",
    "реестр и класс": "реестр российского ПО, тот же класс",
    "продажи": "подтверждено покупками",
    "модель": "предложено моделью, проверено критиком",
    "витрина": "порядок витрины магазина",
}
RENEW_WINDOW = (-60, 120)  # дней до конца срока: от «просрочено два месяца» до «осталось четыре»


@dataclass
class Purchase:
    family: str                      # ключ семейства «вендор/семейство»
    date: date | None = None
    term_months: int | None = None   # срок лицензии; 0 — бессрочная; None — неизвестен
    license_type: str = ""
    platform: str = ""
    qty: int | None = None
    sku: str = ""


@dataclass
class Client:
    client_type: str                 # «b2b» или «b2c»
    purchases: list[Purchase] = field(default_factory=list)
    industry: str = ""
    today: date = field(default_factory=date.today)


@dataclass
class Rec:
    family: str
    name: str
    vendor: str
    url: str
    slot: str
    slot_title: str
    score: float
    why: str
    label: str
    paths: list[dict]
    fallback: bool = False           # совет «часто берут», а не вывод из покупок клиента
    also: str = ""                   # попутный совет по той же линейке: «при продлении можно перейти выше»
    also_family: str = ""
    via_class: str = ""              # совет выведен через связь классов: из одного класса берём один товар

    def to_dict(self) -> dict:
        return asdict(self)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def _q(name: str) -> str:
    """Название в кавычках-ёлочках; если оно уже в них, вторых не ставим."""
    name = (name or "").strip()
    return name if name.startswith("«") and name.endswith("»") else f"«{name}»"


def _split(s: str) -> set[str]:
    return {x for x in (s or "").split("|") if x}


class Graph:
    """Граф связей в памяти: узлы-семейства, исходящие связи, линейки и аналоги для запретов."""

    def __init__(self, nodes: dict[str, dict], edges: list[dict],
                 class_links: list[dict] | None = None, class_rank: dict[str, list[str]] | None = None,
                 popular: dict[str, list[dict]] | None = None, class_names: dict[str, str] | None = None):
        self.nodes = nodes
        self.out: dict[str, list[dict]] = defaultdict(list)
        self.line: dict[str, set[str]] = defaultdict(set)
        self.analogs: dict[str, set[str]] = defaultdict(set)
        for e in edges:
            e["confidence"] = float(e["confidence"])
            if e["type"] == "same_line":
                self.line[e["src"]].add(e["dst"])
                self.line[e["dst"]].add(e["src"])
                continue
            if e["type"] == "analog_ru":
                self.analogs[e["src"]].add(e["dst"])
                self.analogs[e["dst"]].add(e["src"])
            self.out[e["src"]].append(e)
        # связи между классами: «к антивирусу обычно берут резервное копирование»
        self.class_links: dict[str, list[dict]] = defaultdict(list)
        for link in class_links or []:
            link["confidence"] = float(link["confidence"])
            self.class_links[link["src_class"]].append(link)
        # семейства класса в порядке витрины или популярности
        self.class_rank = class_rank or {}
        self.class_names: dict[str, str] = class_names or {}
        # российские продукты из реестра по классам — чем заменить снятый с продажи зарубежный продукт.
        # Порядок: сначала те, что в продаже, затем по месту на витрине класса (первые 12 мест), затем по ключу.
        self.ru_by_class: dict[str, list[str]] = defaultdict(list)
        for f, n in nodes.items():
            if n.get("class") and str(n.get("ru", "0")) == "1" and n.get("kind", "software") != "hardware":
                self.ru_by_class[n["class"]].append(f)
        for cid, fams in self.ru_by_class.items():
            pos = {f: i for i, f in enumerate(self.class_rank.get(cid, [])[:12])}
            fams.sort(key=lambda f: (0 if self.on_sale(f) else 1, pos.get(f, 10 ** 6), f))
        # «чаще всего берут» по сегменту: {"b2b": [...], "b2c": [...], "b2b|отрасль": [...]}
        self.popular: dict[str, list[dict]] = popular or self._popular_from_store()

    def _popular_from_store(self) -> dict[str, list[dict]]:
        """Пока продаж нет: самое обсуждаемое на витрине — товары с наибольшим числом отзывов покупателей."""
        def reviews(n: dict) -> int:
            return int(float(n.get("reviews") or 0))

        on_sale = [n for n in self.nodes.values()
                   if str(n.get("on_sale", "0")) == "1" and reviews(n) > 0 and n.get("kind", "software") == "software"]
        on_sale.sort(key=lambda n: (-reviews(n), -float(n.get("rating") or 0)))

        def item(n: dict) -> dict:
            return {"family": n["family"], "source": "витрина",
                    "evidence": f"{reviews(n)} отзывов покупателей в магазине, оценка {n.get('rating') or '—'}"}

        return {
            "b2b": [item(n) for n in on_sale if _split(n.get("client_types", "")) != {"b2c"}][:60],
            "b2c": [item(n) for n in on_sale if str(n.get("b2b_only", "0")) != "1"][:60],
        }

    @classmethod
    def load(cls, folder: Path = INTERIM) -> "Graph":
        def rows(name: str) -> list[dict]:
            p = folder / name
            if not p.exists():
                return []
            with p.open(encoding="utf-8") as f:
                return list(csv.DictReader(f))

        def obj(name: str) -> dict:
            p = folder / name
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

        nodes = {r["family"]: r for r in rows("graph_nodes.csv")}
        return cls(nodes, rows("graph_edges.csv"), rows("class_links.csv"),
                   obj("class_rank.json"), obj("popular.json") or None, obj("class_names.json"))

    def klass(self, family: str) -> str:
        n = self.nodes.get(family) or {}
        return n.get("class") or f"{n.get('category', '')}/{n.get('subcategory', '')}"

    def on_sale(self, family: str) -> bool:
        return str((self.nodes.get(family) or {}).get("on_sale", "0")) == "1"

    def by_request(self, family: str) -> bool:
        """Российский продукт из реестра без цены на витрине: организация может запросить цену."""
        n = self.nodes.get(family) or {}
        return (str(n.get("on_sale", "0")) != "1" and str(n.get("on_request", "0")) == "1"
                and str(n.get("ru", "0")) == "1")

    def available(self, family: str, client_type: str) -> bool:
        """Можно ли советовать товар: он продаётся, либо (для организаций) его можно запросить."""
        return self.on_sale(family) or (client_type == "b2b" and self.by_request(family))


class Recommender:
    def __init__(self, graph: Graph, weights: dict[str, float] | None = None,
                 min_confidence: float = 0.0, diversify: bool = True,
                 renew_window: tuple[int, int] = RENEW_WINDOW, fill: bool = False):
        self.g = graph
        self.w = {**WEIGHT, **(weights or {})}
        self.min_confidence = min_confidence   # ползунок «охват — точность»
        self.diversify = diversify
        self.renew_window = renew_window
        self.fill = fill                       # добирать блок до трёх советов тем, что «чаще всего берут»

    # ---- запасной уровень ----------------------------------------------------------------

    def _popular(self, client: Client, owned: dict[str, Purchase], platforms: set[str],
                 taken: list[Rec], k: int) -> list[Rec]:
        """Добор блока: что чаще всего берут клиенты того же типа (и отрасли, если она известна)."""
        pool = (self.g.popular.get(f"{client.client_type}|{client.industry}") if client.industry else None) \
            or self.g.popular.get(client.client_type) or []
        block = set()
        for r in taken:
            block |= {r.family} | self.g.line.get(r.family, set())
        out: list[Rec] = []
        for it in pool:
            if len(taken) + len(out) >= k:
                break
            fam = it["family"]
            if fam in block or fam not in self.g.nodes or self._banned(client, owned, platforms, fam, "popular", ""):
                continue
            n = self.g.nodes[fam]
            who = "клиенты вашей отрасли" if client.industry and f"{client.client_type}|{client.industry}" in self.g.popular \
                else ("организации" if client.client_type == "b2b" else "частные покупатели")
            source = it.get("source", "продажи")
            why = (f"Чаще всего берут {who}: {it.get('evidence', '')}".rstrip(": ") + "." if source == "продажи"
                   else f"Один из самых востребованных товаров магазина: {it.get('evidence', '')}.")
            edge = {"src": "", "dst": fam, "type": "popular", "source": source,
                    "confidence": 0.0, "evidence": it.get("evidence", ""), "score": 0.0}
            out.append(Rec(family=fam, name=n["name"], vendor=n.get("vendor", ""), url=n.get("url", ""),
                           slot="popular", slot_title=SLOT_TITLE["popular"], score=0.0, why=why,
                           label=LABEL.get(source, source), paths=[edge], fallback=True))
            block |= {fam} | self.g.line.get(fam, set())
        return out

    # ---- продление -----------------------------------------------------------------------

    def _term(self, p: Purchase) -> tuple[int | None, bool]:
        """Срок лицензии в месяцах и признак «срок предположен по типичному для семейства»."""
        if p.term_months is not None:
            return p.term_months, False
        t = (self.g.nodes.get(p.family) or {}).get("typical_term", "")
        return (int(float(t)), True) if t not in ("", None) else (None, True)

    def _renewal(self, client: Client, p: Purchase) -> tuple[dict, float] | None:
        """Совет о продлении: то же семейство, а если его уже не продают — его новая версия."""
        term, assumed = self._term(p)
        if term == 0:
            return None                                 # бессрочная лицензия не продлевается
        n = self.g.nodes.get(p.family) or {}
        has_renewal = "renewal" in _split(n.get("license_types", ""))
        if p.date and term:
            end = _add_months(p.date, term)
            left = (end - client.today).days
            if not self.renew_window[0] <= left <= self.renew_window[1]:
                return None                             # срок ещё далеко или давно прошёл
            strength = (0.95 if has_renewal else 0.85) * (0.8 if assumed else 1.0)
            when = f"истекает {end:%d.%m.%Y}" if left >= 0 else f"истекла {end:%d.%m.%Y}"
            about = "обычно на" if assumed else "на"
            note = f"куплена {p.date:%d.%m.%Y}, {about} {term} мес., {when}"
        elif has_renewal:
            strength, note = 0.35, "срок покупки неизвестен; в магазине есть лицензия продления"
        else:
            return None
        target = p.family
        if not self.g.available(target, client.client_type):
            newer_ = [e for e in self.g.out.get(p.family, ())
                      if e["type"] == "new_version" and self.g.available(e["dst"], client.client_type)]
            if not newer_:
                return None                             # замену подберёт связь «российский аналог»
            target = max(newer_, key=lambda e: e["confidence"])["dst"]
            note += "; эта версия снята с продажи, продаётся следующая"
        edge = {
            "src": p.family, "dst": target, "type": "renewal",
            "source": "типы лицензий на странице" if has_renewal else "срок лицензии",
            "confidence": round(strength, 2), "evidence": note,
        }
        return edge, self.w["renewal"] * strength

    def _lapsing(self, client: Client, p: Purchase) -> str:
        """Срок лицензии подошёл, а продлить её нельзя: продукт клиенту не продаётся и новой версии нет.

        Возвращает пояснение для клиента («куплена …, на 12 мес., истекла …») или пустую строку.
        """
        term, assumed = self._term(p)
        if not term or not p.date:
            return ""
        end = _add_months(p.date, term)
        left = (end - client.today).days
        if not self.renew_window[0] <= left <= self.renew_window[1]:
            return ""
        if self.g.available(p.family, client.client_type):
            return ""
        if any(e["type"] == "new_version" and self.g.available(e["dst"], client.client_type)
               for e in self.g.out.get(p.family, ())):
            return ""
        when = f"истекает {end:%d.%m.%Y}" if left >= 0 else f"истекла {end:%d.%m.%Y}"
        about = "обычно на" if assumed else "на"
        return f"куплена {p.date:%d.%m.%Y}, {about} {term} мес., {when}"

    # ---- запреты -------------------------------------------------------------------------

    def _banned(self, client: Client, owned: dict[str, Purchase], platforms: set[str],
                dst: str, edge_type: str, src: str) -> str:
        """Возвращает причину запрета или пустую строку."""
        n = self.g.nodes.get(dst)
        if not n or not self.g.available(dst, client.client_type):
            return "нет в продаже"
        if edge_type != "renewal" and dst in owned:
            return "уже куплено"

        def b2b_only(node: dict) -> bool:
            return str(node.get("b2b_only", "0")) == "1"

        def home_only(node: dict) -> bool:
            return _split(node.get("client_types", "")) == {"b2c"} and not b2b_only(node)

        # Ограничение по типу клиента не действует внутри линейки, которую клиент уже покупает:
        # раз он купил такую лицензию, продление и следующая ступень ему доступны.
        s = self.g.nodes.get(src) or {}
        in_line = edge_type in ("renewal", "upgrade", "new_version")
        if client.client_type == "b2c" and b2b_only(n) and not (in_line and b2b_only(s)):
            return "продаётся только юридическим лицам"
        if client.client_type == "b2b" and home_only(n) and not (in_line and home_only(s)):
            return "лицензия только для дома"
        np_ = {p.strip().lower() for x in _split(n.get("platforms", "")) for p in x.split(",") if p.strip()}
        if edge_type != "renewal" and platforms and np_ and not (platforms & np_):
            return "не та платформа"
        if edge_type not in ("upgrade", "new_version", "renewal"):
            for o in owned:
                if dst in self.g.line.get(o, ()):
                    return "та же линейка, что уже куплена"
                if edge_type != "analog_ru" and dst in self.g.analogs.get(o, ()) and self.g.on_sale(o):
                    return "аналог уже купленного"
        if edge_type in ("upgrade", "new_version"):
            # нельзя предлагать ступень, которая не выше уже купленной в этой же линейке
            for o in owned:
                if o != src and dst in self.g.line.get(o, ()):
                    ro, rd = self.g.nodes[o].get("edition_rank"), n.get("edition_rank")
                    if ro not in ("", None) and rd not in ("", None) and float(ro) >= float(rd):
                        return "в линейке уже куплена такая же или старшая редакция"
        return ""

    # ---- объяснения ----------------------------------------------------------------------

    def _why(self, edge: dict, p: Purchase | None, client: Client) -> str:
        src = _q(self.g.nodes.get(edge["src"], {}).get("name", edge["src"]))
        dst = _q(self.g.nodes.get(edge["dst"], {}).get("name", edge["dst"]))
        t, ev = edge["type"], edge.get("evidence", "")
        if edge.get("class"):
            cname = self.g.class_names.get(edge["class"], edge["class"])
            return (f"{ev.rstrip('. ')}. Подойдёт {dst} — {edge['rank']}-е место на витрине магазина "
                    f"в классе «{cname}».")
        if t == "renewal":
            if edge["src"] != edge["dst"]:
                return f"Лицензия {src}: {ev}. Актуальная версия — {dst}."
            return f"Лицензия {src}: {ev}. Продление сохраняет работу без перерыва."
        if t == "upgrade":
            gained = ev.split("добавляет:")[1].split("; http")[0].strip() if "добавляет:" in ev else ""
            if gained:
                return f"У вас {src}. Редакция {dst} добавляет то, чего нет в вашей: {gained}."
            return f"У вас {src}. Следующая редакция в этой линейке — {dst}."
        if t == "new_version":
            return f"У вас {src}. В продаже есть более новая версия — {dst}."
        if t in ("analog_ru", "analog_class"):
            on_request = str(self.g.nodes.get(edge["src"], {}).get("on_request", "0")) == "1"
            if t == "analog_ru":
                reg = ev.split("(реестр № ")[1].split(")")[0] if "(реестр № " in ev else ""
                tail = f" (реестр российского ПО, запись № {reg})" if reg else ""
                target = f"Российский аналог: {dst}{tail}."
            else:
                cname = self.g.class_names.get(edge.get("of_class", ""), "")
                where = f" («{cname}»)" if cname else ""
                sib = edge.get("sibling", "")
                via = (f" В таблице замещения он назван аналогом {_q(self.g.nodes.get(sib, {}).get('name', sib))}."
                       if sib else "")
                target = (f"Полной замены в таблице замещения нет. Ближайший российский продукт того же класса{where} "
                          f"из реестра — {dst}.{via}")
            lapse = edge.get("lapse", "")
            if lapse:                                   # срок подошёл, а продлить лицензию нельзя
                how = ("В открытой продаже продукта нет: продлить её можно только по запросу цены у менеджера."
                       if on_request else "Продлить её нельзя: продукт снят с продажи.")
                return f"Лицензия {src}: {lapse}. {how} {target}"
            if self.g.on_sale(edge["src"]):
                gone = ""
            elif on_request:
                gone = ", который теперь продаётся только по запросу"
            else:
                gone = ", которого больше нет в продаже"
            return f"{src} — зарубежный продукт{gone}. {target}"
        if t == "requires":
            return f"Для работы {src} нужен {dst}. {ev}".strip()
        if t in ("copurchase", "next_purchase"):
            return f"Клиенты, купившие {src}, берут и {dst}. {ev}".strip()
        if t == "mentions":
            return f"На странице {src} магазин упоминает {dst}."
        return f"К {src} подходит {dst}. {ev}".strip()

    # ---- подбор --------------------------------------------------------------------------

    def recommend(self, client: Client, k: int = 3, explain_bans: bool = False) -> list[Rec] | tuple[list[Rec], list[dict]]:
        owned: dict[str, Purchase] = {}
        for p in sorted(client.purchases, key=lambda x: x.date or date.min):
            owned[p.family] = p            # по каждому семейству помним последнюю покупку
        platforms = {x.strip().lower() for p in client.purchases for x in (p.platform or "").split(",") if x.strip()}

        cands: dict[tuple[str, str], dict] = {}
        bans: list[dict] = []

        def push(dst: str, slot: str, s: float, edge: dict, p: Purchase | None) -> None:
            c = cands.setdefault((dst, slot), {"miss": 1.0, "paths": [], "best": (0.0, None, None)})
            c["miss"] *= 1.0 - min(max(s, 0.0), 0.99)
            c["paths"].append({**edge, "score": round(s, 3)})
            if s > c["best"][0]:
                c["best"] = (s, edge, p)

        for fam, p in owned.items():
            age = (client.today - p.date).days if p.date else None
            recency = 0.7 if age is None else max(0.3, math.exp(-max(age, 0) / 730))
            lapse = self._lapsing(client, p)            # срок подошёл, а продлить нельзя: замена срочная
            renewal = self._renewal(client, p)
            if renewal:
                edge, s = renewal
                reason = self._banned(client, owned, platforms, edge["dst"], "renewal", fam)
                if reason:
                    bans.append({"family": edge["dst"], "type": "renewal", "from": fam, "reason": reason})
                else:
                    push(edge["dst"], "renew", s, edge, p)
            direct = False                              # у продукта нашёлся прямой российский аналог
            for e in self.g.out.get(fam, ()):
                t = e["type"]
                if t == "renewal" or e["confidence"] < self.min_confidence or t not in self.w:
                    continue                            # продление посчитано выше, по сроку покупки
                reason = self._banned(client, owned, platforms, e["dst"], t, fam)
                if reason:
                    bans.append({"family": e["dst"], "type": t, "from": fam, "reason": reason})
                    continue
                s = self.w[t] * e["confidence"] * recency
                if t == "analog_ru" and not self.g.on_sale(fam):
                    s = min(0.95, s * 2.0)              # купленного больше не продают: замена важнее
                    if lapse:
                        s = max(s, LAPSE_ANALOG * e["confidence"])
                        e = {**e, "lapse": lapse}
                if not self.g.on_sale(e["dst"]):
                    s *= 0.8                            # цена только по запросу: совет чуть слабее
                direct = direct or t == "analog_ru"
                push(e["dst"], SLOT[t], s, e, p)

            # замена по классу: купленный зарубежный продукт сняли с продажи, а прямого аналога в таблице
            # замещения нет — советуем российские продукты того же класса из реестра
            node = self.g.nodes.get(fam) or {}
            cls = node.get("class") or ""
            if (cls and not direct and not self.g.on_sale(fam) and str(node.get("ru", "0")) != "1"
                    and node.get("kind", "software") != "hardware"
                    and CLASS_ANALOG_CONFIDENCE >= self.min_confidence):
                vendor = fam.split("/")[0]

                def sibling(d: str) -> str:
                    """Продукт того же вендора и класса, аналогом которого d назван в таблице замещения."""
                    for x in sorted(self.g.analogs.get(d, ())):
                        if x != fam and x.split("/")[0] == vendor and (self.g.nodes.get(x) or {}).get("class") == cls:
                            return x
                    return ""

                pool = [(d, sibling(d)) for d in self.g.ru_by_class.get(cls, ())
                        if d != fam and d not in self.g.analogs.get(fam, ())]
                # сначала те, кого таблица замещения называет аналогом другого продукта того же вендора
                pool = [x for x in pool if x[1]] + [x for x in pool if not x[1]]
                for rank, (dst, sib) in enumerate(pool[:CLASS_ANALOG_TOP]):
                    if self._banned(client, owned, platforms, dst, "analog_class", fam):
                        continue
                    s = min(0.95, self.w["analog_class"] * CLASS_ANALOG_CONFIDENCE * recency * 2.0)
                    if lapse:
                        s = max(s, LAPSE_CLASS)
                    if not self.g.on_sale(dst):
                        s *= 0.8
                    edge = {"src": fam, "dst": dst, "type": "analog_class", "source": "реестр и класс",
                            "confidence": CLASS_ANALOG_CONFIDENCE, "evidence": "", "lapse": lapse,
                            "of_class": cls, "group": f"replace:{fam}", "sibling": sib}
                    push(dst, "replace", s / (1 + 0.35 * rank), edge, p)

            # запасной уровень: связи между классами
            for link in self.g.class_links.get(self.g.klass(fam), ()):
                if link["confidence"] < self.min_confidence:
                    continue
                if link.get("audience", "any") not in ("any", "", client.client_type):
                    continue                            # совет адресован другому типу клиента
                t = link.get("type") if link.get("type") in self.w else "complements"
                for rank, dst in enumerate(self.g.class_rank.get(link["dst_class"], ())[:10]):
                    reason = self._banned(client, owned, platforms, dst, t, fam)
                    if reason:
                        continue
                    edge = {
                        "src": fam, "dst": dst, "type": t, "source": link.get("source", "модель"),
                        "confidence": link["confidence"], "evidence": link.get("reason", ""),
                        "class": link["dst_class"], "rank": rank + 1,
                    }
                    # чем ниже товар на витрине своего класса, тем слабее совет
                    push(dst, "missing", self.w[t] * link["confidence"] * recency / (1 + 0.35 * rank), edge, p)

        recs = []
        for (dst, slot), c in cands.items():
            n = self.g.nodes[dst]
            s, edge, p = c["best"]
            recs.append(Rec(
                family=dst, name=n["name"], vendor=n.get("vendor", ""), url=n.get("url", ""),
                slot=slot, slot_title=SLOT_TITLE[slot], score=round(1.0 - c["miss"], 4),
                why=self._why(edge, p, client), label=LABEL.get(edge.get("source", ""), edge.get("source", "")),
                paths=sorted(c["paths"], key=lambda x: -x["score"])[:4],
                via_class=edge.get("class", "") or edge.get("group", ""),
            ))
        top = self._pick(recs, k)
        if self.fill and len(top) < k:
            top += self._popular(client, owned, platforms, top, k)
        return (top, bans) if explain_bans else top

    def _pick(self, recs: list[Rec], k: int) -> list[Rec]:
        """Три совета: по убыванию оценки, без повторов линейки, по возможности разных видов."""
        pool = sorted(recs, key=lambda r: -r.score)
        chosen: list[Rec] = []
        used = Counter()
        while pool and len(chosen) < k:
            best = max(pool, key=lambda r: r.score * (0.6 ** used[r.slot] if self.diversify else 1.0))
            chosen.append(best)
            used[best.slot] += 1
            block = {best.family} | self.g.line.get(best.family, set())
            if best.slot == "renew":
                # продление и переход выше в той же линейке — один совет, а не два места в блоке
                steps = [r for r in pool if r.slot == "next" and r.family in block and r.family != best.family]
                if steps:
                    step = max(steps, key=lambda r: r.score)
                    best.also, best.also_family = step.why, step.family
            pool = [r for r in pool if r.family not in block
                    and not (best.via_class and r.via_class == best.via_class)]
        return chosen
