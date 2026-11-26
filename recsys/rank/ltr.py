"""Обучаемый выбор трёх товаров по протоколу организаторов.

Модель (градиентный бустинг по признакам пары «клиент — кандидат») учится на копиях протокола,
сдвинутых в прошлое: дата разреза двигается с шагом четыре недели, окно проверки — те же 12 недель.
Для каждой даты признаки считаются только по данным до неё, метка — купил ли клиент этот новый
товар в окне. Официальный проверочный период в обучении не участвует никак: берутся только копии,
у которых окно закончилось не позже конца официального обучения.

Запуск:  python -m recsys.rank.ltr              внутренняя проверка + итог на официальном разрезе
         python -m recsys.rank.ltr --inner      только внутренняя проверка
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import sys
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from recsys.config import DATASET, ROOT, TASK_DIR
from recsys.eval import official as org
from recsys.rank.breakdown import breakdown, calibration, text
from recsys.rank.data import OFFICIAL, Fold, Task, load_task, make_fold
from recsys.rank.features import FEATURES, FoldStats, build_attrs
from recsys.stable import rerun_with_fixed_order

MODEL_FILE = TASK_DIR / "ltr_model.pkl"
BREAKDOWN_FILE = ROOT / "reports" / "hits_breakdown.json"
WINDOW_WEEKS = 12
FIRST_CUTOFF = date(2025, 8, 4)          # раньше в данных слишком мало истории
COLS = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"]


def copies(limit: date, step_weeks: int = 4) -> list[tuple[str, str, str, str]]:
    """Копии протокола, окно проверки которых заканчивается не позже limit."""
    out, t = [], FIRST_CUTOFF
    while t + timedelta(weeks=WINDOW_WEEKS) <= limit:
        out.append((f"копия {t}", t.isoformat(), (t + timedelta(weeks=1)).isoformat(),
                    (t + timedelta(weeks=WINDOW_WEEKS)).isoformat()))
        t += timedelta(weeks=step_weeks)
    return out


class Prepared:
    def __init__(self, task: Task, fold: Fold, attrs):
        self.task, self.fold = task, fold
        self.stats = FoldStats(task, fold, attrs)
        self.clients, self.items, X, y = [], [], [], []
        self.fallback = {}
        for c in fold.group_a:
            rows = self.stats.rows(c)
            self.fallback[c] = self.stats.fallback(c)
            tgt = fold.target[c]
            for b, vec in rows.items():
                self.clients.append(c)
                self.items.append(b)
                X.append(vec)
                y.append(1 if b in tgt else 0)
        self.X = np.array(X, dtype=np.float32) if X else np.zeros((0, len(FEATURES)), dtype=np.float32)
        self.y = np.array(y, dtype=np.int8)

    def evaluate(self, scores: np.ndarray) -> dict[str, dict]:
        fold, task = self.fold, self.task
        by_client: dict[str, list[tuple[float, str]]] = {}
        for c, b, s in zip(self.clients, self.items, scores):
            by_client.setdefault(c, []).append((float(s), b))
        per = {}
        self.recs = {}
        for c in fold.group_a:
            ranked = [b for _, b in sorted(by_client.get(c, []), key=lambda x: (-x[0], x[1]))][:3]
            derived = len(ranked)
            for b in self.fallback[c]:
                if len(ranked) >= 3:
                    break
                if b not in ranked:
                    ranked.append(b)
            self.recs[c] = ranked
            per[c] = (len(set(ranked) & fold.target[c]) / 3, derived > 0)
        out = {}
        for gname, group in (("A", fold.group_a), ("B", fold.group_b)):
            for seg in ("все", "B2B", "B2C"):
                ids = [c for c in group if seg == "все" or task.clients[c]["client_group"] == seg]
                if ids:
                    out[f"{gname} {seg}"] = {"clients": len(ids), "p3": sum(per[c][0] for c in ids) / len(ids),
                                             "derived": sum(per[c][1] for c in ids) / len(ids),
                                             "values": [per[c][0] for c in ids]}
        return out


def fit(parts: list[Prepared], seed: int = 0) -> HistGradientBoostingClassifier:
    X = np.vstack([p.X for p in parts])
    y = np.concatenate([p.y for p in parts])
    model = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=300, max_leaf_nodes=12, min_samples_leaf=60,
        l2_regularization=2.0, max_bins=64, random_state=seed)
    model.fit(X, y)
    return model


def line(name: str, res: dict[str, dict]) -> str:
    return f"{name:34}" + "".join(f"{res[c]['p3']:>9.1%}" if c in res else f"{'—':>9}" for c in COLS)


def header() -> str:
    return f"{'':34}" + "".join(f"{c:>9}" for c in COLS)


def ci(values: list[float], n: int = 2000, seed: int = 3) -> tuple[float, float]:
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choices(values, k=len(values))) / len(values) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def single(p: Prepared, feature: str) -> dict[str, dict]:
    """Проверка одного признака как самостоятельного способа."""
    return p.evaluate(p.X[:, FEATURES.index(feature)] + 1e-9 * p.X[:, FEATURES.index("pop_follow")])


def main() -> None:
    rerun_with_fixed_order("recsys.rank.ltr")           # одно и то же число при каждом запуске
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--inner", action="store_true", help="не трогать официальный разрез")
    args = ap.parse_args()

    task = load_task()
    attrs = build_attrs(task)
    print(f"датасет: {DATASET.name}, товаров {len(task.names)}")
    cache: dict[str, Prepared] = {}

    def prep(spec) -> Prepared:
        if spec[1] not in cache:
            cache[spec[1]] = Prepared(task, make_fold(task, *spec), attrs)
        return cache[spec[1]]

    # ---- внутренняя проверка: учимся на копиях до 2026-06-01, проверяем на окне июнь–август
    v_spec = ("внутренняя проверка", "2026-06-01", "2026-06-08", "2026-08-31")
    v_train = [prep(s) for s in copies(date(2026, 6, 1))]
    val = prep(v_spec)
    print(f"копий протокола для обучения: {len(v_train)}, строк {sum(len(p.y) for p in v_train)}, "
          f"из них покупок {int(sum(p.y.sum() for p in v_train))}")
    model_v = fit(v_train)
    print(f"\nВНУТРЕННЯЯ ПРОВЕРКА (обучение до {val.fold.train_until}, окно июнь–август): "
          f"A {len(val.fold.group_a)}, B {len(val.fold.group_b)}")
    print(header())
    print(line("популярные докупки", single(val, "pop_follow")))
    print(line("«потом брали» по товару", single(val, "pair_sum")))
    print(line("«потом брали» по семейству", single(val, "fam_sum")))
    res_v = val.evaluate(model_v.predict_proba(val.X)[:, 1])
    print(line("обученное ранжирование", res_v))
    cand = {c for c, yv in zip(val.clients, val.y) if yv}
    print(f"цель есть среди кандидатов у {len(cand) / len(val.fold.group_a):.0%} клиентов; "
          f"кандидатов на клиента в среднем {len(val.y) / len(val.fold.group_a):.0f}")
    if args.inner:
        return

    # ---- итог: учимся на всех копиях внутри официального обучения, веса больше не трогаем
    off_spec = OFFICIAL
    o_train = [prep(s) for s in copies(date(2026, 8, 31))]
    off = prep(off_spec)
    model = fit(o_train)
    MODEL_FILE.write_bytes(pickle.dumps({"model": model, "features": FEATURES}))
    print(f"\nОФИЦИАЛЬНЫЙ РАЗРЕЗ: копий для обучения {len(o_train)}, группа A {len(off.fold.group_a)}, B {len(off.fold.group_b)}")
    print(header())
    print(line("популярные докупки", single(off, "pop_follow")))
    print(line("«потом брали» по товару", single(off, "pair_sum")))
    res = off.evaluate(model.predict_proba(off.X)[:, 1])
    print(line("обученное ранжирование", res))
    print("\n95% интервалы:")
    for c in COLS:
        if c in res:
            lo, hi = ci(res[c]["values"])
            print(f"  {c:6} клиентов {res[c]['clients']:>4}: Precision@3 {res[c]['p3']:.1%} [{lo:.1%} – {hi:.1%}]")
    # ---- база организаторов на тех же клиентах и разбивка попаданий по видам
    ot = org.load()
    base = {"популярность": org.popularity(ot), "совместные покупки": org.co_purchase(ot)}
    report = {
        "dataset": DATASET.name, "split": {"train_until": OFFICIAL[1], "test_from": OFFICIAL[2]},
        "kinds": {"вариант": "другой артикул продукта, который у клиента уже есть (то же семейство)",
                  "версия": "тот же продукт в другой версии, редакции или комплекте: та же линейка по названию "
                            "либо название близко к купленному (и производитель не другой)",
                  "новый продукт": "ничего похожего у клиента не было"},
        "name_similarity": calibration(attrs),
        "methods": {},
    }
    print("\nиз чего состоят попадания:")
    for name, rec in base.items():
        res_b = breakdown(task, off.fold, attrs, {c: rec(c) for c in off.fold.group_a})
        report["methods"][name] = res_b
        print(" ", text(name, res_b, "A B2B"))
    ours = breakdown(task, off.fold, attrs, off.recs)
    report["methods"]["обученное ранжирование"] = ours
    for key in ("A B2B", "B B2B", "A B2C"):
        print(" ", text("обученное ранжирование", ours, key))
    a = ours["A все"]
    print(f"\nцели группы A: вариант {a['targets']['вариант']}, версия или редакция {a['targets']['версия']}, "
          f"новый продукт {a['targets']['новый продукт']}; холодных товаров (не продавались в обучении) "
          f"{a['cold_targets']} ({a['cold_target_share']:.1%} целей, у {a['clients_with_cold_target']} клиентов)")
    report["future_information"] = [
        "семейство и класс товара берутся из текущего фида магазина (снимка каталога на дату разреза нет)",
        "вид лицензии и срок частично берутся из параметров текущего фида",
        "привязка товара продаж к каталогу сделана по текущему фиду",
        "профиль клиента (группа, тип, отрасль, сегмент) взят из листа «Клиенты» как есть: это состояние "
        "на дату выгрузки, а не на дату разреза; по нему считаются признаки клиента и докупки «таких же» клиентов",
    ]
    BREAKDOWN_FILE.parent.mkdir(exist_ok=True)
    BREAKDOWN_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {BREAKDOWN_FILE}")
    print("\nбаза организаторов (v4), Precision@3 все / B2B / B2C: популярность A 5.44 / 3.75 / 20.0, "
          "B 7.36 / 5.22 / 20.0; совместные покупки A 5.64 / 4.53 / 15.24, B 6.01 / 4.54 / 14.67")


if __name__ == "__main__":
    main()
