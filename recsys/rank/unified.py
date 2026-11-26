"""Единый порядок советов: одно число для любого кандидата и два вида одного списка.

Число — оценка вероятности, что клиент купит товар в ближайшие три месяца (среди клиентов, которые
в этот срок вообще что-то докупают: так устроен протокол организаторов).
  товар из продаж               — оценку даёт обученная модель (recsys.rank.ltr), как и раньше;
  товар каталога вне продаж     — попадает в список только по проверенной связи открытого графа
                                  (новая версия, прямой аналог снятого с продажи продукта, старшая
                                  редакция, дополнение из названия) и получает осторожную оценку,
                                  привязанную к тому, как часто связь такого вида сбывалась в продажах
                                  (reports/link_rates.json); непроверенные связи классов сюда не входят;
  недоступный товар             — уступает место преемнику (тот же товар в продаваемом артикуле,
                                  новая версия, прямой аналог) и передаёт ему свою оценку.

Два вида одного и того же порядка:
  protocol(c) — три лучших товара из продаж, без правил показа: по нему считается официальная цифра
                (совпадает с режимом «полная модель» в recsys.eval.value);
  cabinet(c)  — как в блоке кабинета: только то, что можно купить; один совет на продукт; дополнения
                только к купленному; не дороже самой дорогой прошлой покупки больше чем в пять раз.
                Три строки, и популярное с личным не смешивается:
                  «Пора продлить»      — продления купленного, когда срок подходит (не больше двух);
                  «К вашим продуктам»  — до трёх советов, у каждого основание в покупках клиента;
                  «Часто докупают»     — продукт, который клиенты той же группы чаще всего берут впервые,
                                         когда возвращаются за покупкой; подписан как популярный, а не
                                         как личный совет. Одна карточка; если личных советов меньше
                                         трёх — по числу свободных мест.

Запуск:  python -m recsys.rank.unified     сверка с официальной цифрой и сводка по виду «кабинет»
Отчёт:   reports/unified.json (только сводные числа)
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Callable

import numpy as np

from recsys.catalog.items import license_kind, seat_band, term_months
from recsys.config import INTERIM, ROOT
from recsys.rank.data import OFFICIAL, Task, load_task, make_fold, months_between
from recsys.rank.features import FEATURES, FoldStats, build_attrs

REPORT = ROOT / "reports" / "unified.json"
COL = {name: i for i, name in enumerate(FEATURES)}
# признаки «собственного основания»: совет выведен из купленного клиентом, а не из общей популярности
OWN_GROUND = ["pair_sum", "co_sum", "same_line", "same_vkey", "fam_sum"]
ADDON_KINDS = ("media", "support", "renewal", "extension")
PRICE_CAP = 5.0
RENEW_MAX, NEW_MAX = 2, 3
RECENT_DAYS = 183
DUE_MONTHS = 4          # «Пора продлить» — только когда до конца срока осталось не больше четырёх месяцев
YEARLY = re.compile(r"\bгодов(?:ая|ой|ую)\b", re.I)
# «Часто докупают»: продукт, который впервые взяли не меньше 5 % клиентов той же группы (организации
# или частные лица), вернувшихся за покупкой, и не меньше десяти таких клиентов. Продления и варианты
# уже купленного в счёт не идут, иначе «часто докупают» превращается в «часто продлевают».
POPULAR_SHARE, POPULAR_CLIENTS = 0.05, 10
# Домашняя лицензия (для семьи, персональная, для дома и учёбы) — не для организации. Исключение — организация,
# которая такие лицензии уже покупала: её собственные покупки важнее правила.
HOME = re.compile(r"\bfamily\b|\bpersonal\b|для семьи|для дома|home and student|\bhome\b(?! and business)", re.I)
POPULAR_MIN = 1         # карточек в строке «Часто докупают»: одна, а если личных советов меньше трёх — по числу свободных мест
# связи с измеренной поддержкой идут наравне с советами модели; слабые (редко сбываются или проверить
# нечем) — после «часто берут», по своей оценке
STRONG_LINKS = {"new_version", "analog_ru"}

# Оценки для связей графа, ведущих к товарам вне продаж. Измерить можно лишь то, что попало в продажи
# (python -m recsys.rank.link_rates): новая версия сбылась в 19 % случаев (одна пара семейств),
# прямой аналог — в 2–4 %, старшая редакция — меньше 1 %, дополнения из названий проверить нечем.
# Поэтому значения взяты с запасом вниз и служат только для расстановки среди прочих советов.
PRIOR = {"new_version": 0.15, "analog_ru": 0.04, "upgrade": 0.01, "complements": 0.005}
BASIS = {"new_version": "новая версия", "analog_ru": "замена", "upgrade": "старшая редакция",
         "complements": "дополнение"}
VERIFIED = {("upgrade", "таблица сравнения магазина"), ("upgrade", "названия редакций"),
            ("new_version", "номера версий в названиях"), ("analog_ru", "таблица замещения АРПП"),
            ("complements", "название товара")}


# Особые лицензии: академические, учебные, государственные, некоммерческие. Их подбираем только к такой же
# купленной: коммерческому клиенту академическая лицензия не положена, как бы хорошо ни совпали места и срок.
# Признак берётся из названия: поле каталога «Тип организации» ненадёжно (у половины проданных товаров
# с пометкой «Академическая» или «Государственная» в названии ничего такого нет — например, Astra Linux).
SPECIAL = re.compile(r"academ|академ|образоват|educat|учебн|студен|преподават|\bschool\b|government|\bgov\b"
                     r"|государственных (?:организац|учрежд)|некоммерч|non-?profit", re.I)


def special_licence(name: str) -> bool:
    """Лицензия не для обычного коммерческого клиента (по названию товара)."""
    return bool(SPECIAL.search(name or ""))


@dataclass
class Scored:
    item: str                       # item_id из продаж или «K:<id товара каталога>» для товара вне продаж
    p: float                        # оценка вероятности покупки за три месяца
    basis: str                      # «модель» или вид связи графа
    grounded: bool                  # есть собственное основание, а не только «часто берут»
    in_sales: bool = True
    title: str = ""
    url: str = ""
    price: float | None = None
    src: str = ""                   # купленный товар, от которого идёт связь или подстановка
    evidence: str = ""              # основание связи графа
    replaces: str = ""              # недоступный товар, место которого занял этот
    slot: str = "new"               # «renew» — продление купленного, «new» — личный совет, «popular» — «часто докупают»
    share: float = 0.0              # для «popular»: доля вернувшихся клиентов группы, впервые взявших этот продукт
    features: dict = field(default_factory=dict, repr=False)


class Catalog:
    """Каталог магазина (фид) и проверенные связи открытого графа между семействами."""

    def __init__(self, task: Task):
        self.task = task
        self.offers: dict[str, dict] = {}
        self.by_family: dict[str, list[str]] = defaultdict(list)
        need = {r.get("family_id") for r in task.items.values() if r.get("family_id")}
        self.offer_item = {r["offer_id"]: i for i, r in task.items.items() if r.get("offer_id")}
        nodes = {}
        with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                nodes[r["family"]] = r
        self.links: dict[str, list[dict]] = defaultdict(list)      # семейство (id фида) -> проверенные связи
        with (INTERIM / "graph_edges.csv").open(encoding="utf-8") as f:
            for e in csv.DictReader(f):
                if (e["type"], e["source"]) not in VERIFIED:
                    continue
                s, d = nodes.get(e["src"]), nodes.get(e["dst"])
                if not s or not d or not s["family_id"] or not d["family_id"] or s["family_id"] == d["family_id"]:
                    continue
                self.links[s["family_id"]].append({
                    "dst": d["family_id"], "type": e["type"], "source": e["source"],
                    "confidence": float(e["confidence"]), "evidence": e["evidence"], "dst_name": d["name"],
                })
                need.add(d["family_id"])
        with gzip.open(INTERIM / "feed_offers.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                if o.get("family_id") in need or o["offer_id"] in self.offer_item:
                    self.offers[o["offer_id"]] = o
                    if o.get("family_id"):
                        self.by_family[o["family_id"]].append(o["offer_id"])
        self._parsed: dict[str, tuple] = {}

    def offer_of(self, item: str) -> dict | None:
        return self.offers.get((self.task.items.get(item) or {}).get("offer_id") or "")

    def purchasable(self, item: str) -> bool | None:
        """Можно ли купить товар на витрине сейчас; None — товара нет в каталоге (например, облачная подписка)."""
        if item.startswith("K:"):
            o = self.offers.get(item[2:])
        else:
            o = self.offer_of(item)
        return None if o is None else bool(o["available"])

    def price(self, offer: dict | None) -> float | None:
        try:
            p = float(offer["price"]) if offer else 0.0
        except (TypeError, ValueError):
            return None
        return p if p > 0 else None

    def parsed(self, offer_id: str) -> tuple:
        """(вид лицензии, нижняя граница диапазона мест, срок в месяцах, для кого) товара каталога."""
        if offer_id not in self._parsed:
            o = self.offers[offer_id]
            lo, _, _ = seat_band(o["name"])
            self._parsed[offer_id] = (license_kind(o["name"], o.get("license", "")), lo,
                                      term_months(o["name"], o.get("term", "")), (o.get("user") or "").lower())
        return self._parsed[offer_id]

    def family_alive(self, family_id: str) -> bool:
        return any(self.offers[x]["available"] for x in self.by_family.get(family_id, ()))

    def pick(self, family_id: str, like: dict | None, group: str, kinds: tuple = ("base", "")) -> dict | None:
        """Товар семейства, который можно купить и который ближе всего к купленному: те же места и срок."""
        best, best_key = None, None
        want_special = special_licence((like or {}).get("name", ""))
        band = int(like["band_lo"]) if like and like.get("band_lo") else None
        term = int(float(like["term_months"])) if like and like.get("term_months") not in ("", None) else None
        for oid in self.by_family.get(family_id, ()):
            o = self.offers[oid]
            if not o["available"]:
                continue
            kind, lo, t, user = self.parsed(oid)
            if special_licence(o["name"]) != want_special:
                continue                                    # академическая — только тому, кто такую и покупал
            if group == "B2C" and "юр" in user and "физ" not in user:
                continue
            if group == "B2B" and "физ" in user and "юр" not in user:
                continue
            key = (kind in kinds, band is not None and lo == band, term is not None and t == term,
                   -(self.price(o) or 1e12))
            if best_key is None or key > best_key:
                best, best_key = o, key
        return best

    def twin(self, item: str) -> dict | None:
        """Тот же товар в артикуле, который продаётся сейчас: то же семейство, вид лицензии, места и срок."""
        r = self.task.items.get(item) or {}
        fam = r.get("family_id")
        if not fam:
            return None
        want = (r.get("license_kind", ""), int(r["band_lo"]) if r.get("band_lo") else None,
                int(float(r["term_months"])) if r.get("term_months") not in ("", None) else None)
        special = special_licence(r.get("name", "") or self.task.names.get(item, ""))
        same = [self.offers[x] for x in self.by_family.get(fam, ())
                if self.offers[x]["available"] and self.parsed(x)[:3] == want
                and special_licence(self.offers[x]["name"]) == special]
        return min(same, key=lambda o: self.price(o) or 1e12) if same else None


class Unified:
    def __init__(self, st: FoldStats, model, catalog: Catalog, regime: str = "full"):
        self.st, self.model, self.cat, self.regime = st, model, catalog, regime
        self.task, self.fold, self.at = st.task, st.fold, st.attrs
        self.last_sold: dict[str, date] = {}
        for r in self.fold.train:
            self.last_sold[r.item] = r.week
        self.popular = self._popular_products()
        self.home_items = {i for i, n in self.task.names.items() if HOME.search(n)}

    def _popular_products(self) -> dict[str, dict[str, float]]:
        """Группа клиентов -> {продукт: доля вернувшихся клиентов группы, впервые взявших его при докупке}.

        Считается по обучающей части разреза. Клиент «вернулся», если покупал что-то новое позже своей
        первой недели. Продукт засчитывается ему, только если до этой покупки у него не было ни одного
        товара того же продукта: продления и другие варианты купленного сюда не попадают. Покупка
        продления, поддержки или расширения тоже не в счёт, даже когда в данных она первая: такой
        клиент владел продуктом раньше, чем начинаются данные.
        """
        at = self.at
        got: dict[str, Counter] = defaultdict(Counter)
        back = Counter()
        for c, d in self.fold.first.items():
            seq = sorted(d.items(), key=lambda kv: (kv[1], kv[0]))
            start = seq[0][1]
            if seq[-1][1] <= start:
                continue
            g = self.task.clients.get(c, {}).get("client_group", "")
            back[g] += 1
            had: set[str] = set()
            fresh: set[str] = set()
            i = 0
            while i < len(seq):
                j = i
                while j < len(seq) and seq[j][1] == seq[i][1]:
                    j += 1
                week = [at[b] for b, _ in seq[i:j]]
                if seq[i][1] > start:
                    fresh.update(ab.nline or ab.line for ab in week if ab.kind not in ADDON_KINDS
                                 and ab.line not in had and not (ab.nline and ab.nline in had))
                for ab in week:
                    had.add(ab.line)
                    if ab.nline:
                        had.add(ab.nline)
                i = j
            for k in fresh:
                got[g][k] += 1
        return {g: {k: n / back[g] for k, n in cnt.items() if n >= POPULAR_CLIENTS and n / back[g] >= POPULAR_SHARE}
                for g, cnt in got.items()}

    def sellable(self, item: str) -> bool:
        """Можно ли предлагать товар сейчас: он есть на витрине или его покупали в последние полгода.

        Многие корпоративные товары на витрине стоят «под запрос» и продаются через менеджеров,
        поэтому одного признака «в наличии» мало. Товар, которого нет в каталоге вовсе (облачные
        подписки), не отбрасываем: судить о нём по витрине нельзя.
        """
        if item.startswith("K:"):
            return bool(self.cat.offers[item[2:]]["available"])
        o = self.cat.offer_of(item)
        if o is None or o["available"]:
            return True
        last = self.last_sold.get(item)
        return last is not None and (self.fold.train_until - last).days <= RECENT_DAYS

    # ---- оценка моделью: товары из продаж ------------------------------------------------

    def _model_scores(self, c: str) -> list[tuple[str, float, list[float]]]:
        from recsys.eval.value import rows_in_regime            # тот же путь, что у официальной цифры

        rows = sorted(rows_in_regime(self.st, c, self.regime).items())
        if not rows:
            return []
        X = np.array([vec for _, vec in rows], dtype=np.float32)
        p = self.model.predict_proba(X)[:, 1]
        return [(b, float(s), vec) for (b, vec), s in zip(rows, p)]

    def protocol(self, c: str, top: int = 3) -> list[str]:
        """Как в протоколе организаторов: первые три товара из продаж по оценке модели."""
        ranked = [b for b, _, _ in sorted(self._model_scores(c), key=lambda x: (-x[1], x[0]))][:top]
        for b in self.st.fallback(c):
            if len(ranked) >= top:
                break
            if b not in ranked:
                ranked.append(b)
        return ranked

    # ---- единый порядок ------------------------------------------------------------------

    def order(self, c: str) -> list[Scored]:
        """Все кандидаты клиента по убыванию оценки: и из продаж, и из каталога по проверенным связям."""
        at, cat = self.at, self.cat
        h = self.fold.history[c]
        owned = set(h)
        group = self.task.clients.get(c, {}).get("client_group", "")
        own_lines = {at[a].line for a in h} | {at[a].nline for a in h if at[a].nline}
        out: dict[str, Scored] = {}
        for b, p, vec in self._model_scores(c):
            o = cat.offer_of(b)
            ab = at[b]
            variant = ab.line in own_lines or (ab.nline and ab.nline in own_lines)
            out[b] = Scored(
                item=b, p=p, basis="модель", grounded=any(vec[COL[n]] > 0 for n in OWN_GROUND),
                title=(o or {}).get("name") or self.task.names.get(b, b), url=(o or {}).get("url", ""),
                price=cat.price(o), slot="renew" if variant and self._is_renewal(ab, h) else "new",
                features={n: vec[COL[n]] for n in ("pair_n", "same_line", "same_vkey", "since_line", "pop_follow")},
            )
        # товары каталога по проверенным связям открытого графа
        own_fams = {(self.task.items.get(a) or {}).get("family_id", "") for a in h} - {""}
        for a in h:
            ra = self.task.items.get(a) or {}
            fam = ra.get("family_id")
            if not fam:
                continue
            alive = cat.family_alive(fam)
            for link in cat.links.get(fam, ()):
                if link["dst"] in own_fams:
                    continue
                if link["type"] == "analog_ru" and alive:
                    continue                                # замену предлагаем, только когда купленное больше не продают
                offer = cat.pick(link["dst"], ra, group)
                if not offer:
                    continue
                item = cat.offer_item.get(offer["offer_id"]) or "K:" + offer["offer_id"]
                if item in owned:
                    continue
                p = PRIOR[link["type"]] * min(1.0, link["confidence"] / 0.9)
                cur = out.get(item)
                if cur is not None:                         # товар уже оценён моделью: ей верим больше,
                    if not cur.evidence:                    # связь добавляем как основание
                        cur.src, cur.evidence = a, link["evidence"]
                        cur.grounded = cur.grounded or link["type"] in STRONG_LINKS
                    continue
                out[item] = Scored(
                    item=item, p=p, basis=BASIS[link["type"]], grounded=link["type"] in STRONG_LINKS,
                    in_sales=not item.startswith("K:"),
                    title=offer["name"], url=offer["url"], price=cat.price(offer), src=a, evidence=link["evidence"],
                )
        return sorted(out.values(), key=lambda s: (-s.p, s.item))

    def _is_renewal(self, ab, history: list[str]) -> bool:
        """Продление купленного: тот же продукт, а сам товар — продление, поддержка или очередной платёж подписки."""
        return ab.kind in ("renewal", "support") or ab.period is not None

    def months_left(self, c: str, item: str) -> int | None:
        """Сколько месяцев осталось до конца срока купленной лицензии того же продукта; None — срок неизвестен.

        Считается так же, как в объяснениях (recsys.rank.explain): срок купленного товара минус месяцы,
        прошедшие с его последней покупки; «годовая» в названии читается как 12 месяцев.
        """
        at = self.at
        ab = at.get(item)
        if ab is None:
            return None
        near = [a for a in self.fold.history[c] if at[a].line == ab.line or (ab.nline and at[a].nline == ab.nline)]
        if not near:
            return None
        a = max(near, key=lambda x: self.fold.last[c][x])
        term = at[a].term
        if not term and YEARLY.search(self.task.names.get(a, "")):
            term = 12
        if not term:
            return None
        return term - int(round(months_between(self.fold.last[c][a], self.fold.test_from)))

    # ---- вид «как в кабинете» ------------------------------------------------------------

    def cabinet(self, c: str, prices: dict[str, float] | None = None,
                keep: Callable[[list[str], Scored], tuple[bool, str]] | None = None,
                popular_keep: Callable[[list[str], Scored], tuple[bool, str]] | None = None,
                popular_n: int | None = None) -> dict:
        """Что видит клиент в кабинете.

        {"renew":   до двух продлений, срок которых подходит,
         "new":     до трёх личных советов — у каждого основание в покупках клиента,
         "popular": строка «Часто докупают» — популярный продукт группы, честно подписанный как популярный,
         "dropped": [{"item", "reason"}, ...]}

        Личный совет — это другой вариант купленного продукта, товар по проверенной связи графа или товар,
        который брали после купленного. Продукт из «часто докупают» личным советом не считается, даже если
        после купленного его брали: его берут после чего угодно. Он может попасть только в свою строку.

        keep — необязательный фильтр личных советов: функция (история клиента, кандидат) -> (оставить, причина).
        Через неё накладываются заранее вынесенные вердикты «нужно / уже есть / то же самое / не связано».
        Когда фильтр задан, решает он: одобренный им популярный продукт идёт в личные советы (у него есть
        «зачем»), отклонённый — остаётся кандидатом в строку «Часто докупают».
        popular_keep — такой же необязательный фильтр для строки «Часто докупают»: им убирают популярное,
        которое у клиента уже есть или решает ту же задачу, что купленное.
        popular_n — сколько карточек в строке «Часто докупают»; по умолчанию одна, а если личных советов
        меньше трёх — по числу свободных мест. Строку продлений фильтры не трогают.
        """
        h = self.fold.history[c]
        own = self._own(h)
        cap = self._cap(h, prices)
        group = self.task.clients.get(c, {}).get("client_group", "")
        no_home = self._no_home(group, h)
        common = self.popular.get(group, {})
        ordered, dropped = self._sellable_order(c, group)
        seen: set[str] = set()
        renew, new = [], []
        for s in sorted(ordered, key=lambda s: (not s.grounded, -s.p, s.item)):   # сначала советы с собственным основанием
            ab, line, nline = self._product(s)
            if line in seen or (nline and nline in seen):
                dropped.append({"item": s.item, "reason": "уже есть совет по этому продукту"})
                continue
            variant = bool(ab) and (ab.line in own or bool(nline and nline in own))
            why = self._ban(s, ab, variant, cap, no_home)
            if why:
                dropped.append({"item": s.item, "reason": why})
                continue
            if s.slot == "renew":
                left = self.months_left(c, s.replaces or s.item)
                if left is None or left > DUE_MONTHS:
                    dropped.append({"item": s.item, "reason": "продлевать рано: срок лицензии ещё далеко или неизвестен"})
                    continue
                target, limit = renew, RENEW_MAX
            else:
                if keep is not None:
                    personal, why = keep(h, s)
                    why = why or "отсеяно проверкой пары"
                else:                                       # преемник судится как товар, который он заменил
                    by_link = s.basis != "модель" and not s.replaces
                    share = 0.0 if variant or not ab else common.get(nline or ab.line, 0.0)
                    personal = by_link or variant or (s.grounded and not share)
                    why = "популярный продукт: его место в строке «Часто докупают»" if share \
                        else "нет основания в покупках клиента"
                if not personal:
                    dropped.append({"item": s.item, "reason": why})
                    continue
                target, limit = new, NEW_MAX
            if len(target) >= limit:
                continue
            seen.add(line)
            if nline:
                seen.add(nline)
            target.append(s)
        popular = self.popular_row(c, renew + new, prices, popular_keep, popular_n, ordered)
        return {"renew": renew, "new": new, "popular": popular, "dropped": dropped}

    def popular_row(self, c: str, shown: list[Scored] | tuple = (), prices: dict[str, float] | None = None,
                    popular_keep: Callable[[list[str], Scored], tuple[bool, str]] | None = None,
                    n: int | None = None, ordered: list[Scored] | None = None) -> list[Scored]:
        """Строка «Часто докупают» для клиента, которому уже показаны карточки shown.

        Кандидаты — продукты, популярные в группе клиента (self.popular): их нет у клиента и нет среди
        показанного. Порядок — по той же единой оценке; запреты те же, что у личных советов (можно
        купить, не дополнение к чужому продукту, не намного дороже прошлых покупок). У карточек
        slot = "popular", basis = "часто докупают", share — доля вернувшихся клиентов группы,
        впервые взявших этот продукт: её можно показать клиенту как есть.

        n — сколько карточек; по умолчанию одна, а если личных советов среди shown меньше трёх —
        по числу свободных мест. popular_keep — необязательный фильтр (история, кандидат) -> (оставить,
        причина): им убирают популярное, которое у клиента уже есть или решает ту же задачу.
        Вызывается и отдельно — когда личные советы собраны снаружи в несколько шагов.
        """
        h = self.fold.history[c]
        group = self.task.clients.get(c, {}).get("client_group", "")
        common = self.popular.get(group, {})
        room = max(POPULAR_MIN, NEW_MAX - sum(x.slot == "new" for x in shown)) if n is None else n
        if not common or room <= 0:
            return []
        own, cap, no_home = self._own(h), self._cap(h, prices), self._no_home(group, h)
        seen: set[str] = set()
        for x in shown:
            _, line, nline = self._product(x)
            seen.update(k for k in (line, nline) if k)
        if ordered is None:
            ordered, _ = self._sellable_order(c, group)
        row: list[Scored] = []
        for s in sorted(ordered, key=lambda s: (-s.p, s.item)):
            if len(row) >= room:
                break
            ab, line, nline = self._product(s)
            if not ab or s.slot != "new" or ab.line in own or (nline and nline in own):
                continue
            share = common.get(nline or ab.line, 0.0)
            if not share or line in seen or (nline and nline in seen) or self._ban(s, ab, False, cap, no_home):
                continue
            if popular_keep is not None and not popular_keep(h, s)[0]:
                continue
            seen.update(k for k in (line, nline) if k)
            row.append(replace(s, slot="popular", basis="часто докупают", share=share))
        return row

    def _own(self, h: list[str]) -> set[str]:
        at = self.at
        return {at[a].line for a in h} | {at[a].nline for a in h if at[a].nline}

    def _cap(self, h: list[str], prices: dict[str, float] | None) -> float:
        """Самая дорогая прошлая покупка клиента: от неё считается потолок цены совета."""
        cat = self.cat
        return max(((prices or {}).get(a) or cat.price(cat.offer_of(a)) or 0 for a in h), default=0)

    def _sellable_order(self, c: str, group: str) -> tuple[list[Scored], list[dict]]:
        """Кандидаты клиента, которых можно купить (недоступные заменены преемниками), и убранные без преемника."""
        ordered, dropped = [], []
        for s in self.order(c):
            if not self.sellable(s.item):
                heir = self._heir(s, group)
                if heir is None:
                    dropped.append({"item": s.item, "reason": "нет в продаже, преемника нет"})
                    continue
                s = heir
            ordered.append(s)
        return ordered, dropped

    def _product(self, s: Scored) -> tuple:
        """(признаки товара, ключ продукта, линейка по названию); преемник наследует продукт того, кого заменил."""
        ab = self.at.get(s.item) or self.at.get(s.replaces)
        return ab, ((ab.pkey or ab.line) if ab else "offer:" + s.item), (ab.nline if ab else "")

    def _no_home(self, group: str, h: list[str]) -> bool:
        """Организация, которая ни разу не покупала домашних лицензий: ей их и не советуем."""
        return group == "B2B" and not any(a in self.home_items for a in h)

    def _ban(self, s: Scored, ab, variant: bool, cap: float, no_home: bool) -> str:
        """Причина, по которой карточку нельзя показывать ни в одной строке; пусто — можно."""
        if ab and ab.kind in ADDON_KINDS and not variant:
            return "дополнение к продукту, которого у клиента нет"
        if cap and s.price and s.price > PRICE_CAP * cap:
            return "намного дороже прошлых покупок клиента"
        if no_home and ((s.replaces or s.item) in self.home_items or HOME.search(s.title)):
            return "домашняя лицензия, а клиент — организация"
        return ""

    def _heir(self, s: Scored, group: str) -> Scored | None:
        """Преемник товара, который сейчас нельзя купить: наследует его оценку."""
        cat = self.cat
        tw = cat.twin(s.item)
        if tw:
            return self._from_offer(tw, s, s.p, "тот же товар", "актуальный артикул того же товара")
        r = self.task.items.get(s.item) or {}
        for kind, factor in (("new_version", 0.8), ("analog_ru", 1.0)):
            for link in cat.links.get(r.get("family_id", ""), ()):
                if link["type"] != kind:
                    continue
                offer = cat.pick(link["dst"], r, group)
                if offer:
                    p = s.p * factor * (min(1.0, link["confidence"] / 0.9) if kind == "analog_ru" else 1.0)
                    return self._from_offer(offer, s, p, BASIS[kind], link["evidence"])
        return None

    def _from_offer(self, offer: dict, was: Scored, p: float, basis: str, evidence: str) -> Scored:
        item = self.cat.offer_item.get(offer["offer_id"]) or "K:" + offer["offer_id"]
        return Scored(item=item, p=p, basis=basis, grounded=was.grounded, in_sales=not item.startswith("K:"),
                      title=offer["name"], url=offer["url"], price=self.cat.price(offer), src=was.src,
                      evidence=evidence, replaces=was.item, slot=was.slot, features=was.features)


def cabinet_summary(uni: Unified, keep=None, prices: dict[str, float] | None = None,
                    popular_keep=None, popular_n: int | None = None) -> dict:
    """Вид «как в кабинете» на клиентах официальной проверки: сколько карточек показано и сколько сбылось.

    keep, popular_keep, popular_n — те же необязательные настройки, что в Unified.cabinet: так измеряется их цена.
    """
    from recsys.eval.value import by_segment

    task, fold = uni.task, uni.fold
    stats, basis = Counter(), Counter()
    shown3, shown_any = {}, {}
    per: dict[str, Counter] = defaultdict(Counter)
    for c in fold.group_a:
        cab = uni.cabinet(c, prices, keep, popular_keep, popular_n)
        cards = cab["renew"] + cab["new"] + cab["popular"]
        tgt = fold.target[c]
        g = task.clients[c]["client_group"]
        cold = len(fold.history[c]) == 1
        for key in [f"A {g}", "A все"] + ([f"B {g}", "B все"] if cold else []):
            r = per[key]
            r["clients"] += 1
            for row in ("renew", "new", "popular"):
                r[f"{row}_cards"] += len(cab[row])
                r[f"{row}_hits"] += sum(x.item in tgt for x in cab[row])
                r[f"clients_with_{row}"] += bool(cab[row])
            r["clients_without_new"] += not cab["new"]
            r["clients_without_cards"] += not cards
        stats["клиентов"] += 1
        stats["с продлением"] += bool(cab["renew"])
        stats["с личным советом"] += bool(cab["new"])
        stats["со строкой «часто докупают»"] += bool(cab["popular"])
        stats["карточек"] += len(cards)
        stats["товаров вне продаж"] += sum(not x.in_sales for x in cards)
        stats["подстановок преемника"] += sum(bool(x.replaces) for x in cards)
        stats["убрано: нет в продаже"] += sum(d["reason"].startswith("нет в продаже") for d in cab["dropped"])
        stats["убрано: продлевать рано"] += sum(d["reason"].startswith("продлевать рано") for d in cab["dropped"])
        for x in cards:
            basis[x.basis] += 1
        first3 = sorted(cards, key=lambda x: -x.p)[:3]
        shown3[c] = len({x.item for x in first3} & tgt) / 3
        shown_any[c] = float(bool({x.item for x in cards} & tgt))
    s3, s5 = by_segment(task, fold, shown3), by_segment(task, fold, shown_any)
    return {
        "precision@3_of_three_best_cards": {k: v["value"] for k, v in s3.items()},
        "clients_with_a_hit_among_shown": {k: v["value"] for k, v in s5.items()},
        "cards": {k: dict(v) for k, v in per.items()},
        "counts": dict(stats), "basis": dict(basis),
    }


def as_dict(s: Scored) -> dict:
    d = asdict(s)
    d.pop("features", None)
    return d


def main() -> None:
    from recsys.eval.value import Prep, by_segment
    from recsys.rank.ltr import copies, fit
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.unified")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    attrs = build_attrs(task)
    cat = Catalog(task)
    train = [Prep(FoldStats(task, make_fold(task, *s), attrs), "full") for s in copies(date.fromisoformat(OFFICIAL[1]))]
    off = FoldStats(task, make_fold(task, *OFFICIAL), attrs)
    model = fit(train)
    uni = Unified(off, model, cat)
    fold = off.fold
    segs = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"]

    # 1) вид «как в протоколе» обязан дать ту же цифру, что режим «полная модель»
    proto = {c: uni.protocol(c) for c in fold.group_a}
    seg = by_segment(task, fold, {c: len(set(proto[c]) & fold.target[c]) / 3 for c in fold.group_a})
    print("вид «как в протоколе», Precision@3: " + " / ".join(f"{s} {seg[s]['value']:.1%}" for s in segs))
    report = {"protocol_view": {s: seg[s]["value"] for s in segs}, "priors": PRIOR}
    ref = ROOT / "reports" / "value.json"
    if ref.exists():
        full = json.loads(ref.read_text(encoding="utf-8"))["regimes"]["full"]["precision@3"]
        same = all(abs(full[s] - seg[s]["value"]) < 1e-9 for s in segs)
        report["protocol_equals_value_report"] = same
        print("совпадает с reports/value.json (режим «полная модель»):", "да" if same else "НЕТ: " + str(full))

    # 2) вид «как в кабинете» на тех же клиентах: что показываем и чего это стоит по мерке протокола
    cab = cabinet_summary(uni)
    print("вид «как в кабинете»: три лучшие карточки по мерке протокола: "
          + " / ".join(f"{k} {cab['precision@3_of_three_best_cards'][k]:.1%}" for k in ("A B2B", "B B2B", "A B2C")))
    print("доля клиентов, у которых сбылась хотя бы одна показанная карточка: "
          + " / ".join(f"{k} {cab['clients_with_a_hit_among_shown'][k]:.1%}" for k in ("A B2B", "B B2B", "A B2C")))
    rows = (("renew", "«Пора продлить»"), ("new", "«К вашим продуктам»"), ("popular", "«Часто докупают»"))
    for k in ("A B2B", "A B2C"):
        r = cab["cards"][k]
        print(f"  {k}, клиентов {r['clients']}, без единой карточки {r['clients_without_cards']}:")
        for row, title in rows:
            n, hit = r[f"{row}_cards"], r[f"{row}_hits"]
            print(f"     {title:20} клиентов {r[f'clients_with_{row}']:>3}, карточек {n:>3}, "
                  f"сбылось {hit:>3}" + (f" ({hit / n:.1%})" if n else ""))
    print("что показано:", cab["counts"])
    print("основания карточек:", cab["basis"])
    report["cabinet_view"] = cab
    report["popular_products"] = {
        "rule": f"продукт впервые взяли не меньше {POPULAR_SHARE:.0%} вернувшихся клиентов группы "
                f"и не меньше {POPULAR_CLIENTS} клиентов; продления и варианты купленного не в счёт",
        "count": {g: len(v) for g, v in sorted(uni.popular.items())},
        "top_share": {g: round(max(v.values()), 4) for g, v in sorted(uni.popular.items()) if v},
    }
    print("продуктов «часто докупают»: " + ", ".join(
        f"{g or 'без группы'} — {len(v)} (самый частый у {max(v.values()):.0%} вернувшихся)"
        for g, v in sorted(uni.popular.items()) if v))

    # 3) сколько карточек класть в строку «Часто докупают»: ни одной, одну, по свободным местам (так по умолчанию)
    report["popular_row_size"] = {}
    for title, n in (("без строки", 0), ("всегда одна карточка", 1), ("по свободным местам, не меньше одной", None)):
        v = cab if n is None else cabinet_summary(uni, popular_n=n)
        line = {}
        for k in ("A B2B", "A B2C"):
            r = v["cards"][k]
            line[k] = {"popular_cards": r["popular_cards"], "popular_hits": r["popular_hits"],
                       "clients_without_cards": r["clients_without_cards"],
                       "clients_with_a_hit_among_shown": v["clients_with_a_hit_among_shown"][k]}
        report["popular_row_size"][title] = line
        print(f"  строка «Часто докупают» — {title}: " + "; ".join(
            f"{k}: карточек {x['popular_cards']}, сбылось {x['popular_hits']}, клиентов без карточек "
            f"{x['clients_without_cards']}, с попаданием {x['clients_with_a_hit_among_shown']:.1%}"
            for k, x in line.items()))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {REPORT}")


if __name__ == "__main__":
    main()
