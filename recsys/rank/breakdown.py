"""Из чего состоят попадания: другой вариант уже купленного продукта или действительно новый продукт.

Организаторы считают разными товарами разные артикулы одного продукта (10–14 и 15–19 узлов,
Base и Renewal) и просят показать на питче, какая доля попаданий приходится на такие варианты.
Каждая цель клиента отнесена к одному из трёх видов:
  вариант       — другой артикул продукта, который у клиента уже есть (то же семейство каталога;
                  если товара в каталоге нет — та же линейка по названию);
  версия        — тот же продукт в другой версии, редакции или комплекте: семейство другое, но та же
                  линейка по названию либо название близко к названию купленного товара
                  (медиапакет и расширение лицензии одного антивируса, Personal и Family одной
                  подписки, другая комплектация того же пакета);
  новый продукт — ничего похожего у клиента не было.
Precision@3 раскладывается на три слагаемых по этим видам: их сумма равна общей цифре.

Близость названий — косинус по словам названия с весами редкости слова среди товаров из продаж.
Порог NAME_SIM подобран по каталогу, без проверочного периода: пары товаров одного семейства —
«тот же продукт», пары одного производителя из разных классов — «разные продукты»; берётся порог
с наибольшей разницей долей (python -m recsys.rank.breakdown печатает подбор). Если у обоих товаров
производитель известен и он разный, близость названий не учитывается: похожие слова лицензии
(«на одном рабочем месте», «версии 5») не делают два продукта одним.

Отдельно считаются «холодные» товары — без покупок в периоде обучения: посоветовать их по истории нельзя.
В отчёт идут только сводные числа, без клиентов и товаров.

Запуск:  python -m recsys.rank.breakdown     подбор порога близости названий по каталогу
"""
from __future__ import annotations

import bisect
import math
import sys
from collections import Counter
from itertools import combinations

from recsys.rank.data import Fold, Task
from recsys.rank.features import Attr

KINDS = ("вариант", "версия", "новый продукт")
NAME_SIM = 0.4          # порог близости названий; при 0.35–0.45 разбор попаданий одинаковый


class Names:
    """Близость названий товаров: косинус по словам названия с весами редкости."""

    def __init__(self, attrs: dict[str, Attr]):
        self.attrs = attrs              # ссылка держит словарь живым: его id не достанется другому, пока запись в кэше
        n = max(len(attrs), 1)
        df = Counter(t for a in attrs.values() for t in a.toks)
        self.w = {t: math.log(n / k) ** 2 for t, k in df.items()}
        # fsum: сумма не зависит от порядка обхода множества слов, значит и от запуска к запуску
        self.norm = {i: math.sqrt(math.fsum(self.w[t] for t in a.toks)) or 1.0 for i, a in attrs.items()}

    def sim(self, x: str, y: str) -> float:
        a, b = self.attrs[x].toks, self.attrs[y].toks
        if len(a) > len(b):
            a, b = b, a
        return math.fsum(self.w[t] for t in a if t in b) / (self.norm[x] * self.norm[y])


_NAMES: dict[int, Names] = {}


def names_of(attrs: dict[str, Attr]) -> Names:
    got = _NAMES.get(id(attrs))
    if got is None or got.attrs is not attrs:
        got = _NAMES[id(attrs)] = Names(attrs)
    return got


def kind_of(item: str, history: list[str], attrs: dict[str, Attr]) -> str:
    a = attrs.get(item)
    if a is None:
        return "новый продукт"
    own = [(h, attrs[h]) for h in history if h in attrs]
    if any(o.line == a.line for _, o in own):
        return "вариант"
    if any(o.nline and o.nline == a.nline for _, o in own):
        return "версия"
    names = names_of(attrs)
    for h, o in own:
        if a.vendor and o.vendor and a.vendor != o.vendor:
            continue                    # разные производители — разные продукты, как бы ни были похожи названия
        if names.sim(item, h) >= NAME_SIM:
            return "версия"
    return "новый продукт"


def breakdown(task: Task, fold: Fold, attrs: dict[str, Attr], recs: dict[str, list[str]]) -> dict:
    """Сводка по группам A и B (все / B2B / B2C): цели и попадания по видам, разложение Precision@3."""
    train_items = {r.item for r in fold.train}
    out = {}
    for gname, group in (("A", fold.group_a), ("B", fold.group_b)):
        for seg in ("все", "B2B", "B2C"):
            ids = [c for c in group if seg == "все" or task.clients[c]["client_group"] == seg]
            if not ids:
                continue
            targets = dict.fromkeys(KINDS, 0)
            hits = dict.fromkeys(KINDS, 0)
            cold_targets = cold_clients = 0
            clients_with = dict.fromkeys(KINDS, 0)
            new_hit_items = Counter()
            for c in ids:
                h = fold.history[c]
                got = set(recs.get(c, [])[:3])
                seen = set()
                has_cold = False
                for t in fold.target[c]:
                    k = kind_of(t, h, attrs)
                    targets[k] += 1
                    seen.add(k)
                    if t in got:
                        hits[k] += 1
                        if k == "новый продукт":
                            new_hit_items[t] += 1
                    if t not in train_items:
                        cold_targets += 1
                        has_cold = True
                cold_clients += has_cold
                for k in seen:
                    clients_with[k] += 1
            n = len(ids)
            total_hits = sum(hits.values())
            out[f"{gname} {seg}"] = {
                "clients": n,
                "targets": targets,
                "clients_with_target": clients_with,
                "hits": hits,
                "hit_share": {k: (hits[k] / total_hits if total_hits else 0.0) for k in KINDS},
                "precision@3": total_hits / (3 * n),
                "precision@3_parts": {k: hits[k] / (3 * n) for k in KINDS},
                # сколько попаданий в новые продукты приходится на один, самый частый товар
                "new_product_hits_on_one_item": max(new_hit_items.values(), default=0),
                "new_product_hit_items": len(new_hit_items),
                "cold_targets": cold_targets, "cold_target_share": cold_targets / max(sum(targets.values()), 1),
                "clients_with_cold_target": cold_clients,
            }
    return out


def text(name: str, res: dict, key: str = "A B2B") -> str:
    r = res[key]
    parts = r["precision@3_parts"]
    return (f"{name:28} {key}: Precision@3 {r['precision@3']:.1%} = вариант {parts['вариант']:.1%} "
            f"+ версия или редакция {parts['версия']:.1%} + новый продукт {parts['новый продукт']:.1%}  "
            f"(попаданий {sum(r['hits'].values())}: {r['hits']['вариант']} / {r['hits']['версия']} / "
            f"{r['hits']['новый продукт']}; из новых на один товар приходится {r['new_product_hits_on_one_item']})")


def calibration(attrs: dict[str, Attr]) -> dict:
    """Подбор порога близости названий по каталогу: свои — одно семейство, чужие — один производитель, разные классы."""
    names = names_of(attrs)
    known = [i for i, a in attrs.items() if not a.line.startswith("L:") and a.cls and a.vendor]
    same, other = [], []
    for x, y in combinations(sorted(known), 2):
        ax, ay = attrs[x], attrs[y]
        if ax.vendor != ay.vendor:
            continue
        if ax.line == ay.line:
            same.append(names.sim(x, y))
        elif ax.cls != ay.cls:
            other.append(names.sim(x, y))
    same.sort()
    other.sort()

    def above(values: list[float], t: float) -> float:
        return 1 - bisect.bisect_left(values, t) / max(len(values), 1)

    best = max((above(same, k / 100) - above(other, k / 100), -k) for k in range(5, 96))
    return {
        "threshold": NAME_SIM, "best_threshold": -best[1] / 100,
        "pairs_same_family": len(same), "pairs_same_vendor_other_class": len(other),
        "same_family_above_threshold": above(same, NAME_SIM),
        "other_class_above_threshold": above(other, NAME_SIM),
        "items_with_catalog_vendor": len(known), "items": len(attrs),
    }


def main() -> None:
    from recsys.rank.data import load_task
    from recsys.rank.features import build_attrs

    sys.stdout.reconfigure(encoding="utf-8")
    c = calibration(build_attrs(load_task()))
    print(f"товаров из продаж {c['items']}, с производителем и классом по каталогу {c['items_with_catalog_vendor']}")
    print(f"пар одного семейства {c['pairs_same_family']}, пар одного производителя из разных классов "
          f"{c['pairs_same_vendor_other_class']}")
    print(f"порог с наибольшей разницей долей: {c['best_threshold']:.2f}; принят {c['threshold']:.2f}: "
          f"выше него {c['same_family_above_threshold']:.1%} пар одного семейства и "
          f"{c['other_class_above_threshold']:.1%} пар из разных классов")


if __name__ == "__main__":
    main()
