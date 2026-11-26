"""Проверка связей между классами продажами: видна ли связь, предложенная моделью, в покупках клиентов.

Связь «класс А -> класс Б» считается подтверждённой, если не меньше трёх клиентов купили товар
класса Б не раньше товара класса А. Проверить можно только связи, оба класса которых есть в продажах:
датасет покрывает около трёх десятков классов каталога из трёхсот.

Что получается:
  data/interim/class_links_support.json  — «А|Б» -> ступень поддержки (3, 10 или 50 клиентов); точные
                                           числа наружу не идут, только ступень
  reports/class_links_check.json         — сводка: сколько связей удалось проверить и сколько подтвердилось

Подтверждённые связи движок показывает клиенту сразу, остальные ждут решения менеджера.

Запуск:  python -m recsys.eval.class_support      (нужен датасет организаторов)
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict

from recsys.config import INTERIM, ROOT
from recsys.rank.data import load_task

MIN_SUPPORT = 3          # столько клиентов должны купить Б после А
MIN_OWNERS = 20          # класс «есть в продажах», если его товары покупали не меньше стольких клиентов
STEPS = (50, 10, 3)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    with (INTERIM / "class_links.csv").open(encoding="utf-8") as f:
        links = list(csv.DictReader(f))
    names = json.loads((INTERIM / "class_names.json").read_text(encoding="utf-8"))
    by_name = defaultdict(list)
    for key, name in names.items():
        by_name[name.strip().lower()].append(key)
    # класс графа для товара из продаж — по названию подкатегории каталога (если оно однозначно)
    cls = {}
    for item, r in task.items.items():
        keys = by_name.get(((r or {}).get("subcategory") or "").strip().lower(), [])
        cls[item] = keys[0] if len(keys) == 1 else ""

    first: dict[str, dict] = defaultdict(dict)           # клиент -> класс -> неделя первой покупки
    for r in task.rows:
        c = cls.get(r.item)
        if c and (c not in first[r.client] or r.week < first[r.client][c]):
            first[r.client][c] = r.week
    owners = Counter(c for d in first.values() for c in d)
    after = Counter()
    for d in first.values():
        for a, wa in d.items():
            for b, wb in d.items():
                if a != b and wb >= wa:
                    after[(a, b)] += 1

    support, checkable, confirmed, silent = {}, 0, 0, 0
    for link in links:
        a, b = link["src_class"], link["dst_class"]
        n = after.get((a, b), 0)
        both = owners.get(a, 0) >= MIN_OWNERS and owners.get(b, 0) >= MIN_OWNERS
        checkable += both
        confirmed += both and n >= MIN_SUPPORT
        silent += both and n == 0
        if n >= MIN_SUPPORT:
            support[f"{a}|{b}"] = next(s for s in STEPS if n >= s)
    (INTERIM / "class_links_support.json").write_text(json.dumps(support, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {
        "links": len(links), "classes_in_sales": len({c for c in cls.values() if c}),
        "checkable": checkable, "confirmed": confirmed, "no_support": silent,
        "confirmed_total": len(support), "min_support": MIN_SUPPORT, "min_owners": MIN_OWNERS,
    }
    (ROOT / "reports" / "class_links_check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"связей между классами: {len(links)}; классов каталога в продажах: {summary['classes_in_sales']}")
    print(f"проверить продажами можно {checkable} связей; подтвердились {confirmed}, без единой покупки {silent}")
    print(f"всего связей с поддержкой от {MIN_SUPPORT} клиентов: {len(support)}")


if __name__ == "__main__":
    main()
