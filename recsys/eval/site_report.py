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


def honest(title: str) -> str:
    """Режим проверки прячет историю самого товара, а соседние артикулы линейки и каталог остаются:
    «новичком» это называть нельзя. Отчёты, посчитанные до переименования, показываются с новым названием."""
    return title.replace("товар-новичок", "товар без своей истории").replace("семейство-новичок", "семейство без своей истории")


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
        "regimes": {k: {"title": honest(r["title"]), "p3": r["precision@3"], "ci": r["ci"]} for k, r in v["regimes"].items()},
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
            "cost": ("Личных советов стало меньше ({} вместо {}), а сбываются они чаще: {} % против {} %."
                     if stand["доля сбывшихся новых советов"] > first["доля сбывшихся новых советов"] else
                     "Личных советов стало меньше ({} вместо {}), доля сбывшихся почти та же: {} % против {} %.").format(
                stand["новых советов"], first["новых советов"],
                f"{stand['доля сбывшихся новых советов'] * 100:.1f}".replace(".", ","),
                f"{first['доля сбывшихся новых советов'] * 100:.1f}".replace(".", ",")),
        }
        # Тройка протокола и блок, который видит клиент, — рядом: сравнение с baseline считается по первой,
        # а продукт — второй. Состав тройки (продления, варианты) — из recsys.rank.composition.
        comp = ROOT / "reports" / "top3_composition.json"
        if comp.exists():
            p3 = json.loads(comp.read_text(encoding="utf-8"))["precision@3"]

            def triple(name: str) -> dict:
                ours, base = p3[name]["наша модель"][seg], p3[name]["популярность"][seg]
                return {"p3": ours["value"], "hits": ours["hits"], "baseline": base["value"]}

            out["shown"] = {
                "clients": stand["клиентов"], "protocol": triple("как в протоколе"),
                "no_renewals": triple("без продлений купленного"),
                "block": {"cards": stand["новых советов"], "hits": stand["новых сбылось"],
                          "kit_cards": stand.get("вид: kit", 0), "kit_hits": stand.get("сбылось: kit", 0)},
                "renew": {"cards": stand["продлений"], "hits": stand["продлений сбылось"]},
                "popular": {"cards": stand["строк «часто докупают»"], "hits": stand["«часто докупают» сбылось"]},
                "any_hit": stand["клиентов, у кого сбылась хоть одна карточка"],
            }
    p = ROOT / "reports" / "block_fill.json"
    if p.exists():                                       # показ блока в единицах заказчика (recsys.rank.fill)
        f = json.loads(p.read_text(encoding="utf-8"))

        def group(name: str, groups: dict | None = None) -> dict:
            g = (groups or f["groups"])[name]
            n = g["clients"]
            return {"clients": n, "own": g["share_with_own_ground_advice"], "personal": g["share_with_any_personal_advice"],
                    "kit_only": g["by_strongest_advice"]["kit"] / n if n else 0.0}

        out["block_fill"] = {"today": f["today"], "now": f["current_block_fill_by_visits"],
                             "b2b": group("с доступом в кабинет: B2B"), "b2c": group("с доступом в кабинет: B2C")}
        before = f.get("before_holes")                   # тот же расчёт без связей агента: что дал агент, закрывая дыры
        if isinstance(before, dict) and "с доступом в кабинет: B2B" in before:
            out["block_fill"]["before_holes"] = {"b2b": group("с доступом в кабинет: B2B", before),
                                                 "b2c": group("с доступом в кабинет: B2C", before)}
            out["block_fill"]["agent_links"] = f.get("agent_links_in_verdict_table", 0)
    p = ROOT / "reports" / "holes.json"
    if p.exists():                                       # сводка очереди дыр: только числа, без товаров
        h = json.loads(p.read_text(encoding="utf-8"))
        kinds = h.get("by_kind", {})
        out["holes"] = {"today": h.get("today"), "holes": h.get("holes"),
                        "without_advice": h.get("clients_without_own_ground_advice"),
                        "agent": kinds.get("закрывает агент"), "renew": kinds.get("нужно продление, а не совет")}
    p = ROOT / "reports" / "restore.json"
    if p.exists():                                       # тест «спрятали — вернул» (recsys.rank.restore): только сводные числа
        r = json.loads(p.read_text(encoding="utf-8"))
        queue = {k: v for k, v in (r.get("queue") or {}).items() if k != "largest_product_clients"}   # число по одному товару наружу не идёт
        keep = ("products", "products_with_agent_link", "owners_with_access", "clients_with_access", "with_own_ground_advice",
                "lost_when_hidden", "returned_by_agent", "share_returned", "returned_with_manager", "share_returned_with_manager",
                "lost_share_of_all_clients", "returned_share_of_all_clients", "agent_links", "agent_links_confirmed",
                "agent_links_to_products_outside_graph", "pairs_vs_labelling", "need_pairs_hidden", "need_pairs_found_by_agent",
                "need_pairs_proposed_by_agent", "need_pairs_hidden_in_catalog", "share_of_hidden_need_found", "agent_run")

        def measure(name: str) -> dict:
            m = r.get(name) or {}
            return {k: m[k] for k in keep if k in m}

        out["restore"] = {"today": r.get("today"), "clients_with_access": r.get("clients_with_access"),
                          "clients_by_state": r.get("clients_by_state") or {}, "queue": queue,
                          "queue_wide": r.get("queue_wide") or {},
                          "measured": measure("measured"),           # товары, на которых держится больше всего клиентов
                          "measured_wide": measure("measured_wide"),   # широкая выборка, которую никто не разглядывал
                          "measured_wide_thinking": measure("measured_wide_thinking")}   # она же с рассуждением модели
    p = ROOT / "reports" / "coverage.json"
    if p.exists():                                       # у скольких товаров в продаже есть личный совет
        out["coverage"] = json.loads(p.read_text(encoding="utf-8"))
    path = ROOT / "site" / "data" / "report.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", path)


if __name__ == "__main__":
    main()
