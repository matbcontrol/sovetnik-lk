"""Проверка на истории по протоколу организаторов и их базовые способы.

Правила (лист «Протокол_проверки» датасета v4 и приложенный к нему baseline.py) повторены дословно:
  обучение    — строки с order_week не позже 2026-08-31, проверка — позже;
  корзина     — пара (order_id, order_week): один order_id в разные недели — это разные покупки;
  цель        — товары (item_id), новые для клиента: куплены в проверке, в обучении у клиента их не было;
  совет       — ровно 3 item_id на клиента, без товаров из его истории; если выдано меньше трёх,
                недостающие места считаются промахом (всегда делим на 3);
  Precision@3 — число угаданных / 3, среднее по клиентам группы;
  группа A    — клиенты с покупками в обучении и хотя бы одним новым товаром в проверке;
  группа B    — часть группы A с ровно одним товаром в истории (холодный старт клиента);
  Fill Rate   — доля клиентов группы, которым способ выдал хотя бы один совет.
Базовые способы:
  популярность       — число разных клиентов, купивших товар в обучении;
  совместные покупки — число корзин обучения, где оба товара встретились, сумма по товарам истории;
  ничьи              — выше счёт, затем выше популярность, затем item_id по алфавиту.

Датасет организаторов публиковать нельзя: он лежит в data/private и в репозиторий не попадает.

Запуск:  python -m recsys.eval.official
"""
from __future__ import annotations

import itertools
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from recsys.config import DATASET

TRAIN_UNTIL = "2026-08-31"

# цифры организаторов для v4 (лист «Протокол_проверки»): Precision@3 и Fill Rate, все / B2B / B2C
PUBLISHED = {
    "популярность": {"A": (5.44, 3.75, 20.0, 100.0), "B": (7.36, 5.22, 20.0, 100.0)},
    "совместные покупки": {"A": (5.64, 4.53, 15.24, 92.3), "B": (6.01, 4.54, 14.67, 87.2)},
}
PUBLISHED_GROUPS = {"A": (337, 302, 35), "B": (172, 147, 25)}


@dataclass
class Task:
    group: dict[str, str]               # client_id -> B2B / B2C
    hist: dict[str, set[str]]           # client_id -> товары обучения
    test: dict[str, set[str]]           # client_id -> товары проверки
    baskets: dict[tuple, set[str]]      # (order_id, order_week) -> товары, только обучение
    pop: Counter                        # товар -> число разных клиентов в обучении
    group_a: list[str]
    group_b: list[str]
    names: dict[str, str]


def load(path: Path = DATASET) -> Task:
    sales = pd.read_excel(path, sheet_name="Продажи", dtype=str, engine="openpyxl")
    cl = pd.read_excel(path, sheet_name="Клиенты", dtype=str, engine="openpyxl")
    group = dict(zip(cl["client_id"], cl["client_group"]))
    hist, test, baskets = defaultdict(set), defaultdict(set), defaultdict(set)
    for order, client, week, item in zip(sales["order_id"], sales["client_id"], sales["order_week"], sales["item_id"]):
        if week <= TRAIN_UNTIL:
            hist[client].add(item)
            baskets[(order, week)].add(item)
        else:
            test[client].add(item)
    pop = Counter()
    for items in hist.values():
        pop.update(items)
    group_a = sorted(c for c in test if c in hist and test[c] - hist[c])
    group_b = [c for c in group_a if len(hist[c]) == 1]
    return Task(group, dict(hist), dict(test), dict(baskets), pop, group_a, group_b,
                dict(zip(sales["item_id"], sales["item_name"])))


Recommender = Callable[[str], list[str]]     # client_id -> до трёх item_id


def top3(task: Task, scores: dict[str, float], seen: set[str]) -> list[str]:
    """Три лучших по счёту; ничьи — выше популярность, затем item_id по алфавиту (как в baseline.py)."""
    cand = [i for i in scores if i not in seen]
    cand.sort(key=lambda i: (-scores[i], -task.pop[i], i))
    return cand[:3]


def popularity(task: Task) -> Recommender:
    return lambda c: top3(task, task.pop, task.hist[c])


def co_purchase(task: Task) -> Recommender:
    co: dict[str, Counter] = defaultdict(Counter)
    for items in task.baskets.values():
        for a, b in itertools.permutations(items, 2):
            co[a][b] += 1

    def rec(c: str) -> list[str]:
        scores = Counter()
        for a in task.hist[c]:
            scores.update(co[a])
        return top3(task, scores, task.hist[c])

    return rec


def evaluate(task: Task, recommend: Recommender) -> dict[str, dict]:
    """Precision@3 и Fill Rate по группам A и B: все / B2B / B2C."""
    recs = {c: [i for i in dict.fromkeys(recommend(c)) if i not in task.hist[c]][:3] for c in task.group_a}
    out = {}
    for gname, clients in (("A", task.group_a), ("B", task.group_b)):
        for seg in ("все", "B2B", "B2C"):
            ids = [c for c in clients if seg == "все" or task.group[c] == seg]
            if not ids:
                continue
            hits = [len(set(recs[c]) & (task.test[c] - task.hist[c])) / 3 for c in ids]
            out[f"{gname} {seg}"] = {
                "clients": len(ids), "precision@3": sum(hits) / len(ids),
                "fill_rate": sum(1 for c in ids if recs[c]) / len(ids),
            }
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    task = load()
    print(f"датасет: {DATASET.name}")
    for g, clients in (("A", task.group_a), ("B", task.group_b)):
        b2b = sum(task.group[c] == "B2B" for c in clients)
        print(f"группа {g}: {len(clients)} (B2B {b2b}, B2C {len(clients) - b2b}); "
              f"у организаторов {PUBLISHED_GROUPS[g][0]} ({PUBLISHED_GROUPS[g][1]} / {PUBLISHED_GROUPS[g][2]})")
    ok = True
    for name, rec in (("популярность", popularity(task)), ("совместные покупки", co_purchase(task))):
        res = evaluate(task, rec)
        for g in ("A", "B"):
            mine = tuple(round(100 * res[f"{g} {s}"]["precision@3"], 2) for s in ("все", "B2B", "B2C")) \
                + (round(100 * res[f"{g} все"]["fill_rate"], 1),)
            same = mine == PUBLISHED[name][g]
            ok &= same
            print(f"{name:20} группа {g}: {mine[0]:.2f} / {mine[1]:.2f} / {mine[2]:.2f}, Fill Rate {mine[3]:.1f}  "
                  f"{'= как у организаторов' if same else 'НЕ СОВПАЛО, у организаторов ' + str(PUBLISHED[name][g])}")
    print("\nнаш счёт совпадает с baseline.py организаторов" if ok else "\nЕСТЬ РАСХОЖДЕНИЯ с цифрами организаторов")


if __name__ == "__main__":
    main()
