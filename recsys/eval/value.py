"""Насколько новый блок полезнее нынешнего: пять замеров на данных задачи.

  1. Показ      — какой доле клиентов есть что обоснованно показать (без подстановки «самого популярного»).
  2. Попадание  — Precision@3 по протоколу организаторов против их базовых способов.
  3. Вклад знаний о товаре — одна и та же обучаемая модель в разных режимах:
       «полная»                  — вся история и знания каталога (семейство, класс, параметры лицензии);
       «без каталога»            — только поведение по самим товарам;
       «товар-новичок»           — история купленных клиентом товаров спрятана (как у только что
                                   заведённого или «соло»-товара), знания каталога остаются;
       «товар-новичок без каталога» — то же, но и знаний каталога нет: остаётся общая популярность;
       «семейство-новичок»       — спрятана история всего семейства купленного товара.
  4. Охват каталога — для какой доли товаров совет можно вывести из истории и для какой — из каталога.
  5. Деньги     — сумма цен витрины по угаданным покупкам на 100 клиентов.

Все режимы обучаются на копиях протокола внутри периода обучения; официальный проверочный период
используется только для итоговых цифр. Датасет организаторов публиковать нельзя: в отчёт попадают
только сводные числа, без клиентов и строк продаж.

Запуск:  python -m recsys.eval.value
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date

import numpy as np

from recsys.config import DATA, INTERIM, ROOT
from recsys.rank.data import OFFICIAL, Fold, Task, load_task, make_fold
from recsys.rank.features import EMPTY, FEATURES, FoldStats, build_attrs
from recsys.rank.ltr import copies, fit
from recsys.stable import rerun_with_fixed_order

REGIMES = {
    "full": "полная модель",
    "no_catalog": "без знаний каталога",
    "new_item": "товар-новичок, каталог есть",
    "new_item_no_catalog": "товар-новичок, каталога нет",
    "new_family": "семейство-новичок, каталог есть",
}
# признаки, которые опираются на знание каталога: семейство, класс, параметры лицензии, название
CATALOG = ["fam_sum", "fam_max", "fam_t", "cls_max", "same_line", "n_owned_in_line", "since_line", "due_same",
           "kind_b", "kind_a", "kind_tr", "band_rel", "band_ratio", "term_same", "term_b", "period_delta",
           "same_vkey", "version_delta", "name_sim", "share_in_line", "edu_b", "cert_b"]
COL = {name: i for i, name in enumerate(FEATURES)}
CLASS_LINKS = INTERIM / "class_links.csv"
LINK_REGIMES = {
    "full_links": "полная модель + связи классов от ИИ",
    "new_item_links": "товар-новичок + связи классов от ИИ",
}


class ClassLinks:
    """Связи между классами каталога (разметка моделью с проверкой) в терминах товаров из продаж.

    Для клиента дают кандидатов из других линеек: самые ходовые товары тех классов, которые,
    по разметке, обычно берут вместе с классами его покупок. Сами связи получены без данных продаж.
    """

    def __init__(self, task: Task):
        names = json.loads((INTERIM / "class_names.json").read_text(encoding="utf-8"))
        slug_of = {}
        for slug, name in names.items():
            slug_of.setdefault(name.strip().lower(), slug)
        self.slug = {i: slug_of.get((r.get("subcategory") or "").strip().lower(), "") for i, r in task.items.items()}
        self.links: dict[str, list[tuple[str, float, str, str]]] = defaultdict(list)
        with CLASS_LINKS.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.links[r["src_class"]].append((r["dst_class"], float(r["confidence"]), r["type"], r.get("audience") or "any"))
        self._by_class: dict[int, dict[str, list[str]]] = {}

    def items_of(self, st: FoldStats) -> dict[str, list[str]]:
        key = id(st)
        if key not in self._by_class:
            by: dict[str, list[str]] = defaultdict(list)
            for item, _ in st.owners.most_common():           # по убыванию числа покупателей до даты разреза
                if self.slug.get(item):
                    by[self.slug[item]].append(item)
            self._by_class[key] = by
        return self._by_class[key]

    def candidates(self, st: FoldStats, c: str, per_class: int = 5) -> dict[str, tuple[float, int]]:
        h = st.fold.history[c]
        owned = set(h)
        group = "b2b" if st.task.clients.get(c, {}).get("client_group") == "B2B" else "b2c"
        by = self.items_of(st)
        out: dict[str, tuple[float, int]] = {}
        for src in {self.slug.get(a, "") for a in h} - {""}:
            for dst, conf, typ, aud in self.links.get(src, ()):
                if aud not in ("any", "", group):
                    continue
                w = conf * (1.3 if typ == "requires" else 1.0)
                for b in by.get(dst, [])[:per_class]:
                    if b not in owned:
                        cur = out.get(b, (0.0, 0))
                        out[b] = (max(cur[0], w), cur[1] + 1)
        return out


LINKS: ClassLinks | None = None
SEGS = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"]


@contextmanager
def in_regime(st: FoldStats, h: list[str], regime: str):
    """На время блока подменяет статистику разреза так, как её видел бы клиент с историей h в режиме regime."""
    keep = (st.pair, st.co, st.fam, st.cls, st.line_items, st.nline_items)
    if regime == "full":
        yield
        return
    at = st.attrs
    pair0, _, fam0, cls0, _, _ = keep
    try:
        if regime in ("new_item", "new_item_no_catalog", "new_family"):
            fam, cls = {}, {}
            if regime != "new_family":
                # статистика семейства без вклада самих купленных товаров
                for la in {at[a].line for a in h}:
                    sub = Counter()
                    for a in h:
                        if at[a].line == la:
                            sub.update(pair0.get(a, EMPTY))
                    fam[la] = +Counter({b: n - sub.get(b, 0) for b, n in fam0.get(la, EMPTY).items()})
            for cl in {at[a].cls for a in h if at[a].cls}:
                sub = Counter()
                if regime == "new_family":
                    for la in {at[a].line for a in h if at[a].cls == cl}:
                        sub.update(fam0.get(la, EMPTY))
                else:
                    for a in h:
                        if at[a].cls == cl:
                            sub.update(pair0.get(a, EMPTY))
                cls[cl] = +Counter({b: n - sub.get(b, 0) for b, n in cls0.get(cl, EMPTY).items()})
            st.pair, st.co, st.fam, st.cls = {}, {}, fam, cls
        if regime in ("no_catalog", "new_item_no_catalog"):
            st.fam, st.cls, st.line_items, st.nline_items = {}, {}, {}, {}
        yield
    finally:
        st.pair, st.co, st.fam, st.cls, st.line_items, st.nline_items = keep


def rows_in_regime(st: FoldStats, c: str, regime: str) -> dict[str, list[float]]:
    """Кандидаты и признаки клиента в заданном режиме."""
    with_links = regime.endswith("_links")
    base = regime[: -len("_links")] if with_links else regime
    extra = LINKS.candidates(st, c) if with_links and LINKS else {}
    with in_regime(st, st.fold.history[c], base):
        keep = st.pop_top
        if extra:                                              # кандидаты из связанных классов
            st.pop_top = keep + [b for b in extra if b not in keep]
        try:
            rows = st.rows(c)
        finally:
            st.pop_top = keep
    if base in ("no_catalog", "new_item_no_catalog"):
        idx = [COL[n] for n in CATALOG]
        for vec in rows.values():
            for i in idx:
                vec[i] = 0.0
    if with_links:                                             # два признака: сила связи классов и число связей
        for b, vec in rows.items():
            conf, n = extra.get(b, (0.0, 0))
            vec.extend([conf, float(n)])
    return rows


class Prep:
    """Строки «клиент — кандидат» одного разреза в одном режиме."""

    def __init__(self, st: FoldStats, regime: str):
        self.st, self.fold, self.task, self.regime = st, st.fold, st.task, regime
        self.clients, self.items, X, y = [], [], [], []
        self.fallback = {}
        for c in self.fold.group_a:
            self.fallback[c] = st.fallback(c)
            tgt = self.fold.target.get(c, ())
            for b, vec in sorted(rows_in_regime(st, c, regime).items()):      # порядок строк не зависит от запуска
                self.clients.append(c)
                self.items.append(b)
                X.append(vec)
                y.append(1 if b in tgt else 0)
        width = len(FEATURES) + (2 if regime.endswith("_links") else 0)
        self.X = np.array(X, dtype=np.float32) if X else np.zeros((0, width), dtype=np.float32)
        self.y = np.array(y, dtype=np.int8)

    def recommend(self, scores: np.ndarray, top: int = 3) -> dict[str, list[str]]:
        """Первые top товаров каждому клиенту по убыванию оценки; не хватает — добор частыми докупками."""
        by: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for c, b, s in zip(self.clients, self.items, scores):
            by[c].append((float(s), b))
        recs = {}
        for c in self.fold.group_a:
            ranked = [b for _, b in sorted(by.get(c, []), key=lambda x: (-x[0], x[1]))][:top]
            for b in self.fallback[c]:
                if len(ranked) >= top:
                    break
                if b not in ranked:
                    ranked.append(b)
            recs[c] = ranked
        return recs

    def specific(self) -> dict[str, dict[str, bool]]:
        """Есть ли у клиента кандидат с собственным основанием — и каким именно."""
        groups = {"история товара": ["pair_sum", "co_sum"], "линейка": ["same_line", "same_vkey", "fam_sum"],
                  "класс": ["cls_max"]}
        out: dict[str, dict[str, bool]] = {c: {g: False for g in groups} for c in self.fold.group_a}
        for g, names in groups.items():
            mask = np.zeros(len(self.y), dtype=bool)
            for n in names:
                mask |= self.X[:, COL[n]] > 0
            for c, m in zip(self.clients, mask):
                if m:
                    out[c][g] = True
        return out


def by_segment(task: Task, fold: Fold, per_client: dict[str, float]) -> dict[str, dict]:
    out = {}
    for gname, group in (("A", fold.group_a), ("B", fold.group_b)):
        for seg in ("все", "B2B", "B2C"):
            ids = [c for c in group if seg == "все" or task.clients[c]["client_group"] == seg]
            if ids:
                vals = [per_client[c] for c in ids]
                out[f"{gname} {seg}"] = {"clients": len(ids), "value": float(np.mean(vals)), "values": vals}
    return out


def ci(values: list[float], n: int = 2000, seed: int = 3) -> tuple[float, float]:
    v = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), size=(n, len(v)))].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def paired(a: list[float], b: list[float]) -> tuple[float, float, float]:
    d = np.asarray(a) - np.asarray(b)
    lo, hi = ci(list(d))
    return float(d.mean()), lo, hi


def load_feed() -> dict[str, dict]:
    offers = {}
    with gzip.open(INTERIM / "feed_offers.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            offers[o["offer_id"]] = o
    return offers


def catalog_coverage(st: FoldStats, offers: dict[str, dict]) -> dict:
    """Для какой доли каталога совет можно вывести из истории продаж и для какой — из самого каталога."""
    n_all = len(offers)
    avail = [o for o in offers.values() if str(o["available"]) == "1"]
    hist_any = {a for a, cnt in st.pair.items() if cnt}
    hist_3 = {a for a, cnt in st.pair.items() if cnt and max(cnt.values()) >= 3}
    fam_avail = Counter(o["family_id"] for o in avail if o["family_id"])
    cls_fams: dict[str, set[str]] = defaultdict(set)
    for o in avail:
        if o["class_id"] and o["family_id"]:
            cls_fams[o["class_id"]].add(o["family_id"])
    line = sum(1 for o in avail if o["family_id"] and fam_avail[o["family_id"]] >= 2)
    klass = sum(1 for o in avail if o["class_id"] and len(cls_fams[o["class_id"]]) >= 2)
    either = sum(1 for o in avail if (o["family_id"] and fam_avail[o["family_id"]] >= 2)
                 or (o["class_id"] and len(cls_fams[o["class_id"]]) >= 2))
    return {
        "offers": n_all, "available": len(avail), "items_sold": len(st.owners),
        "history_any": len(hist_any), "history_3": len(hist_3),
        "line_available": line, "class_available": klass, "line_or_class": either,
    }


def main() -> None:
    rerun_with_fixed_order("recsys.eval.value")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    attrs = build_attrs(task)
    offers = load_feed()
    price = {}
    for item, r in task.items.items():
        o = offers.get(r.get("offer_id") or "")
        if o:
            try:
                price[item] = float(o["price"])
            except ValueError:
                pass

    specs = copies(date.fromisoformat(OFFICIAL[1]))
    stats = {s[1]: FoldStats(task, make_fold(task, *s), attrs) for s in specs}
    off = FoldStats(task, make_fold(task, *OFFICIAL), attrs)
    fold = off.fold
    print(f"копий протокола для обучения: {len(specs)}; официальный разрез: A {len(fold.group_a)}, B {len(fold.group_b)}")

    report: dict = {"protocol": {"train_until": OFFICIAL[1], "test_from": OFFICIAL[2]}, "regimes": {}}
    recs_by, values_by = {}, {}
    regimes = dict(REGIMES)
    if CLASS_LINKS.exists():
        global LINKS
        LINKS = ClassLinks(task)
        regimes.update(LINK_REGIMES)
        print(f"связей между классами (разметка моделью): {sum(len(v) for v in LINKS.links.values())}")
    for regime, title in regimes.items():
        train = [Prep(st, regime) for st in stats.values()]
        model = fit(train)
        test = Prep(off, regime)
        recs = test.recommend(model.predict_proba(test.X)[:, 1]) if len(test.y) else {c: test.fallback[c][:3] for c in fold.group_a}
        p3 = {c: len(set(recs[c]) & fold.target[c]) / 3 for c in fold.group_a}
        seg = by_segment(task, fold, p3)
        recs_by[regime], values_by[regime] = recs, seg
        spec = test.specific()
        fill = by_segment(task, fold, {c: float(any(spec[c].values())) for c in fold.group_a})
        report["regimes"][regime] = {
            "title": title,
            "precision@3": {k: v["value"] for k, v in seg.items()},
            "ci": {k: ci(v["values"]) for k, v in seg.items() if k in ("A B2B", "B B2B", "A все", "B все")},
            "specific_fill": {k: v["value"] for k, v in fill.items()},
        }
        print(f"готов режим: {title}", flush=True)

    # ---- базовый способ организаторов: популярность
    pop = [i for i, _ in off.item_rows.most_common(60)]
    pop_recs = {c: [i for i in pop if i not in set(fold.history[c])][:3] for c in fold.group_a}
    pop_seg = by_segment(task, fold, {c: len(set(pop_recs[c]) & fold.target[c]) / 3 for c in fold.group_a})

    print("\n== 2–3. Precision@3 по протоколу организаторов ==")
    print(f"{'':36}" + "".join(f"{s:>9}" for s in SEGS))
    print(f"{'популярность (организаторы)':36}" + "".join(f"{pop_seg[s]['value']:>9.1%}" for s in SEGS))
    for regime, title in regimes.items():
        r = report["regimes"][regime]["precision@3"]
        print(f"{title:36}" + "".join(f"{r[s]:>9.1%}" for s in SEGS))
    print("\n95% интервалы для организаций (группа A / группа B):")
    for regime, title in regimes.items():
        c = report["regimes"][regime]["ci"]
        print(f"  {title:34} A {c['A B2B'][0]:.1%}–{c['A B2B'][1]:.1%}   B {c['B B2B'][0]:.1%}–{c['B B2B'][1]:.1%}")
    report["baseline_popularity"] = {s: pop_seg[s]["value"] for s in SEGS}
    # из чего состоят попадания главного режима: вариант уже купленного, другая версия, новый продукт
    from recsys.rank.breakdown import breakdown
    report["hits_breakdown"] = breakdown(task, fold, attrs, recs_by["full"])
    part = report["hits_breakdown"].get("A B2B", {})
    if part:
        print("\nпопадания организаций (группа A) по видам: "
              + ", ".join(f"{k} {v}" for k, v in part["hits"].items())
              + "; целей: " + ", ".join(f"{k} {v}" for k, v in part["targets"].items()))

    print("\nразница на одних и тех же клиентах (организации), в процентных пунктах:")
    diffs = {}
    pairs = [("знания каталога при полной истории", "full", "no_catalog"),
             ("знания каталога для товара-новичка", "new_item", "new_item_no_catalog"),
             ("цена потери истории товара", "new_item", "full"),
             ("цена потери истории семейства", "new_family", "full")]
    if "full_links" in regimes:
        pairs += [("связи классов от ИИ при полной истории", "full_links", "full"),
                  ("связи классов от ИИ для товара-новичка", "new_item_links", "new_item")]
    for name, a, b in pairs:
        diffs[name] = {}
        for s in ("A B2B", "B B2B"):
            d, lo, hi = paired(values_by[a][s]["values"], values_by[b][s]["values"])
            diffs[name][s] = (d, lo, hi)
        print(f"  {name:38} A {diffs[name]['A B2B'][0] * 100:+5.1f} [{diffs[name]['A B2B'][1] * 100:+.1f}; {diffs[name]['A B2B'][2] * 100:+.1f}]"
              f"   B {diffs[name]['B B2B'][0] * 100:+5.1f} [{diffs[name]['B B2B'][1] * 100:+.1f}; {diffs[name]['B B2B'][2] * 100:+.1f}]")
    report["differences"] = diffs

    # ---- 1. показ: кому есть что обоснованно показать
    print("\n== 1. Показ: доля клиентов, которым есть совет с собственным основанием ==")
    spec = Prep(off, "full").specific()
    for label, cond in [("по истории самого товара", lambda s: s["история товара"]),
                        ("по линейке (каталог)", lambda s: s["линейка"]),
                        ("по классу (каталог)", lambda s: s["класс"]),
                        ("хотя бы одно основание", lambda s: any(s.values())),
                        ("только благодаря каталогу", lambda s: not s["история товара"] and (s["линейка"] or s["класс"]))]:
        seg = by_segment(task, fold, {c: float(cond(spec[c])) for c in fold.group_a})
        print(f"  {label:30}" + "".join(f"{seg[s]['value']:>9.0%}" for s in SEGS))
        report.setdefault("fill", {})[label] = {s: seg[s]["value"] for s in SEGS}
    # все клиенты с покупками (не только попавшие в проверку): как у кабинета в целом
    all_clients = list(fold.history)
    at = attrs
    cnt = Counter()
    for c in all_clients:
        h = fold.history[c]
        owned = set(h)
        g = task.clients.get(c, {}).get("client_group", "")
        lk = task.clients.get(c, {}).get("lk_access", "")
        hist = any(n >= 3 and b not in owned for a in h for b, n in off.pair.get(a, EMPTY).items())
        line = any(b not in owned for a in h for b in off.line_items.get(at[a].line, ()))
        klass = any(b not in owned for a in h if at[a].cls for b in off.cls.get(at[a].cls, EMPTY))
        for key in (("все", ""), (g, ""), ("все", lk), (g, lk)):
            cnt[(key, "n")] += 1
            cnt[(key, "история ≥3")] += hist
            cnt[(key, "линейка")] += line
            cnt[(key, "история или линейка")] += hist or line
            cnt[(key, "любое")] += hist or line or klass
    print("\n  все клиенты с покупками в периоде обучения:")
    report["fill_all"] = {}
    for key in sorted({k for k, _ in cnt}, key=str):
        n = cnt[(key, "n")]
        if n < 200:
            continue
        row = {m: cnt[(key, m)] / n for m in ("история ≥3", "линейка", "история или линейка", "любое")}
        name = f"{key[0] or '—'}{', доступ в ЛК: ' + key[1] if key[1] else ''}"
        print(f"    {name:34} клиентов {n:>6} | история (пара у ≥3 клиентов) {row['история ≥3']:.0%} | линейка {row['линейка']:.0%} | "
              f"история или линейка {row['история или линейка']:.0%} | с классом {row['любое']:.0%}")
        report["fill_all"][name] = {"clients": n, **row}

    # ---- 4. охват каталога
    cov = catalog_coverage(off, offers)
    report["catalog"] = cov
    print("\n== 4. Охват каталога ==")
    print(f"  товаров в фиде {cov['offers']}, из них в наличии {cov['available']}; товаров в продажах (обучение) {cov['items_sold']}")
    print(f"  из истории: товаров, после которых хоть кто-то купил другой товар — {cov['history_any']}; "
          f"где такая пара есть у ≥3 клиентов — {cov['history_3']} ({cov['history_3'] / cov['available']:.1%} от товаров в наличии)")
    print(f"  из каталога: у {cov['line_available']} товаров в наличии ({cov['line_available'] / cov['available']:.0%}) есть другой товар той же линейки; "
          f"у {cov['class_available']} ({cov['class_available'] / cov['available']:.0%}) — другое семейство того же класса; "
          f"хотя бы одно — {cov['line_or_class'] / cov['available']:.0%}")

    # ---- 5. деньги
    print("\n== 5. Деньги: сумма цен витрины по угаданным покупкам, на 100 клиентов-организаций (группа A) ==")
    b2b = [c for c in fold.group_a if task.clients[c]["client_group"] == "B2B"]
    report["money"] = {}
    for name, recs in [("популярность (организаторы)", pop_recs), ("полная модель", recs_by["full"]),
                       ("товар-новичок, каталог есть", recs_by["new_item"])]:
        hits = [b for c in b2b for b in set(recs[c]) & fold.target[c]]
        priced = [price[b] for b in hits if price.get(b, 0) > 0]
        total = sum(priced) / len(b2b) * 100
        print(f"  {name:32} угадано {len(hits):>4} покупок, с известной ценой {len(priced):>4}, "
              f"сумма на 100 клиентов {total:>12,.0f} ₽".replace(",", " "))
        report["money"][name] = {"hits": len(hits), "priced": len(priced), "rub_per_100_clients": total}
    all_t = [b for c in b2b for b in fold.target[c]]
    all_priced = [price[b] for b in all_t if price.get(b, 0) > 0]
    print(f"  всего новых покупок у этих клиентов {len(all_t)}, с известной ценой {len(all_priced)}, "
          f"сумма на 100 клиентов {sum(all_priced) / len(b2b) * 100:,.0f} ₽ (цены нынешние, количество лицензий неизвестно)".replace(",", " "))
    report["money"]["all_targets"] = {"targets": len(all_t), "priced": len(all_priced),
                                      "rub_per_100_clients": sum(all_priced) / len(b2b) * 100}

    out = ROOT / "reports" / "value.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\nотчёт (только сводные числа): {out}")


if __name__ == "__main__":
    main()
