"""Сколько стоит класс «по соседям» без модели: проверка на спрятанных товарах каталога.

Агент каталога сначала пробует определить класс нового товара без модели — по самым похожим товарам
каталога (Catalog.neighbor_class). Класс берётся у соседей, только если соседи единодушны
(уверенность не ниже SURE) и похожи на товар (доля его слов по весу не ниже ALIKE). Здесь эти пороги
проверяются: товары каталога прячут по одному и смотрят, совпал ли класс соседей с рубрикой магазина.

Выборка фиксирована: товары в продаже с классом, случайные, зерно SEED. Модель не вызывается.

Запуск:  python -m recsys.agent.calibrate          ->  reports/agent_neighbors.json
"""
from __future__ import annotations

import json
import random
import sys

from recsys.agent.agent import ALIKE, SURE
from recsys.agent.tools import Catalog
from recsys.config import ROOT

N, SEED = 800, 5


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cat = Catalog()
    rnd = random.Random(SEED)
    ids = sorted(k for k, n in cat.nodes.items() if n.get("class") and n["on_sale"] == "1")
    rows = []
    for k in rnd.sample(ids, N):
        n = cat.nodes[k]
        cat.hidden = {k}
        h = cat.neighbor_class(n["name"], n["vendor"])
        rows.append((h["confidence"], h.get("similarity", 0.0), h["class_id"] == n["class"]))
    cat.hidden = set()

    def share(part: list, of: int) -> float:
        return round(len(part) / of, 3) if of else 0.0

    def right(part: list) -> float:
        return round(sum(r[2] for r in part) / len(part), 3) if part else 0.0

    used = [r for r in rows if r[0] >= SURE and r[1] >= ALIKE]          # условие, при котором класс берётся без модели
    unanimous = [r for r in rows if r[0] >= SURE]
    report = {
        "items": N, "seed": SEED, "sure": SURE, "alike": ALIKE,
        "any_neighbors": {"share": share([r for r in rows if r[0] > 0], N), "correct": right([r for r in rows if r[0] > 0])},
        "unanimous": {"share": share(unanimous, N), "correct": right(unanimous)},
        "used_without_model": {"share": share(used, N), "correct": right(used)},
        "left_to_model": {"share": share([r for r in rows if not (r[0] >= SURE and r[1] >= ALIKE)], N)},
    }
    out = ROOT / "reports" / "agent_neighbors.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    u = report["used_without_model"]
    print(f"спрятано {N} товаров в продаже (зерно {SEED}). Класс берётся без модели у {u['share']:.0%} товаров, "
          f"верен в {u['correct']:.1%}; соседи единодушны у {report['unanimous']['share']:.0%}, "
          f"верен в {report['unanimous']['correct']:.1%}. Остальное решает модель. -> {out}")


if __name__ == "__main__":
    main()
