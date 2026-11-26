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
import pickle
import random
import sys
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from recsys.config import DATA
from recsys.rank.data import OFFICIAL, Fold, Task, load_task, make_fold
from recsys.rank.features import FEATURES, FoldStats, build_attrs

MODEL_FILE = DATA / "private" / "task" / "ltr_model.pkl"
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
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--inner", action="store_true", help="не трогать официальный разрез")
    args = ap.parse_args()

    task = load_task()
    attrs = build_attrs(task)
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
    print("\nу организаторов: популярность A 5.2 / 3.6 / 19.4, B 7.0 / 5.1 / 19.2; совм. покупки A 4.8 / 3.9 / 13.0, B 5.1 / 3.8 / 12.8")


if __name__ == "__main__":
    main()
