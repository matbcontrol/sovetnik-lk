"""Сведение сигналов в три совета и измерение по протоколу организаторов.

Вес каждого сигнала подбирается на внутренних разрезах (внутри обучения). На официальном разрезе
веса уже не меняются — он нужен только для итоговой цифры.

Запуск:  python -m recsys.rank.blend            подбор на внутренних разрезах + итог на официальном
         python -m recsys.rank.blend --inner    только внутренние разрезы
"""
from __future__ import annotations

import argparse
import json
import random
import sys

from recsys.config import DATA
from recsys.rank.data import Fold, Task, inner, load_task, official
from recsys.rank.signals import SIGNALS, Signals

WEIGHTS_FILE = DATA / "private" / "task" / "weights.json"
GRID = [0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]


class Prepared:
    """Признаки кандидатов для всех клиентов группы A одного разреза (считаются один раз)."""

    def __init__(self, task: Task, fold: Fold):
        self.task, self.fold = task, fold
        self.sig = Signals(task, fold)
        self.feats = {c: {b: dict(v) for b, v in self.sig.features(c).items()} for c in fold.group_a}
        self.fallback = {c: self.sig.fallback(c) for c in fold.group_a}

    def recommend(self, c: str, w: dict[str, float], k: int = 3) -> tuple[list[str], int]:
        """Три товара и число советов, выведенных из связей (не из запасного списка)."""
        scored = []
        for b, fv in self.feats[c].items():
            s = sum(w.get(name, 0.0) * v for name, v in fv.items())
            if s > 0:
                scored.append((s, b))
        scored.sort(key=lambda x: (-x[0], x[1]))
        recs = [b for _, b in scored[:k]]
        derived = len(recs)
        for b in self.fallback[c]:
            if len(recs) >= k:
                break
            if b not in recs:
                recs.append(b)
        return recs, derived

    def evaluate(self, w: dict[str, float]) -> dict[str, dict]:
        fold, task = self.fold, self.task
        per = {}
        for c in fold.group_a:
            recs, derived = self.recommend(c, w)
            per[c] = (len(set(recs) & fold.target[c]) / 3, derived > 0)
        out = {}
        for gname, group in (("A", fold.group_a), ("B", fold.group_b)):
            for seg in ("все", "B2B", "B2C"):
                ids = [c for c in group if seg == "все" or task.clients[c]["client_group"] == seg]
                if ids:
                    out[f"{gname} {seg}"] = {
                        "clients": len(ids),
                        "p3": sum(per[c][0] for c in ids) / len(ids),
                        "derived": sum(per[c][1] for c in ids) / len(ids),
                        "values": [per[c][0] for c in ids],
                    }
        return out


def objective(folds: list[Prepared], w: dict[str, float]) -> float:
    """Что максимизируем при подборе: организации в основной группе и в холодном старте."""
    total = 0.0
    for p in folds:
        r = p.evaluate(w)
        total += 0.55 * r["A B2B"]["p3"] + 0.35 * r["B B2B"]["p3"] + 0.10 * r.get("A B2C", {"p3": 0})["p3"]
    return total / len(folds)


def tune(folds: list[Prepared], passes: int = 3, verbose: bool = True) -> dict[str, float]:
    w = {s: 1.0 for s in SIGNALS}
    best = objective(folds, w)
    for n in range(passes):
        changed = False
        for s in SIGNALS:
            keep = w[s]
            for g in GRID:
                if g == keep:
                    continue
                w[s] = g
                val = objective(folds, w)
                if val > best + 1e-6:
                    best, keep, changed = val, g, True
            w[s] = keep
        if verbose:
            print(f"  проход {n + 1}: цель {best:.4f}, веса {w}")
        if not changed:
            break
    return w


def ci(values: list[float], n: int = 2000, seed: int = 3) -> tuple[float, float]:
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choices(values, k=len(values))) / len(values) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


COLS = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"]


def line(name: str, res: dict[str, dict]) -> str:
    return f"{name:30}" + "".join(f"{res[c]['p3']:>9.1%}" if c in res else f"{'—':>9}" for c in COLS)


def header() -> str:
    return f"{'':30}" + "".join(f"{c:>9}" for c in COLS)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--inner", action="store_true", help="не трогать официальный разрез")
    args = ap.parse_args()

    task = load_task()
    folds = [Prepared(task, f) for f in inner(task)]
    print("внутренние разрезы:", ", ".join(f"{p.fold.name}: A {len(p.fold.group_a)}" for p in folds))

    print("\nподбор весов на внутренних разрезах 1 и 2 (в третьем меньше года истории):")
    w = tune(folds[:2])
    WEIGHTS_FILE.write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")

    singles = {"только популярное": {"pop": 1.0}, "только «потом брали»": {"pair": 1.0, "pop": 1e-6},
               "только линейка": {"line": 1.0, "pop": 1e-6}}
    for p in folds:
        print(f"\n{p.fold.name} (обучение до {p.fold.train_until})")
        print(header())
        for name, ww in singles.items():
            print(line(name, p.evaluate(ww)))
        print(line("все сигналы, подобранные веса", p.evaluate(w)))

    if args.inner:
        return
    off = Prepared(task, official(task))
    print(f"\nОФИЦИАЛЬНЫЙ РАЗРЕЗ (веса уже не меняются): группа A {len(off.fold.group_a)}, B {len(off.fold.group_b)}")
    print(header())
    for name, ww in singles.items():
        print(line(name, off.evaluate(ww)))
    res = off.evaluate(w)
    print(line("все сигналы, подобранные веса", res))
    print("\n95% интервалы и доля клиентов с советом из связей:")
    for c in COLS:
        if c in res:
            lo, hi = ci(res[c]["values"])
            print(f"  {c:6} клиентов {res[c]['clients']:>4}: Precision@3 {res[c]['p3']:.1%} [{lo:.1%} – {hi:.1%}], "
                  f"совет из связей у {res[c]['derived']:.0%}")
    print("\nвклад сигналов на официальном разрезе (что будет, если сигнал убрать):")
    for s in SIGNALS:
        if w.get(s):
            ww = {**w, s: 0.0}
            r = off.evaluate(ww)
            print(f"  без {s:7}: A B2B {r['A B2B']['p3']:.1%} ({r['A B2B']['p3'] - res['A B2B']['p3']:+.1%}), "
                  f"B B2B {r['B B2B']['p3']:.1%} ({r['B B2B']['p3'] - res['B B2B']['p3']:+.1%})")
    print("\nу организаторов: популярность A 5.2 / 3.6 / 19.4, B 7.0 / 5.1 / 19.2; совм. покупки A 4.8 / 3.9 / 13.0, B 5.1 / 3.8 / 12.8")


if __name__ == "__main__":
    main()
