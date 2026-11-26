"""Как часто сбываются проверенные связи открытого графа — по покупкам, где это можно проверить.

Связь между семействами проверяема, только если оба семейства есть в продажах. Для клиента, который
в окне копии протокола что-то докупил и у которого есть товар семейства-источника, связь «сбылась»,
если среди его новых покупок есть товар семейства-назначения. Считается по копиям протокола внутри
периода обучения, официальный проверочный период не используется.

Для сравнения рядом стоят две привычные мерки на тех же клиентах: покупка ещё одного товара своего
семейства и один из трёх самых частых товаров докупки.

Запуск:  python -m recsys.rank.link_rates
Результат: reports/link_rates.json (только сводные числа; пары семейств названы именами из открытого каталога)
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date

from recsys.config import INTERIM, ROOT
from recsys.rank.data import OFFICIAL, load_task, make_fold
from recsys.rank.ltr import copies
from recsys.stable import rerun_with_fixed_order

OUT = ROOT / "reports" / "link_rates.json"
TITLE = {
    ("new_version", "номера версий в названиях"): "новая версия",
    ("analog_ru", "таблица замещения АРПП"): "прямой аналог из таблицы замещения",
    ("upgrade", "таблица сравнения магазина"): "старшая редакция (таблица сравнения магазина)",
    ("upgrade", "названия редакций"): "старшая редакция (по названиям)",
    ("complements", "название товара"): "дополнение из названия",
    ("mentions", "ссылка на странице магазина"): "упоминание на странице магазина",
}


def main() -> None:
    rerun_with_fixed_order("recsys.rank.link_rates")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    fam_of = {i: r.get("family_id", "") for i, r in task.items.items()}
    sold = set(fam_of.values()) - {""}
    nodes = {}
    with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            nodes[r["family"]] = r
    name = {r["family_id"]: r["name"] for r in nodes.values()}
    links: dict[str, list[tuple[str, tuple]]] = defaultdict(list)
    total = Counter()
    with (INTERIM / "graph_edges.csv").open(encoding="utf-8") as f:
        for e in csv.DictReader(f):
            key = (e["type"], e["source"])
            if key not in TITLE:
                continue
            s, d = nodes.get(e["src"]), nodes.get(e["dst"])
            if not s or not d:
                continue
            total[key] += 1
            if s["family_id"] in sold and d["family_id"] in sold and s["family_id"] != d["family_id"]:
                links[s["family_id"]].append((d["family_id"], key))

    tried, hit = Counter(), Counter()
    pair_try, pair_hit = Counter(), Counter()
    same = [0, 0]
    pop = [0, 0]
    clients = 0
    for spec in copies(date.fromisoformat(OFFICIAL[1])):
        fold = make_fold(task, *spec)
        follow = Counter()
        for c, d in fold.first.items():
            start = min(d.values())
            follow.update(b for b, w in d.items() if w > start)
        top3 = [i for i, _ in follow.most_common(3)]
        for c in fold.group_a:
            clients += 1
            owned = set(fold.history[c])
            own_f = {fam_of.get(a, "") for a in owned} - {""}
            tgt_f = {fam_of.get(t, "") for t in fold.target[c]} - {""}
            for fam in own_f:
                same[0] += 1
                same[1] += fam in tgt_f
                for dst, key in links.get(fam, ()):
                    if dst in own_f:
                        continue
                    tried[key] += 1
                    hit[key] += dst in tgt_f
                    pair_try[(key, fam, dst)] += 1
                    pair_hit[(key, fam, dst)] += dst in tgt_f
            for i in top3:
                if i not in owned:
                    pop[0] += 1
                    pop[1] += i in fold.target[c]

    report = {
        "how": "копии протокола внутри периода обучения; клиенты, докупившие что-то новое в окне 12 недель",
        "client_windows": clients,
        "reference": {
            "ещё один товар своего семейства": {"tried": same[0], "confirmed": same[1], "rate": same[1] / max(same[0], 1)},
            "один из трёх самых частых товаров докупки": {"tried": pop[0], "confirmed": pop[1], "rate": pop[1] / max(pop[0], 1)},
        },
        "links": {},
    }
    print(f"окон «клиент × копия»: {clients}")
    for k, v in report["reference"].items():
        print(f"  {k:52} {v['confirmed']:>5} из {v['tried']:>5} ({v['rate']:.1%})")
    for key, title in TITLE.items():
        pairs = sorted(((pair_hit[p], pair_try[p], name.get(p[1], p[1]), name.get(p[2], p[2]))
                        for p in pair_try if p[0] == key), reverse=True)
        report["links"][title] = {
            "links_in_graph": total[key], "checkable_family_pairs": len(pairs),
            "tried": tried[key], "confirmed": hit[key], "rate": hit[key] / tried[key] if tried[key] else None,
            "pairs": [{"from": a, "to": b, "confirmed": h, "tried": t} for h, t, a, b in pairs[:6]],
        }
        rate = f"{hit[key] / tried[key]:.1%}" if tried[key] else "проверить нечем"
        print(f"  {title:52} {hit[key]:>5} из {tried[key]:>5} ({rate}); связей в графе {total[key]}, "
              f"проверяемых пар семейств {len(pairs)}")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {OUT}")


if __name__ == "__main__":
    main()
