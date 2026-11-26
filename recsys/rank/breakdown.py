"""Из чего состоят попадания: другой вариант уже купленного продукта или действительно новый продукт.

Организаторы считают разными товарами разные артикулы одного продукта (10–14 и 15–19 узлов,
Base и Renewal) и просят показать на питче, какая доля попаданий приходится на такие варианты.
Каждая цель клиента отнесена к одному из трёх видов:
  вариант       — другой артикул продукта, который у клиента уже есть (то же семейство каталога;
                  если товара в каталоге нет — та же линейка по названию);
  версия        — тот же продукт в другой версии или редакции (другое семейство, та же линейка по названию);
  новый продукт — у клиента ничего из этой линейки не было.
Precision@3 раскладывается на три слагаемых по этим видам: их сумма равна общей цифре.

Отдельно считаются «холодные» товары — без покупок в периоде обучения: посоветовать их по истории нельзя.
В отчёт идут только сводные числа, без клиентов и товаров.
"""
from __future__ import annotations

from recsys.rank.data import Fold, Task
from recsys.rank.features import Attr

KINDS = ("вариант", "версия", "новый продукт")


def kind_of(item: str, history: list[str], attrs: dict[str, Attr]) -> str:
    a = attrs.get(item)
    if a is None:
        return "новый продукт"
    own = [attrs[h] for h in history if h in attrs]
    if any(o.line == a.line for o in own):
        return "вариант"
    if any(o.nline and o.nline == a.nline for o in own):
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
                "cold_targets": cold_targets, "cold_target_share": cold_targets / max(sum(targets.values()), 1),
                "clients_with_cold_target": cold_clients,
            }
    return out


def text(name: str, res: dict, key: str = "A B2B") -> str:
    r = res[key]
    parts = r["precision@3_parts"]
    return (f"{name:28} {key}: Precision@3 {r['precision@3']:.1%} = вариант {parts['вариант']:.1%} "
            f"+ версия {parts['версия']:.1%} + новый продукт {parts['новый продукт']:.1%}  "
            f"(попаданий {sum(r['hits'].values())}: {r['hits']['вариант']} / {r['hits']['версия']} / {r['hits']['новый продукт']})")
