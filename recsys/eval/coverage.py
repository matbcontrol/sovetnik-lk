"""Охват открытого каталога личными советами: у скольких товаров в продаже советнику есть что предложить.

Для каждого товара в продаже берётся клиент с одной покупкой: товар куплен 1 марта 2026 г., «сегодня» —
27 ноября 2026 г. Тип клиента — тот, кому товар адресован: товар для дома — частное лицо, остальное —
организация. Продление своей же покупки и строка «Часто докупают» личным советом не считаются.

Запуск: python -m recsys.eval.coverage  →  reports/coverage.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date

from recsys.config import ROOT
from recsys.recommend.engine import LABEL, PICK_SOURCE, Client, Graph, Purchase, Recommender

BOUGHT, TODAY = date(2026, 3, 1), date(2026, 11, 27)
OUT = ROOT / "reports" / "coverage.json"
PICK_LABELS = {LABEL[s] for s in PICK_SOURCE.values()}
TIERS = ("own_need", "own_kit", "class_need", "class_kit")
WHY = ("manager", "judged", "class_only", "nothing")


def personal(r: Recommender, who: str, fam: str) -> list:
    client = Client(who, [Purchase(fam, BOUGHT)], today=TODAY)
    return [x for x in r.recommend(client, 5) if x.slot != "renew" and not x.fallback]


def tier(recs: list) -> str:
    """На чём держится совет: на паре с самим товаром или на проверке товара для его класса."""
    own = [x for x in recs if x.label not in PICK_LABELS]
    if any(x.slot != "kit" for x in own):
        return "own_need"
    if own:
        return "own_kit"
    return "class_need" if any(x.slot != "kit" for x in recs) else "class_kit"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from recsys.llm.pair_verdicts import CLASS_FILE, FAMILY_FILE, read

    g = Graph.load()
    to_client, to_manager = Recommender(g), Recommender(g, unverified=True)
    judged = {a for a, _ in read(FAMILY_FILE)}
    linked = {a for (a, _), v in read(CLASS_FILE).items() if v.shown}
    stat: dict[str, Counter] = defaultdict(Counter)
    solo_class: Counter = Counter()
    size_class: Counter = Counter()
    for fam, n in g.nodes.items():
        if n["on_sale"] != "1":
            continue
        kind = "hardware" if n.get("kind") == "hardware" else "software"
        home = n.get("client_types") == "b2c" or "для дома" in (n.get("class_name") or "").lower()
        who, other = ("b2c", "b2b") if home else ("b2b", "b2c")
        s = stat[kind]
        s["on_sale"] += 1
        size_class[n.get("class_name") or ""] += 1
        recs = personal(to_client, who, fam)
        if recs:
            s["with_advice"] += 1
            s[tier(recs)] += 1
            continue
        s["solo"] += 1
        solo_class[n.get("class_name") or ""] += 1
        if not personal(to_client, other, fam):
            s["solo_any_client"] += 1
        if personal(to_manager, who, fam):
            s["manager"] += 1                             # подсказка есть, но сама пара не проверена: ждёт менеджера
        elif fam in judged:
            s["judged"] += 1                              # пары с этим товаром проверяли — ни одна не подошла
        elif n["class"] in linked:
            s["class_only"] += 1                          # класс связан с другими, но универсального товара нет
        else:
            s["nothing"] += 1                             # у класса нет связей
    total: Counter = Counter()
    for s in stat.values():
        total.update(s)
    keys = ("on_sale", "with_advice", *TIERS, "solo", "solo_any_client", *WHY)
    out = {
        "bought": BOUGHT.isoformat(), "today": TODAY.isoformat(),
        "kinds": {k: {x: stat[k][x] for x in keys} for k in ("software", "hardware")},
        "total": {x: total[x] for x in keys},
        "solo_classes": [{"class": c, "solo": k, "on_sale": size_class[c]} for c, k in solo_class.most_common(12)],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    title = {"software": "программы", "hardware": "оборудование"}
    for k, s in out["kinds"].items():
        print(f"{title[k]}: в продаже {s['on_sale']}, с личным советом {s['with_advice']} "
              f"({s['with_advice'] / s['on_sale']:.0%}), без совета {s['solo']}")
        print(f"   совет по паре с самим товаром: «нужно» {s['own_need']}, только «комплект» {s['own_kit']}; "
              f"по проверке товара для класса: «нужно» {s['class_need']}, только «комплект» {s['class_kit']}")
        print(f"   без совета: ждёт менеджера {s['manager']}, пары проверены и не подошли {s['judged']}, "
              f"у класса нет универсального товара {s['class_only']}, у класса нет связей {s['nothing']}")
    t = out["total"]
    print(f"всего в продаже {t['on_sale']}: с личным советом {t['with_advice']} ({t['with_advice'] / t['on_sale']:.0%}), "
          f"без совета {t['solo']} ({t['solo'] / t['on_sale']:.0%}); без совета клиенту любого типа {t['solo_any_client']}")
    print("классы, где без совета больше всего товаров:",
          "; ".join(f"{x['class']} — {x['solo']} из {x['on_sale']}" for x in out["solo_classes"][:8]))
    print("записано:", OUT)


if __name__ == "__main__":
    main()
