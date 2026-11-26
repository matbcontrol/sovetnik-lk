"""Готовит данные для стенда: один файл графа, который страница читает сама.

Запуск:  python -m recsys.export
Результат: site/data/graph.json   — узлы, связи, классы, «чаще всего берут»
           site/data/parity.json  — контрольные клиенты с ответами движка на Python:
                                    по ним проверяется, что движок страницы отвечает так же

Страница стенда не обращается ни к серверу, ни к модели: совет считается в браузере по этому файлу.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

from recsys.config import INTERIM, ROOT
from recsys.recommend.engine import Client, Graph, Purchase, Recommender

SITE = ROOT / "site" / "data"


def _num(v, cast=float):
    return cast(float(v)) if v not in ("", None) else None


def node_out(n: dict) -> dict:
    out = {
        "id": n["family"], "name": n["name"], "vendor": n.get("vendor", ""), "cls": n.get("class", ""),
        "sale": int(str(n.get("on_sale", "0")) == "1"), "req": int(str(n.get("on_request", "0")) == "1"),
        "b2b": int(str(n.get("b2b_only", "0")) == "1"), "ru": int(str(n.get("ru", "0")) == "1"),
        "ct": n.get("client_types", ""),
        "pf": n.get("platforms", ""), "lic": n.get("license_types", ""),
        "term": _num(n.get("typical_term"), int), "rank": _num(n.get("edition_rank")),
        "url": n.get("url", ""), "price": _num(n.get("price_from")),
        "rating": _num(n.get("rating")), "reviews": _num(n.get("reviews"), int),
        "promo": n.get("promo", ""), "kind": n.get("kind", "software"),
    }
    flags = ("sale", "req", "b2b", "ru")                      # нулевой флаг не пишем; срок 0 (бессрочно) — пишем
    return {k: v for k, v in out.items() if v not in ("", None) and not (k in flags and v == 0)}


def parity_clients(g: Graph, n: int = 300, seed: int = 5) -> list[dict]:
    """Контрольные клиенты: случайные наборы покупок вокруг семейств, у которых есть связи."""
    rnd = random.Random(seed)
    linked = [f for f in g.out if f in g.nodes]
    everything = list(g.nodes)
    today = date(2026, 11, 26)
    rec = Recommender(g, fill=True)
    out = []
    for i in range(n):
        ctype = rnd.choice(["b2b", "b2b", "b2c"])
        purchases = []
        for _ in range(rnd.choice([0, 1, 1, 2, 3, 5])):
            fam = rnd.choice(linked) if linked and rnd.random() < 0.8 else rnd.choice(everything)
            d = today - timedelta(days=rnd.randrange(0, 1500)) if rnd.random() < 0.85 else None
            term = rnd.choice([None, None, 12, 12, 24, 36, 0])
            purchases.append(Purchase(fam, d, term))
        client = Client(ctype, purchases, today=today)
        recs = rec.recommend(client)
        out.append({
            "client": {"type": ctype, "today": today.isoformat(),
                       "purchases": [{"family": p.family, "date": p.date.isoformat() if p.date else None,
                                      "term": p.term_months} for p in purchases]},
            "expect": [{"family": r.family, "slot": r.slot, "score": r.score, "why": r.why,
                        "also": r.also_family} for r in recs],
        })
    return out


def examples(g: Graph, today: date = date(2026, 11, 26)) -> list[dict]:
    """Клиенты-примеры для стенда. Собраны из графа и подписаны как примеры: это не данные задачи."""
    def purchase(fam: str, months_ago: int, term: int | None = None) -> dict:
        y, m = divmod(today.year * 12 + today.month - 1 - months_ago, 12)
        return {"family": fam, "date": date(y, m + 1, min(today.day, 28)).isoformat(), "term": term}

    def first(pred) -> str | None:
        return next((f for f in g.nodes if pred(f)), None)

    def has(fam: str, etype: str, source: str = "") -> bool:
        return any(e["type"] == etype and (not source or e["source"] == source) and g.on_sale(e["dst"])
                   for e in g.out.get(fam, ()))

    out = []
    # Клиент из тестового кабинета магазина (учётная запись, которую организаторы выдали участникам):
    # четыре продукта со страницы «Продукты» кабинета на 26.11.2026 и даты, выведенные из сроков истечения.
    # Это не строка датасета. В кабинете блок «Вам может подойти» показывает ему шесть товаров без объяснений.
    cabinet = [("kaspersky/kaspersky-endpoint-security-dlya-biznesa-standartnyiy", "2026-05-11"),
               ("adobe/adobe-creative-cloud", "2025-11-23"),
               ("drweb/drweb-desktop-security-suite", "2026-05-11"),
               ("microsoft/microsoft-365-personal", "2026-04-30")]
    if all(f in g.nodes for f, _ in cabinet):
        out.append({"title": "Тестовый клиент из кабинета Softline: четыре продукта",
                    "client": {"type": "b2b", "today": today.isoformat(),
                               "purchases": [{"family": f, "date": d, "term": 12} for f, d in cabinet]}})
    ladder = first(lambda f: g.on_sale(f) and has(f, "upgrade", "таблица сравнения магазина")) \
        or first(lambda f: g.on_sale(f) and has(f, "upgrade"))
    gone = [f for f in g.nodes if not g.on_sale(f) and has(f, "analog_ru")]
    gone_pick, seen_cls = [], set()
    for f in gone:                                      # по одному ушедшему продукту из разных классов
        if g.klass(f) not in seen_cls:
            seen_cls.add(g.klass(f))
            gone_pick.append(f)
        if len(gone_pick) == 2:
            break
    if ladder:
        out.append({"title": "Организация: лицензия истекает, есть старшая редакция",
                    "client": {"type": "b2b", "today": today.isoformat(),
                               "purchases": [purchase(ladder, 11, 12)] + [purchase(f, 30) for f in gone_pick[:1]]}})
    if gone_pick:
        out.append({"title": "Организация: куплены продукты ушедших вендоров",
                    "client": {"type": "b2b", "today": today.isoformat(),
                               "purchases": [purchase(f, 40 + 6 * i) for i, f in enumerate(gone_pick)]}})
    home = first(lambda f: str(g.nodes[f].get("b2b_only", "0")) != "1" and has(f, "new_version"))
    if home:
        out.append({"title": "Частное лицо: куплена прошлая версия",
                    "client": {"type": "b2c", "today": today.isoformat(), "purchases": [purchase(home, 20)]}})
    out.append({"title": "Частное лицо без истории покупок",
                "client": {"type": "b2c", "today": today.isoformat(), "purchases": []}})
    out.append({"title": "Организация без истории покупок",
                "client": {"type": "b2b", "today": today.isoformat(), "purchases": []}})
    return out


def main() -> None:
    g = Graph.load()
    edges = []
    for src, es in g.out.items():
        for e in es:
            edges.append([e["src"], e["dst"], e["type"], e["source"], e["confidence"], e.get("evidence", "")])
    for a, others in g.line.items():
        for b in others:
            if a < b:
                edges.append([a, b, "same_line", "названия", 0.7, ""])
    class_names = {}
    p = INTERIM / "class_names.json"
    if p.exists():
        class_names = json.loads(p.read_text(encoding="utf-8"))
    class_links = [link for links in g.class_links.values() for link in links]
    data = {
        "meta": {
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "nodes": len(g.nodes), "edges": len(edges),
            "on_sale": sum(1 for f in g.nodes if g.on_sale(f)),
        },
        "nodes": [node_out(n) for n in g.nodes.values()],
        "classes": class_names,
        "edges": edges,
        "class_links": class_links,
        "class_rank": {k: v[:12] for k, v in g.class_rank.items()},
        "popular": g.popular,
    }
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "graph.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    parity = parity_clients(g)
    (SITE / "parity.json").write_text(json.dumps(parity, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "examples.json").write_text(json.dumps(examples(g), ensure_ascii=False, indent=1), encoding="utf-8")
    if not (SITE / "report.json").exists():             # отчёт проверки на истории появится после данных задачи
        (SITE / "report.json").write_text("{}", encoding="utf-8")
    size = (SITE / "graph.json").stat().st_size / 1e6
    print(f"graph.json: узлов {len(data['nodes'])}, связей {len(edges)}, {size:.1f} МБ; контрольных клиентов {len(parity)}")


if __name__ == "__main__":
    main()
