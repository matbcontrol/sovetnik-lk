"""Данные для подбора и проверки: разрезы по времени, устроенные так же, как протокол организаторов.

Официальный разрез (обучение до 31.08.2026, проверка с 07.09.2026) используется только для итоговой
цифры. Подбор весов и выбор способов идут на внутренних разрезах — тех же правилах, но сдвинутых
в прошлое, целиком внутри обучения. Так итоговая цифра не подогнана под проверочный период.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from recsys.config import DATA

DATASET = DATA / "private" / "task" / "dataset_v3.xlsx"
ITEMS = DATA / "private" / "task" / "items.csv"

OFFICIAL = ("официальный", "2026-08-31", "2026-09-07", "2026-11-30")
# внутренние разрезы: проверочное окно той же длины (12 недель), всё — до конца официального обучения
INNER = [
    ("внутренний 1", "2026-06-01", "2026-06-08", "2026-08-31"),
    ("внутренний 2", "2026-03-02", "2026-03-09", "2026-06-01"),
    ("внутренний 3", "2025-12-01", "2025-12-08", "2026-03-02"),
]


@dataclass
class Row:
    client: str
    week: date
    order: str
    item: str


@dataclass
class Fold:
    name: str
    train_until: date
    test_from: date
    test_until: date
    train: list[Row]
    history: dict[str, list[str]]                 # клиент -> товары обучения в порядке первой покупки
    first: dict[str, dict[str, date]]             # клиент -> товар -> неделя первой покупки
    last: dict[str, dict[str, date]]              # клиент -> товар -> неделя последней покупки
    orders: dict[str, list[str]]                  # заказ -> товары (только обучение)
    target: dict[str, set[str]]
    group_a: list[str] = field(default_factory=list)
    group_b: list[str] = field(default_factory=list)


@dataclass
class Task:
    rows: list[Row]
    clients: dict[str, dict]
    items: dict[str, dict]                        # item_id -> строка items.csv (семейство, класс, вид лицензии…)
    names: dict[str, str]


def load_task(dataset: Path = DATASET, items_path: Path = ITEMS) -> Task:
    sales = pd.read_excel(dataset, sheet_name="Продажи", dtype=str, engine="openpyxl").fillna("")
    cl = pd.read_excel(dataset, sheet_name="Клиенты", dtype=str, engine="openpyxl").fillna("")
    sales = sales.sort_values(["order_week", "order_id"], kind="stable")
    rows = [Row(c, date.fromisoformat(w), o, i)
            for c, w, o, i in zip(sales["client_id"], sales["order_week"], sales["order_id"], sales["item_id"])]
    items = {}
    if items_path.exists():
        with items_path.open(encoding="utf-8") as f:
            items = {r["item_id"]: r for r in csv.DictReader(f)}
    names = dict(zip(sales["item_id"], sales["item_name"]))
    return Task(rows, {r["client_id"]: r for r in cl.to_dict("records")}, items, names)


def make_fold(task: Task, name: str, train_until: str, test_from: str, test_until: str) -> Fold:
    tu, tf, tt = date.fromisoformat(train_until), date.fromisoformat(test_from), date.fromisoformat(test_until)
    train = [r for r in task.rows if r.week <= tu]
    first: dict[str, dict[str, date]] = defaultdict(dict)
    last: dict[str, dict[str, date]] = defaultdict(dict)
    orders: dict[str, list[str]] = defaultdict(list)
    for r in train:
        first[r.client].setdefault(r.item, r.week)
        last[r.client][r.item] = r.week
        if r.item not in orders[r.order]:
            orders[r.order].append(r.item)
    history = {c: sorted(d, key=lambda i: d[i]) for c, d in first.items()}
    later: dict[str, set[str]] = defaultdict(set)
    for r in task.rows:
        if tf <= r.week <= tt:
            later[r.client].add(r.item)
    target = {c: items - set(history[c]) for c, items in later.items() if c in history}
    group_a = sorted(c for c, t in target.items() if t)
    group_b = [c for c in group_a if len(history[c]) == 1]
    return Fold(name, tu, tf, tt, train, history, dict(first), dict(last), dict(orders), target, group_a, group_b)


def official(task: Task) -> Fold:
    return make_fold(task, *OFFICIAL)


def inner(task: Task) -> list[Fold]:
    return [make_fold(task, *spec) for spec in INNER]


def months_between(a: date, b: date) -> float:
    return (b - a).days / 30.44


def weeks_ago(d: date, n: int) -> date:
    return d - timedelta(weeks=n)
