"""Сводка замеров для вкладки «Цифры» стенда: reports/value.json -> site/data/report.json.

В сводке только итоговые числа по группам. Строк продаж и клиентов в ней нет.

Запуск:  python -m recsys.eval.site_report      (после python -m recsys.eval.value)
"""
from __future__ import annotations

import json

from recsys.config import ROOT

# базовые способы, посчитанные организаторами (лист «Протокол_проверки»), Precision@3
ORGANISERS = {
    "популярность": {"A все": 0.052, "A B2B": 0.036, "A B2C": 0.194, "B все": 0.070, "B B2B": 0.051, "B B2C": 0.192},
    "совместные покупки": {"A все": 0.048, "A B2B": 0.039, "A B2C": 0.130, "B все": 0.051, "B B2B": 0.038, "B B2C": 0.128},
}
CURRENT_FILL = 0.556       # доля визитов на главную кабинета с показанным блоком, 21.10–22.11.2026


def main() -> None:
    v = json.loads((ROOT / "reports" / "value.json").read_text(encoding="utf-8"))
    out = {
        "protocol": v["protocol"], "organisers": ORGANISERS, "current_fill": CURRENT_FILL,
        "regimes": {k: {"title": r["title"], "p3": r["precision@3"], "ci": r["ci"]} for k, r in v["regimes"].items()},
        "differences": v["differences"], "fill": v["fill"], "fill_all": v["fill_all"],
        "catalog": v["catalog"], "money": v["money"],
    }
    path = ROOT / "site" / "data" / "report.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", path)


if __name__ == "__main__":
    main()
