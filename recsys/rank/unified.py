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
  cabinet(c)  — как в блоке кабинета: только то, что можно купить; продления отдельной строкой
                (не больше двух), три места — новым советам; один совет на продукт; дополнения
                только к купленному; не дороже самой дорогой прошлой покупки больше чем в пять раз;
                сначала советы с собственным основанием, потом «часто берут».

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
from dataclasses import asdict, dataclass, field
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
    slot: str = "new"               # «renew» — продление купленного, «new» — новый совет
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
        band = int(like["band_lo"]) if like and like.get("band_lo") else None
        term = int(float(like["term_months"])) if like and like.get("term_months") not in ("", None) else None
        for oid in self.by_family.get(family_id, ()):
            o = self.offers[oid]
            if not o["available"]:
                continue
            kind, lo, t, user = self.parsed(oid)
            low = o["name"].lower()
            if "academ" in low or "образоват" in low or "educat" in low or "government" in low:
                continue
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
        same = [self.offers[x] for x in self.by_family.get(fam, ())
                if self.offers[x]["available"] and self.parsed(x)[:3] == want]
        return min(same, key=lambda o: self.price(o) or 1e12) if same else None


class Unified:
    def __init__(self, st: FoldStats, model, catalog: Catalog, regime: str = "full"):
        self.st, self.model, self.cat, self.regime = st, model, catalog, regime
        self.task, self.fold, self.at = st.task, st.fold, st.attrs
        self.last_sold: dict[str, date] = {}
        for r in self.fold.train:
            self.last_sold[r.item] = r.week

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
                keep: Callable[[list[str], Scored], tuple[bool, str]] | None = None) -> dict:
        """Что видит клиент в кабинете: {"renew": до двух продлений, "new": до трёх новых советов, "dropped": [...]}.

        keep — необязательный фильтр новых советов: функция (история клиента, кандидат) -> (оставить, причина).
        Через неё накладываются заранее вынесенные вердикты «нужно / уже есть / то же самое / не связано».
        Строку продлений фильтр не трогает. Популярное в свободные места не подмешивается: если после
        фильтра новых советов меньше трёх, их и будет меньше.
        """
        at, cat = self.at, self.cat
        h = self.fold.history[c]
        own = {at[a].line for a in h} | {at[a].nline for a in h if at[a].nline}
        cap = max(((prices or {}).get(a) or cat.price(cat.offer_of(a)) or 0 for a in h), default=0)
        group = self.task.clients.get(c, {}).get("client_group", "")
        ordered, dropped = [], []
        for s in self.order(c):
            if not self.sellable(s.item):
                heir = self._heir(s, group)
                if heir is None:
                    dropped.append({"item": s.item, "reason": "нет в продаже, преемника нет"})
                    continue
                s = heir
            ordered.append(s)
        ordered.sort(key=lambda s: (not s.grounded, -s.p, s.item))     # сначала советы с собственным основанием
        seen: set[str] = set()
        renew, new = [], []
        for s in ordered:
            ab = at.get(s.item)
            line = (ab.pkey or ab.line) if ab else "offer:" + s.item
            nline = ab.nline if ab else ""
            if line in seen or (nline and nline in seen):
                dropped.append({"item": s.item, "reason": "уже есть совет по этому продукту"})
                continue
            if ab and ab.kind in ADDON_KINDS and ab.line not in own and not (nline and nline in own):
                dropped.append({"item": s.item, "reason": "дополнение к продукту, которого у клиента нет"})
                continue
            if cap and s.price and s.price > PRICE_CAP * cap:
                dropped.append({"item": s.item, "reason": "намного дороже прошлых покупок клиента"})
                continue
            if s.slot == "renew":
                left = self.months_left(c, s.replaces or s.item)
                if left is None or left > DUE_MONTHS:
                    dropped.append({"item": s.item, "reason": "продлевать рано: срок лицензии ещё далеко или неизвестен"})
                    continue
            elif keep is not None:
                ok, why = keep(h, s)
                if not ok:
                    dropped.append({"item": s.item, "reason": why or "отсеяно проверкой пары"})
                    continue
            target = renew if s.slot == "renew" else new
            if len(target) >= (RENEW_MAX if s.slot == "renew" else NEW_MAX):
                continue
            seen.add(line)
            if nline:
                seen.add(nline)
            target.append(s)
            if len(renew) >= RENEW_MAX and len(new) >= NEW_MAX:
                break
        return {"renew": renew, "new": new, "dropped": dropped}

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


def cabinet_summary(uni: Unified, keep=None, prices: dict[str, float] | None = None) -> dict:
    """Вид «как в кабинете» на клиентах официальной проверки: сколько карточек показано и сколько сбылось.

    keep — тот же необязательный фильтр новых советов, что в Unified.cabinet: так измеряется его цена.
    """
    from recsys.eval.value import by_segment

    task, fold = uni.task, uni.fold
    stats, basis = Counter(), Counter()
    shown3, shown_any = {}, {}
    per: dict[str, Counter] = defaultdict(Counter)
    for c in fold.group_a:
        cab = uni.cabinet(c, prices, keep)
        cards = cab["renew"] + cab["new"]
        tgt = fold.target[c]
        g = task.clients[c]["client_group"]
        cold = len(fold.history[c]) == 1
        for key in [f"A {g}", "A все"] + ([f"B {g}", "B все"] if cold else []):
            r = per[key]
            r["clients"] += 1
            r["renew_cards"] += len(cab["renew"])
            r["renew_hits"] += sum(x.item in tgt for x in cab["renew"])
            r["new_cards"] += len(cab["new"])
            r["new_hits"] += sum(x.item in tgt for x in cab["new"])
            r["clients_without_new"] += not cab["new"]
            r["clients_without_cards"] += not cards
        stats["клиентов"] += 1
        stats["с продлением"] += bool(cab["renew"])
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
    for k in ("A B2B", "A B2C"):
        r = cab["cards"][k]
        print(f"  {k}: «Пора продлить» — карточек {r['renew_cards']}, сбылось {r['renew_hits']}; новые советы — "
              f"карточек {r['new_cards']}, сбылось {r['new_hits']}; клиентов без новых советов {r['clients_without_new']}")
    print("что показано:", cab["counts"])
    print("основания карточек:", cab["basis"])
    report["cabinet_view"] = cab
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {REPORT}")


if __name__ == "__main__":
    main()
