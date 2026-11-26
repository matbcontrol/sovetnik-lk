"""Сводка замеров для вкладки «Цифры» стенда: reports/value.json -> site/data/report.json.

В сводке только итоговые числа по группам. Строк продаж и клиентов в ней нет.

Запуск:  python -m recsys.eval.site_report      (после python -m recsys.eval.value)
"""
from __future__ import annotations

import json

from recsys.config import ROOT

# Базовые способы организаторов на датасете v4 (их baseline.py), Precision@3. Запасной вариант:
# если рядом лежит reports/hits_breakdown.json, цифры берутся из него — там они пересчитаны нашим кодом
# и сверены с организаторами (python -m recsys.eval.official).
ORGANISERS = {
    "популярность": {"A все": 0.0544, "A B2B": 0.0375, "A B2C": 0.200, "B все": 0.0736, "B B2B": 0.0522, "B B2C": 0.200},
    "совместные покупки": {"A все": 0.0564, "A B2B": 0.0453, "A B2C": 0.1524, "B все": 0.0601, "B B2B": 0.0454, "B B2C": 0.1467},
}
CURRENT_FILL = 0.556       # доля визитов на главную кабинета с показанным блоком, 21.10–22.11.2026
SEGMENTS = ("A B2B", "B B2B")


def part(row: dict) -> dict:
    """Строка разбивки для стенда: попадания и слагаемые Precision@3 по видам целей."""
    return {"clients": row["clients"], "targets": row["targets"], "hits": row["hits"],
            "p3": row["precision@3"], "parts": row["precision@3_parts"]}


def main() -> None:
    v = json.loads((ROOT / "reports" / "value.json").read_text(encoding="utf-8"))
    organisers = {k: dict(seg) for k, seg in ORGANISERS.items()}
    breakdown, future, dataset = {}, [], ""
    p = ROOT / "reports" / "hits_breakdown.json"
    if p.exists():
        b = json.loads(p.read_text(encoding="utf-8"))
        future, dataset = b.get("future_information", []), b.get("dataset", "")
        for method in ORGANISERS:
            rows = b.get("methods", {}).get(method, {})
            for seg, row in rows.items():
                organisers[method][seg] = row["precision@3"]
            breakdown[method] = {seg: part(rows[seg]) for seg in SEGMENTS if seg in rows}
    rule = v.get("baseline_same_line") or {}
    if rule.get("hits_breakdown"):                       # сильный базовый способ без обучения
        breakdown["та же линейка"] = {seg: part(rule["hits_breakdown"][seg]) for seg in SEGMENTS
                                      if seg in rule["hits_breakdown"]}
    ours = v.get("hits_breakdown") or {}
    if ours:                                             # разбивка главного режима — из того же расчёта, что и заголовок
        breakdown["наша модель"] = {seg: part(ours[seg]) for seg in SEGMENTS if seg in ours}
    out = {
        "protocol": v["protocol"], "dataset": dataset, "organisers": organisers, "current_fill": CURRENT_FILL,
        "regimes": {k: {"title": r["title"], "p3": r["precision@3"], "ci": r["ci"]} for k, r in v["regimes"].items()},
        "differences": v["differences"], "fill": v["fill"], "fill_all": v["fill_all"],
        "catalog": v["catalog"], "money": v["money"],
        "breakdown": breakdown, "future_information": future,
        "same_line": {"title": rule.get("title", ""), "p3": rule.get("precision@3", {}), "ci": rule.get("ci", {}),
                      "model_minus_rule": rule.get("model_minus_rule")} if rule else None,
    }
    p = ROOT / "reports" / "verdict_effect.json"
    if p.exists():                                       # что проверка пар меняет в блоке кабинета (только сводные числа)
        e = json.loads(p.read_text(encoding="utf-8"))
        back = {"нужно": "need", "комплект": "kit", "уже есть": "has", "то же самое": "same", "не связано": "unrelated"}
        rows, seg = [], "A B2B"
        for title, m in e["variants"].items():
            r = m[seg]
            rows.append({"title": title[:1].upper() + title[1:], "cards": r["новых советов"], "hits": r["новых сбылось"],
                         "share": r["доля сбывшихся новых советов"], "top_share": r["самый частый товар: доля клиентов"],
                         "without": r["клиентов без новых советов"], "stand": title == e.get("stand")})
        first, stand = e["variants"]["без проверки пар"][seg], e["variants"][e["stand"]][seg]
        out["verdict_effect"] = {
            "verdicts": {back[k]: n for k, n in e["verdicts"].items()}, "clients": first["клиентов"], "rows": rows,
            "top_before": first["самые частые товары"][0][0],
            "cost": "Личных советов стало меньше ({} вместо {}), а сбываются они чаще: {} % против {} %.".format(
                stand["новых советов"], first["новых советов"],
                f"{stand['доля сбывшихся новых советов'] * 100:.1f}".replace(".", ","),
                f"{first['доля сбывшихся новых советов'] * 100:.1f}".replace(".", ",")),
        }
    path = ROOT / "site" / "data" / "report.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", path)


if __name__ == "__main__":
    main()
