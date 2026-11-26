"""Что будет, если убрать признаки срока и вида лицензии. Ответ на вопрос «нет ли утечки через срок».

Срок и вид лицензии частично берутся из текущего каталога магазина (снимков каталога на дату разреза
нет). Чтобы показать, что результат держится не на них, модель переобучается без этих признаков —
тем же способом и на тех же копиях протокола, что и основная (режим «полная модель» из recsys.eval.value).

Запуск:  python -m recsys.rank.ablation
Результат: reports/ablation_term.json (только сводные числа)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from types import SimpleNamespace

from recsys.config import DATASET, ROOT
from recsys.eval.value import Prep, by_segment, ci, paired
from recsys.rank.breakdown import breakdown
from recsys.rank.data import OFFICIAL, load_task, make_fold
from recsys.rank.features import FEATURES, FoldStats, build_attrs
from recsys.rank.ltr import copies, fit
from recsys.stable import rerun_with_fixed_order

OUT = ROOT / "reports" / "ablation_term.json"
VARIANTS = {
    "полная модель": [],
    "без срока и вида лицензии": ["kind_b", "kind_a", "kind_tr", "term_same", "term_b"],
    "без всех признаков лицензии": ["kind_b", "kind_a", "kind_tr", "term_same", "term_b",
                                    "band_rel", "band_ratio", "period_delta", "version_delta"],
}
SEGS = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"]


def main() -> None:
    rerun_with_fixed_order("recsys.rank.ablation")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    attrs = build_attrs(task)
    train = [Prep(FoldStats(task, make_fold(task, *s), attrs), "full") for s in copies(date.fromisoformat(OFFICIAL[1]))]
    test = Prep(FoldStats(task, make_fold(task, *OFFICIAL), attrs), "full")
    fold = test.fold

    report = {"dataset": DATASET.name, "variants": {}, "dropped": VARIANTS,
              "note": "та же модель и те же копии протокола, что в режиме «полная модель»; признаки обнулены и при обучении, и при проверке"}
    values = {}
    print(f"{'':32}" + "".join(f"{s:>9}" for s in SEGS))
    for name, drop in VARIANTS.items():
        idx = [FEATURES.index(f) for f in drop]
        parts = []
        for p in train:
            X = p.X.copy()
            X[:, idx] = 0
            parts.append(SimpleNamespace(X=X, y=p.y))
        model = fit(parts)
        Xt = test.X.copy()
        Xt[:, idx] = 0
        recs = test.recommend(model.predict_proba(Xt)[:, 1])
        seg = by_segment(task, fold, {c: len(set(recs[c]) & fold.target[c]) / 3 for c in fold.group_a})
        values[name] = seg
        bd = breakdown(task, fold, attrs, recs)
        report["variants"][name] = {
            "precision@3": {k: v["value"] for k, v in seg.items()},
            "ci": {k: ci(v["values"]) for k, v in seg.items() if k in ("A все", "A B2B", "B все", "B B2B")},
            "hits": {k: bd[k]["hits"] for k in ("A B2B", "B B2B")},
            "precision@3_parts": {k: bd[k]["precision@3_parts"] for k in ("A B2B", "B B2B")},
        }
        print(f"{name:32}" + "".join(f"{seg[s]['value']:>9.1%}" for s in SEGS), flush=True)
    report["difference_vs_full"] = {}
    print("\nразница с полной моделью, п.п. (организации / холодный старт), 95% интервал:")
    for name in list(VARIANTS)[1:]:
        d = {}
        for k in ("A B2B", "B B2B"):
            m, lo, hi = paired(values[name][k]["values"], values["полная модель"][k]["values"])
            d[k] = [m, lo, hi]
        report["difference_vs_full"][name] = d
        h = report["variants"][name]["hits"]["A B2B"]
        print(f"  {name:30} {d['A B2B'][0] * 100:+.1f} [{d['A B2B'][1] * 100:+.1f}; {d['A B2B'][2] * 100:+.1f}] / "
              f"{d['B B2B'][0] * 100:+.1f} [{d['B B2B'][1] * 100:+.1f}; {d['B B2B'][2] * 100:+.1f}]; "
              f"попадания A B2B: вариант {h['вариант']}, версия {h['версия']}, новый продукт {h['новый продукт']}")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nотчёт: {OUT}")


if __name__ == "__main__":
    main()
