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
HOLES = ROOT / "reports" / "catalog_holes.json"     # очередь для агента каталога: товары в продаже без личного совета
HOLES_TOP = 400
# С каких товаров агенту начинать: с верхних в своём классе магазина, программы раньше оборудования. При равном месте
# первыми идут товары, у чьего класса связь с другими классами есть, а подходящего всем товара нет: там нужен подбор
# под конкретную модель — это и есть работа агента.
ORDER = {"class_only": 0, "manager": 1, "nothing": 2, "judged": 3}
PICK_LABELS = {LABEL[s] for s in PICK_SOURCE.values()}
TIERS = ("own_need", "own_kit", "class_need", "class_kit")
WHY = ("manager", "judged", "class_only", "nothing")


def personal(r: Recommender, who: str, fam: str) -> list:
    client = Client(who, [Purchase(fam, BOUGHT)], today=TODAY)
    return [x for x in r.recommend(client, 5) if x.slot != "renew" and not x.fallback]


def home(n: dict) -> bool:
    """Товар для дома: совет считаем для частного лица, остальным — для организации."""
    return n.get("client_types") == "b2c" or "для дома" in (n.get("class_name") or "").lower()


def agent_gain(g: Graph | None = None) -> dict:
    """Что дал агент каталога: у скольких товаров в продаже личный совет появился только благодаря его связям."""
    from recsys.graph import agent_links

    g = g or Graph.load()
    links = [r for r in agent_links.read() if r["src"] in g.nodes and r["dst"] in g.nodes]
    sources = sorted({r["src"] for r in links if g.nodes[r["src"]]["on_sale"] == "1"})
    gained = []
    if sources:
        now, before = Recommender(g), Recommender(Graph.load(agent=False))
        for fam in sources:
            who = "b2c" if home(g.nodes[fam]) else "b2b"
            if personal(now, who, fam) and not personal(before, who, fam):
                gained.append(fam)
    return {"links": len(links), "products": len(sources), "gained": len(gained)}


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
    holes: list[dict] = []
    for fam, n in g.nodes.items():
        if n["on_sale"] != "1":
            continue
        kind = "hardware" if n.get("kind") == "hardware" else "software"
        who, other = ("b2c", "b2b") if home(n) else ("b2b", "b2c")
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
            why = "manager"                               # подсказка есть, но сама пара не проверена: ждёт менеджера
        elif fam in judged:
            why = "judged"                                # пары с этим товаром проверяли — ни одна не подошла
        elif n["class"] in linked:
            why = "class_only"                            # класс связан с другими, но универсального товара нет
        else:
            why = "nothing"                               # у класса нет связей
        s[why] += 1
        rank = int(n["store_rank"]) if str(n.get("store_rank") or "").isdigit() else 9999
        holes.append({"family": fam, "name": n["name"], "class": n.get("class_name") or "", "product": kind, "why": why, "rank": rank})
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
    gain = agent_gain(g)
    out["agent"] = {**gain, "solo_before": total["solo"] + gain["gained"], "solo_now": total["solo"]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # очередь для агента: открытый каталог, без данных задачи; первыми идут верхние товары своих классов
    holes.sort(key=lambda h: (h["rank"], h["product"] != "software", ORDER[h["why"]], h["class"], h["name"]))
    HOLES.write_text(json.dumps({"today": TODAY.isoformat(), "total": len(holes), "shown": min(len(holes), HOLES_TOP),
                                 "order": "по месту товара в своём классе магазина; при равном месте — сначала программы, затем товары, у класса которых связь есть, а общего для всех товара нет",
                                 "holes": holes[:HOLES_TOP]}, ensure_ascii=False, indent=1), encoding="utf-8")
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
    a = out["agent"]
    print(f"агент каталога: связей в графе {a['links']}, товаров-источников {a['products']}, из них совет появился только "
          f"благодаря агенту у {a['gained']}; без совета было {a['solo_before']}, стало {a['solo_now']}")
    print("записано:", OUT, "и", HOLES)


if __name__ == "__main__":
    main()
