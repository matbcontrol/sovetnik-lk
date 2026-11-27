"""Очередь «дыр»: за какими купленными товарами стоят клиенты кабинета без обоснованного совета.

Дыра — линейка товара, владельцы которой не получают в блоке ни «нужно», ни связи графа (новая версия,
старшая редакция, замена), ни другого варианта своей линейки (см. recsys.rank.fill). Очередь нужна агенту
каталога: закрыв дыру — найдя для товара проверенную связь, — он возвращает блок всем её владельцам.
Дыры идут по убыванию числа клиентов кабинета за ними.

Не всякая дыра закрывается связью. Для каждой считается, что её владельцы делали дальше (по всем продажам):
  rebuy — доля купивших ту же линейку ещё раз в другую неделю (подписку продлевают тем же товаром);
  other — доля купивших потом что-то другое.
Если владельцы в основном продлевают то же самое и почти ничего другого не берут, дыра помечается
«нужно продление, а не совет»: связку здесь искать не из чего, это сценарий продлений кабинета.
Агенту такие дыры не отдаются.

Запуск:  python -m recsys.rank.holes
Нужны модели стенда: data/private/task/demo_models.pkl (их пишет python -m recsys.rank.demo).
Результат: data/private/task/holes.json — очередь с названиями товаров (названия из датасета, не публикуется);
           reports/holes.json — только сводные числа.
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import Counter, defaultdict

from recsys.config import ROOT, TASK_DIR
from recsys.rank.data import load_task, make_fold
from recsys.rank.features import FoldStats, build_attrs
from recsys.rank.fill import OWN
from recsys.rank.unified import Catalog, Unified

QUEUE = TASK_DIR / "holes.json"
REPORT = ROOT / "reports" / "holes.json"
REBUY_MIN, OTHER_MAX = 0.30, 0.10       # «нужно продление, а не совет»: продлевают часто, другое берут редко
OWNERS_MIN = 30                         # по меньшему числу владельцев о привычках линейки судить нельзя


def main() -> None:
    from recsys.llm.pair_verdicts import Verdicts
    from recsys.rank.cabinet import cabinet
    from recsys.rank.demo import CACHE, item_prices
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.holes")
    sys.stdout.reconfigure(encoding="utf-8")
    if not CACHE.exists():
        sys.exit(f"нет моделей стенда ({CACHE}): сначала python -m recsys.rank.demo")
    task = load_task()
    attrs = build_attrs(task)
    last = max(r.week for r in task.rows)
    now = make_fold(task, "сегодня", last.isoformat(), last.isoformat(), last.isoformat())
    cat = Catalog(task)
    uni = Unified(FoldStats(task, now, attrs), pickle.loads(CACHE.read_bytes())["today"]["full"], cat)
    verdicts = Verdicts.load("line")
    if not verdicts.table:
        sys.exit("таблица вердиктов по линейкам пуста: без неё блок считается иначе, чем на стенде")
    prices = item_prices(task)

    # ---- клиенты кабинета без обоснованного совета и их линейки
    behind: dict[str, Counter] = defaultdict(Counter)
    total, weak = Counter(), Counter()
    for c in sorted(now.history):
        info = task.clients.get(c, {})
        if info.get("lk_access") != "да":
            continue
        g = info.get("client_group", "")
        total[g] += 1
        cab = cabinet(uni, c, verdicts, prices)
        if {x.tier for x in cab["new"]} & set(OWN):
            continue
        weak[g] += 1
        lines = {attrs[a].line for a in now.history[c]}
        for ln in lines:
            behind[ln][g] += 1
            behind[ln]["only"] += len(lines) == 1          # дыра — единственная линейка клиента

    # ---- что владельцы линейки делали дальше: по всем продажам
    weeks: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))      # клиент -> линейка -> недели покупок
    for r in task.rows:
        weeks[r.client][attrs[r.item].line].add(r.week)
    owners, rebuy, other = Counter(), Counter(), Counter()
    for c, by in weeks.items():
        for ln, ws in by.items():
            if ln not in behind:
                continue
            owners[ln] += 1
            rebuy[ln] += len(ws) > 1
            first = min(ws)
            other[ln] += any(w > first for l2, w2 in by.items() if l2 != ln for w in w2)

    judged: dict[str, Counter] = defaultdict(Counter)
    for (a, _), v in verdicts.table.items():
        judged[a][v.verdict] += 1
    sample = {}
    for item in sorted(task.names):
        sample.setdefault(attrs[item].line, item)

    queue = []
    for ln, cnt in sorted(behind.items(), key=lambda kv: (-(kv[1]["B2B"] + kv[1]["B2C"]), kv[0])):
        n = owners[ln]
        r, o = rebuy[ln] / max(n, 1), other[ln] / max(n, 1)
        renewal = n >= OWNERS_MIN and r >= REBUY_MIN and o <= OTHER_MAX
        item = sample[ln]
        offer = cat.offer_of(item) or {}
        queue.append({
            "line": ln, "family_id": "" if ln.startswith("L:") else ln, "item": item,
            "name": task.names[item], "catalog_name": offer.get("name", ""), "url": offer.get("url", ""),
            "class_id": attrs[item].cls, "vendor": attrs[item].vendor,
            "clients": cnt["B2B"] + cnt["B2C"], "clients_b2b": cnt["B2B"], "clients_b2c": cnt["B2C"],
            "clients_only_this": cnt["only"], "owners": n, "rebuy_share": round(r, 4), "other_share": round(o, 4),
            "kind": "нужно продление, а не совет" if renewal else "закрывает агент",
            "judged": dict(judged.get(ln, {})),
        })
    QUEUE.write_text(json.dumps({"today": last.isoformat(), "holes": queue}, ensure_ascii=False, indent=1), encoding="utf-8")

    def part(kind: str | None) -> dict:
        rows = [h for h in queue if kind is None or h["kind"] == kind]
        return {"holes": len(rows), "clients_b2b": sum(h["clients_b2b"] for h in rows),
                "clients_b2c": sum(h["clients_b2c"] for h in rows)}

    top = queue[0] if queue else {}
    n_weak = sum(weak.values())
    report = {
        "today": last.isoformat(), "clients_with_access": dict(total),
        "clients_without_own_ground_advice": dict(weak),
        "holes": len(queue),
        "by_kind": {"закрывает агент": part("закрывает агент"),
                    "нужно продление, а не совет": part("нужно продление, а не совет")},
        "largest_hole": {"clients": top.get("clients", 0), "share_of_clients_without_advice": top.get("clients", 0) / max(n_weak, 1),
                         "kind": top.get("kind", ""), "owners": top.get("owners", 0),
                         "rebuy_share": top.get("rebuy_share", 0), "other_share": top.get("other_share", 0)},
        "rule": f"«нужно продление, а не совет»: владельцев не меньше {OWNERS_MIN}, ту же линейку купили снова "
                f"не меньше {REBUY_MIN:.0%}, что-то другое потом — не больше {OTHER_MAX:.0%}",
        "note": "клиент с несколькими линейками засчитан в каждую; суммы по дырам больше числа клиентов",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"клиентов кабинета: {dict(total)}; без обоснованного совета: {dict(weak)}; дыр {len(queue)}")
    for kind, p in report["by_kind"].items():
        print(f"  {kind}: дыр {p['holes']}, за ними организаций {p['clients_b2b']}, частных лиц {p['clients_b2c']}")
    big = report["largest_hole"]
    print(f"  самая большая дыра: {big['clients']} клиентов ({big['share_of_clients_without_advice']:.0%} всех без совета), "
          f"вид «{big['kind']}»; из {big['owners']} владельцев ту же линейку купили снова {big['rebuy_share']:.1%}, "
          f"что-то другое — {big['other_share']:.1%}")
    print(f"очередь: {QUEUE}\nотчёт: {REPORT}")


if __name__ == "__main__":
    main()
