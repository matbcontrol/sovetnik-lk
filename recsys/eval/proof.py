"""Доказательство на условиях организаторов: их собственный скрипт оценки, их датасет, наши ответы.

Что делает. Счёт берётся из baseline.py организаторов дословно (загрузка листа «Продажи», разрез 2026-08-31, группы A и B,
Precision@3, Fill Rate и доля вариантов по product_group); к их двум способам добавлен третий — наши тройки из файла
ответов. Поэтому спорить можно только с ответами, но не со счётом: он их. Поверх счёта — то, чего в их скрипте нет:
парная проверка значимости по клиентам (бутстреп и знаковый тест) и отпечатки файлов, чтобы любой мог убедиться,
что считал на тех же данных.

  python -m recsys.eval.proof            модель обучается заново на продажах до 31.08 -> файл ответов -> счёт.
                                         Нужен только датасет организаторов (data/private/task/dataset_v5.xlsx).
  python -m recsys.eval.proof --answers  только выгрузить файл ответов: data/private/task/proof/our_top3.csv
  python -m recsys.eval.proof --stand    ответы взять из закрытого стенда (site/data/private.json), без обучения

Файл ответов — client_id и три item_id; в нём идентификаторы датасета, поэтому он лежит в закрытой папке и
передаётся организаторам вместе с закрытым стендом. В открытый отчёт reports/proof.json идут только числа.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import itertools
import json
import math
import random
import sys

import openpyxl

from recsys.config import DATASET, ROOT, TASK_DIR

WORK = TASK_DIR / "proof"
ANSWERS = WORK / "our_top3.csv"
OUT = ROOT / "reports" / "proof.json"
PRIVATE = ROOT / "site" / "data" / "private.json"
BOOT, SEED = 10000, 7


def model_answers() -> list[tuple]:
    """Наши тройки по протоколу заново: модель учится только на продажах до разреза и ранжирует товары клиентов группы A.

    Тот же путь, что у разбора тройки (recsys.rank.composition) и у стенда: вердикты пар и закрытые таблицы
    для этого не нужны, только датасет.
    """
    from datetime import date

    from recsys.eval.value import Prep, ensure_items
    from recsys.rank.data import OFFICIAL, load_task, make_fold
    from recsys.rank.features import FoldStats, build_attrs
    from recsys.rank.ltr import copies, fit
    from recsys.rank.unified import Catalog, Unified

    ensure_items()
    task = load_task()
    attrs = build_attrs(task)
    train = [Prep(FoldStats(task, make_fold(task, *s), attrs), "full") for s in copies(date.fromisoformat(OFFICIAL[1]))]
    off = FoldStats(task, make_fold(task, *OFFICIAL), attrs)
    uni = Unified(off, fit(train), Catalog(task))
    return [(c, *uni.protocol(c)[:3]) for c in sorted(off.fold.group_a)]


def stand_answers() -> list[tuple]:
    """Тройки из закрытого стенда (их строит recsys.rank.demo тем же путём)."""
    d = json.loads(PRIVATE.read_text(encoding="utf-8"))
    return sorted((c["id"], *[x["item"] for x in c["protocol"]][:3]) for c in d["clients"] if c["mode"] == "test")


def export_answers() -> None:
    """Наши тройки по протоколу для клиентов группы A — в том виде, в каком их видит счёт организаторов."""
    rows = stand_answers() if "--stand" in sys.argv else model_answers()
    WORK.mkdir(parents=True, exist_ok=True)
    with ANSWERS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["client_id", "item_1", "item_2", "item_3"])
        w.writerows(rows)
    print(f"ответов: {len(rows)} -> {ANSWERS}")


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_test(wins: int, losses: int) -> float:
    """Двусторонний знаковый тест: насколько вероятен такой перевес, если системы на самом деле равны."""
    n, k = wins + losses, min(wins, losses)
    if not n:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main() -> None:
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.eval.proof")        # порядок обхода фиксирован: два запуска дают один файл ответов
    sys.stdout.reconfigure(encoding="utf-8")
    export_answers()
    if "--answers" in sys.argv:
        return

    # ---- дословно из baseline.py организаторов (загрузка, разрез, группы, счёт) ----
    SPLIT = '2026-08-31'          # обучение: order_week <= SPLIT, проверка: order_week > SPLIT
    wb = openpyxl.load_workbook(DATASET, read_only=True)
    rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
    sales = [r[:4] for r in rows]   # order_id, client_id, order_week, item_id
    pgroup = {r[3]: (r[6] if len(r) > 6 and r[6] else r[3]) for r in rows}
    group = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}

    hist, test = collections.defaultdict(set), collections.defaultdict(set)
    baskets = collections.defaultdict(set)
    for order, client, week, item in sales:
        if week <= SPLIT:
            hist[client].add(item)
            baskets[(order, week)].add(item)
        else:
            test[client].add(item)
    pop = collections.Counter()
    for items in hist.values():
        pop.update(items)
    co = collections.defaultdict(collections.Counter)
    for items in baskets.values():
        for a, b in itertools.permutations(items, 2):
            co[a][b] += 1

    def top3(scores, seen):
        cand = [i for i in scores if i not in seen]
        cand.sort(key=lambda i: (-scores[i], -pop[i], i))
        return cand[:3]

    def rec_popularity(client):
        return top3(pop, hist[client])

    def rec_copurchase(client):
        scores = collections.Counter()
        for a in hist[client]:
            scores.update(co[a])
        return top3(scores, hist[client])

    def evaluate(method, clients):
        p = sum(len(set(method(c)) & (test[c] - hist[c])) / 3 for c in clients) / len(clients)
        fill = sum(1 for c in clients if method(c)) / len(clients)
        return round(100 * p, 2), round(100 * fill, 1)

    def hit_split(method, clients):
        variant = new = 0
        for c in clients:
            own = {pgroup[i] for i in hist[c]}
            for i in set(method(c)) & (test[c] - hist[c]):
                if pgroup[i] in own:
                    variant += 1
                else:
                    new += 1
        total = variant + new
        return {'hits': total, 'variant_share_%': round(100 * variant / total, 1) if total else None}

    A = sorted(c for c in test if c in hist and test[c] - hist[c])
    B = [c for c in A if len(hist[c]) == 1]
    # ---- конец заимствованного; дальше — наши ответы и проверка значимости ----

    with ANSWERS.open(encoding="utf-8") as f:
        ours_map = {r["client_id"]: [r[k] for k in ("item_1", "item_2", "item_3") if r.get(k)] for r in csv.DictReader(f, delimiter=";")}

    def rec_ours(client):
        return ours_map.get(client, [])

    methods = {"наша система": rec_ours, "популярность": rec_popularity, "совместные покупки": rec_copurchase}
    hits = lambda m, c: len(set(m(c)) & (test[c] - hist[c]))  # noqa: E731
    rnd = random.Random(SEED)
    report = {"dataset": DATASET.name, "dataset_sha256": sha256(DATASET), "answers": ANSWERS.name,
              "answers_sha256": sha256(ANSWERS), "answers_clients": len(ours_map),
              "groups": {"A": len(A), "B": len(B)}, "segments": {}, "how": (
                  "Счёт — функции evaluate и hit_split из baseline.py организаторов без изменений, на их датасете; "
                  "к их двум способам добавлены наши тройки из файла ответов. Значимость — по клиентам: бутстреп "
                  f"{BOOT} повторов для разницы Precision@3 и двусторонний знаковый тест (сколько клиентов наш набор "
                  "угадал лучше, сколько — хуже).")}
    print(f"датасет {DATASET.name} sha256 {report['dataset_sha256'][:16]}…; ответы {ANSWERS.name} sha256 {report['answers_sha256'][:16]}…")
    for name, cs in (("A", A), ("B", B)):
        for seg in ("all", "B2B", "B2C"):
            x = [c for c in cs if seg == "all" or group[c] == seg]
            row = {"clients": len(x)}
            for m, fn in methods.items():
                p3, fill = evaluate(fn, x)
                row[m] = {"p3": p3, "fill": fill, **hit_split(fn, x)}
            for base in ("популярность", "совместные покупки"):
                d = [hits(rec_ours, c) - hits(methods[base], c) for c in x]
                mean = sum(d) / len(d) / 3
                boot = sorted(sum(d[rnd.randrange(len(d))] for _ in d) / len(d) / 3 for _ in range(BOOT))
                wins, losses = sum(v > 0 for v in d), sum(v < 0 for v in d)
                row[f"против: {base}"] = {"diff_pp": round(100 * mean, 2),
                                          "ci95_pp": [round(100 * boot[int(0.025 * BOOT)], 2), round(100 * boot[int(0.975 * BOOT) - 1], 2)],
                                          "clients_better": wins, "clients_worse": losses, "p_sign": sign_test(wins, losses)}
            report["segments"][f"{name} {seg}"] = row
            o, pp, cc = row["наша система"], row["популярность"], row["совместные покупки"]
            vp, vc = row["против: популярность"], row["против: совместные покупки"]
            print(f"{name} {seg:3} {len(x):3} | наша {o['p3']:5.2f} (fill {o['fill']}, вариантов {o['variant_share_%']} %) | "
                  f"популярность {pp['p3']:5.2f} | совместные {cc['p3']:5.2f} | разница с лучшим: "
                  f"+{min(vp['diff_pp'], vc['diff_pp']):.2f} п.п., клиентов лучше/хуже {vc['clients_better']}/{vc['clients_worse']}, "
                  f"p {max(vp['p_sign'], vc['p_sign']):.1e}")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
