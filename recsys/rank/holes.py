"""Очередь «дыр»: за какими купленными товарами стоят клиенты кабинета без обоснованного совета.

Дыра — линейка товара, владельцы которой не получают в блоке ни «нужно», ни связи графа (новая версия,
старшая редакция, замена), ни другого варианта своей линейки (см. recsys.rank.fill). Очередь нужна агенту
каталога: закрыв дыру — найдя для товара проверенную связь, — он возвращает блок её владельцам.

Не всякая дыра закрывается связью, поэтому у каждой есть вид:
  закрывает агент            — товар есть в открытом каталоге: агенту есть что читать и куда писать связь;
  нет в каталоге             — товара в каталоге нет (облачная подписка, снят с продажи): название известно
                               только из датасета, по API агенту оно не передаётся; такие ждут менеджера;
  нужно продление, а не совет — владельцы в основном покупают ту же линейку снова и почти ничего другого
                               не берут (rebuy_share, other_share считаются по всем продажам): связку здесь
                               искать не из чего, это сценарий продлений кабинета.
Сначала идут дыры для агента по убыванию числа клиентов кабинета за ними, потом остальные. Если есть файл
data/interim/hole_catalog_advice.json (что каталожный движок даёт владельцу одного этого товара), внутри
дыр для агента первыми идут «пусто», затем «только комплект», последними «совет есть»: в последних связка
в каталоге уже есть, а дыра оттого, что клиент советуемое купил или его нет в продажах.

«Закрыто». Если в таблице вердиктов есть связи агента каталога (источник «агент каталога…»), очередь
считается дважды — без них и с ними: дыра, которая была и исчезла, закрыта агентом.
Число клиентов за дырой — верхняя оценка: связь ещё должна пройти вердикт и запреты блока. Настоящий
прирост показа считает python -m recsys.rank.fill.

Запуск:  python -m recsys.rank.holes
Результат: data/private/task/holes.json — очередь с названиями товаров (названия из датасета, не публикуется);
           reports/holes.json — только сводные числа, без разбивки по товарам.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

from recsys.config import INTERIM, ROOT, TASK_DIR
from recsys.rank.fill import OWN, stand_state, without_agent

QUEUE = TASK_DIR / "holes.json"
REPORT = ROOT / "reports" / "holes.json"
AGENT_KIND, NO_CATALOG, RENEWAL = "закрывает агент", "нет в каталоге", "нужно продление, а не совет"
KIND_ORDER = (AGENT_KIND, NO_CATALOG, RENEWAL)
# Что каталожный движок даёт владельцу одного этого товара (выгружает основная сессия, файл необязателен):
# {family_id: "пусто" | "только комплект" | "совет есть"}. Агента ведём с пустых: там связке есть куда встать.
CATALOG_ADVICE = INTERIM / "hole_catalog_advice.json"
ADVICE_ORDER = ("пусто", "только комплект", "", "совет есть")
REBUY_MIN, OTHER_MAX = 0.30, 0.10       # «нужно продление, а не совет»: продлевают часто, другое берут редко
OWNERS_MIN = 30                         # по меньшему числу владельцев о привычках линейки судить нельзя


def weak_lines(uni, attrs: dict, verdicts, prices: dict) -> tuple[dict, Counter, Counter]:
    """Линейки клиентов кабинета без обоснованного совета: линейка -> счётчики; всего клиентов; без совета."""
    from recsys.rank.cabinet import cabinet

    task, now = uni.task, uni.fold
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
    return behind, total, weak


def main() -> None:
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.holes")
    sys.stdout.reconfigure(encoding="utf-8")
    task, attrs, last, cat, uni, verdicts, prices = stand_state()
    before_table, n_agent = without_agent(verdicts)
    behind, total, weak = weak_lines(uni, attrs, verdicts, prices)
    behind_before, _, weak_before = weak_lines(uni, attrs, before_table, prices) if n_agent else (behind, total, weak)

    # ---- что владельцы линейки делали дальше: по всем продажам
    weeks: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))      # клиент -> линейка -> недели покупок
    for r in task.rows:
        weeks[r.client][attrs[r.item].line].add(r.week)
    known = set(behind) | set(behind_before)
    owners, rebuy, other = Counter(), Counter(), Counter()
    for by in weeks.values():
        for ln, ws in by.items():
            if ln not in known:
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

    advice = json.loads(CATALOG_ADVICE.read_text(encoding="utf-8")) if CATALOG_ADVICE.exists() else {}

    def kind_of(ln: str) -> str:
        n = owners[ln]
        if n >= OWNERS_MIN and rebuy[ln] / n >= REBUY_MIN and other[ln] / n <= OTHER_MAX:
            return RENEWAL
        return NO_CATALOG if ln.startswith("L:") else AGENT_KIND

    def row(ln: str, cnt: Counter) -> dict:
        n = max(owners[ln], 1)
        item = sample[ln]
        offer = cat.offer_of(item) or {}
        return {
            "line": ln, "family_id": "" if ln.startswith("L:") else ln, "item": item,
            "name": task.names[item], "catalog_name": offer.get("name", ""), "url": offer.get("url", ""),
            "class_id": attrs[item].cls, "vendor": attrs[item].vendor,
            "clients": cnt["B2B"] + cnt["B2C"], "clients_b2b": cnt["B2B"], "clients_b2c": cnt["B2C"],
            "clients_only_this": cnt["only"], "owners": owners[ln],
            "rebuy_share": round(rebuy[ln] / n, 4), "other_share": round(other[ln] / n, 4),
            "kind": kind_of(ln), "catalog_advice": advice.get(ln, ""), "judged": dict(judged.get(ln, {})),
        }

    def rank(h: dict) -> tuple:
        state = ADVICE_ORDER.index(h["catalog_advice"]) if h["catalog_advice"] in ADVICE_ORDER else 2
        return (KIND_ORDER.index(h["kind"]), state if h["kind"] == AGENT_KIND else 0, -h["clients"], h["line"])

    queue = sorted((row(ln, cnt) for ln, cnt in behind.items()), key=rank)
    closed = sorted((row(ln, cnt) for ln, cnt in behind_before.items() if ln not in behind),
                    key=lambda h: (-h["clients"], h["line"]))
    QUEUE.write_text(json.dumps({"today": last.isoformat(), "holes": queue, "closed_by_agent": closed},
                                ensure_ascii=False, indent=1), encoding="utf-8")

    def part(rows: list[dict]) -> dict:
        return {"holes": len(rows), "clients_b2b": sum(h["clients_b2b"] for h in rows),
                "clients_b2c": sum(h["clients_b2c"] for h in rows)}

    n_weak = sum(weak.values())
    top = max(queue, key=lambda h: h["clients"]) if queue else {}
    report = {
        "today": last.isoformat(), "clients_with_access": dict(total),
        "clients_without_own_ground_advice": dict(weak),
        "clients_without_own_ground_advice_before_agent": dict(weak_before),
        "holes": len(queue),
        "by_kind": {k: part([h for h in queue if h["kind"] == k]) for k in KIND_ORDER},
        "agent_links_in_verdict_table": n_agent,
        "closed_by_agent": part(closed),
        "largest_hole": {"clients": top.get("clients", 0),
                         "share_of_clients_without_advice": top.get("clients", 0) / max(n_weak, 1),
                         "kind": top.get("kind", ""), "owners": top.get("owners", 0),
                         "rebuy_share": top.get("rebuy_share", 0), "other_share": top.get("other_share", 0)},
        "rule": f"«{RENEWAL}»: владельцев не меньше {OWNERS_MIN}, ту же линейку купили снова не меньше "
                f"{REBUY_MIN:.0%}, что-то другое потом — не больше {OTHER_MAX:.0%}; «{NO_CATALOG}»: товара нет "
                f"в открытом каталоге, название из датасета агенту по API не передаётся",
        "note": "клиент с несколькими линейками засчитан в каждую: суммы по дырам больше числа клиентов; "
                "число клиентов за дырой — верхняя оценка прироста, настоящий прирост считает recsys.rank.fill",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"клиентов кабинета: {dict(total)}; без обоснованного совета: {dict(weak)}; дыр {len(queue)}")
    for kind, p in report["by_kind"].items():
        print(f"  {kind}: дыр {p['holes']}, за ними организаций {p['clients_b2b']}, частных лиц {p['clients_b2c']}")
    big = report["largest_hole"]
    print(f"  самая большая дыра: {big['clients']} клиентов ({big['share_of_clients_without_advice']:.0%} всех без совета), "
          f"вид «{big['kind']}»; из {big['owners']} владельцев ту же линейку купили снова {big['rebuy_share']:.1%}, "
          f"что-то другое — {big['other_share']:.1%}")
    c = report["closed_by_agent"]
    print(f"  связей агента в таблице вердиктов: {n_agent}; закрыто дыр {c['holes']} "
          f"(за ними организаций {c['clients_b2b']}, частных лиц {c['clients_b2c']}); "
          f"без совета было {dict(weak_before)}, стало {dict(weak)}")
    print(f"очередь: {QUEUE}\nотчёт: {REPORT}")


if __name__ == "__main__":
    main()
