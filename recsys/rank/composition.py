"""Из чего состоит тройка модели по product_group и что остаётся от цифры без продлений.

Заказчик (ответы организаторов в чате задачи 27.11.2026): метрики считать на всех клиентах групп A и B;
продление уже купленного продукта в блоке нежелательно — для него в кабинете свой сценарий; полезны
переход на старшую редакцию и дополнения к купленному; долю вариантов показывать через product_group.

product_group здесь — group_id товара в фиде магазина (то, что в остальном коде называется семейством).
В листе «Продажи» такого поля нет, поэтому группа известна только у товаров, привязанных к каталогу;
у остальных вместо неё берётся линейка по названию (в отчёте сказано, сколько мест и попаданий на них приходится).

Каждое место тройки и каждая цель клиента отнесены к одному виду:
  продление купленного          — продление, поддержка или очередной платёж подписки по продукту, который у клиента есть;
  вариант той же группы         — другой товар той же product_group, не продление: другое число мест, срок, комплект;
  другая редакция или версия    — другая product_group того же продукта (мерка recsys.rank.breakdown);
  новый продукт                 — ничего похожего у клиента не было;
  продление без продукта        — товар-продление, а самого продукта в истории клиента нет
                                  (купил его раньше, чем начинаются данные, или не у нас).
Товар из истории клиента в тройку не попадает вовсе: это запрещает протокол.

Дальше тройка пересобирается без продлений: убранные места занимают следующие кандидаты той же модели,
модель при этом не переобучается. Тот же отсев применён к базовым способам организаторов.

Запуск:  python -m recsys.rank.composition
Отчёт:   reports/top3_composition.json (только сводные числа)
"""
from __future__ import annotations

import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date

from recsys.config import ROOT
from recsys.rank.breakdown import kind_of
from recsys.rank.data import OFFICIAL, load_task, make_fold
from recsys.rank.features import Attr, FoldStats, build_attrs

REPORT = ROOT / "reports" / "top3_composition.json"
RENEWAL, SAME, EDITION, NEW, ORPHAN = ("продление купленного", "вариант той же группы", "другая редакция или версия",
                                       "новый продукт", "продление без продукта")
KINDS = (RENEWAL, SAME, EDITION, NEW, ORPHAN)
RENEW_KINDS = ("renewal", "support")
RENEW_NAME = re.compile(r"продлени|renewal|техническ\w* поддержк|техподдержк|сопровождени|\bsupport\b", re.I)
SEGS = ("A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C")
# что убираем из тройки в каждом варианте
FILTERS = {
    "как в протоколе": (),
    "без продлений купленного": (RENEWAL,),
    "без любых продлений": (RENEWAL, ORPHAN),
    "без продлений и вариантов той же группы": (RENEWAL, ORPHAN, SAME),
}


def is_renewal(item: str, attrs: dict[str, Attr], names: dict[str, str]) -> bool:
    """Товар-продление: по виду лицензии, по номеру платежа подписки в названии или по слову в названии."""
    a = attrs.get(item)
    return bool(a and (a.kind in RENEW_KINDS or a.period is not None)) or bool(RENEW_NAME.search(names.get(item, "")))


def place_kind(item: str, history: list[str], attrs: dict[str, Attr], names: dict[str, str]) -> str:
    own = kind_of(item, history, attrs)                 # вариант (та же группа) / версия / новый продукт
    renewal = is_renewal(item, attrs, names)
    if own == "новый продукт":
        return ORPHAN if renewal else NEW
    if renewal:
        return RENEWAL
    return SAME if own == "вариант" else EDITION


def main() -> None:
    from recsys.eval import official as org
    from recsys.eval.value import Prep, by_segment, ci, paired
    from recsys.rank.ltr import copies, fit
    from recsys.rank.unified import Catalog, Unified
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.composition")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    attrs = build_attrs(task)
    train = [Prep(FoldStats(task, make_fold(task, *s), attrs), "full") for s in copies(date.fromisoformat(OFFICIAL[1]))]
    off = FoldStats(task, make_fold(task, *OFFICIAL), attrs)
    uni = Unified(off, fit(train), Catalog(task))
    fold = off.fold
    names = task.names

    def kind(c: str, item: str) -> str:
        return place_kind(item, fold.history[c], attrs, names)

    # ---- полные списки кандидатов: модель и два базовых способа организаторов
    def model_ranking(c: str) -> list[str]:
        ranked = [b for b, _, _ in sorted(uni._model_scores(c), key=lambda x: (-x[1], x[0]))]
        seen = set(ranked)
        for b in off.fallback(c):
            if b not in seen:
                seen.add(b)
                ranked.append(b)
        return ranked

    ot = org.load()
    co: dict[str, Counter] = defaultdict(Counter)
    for items in ot.baskets.values():
        for a, b in itertools.permutations(items, 2):
            co[a][b] += 1

    def order(scores: dict, seen: set[str]) -> list[str]:
        return sorted((i for i in scores if i not in seen), key=lambda i: (-scores[i], -ot.pop[i], i))

    def co_ranking(c: str) -> list[str]:
        scores = Counter()
        for a in ot.hist[c]:
            scores.update(co[a])
        return order(scores, ot.hist[c])

    rankings = {
        "наша модель": {c: model_ranking(c) for c in fold.group_a},
        "популярность": {c: order(ot.pop, ot.hist[c]) for c in fold.group_a},
        "совместные покупки": {c: co_ranking(c) for c in fold.group_a},
    }
    assert all(rankings["наша модель"][c][:3] == uni.protocol(c) for c in fold.group_a)
    assert set(fold.group_a) == set(ot.group_a) and set(fold.group_b) == set(ot.group_b)

    def top3(method: str, c: str, drop: tuple) -> list[str]:
        out = []
        for b in rankings[method][c]:
            if drop and kind(c, b) in drop:
                continue
            out.append(b)
            if len(out) == 3:
                break
        return out

    def seg_clients(seg: str) -> list[str]:
        g, s = seg.split()
        ids = fold.group_a if g == "A" else fold.group_b
        return [c for c in ids if s == "все" or task.clients[c]["client_group"] == s]

    report = {
        "product_group": "group_id товара в фиде магазина; у товара без привязки к каталогу — линейка по названию",
        "items": len(attrs), "items_with_catalog_group": sum(not a.line.startswith("L:") for a in attrs.values()),
        "groups": {s: len(seg_clients(s)) for s in SEGS},
        "kinds": list(KINDS), "filters": {k: list(v) for k, v in FILTERS.items()},
        "precision@3": {}, "fill_rate": {}, "composition": {}, "targets": {}, "difference": {},
    }
    print(f"товаров в продажах {report['items']}, с product_group из каталога {report['items_with_catalog_group']}")
    print("клиентов: " + ", ".join(f"{s} {n}" for s, n in report["groups"].items()))

    # ---- а) и в) Precision@3 на полных группах: как в протоколе и без продлений
    per: dict[tuple, dict] = {}
    for fname, drop in FILTERS.items():
        report["precision@3"][fname], report["fill_rate"][fname] = {}, {}
        for method in rankings:
            hits = {c: len(set(top3(method, c, drop)) & fold.target[c]) / 3 for c in fold.group_a}
            full = {c: float(len(top3(method, c, drop)) == 3) for c in fold.group_a}
            some = {c: float(bool(top3(method, c, drop))) for c in fold.group_a}
            seg, fl, sm = by_segment(task, fold, hits), by_segment(task, fold, full), by_segment(task, fold, some)
            per[(fname, method)] = seg
            report["precision@3"][fname][method] = {
                s: {"value": seg[s]["value"], "ci95": list(ci(seg[s]["values"])), "hits": round(3 * sum(seg[s]["values"]))}
                for s in SEGS}
            report["fill_rate"][fname][method] = {s: {"any": sm[s]["value"], "all_three": fl[s]["value"]} for s in SEGS}
        print(f"\nPrecision@3, {fname} (в скобках — попаданий):")
        print(f"  {'':22}" + "".join(f"{s:>16}" for s in SEGS))
        for method in rankings:
            r = report["precision@3"][fname][method]
            print(f"  {method:22}" + "".join(f"{r[s]['value']:>9.1%} ({r[s]['hits']:>3})" for s in SEGS))
    # разница с базой организаторов на тех же клиентах, с интервалом
    for fname in FILTERS:
        report["difference"][fname] = {}
        for base in ("популярность", "совместные покупки"):
            ours, ref_same, ref_off = per[(fname, "наша модель")], per[(fname, base)], per[("как в протоколе", base)]
            report["difference"][fname][base] = {
                s: {"vs_official_baseline": list(paired(ours[s]["values"], ref_off[s]["values"])),
                    "vs_baseline_with_same_filter": list(paired(ours[s]["values"], ref_same[s]["values"]))}
                for s in SEGS}
        d = report["difference"][fname]["популярность"]
        print(f"  {fname}: наша модель минус популярность организаторов, п.п.: " + "; ".join(
            f"{s} {100 * d[s]['vs_official_baseline'][0]:+.1f} [{100 * d[s]['vs_official_baseline'][1]:+.1f}; "
            f"{100 * d[s]['vs_official_baseline'][2]:+.1f}]" for s in ("A B2B", "B B2B", "A B2C", "B B2C")))

    # ---- б) из чего состоит тройка и попадания: по product_group
    print("\nиз чего состоит тройка как в протоколе (мест / попаданий):")
    for method in rankings:
        report["composition"][method] = {}
        for s in SEGS:
            places, hits, by_name = Counter(), Counter(), Counter()
            for c in seg_clients(s):
                for b in top3(method, c, ()):
                    k = kind(c, b)
                    places[k] += 1
                    hit = b in fold.target[c]
                    hits[k] += hit
                    if attrs[b].line.startswith("L:"):          # группы из каталога нет: взята линейка по названию
                        by_name["places"] += 1
                        by_name["hits"] += hit
            n_p, n_h = sum(places.values()), sum(hits.values())
            report["composition"][method][s] = {
                "places": {k: places[k] for k in KINDS}, "hits": {k: hits[k] for k in KINDS},
                "place_share": {k: places[k] / n_p if n_p else 0.0 for k in KINDS},
                "hit_share": {k: hits[k] / n_h if n_h else 0.0 for k in KINDS},
                "places_total": n_p, "hits_total": n_h,
                "without_catalog_group": dict(by_name),
            }
        for s in ("A B2B", "B B2B", "A B2C"):
            r = report["composition"][method][s]
            print(f"  {method:20} {s}: " + "; ".join(
                f"{k} {r['places'][k]} / {r['hits'][k]}" for k in KINDS)
                  + f"  (всего {r['places_total']} / {r['hits_total']}; без группы из каталога "
                    f"{r['without_catalog_group'].get('places', 0)} / {r['without_catalog_group'].get('hits', 0)})")
    # что клиенты покупали на самом деле: цели по тем же видам
    for s in SEGS:
        t = Counter(kind(c, b) for c in seg_clients(s) for b in fold.target[c])
        report["targets"][s] = {k: t[k] for k in KINDS}
    t = report["targets"]["A B2B"]
    print("\nцели клиентов (что они купили на самом деле), A B2B: " + "; ".join(f"{k} {t[k]}" for k in KINDS)
          + f"  (всего {sum(t.values())})")

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nотчёт: {REPORT}")


if __name__ == "__main__":
    main()
