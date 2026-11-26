"""Проверка на истории по протоколу организаторов (лист «Протокол_проверки» датасета).

Правила общие для всех команд, поэтому считаем строго по ним:
  обучение  — заказы с order_week не позже 2026-08-31;
  проверка  — заказы с order_week с 2026-09-07 до конца данных;
  цель      — товары (item_id), новые для клиента: купленные в проверке и не купленные в обучении;
  совет     — ровно 3 item_id на клиента, без товаров из его истории;
  Precision@3 — число угаданных из трёх / 3, среднее по клиентам группы;
  группа A  — клиенты с покупками в обучении и хотя бы одним новым товаром в проверке;
  группа B  — часть группы A с ровно одним товаром в истории (холодный старт);
  Fill Rate — доля клиентов группы, которым способ выдал хотя бы один совет.

Датасет организаторов публиковать нельзя: он лежит в data/private и в репозиторий не попадает.

Запуск:  python -m recsys.eval.official
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from recsys.config import DATA

DATASET = DATA / "private" / "task" / "dataset_v3.xlsx"
TRAIN_UNTIL = "2026-08-31"
TEST_FROM = "2026-09-07"


@dataclass
class Task:
    sales: pd.DataFrame                 # все строки продаж
    clients: dict[str, dict]            # client_id -> строка справочника
    train: pd.DataFrame
    test: pd.DataFrame
    history: dict[str, list[str]]       # client_id -> товары обучения (в порядке покупки, без повторов)
    target: dict[str, set[str]]         # client_id -> новые товары в проверке
    group_a: list[str]
    group_b: list[str]
    names: dict[str, str]               # item_id -> название


def load(path: Path = DATASET) -> Task:
    sales = pd.read_excel(path, sheet_name="Продажи", dtype=str, engine="openpyxl")
    cl = pd.read_excel(path, sheet_name="Клиенты", dtype=str, engine="openpyxl")
    clients = {r["client_id"]: r for r in cl.fillna("").to_dict("records")}
    sales = sales.sort_values(["order_week", "order_id"], kind="stable").reset_index(drop=True)
    train = sales[sales["order_week"] <= TRAIN_UNTIL]
    test = sales[sales["order_week"] >= TEST_FROM]
    history: dict[str, list[str]] = defaultdict(list)
    for c, i in zip(train["client_id"], train["item_id"]):
        if i not in history[c]:
            history[c].append(i)
    bought_later: dict[str, set[str]] = defaultdict(set)
    for c, i in zip(test["client_id"], test["item_id"]):
        bought_later[c].add(i)
    target = {c: items - set(history[c]) for c, items in bought_later.items() if c in history}
    group_a = sorted(c for c, t in target.items() if t)
    group_b = [c for c in group_a if len(history[c]) == 1]
    names = dict(zip(sales["item_id"], sales["item_name"]))
    return Task(sales, clients, train, test, dict(history), target, group_a, group_b, names)


Recommender = Callable[[str, list[str]], list[str]]     # (client_id, история) -> до трёх item_id


def evaluate(task: Task, recommend: Recommender) -> dict[str, dict]:
    """Precision@3 и Fill Rate по группам A и B: все / B2B / B2C. Клиент без совета даёт ноль."""
    rows = {}
    for c in task.group_a:
        owned = set(task.history[c])
        recs = [i for i in dict.fromkeys(recommend(c, task.history[c])) if i not in owned][:3]
        rows[c] = (len(set(recs) & task.target[c]) / 3, bool(recs), len(recs))
    out = {}
    for gname, group in (("A", task.group_a), ("B", task.group_b)):
        for seg in ("все", "B2B", "B2C"):
            ids = [c for c in group if seg == "все" or task.clients[c]["client_group"] == seg]
            if not ids:
                continue
            out[f"{gname} {seg}"] = {
                "clients": len(ids),
                "precision@3": sum(rows[c][0] for c in ids) / len(ids),
                "fill_rate": sum(rows[c][1] for c in ids) / len(ids),
                "hit_clients": sum(rows[c][0] > 0 for c in ids),
            }
    return out


# ---- базовые способы организаторов -------------------------------------------------------

def popularity(task: Task, by: str = "rows") -> Recommender:
    """Три самых покупаемых товара периода обучения, которых нет у клиента."""
    if by == "clients":
        cnt = task.train.drop_duplicates(["client_id", "item_id"])["item_id"].value_counts()
    elif by == "orders":
        cnt = task.train.drop_duplicates(["order_id", "item_id"])["item_id"].value_counts()
    else:
        cnt = task.train["item_id"].value_counts()
    top = list(cnt.index[:50])
    return lambda c, h: [i for i in top if i not in h][:3]


def co_orders(task: Task) -> Recommender:
    """Товары, которые чаще всего встречаются в одних заказах с товарами клиента."""
    pair: dict[str, Counter] = defaultdict(Counter)
    for _, items in task.train.groupby("order_id")["item_id"]:
        items = list(dict.fromkeys(items))
        for a in items:
            for b in items:
                if a != b:
                    pair[a][b] += 1

    def rec(c: str, h: list[str]) -> list[str]:
        score = Counter()
        for a in h:
            score.update(pair.get(a, {}))
        return [i for i, _ in score.most_common() if i not in h][:3]

    return rec


def table(results: dict[str, dict[str, dict]]) -> str:
    cols = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"]
    lines = [f"{'способ':34}" + "".join(f"{c:>16}" for c in cols)]
    for name, res in results.items():
        lines.append(f"{name:34}" + "".join(
            f"{res[c]['precision@3']:>8.1%} /{res[c]['fill_rate']:>5.0%} " if c in res else f"{'—':>16}" for c in cols))
    lines.append("(в ячейке: Precision@3 / Fill Rate)")
    return "\n".join(lines)


def main() -> None:
    task = load()
    b2b = sum(task.clients[c]["client_group"] == "B2B" for c in task.group_a)
    b2b_b = sum(task.clients[c]["client_group"] == "B2B" for c in task.group_b)
    print(f"строк продаж {len(task.sales)}, обучение {len(task.train)}, проверка {len(task.test)}, "
          f"товаров {task.sales['item_id'].nunique()}")
    print(f"группа A: {len(task.group_a)} (B2B {b2b}, B2C {len(task.group_a) - b2b}); "
          f"группа B: {len(task.group_b)} (B2B {b2b_b}, B2C {len(task.group_b) - b2b_b})")
    print("у организаторов: A 367 (331 / 36), B 191 (165 / 26)\n")
    results = {
        "популярность (по строкам)": evaluate(task, popularity(task, "rows")),
        "популярность (по клиентам)": evaluate(task, popularity(task, "clients")),
        "совместные заказы": evaluate(task, co_orders(task)),
    }
    print(table(results))
    print("\nу организаторов: популярность  A 5.2% / 3.6% / 19.4%, B 7.0% / 5.1% / 19.2% (Fill Rate 100%)")
    print("                 совм. покупки A 4.8% / 3.9% / 13.0% (89%), B 5.1% / 3.8% / 12.8% (82%)")


if __name__ == "__main__":
    main()
