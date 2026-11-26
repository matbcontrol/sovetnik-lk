"""ИСКУССТВЕННЫЕ продажи — только чтобы проверить, что проверка на истории считается без ошибок.

Никакие числа, полученные на этих данных, не являются результатом и нигде не публикуются:
закономерности заложены нами же (продления по сроку, переходы по связям графа, покупки набором).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from recsys.eval.backtest import Event
from recsys.recommend.engine import Graph


def generate(graph: Graph, n_clients: int = 3000, seed: int = 11,
             start: date = date(2024, 1, 1), end: date = date(2026, 10, 31)) -> list[Event]:
    rnd = random.Random(seed)
    on_sale = [f for f in graph.nodes if graph.on_sale(f)]
    all_fams = list(graph.nodes)
    weights = [1.0 / (i + 1) ** 0.8 for i in range(len(on_sale))]     # немногие товары берут часто
    bundles = [rnd.sample(on_sale, 2) for _ in range(40)]              # «покупают набором»
    span = (end - start).days
    events: list[Event] = []
    for c in range(n_clients):
        ctype = "b2b" if rnd.random() < 0.7 else "b2c"
        cid = f"c{c}"
        owned: list[tuple[str, date]] = []
        for _ in range(rnd.choice([1, 1, 2, 2, 3, 4])):
            fam = rnd.choices(on_sale, weights)[0] if rnd.random() < 0.85 else rnd.choice(all_fams)
            owned.append((fam, start + timedelta(days=rnd.randrange(span))))
        if rnd.random() < 0.3:
            a, b = rnd.choice(bundles)
            d = start + timedelta(days=rnd.randrange(span))
            owned += [(a, d), (b, d + timedelta(days=rnd.randrange(0, 90)))]
        for fam, d in list(owned):
            events.append(Event(cid, d, fam, graph.klass(fam), ctype, term_months=12))
            t = d
            while rnd.random() < 0.55:                                  # продление через год
                t = t + timedelta(days=365 + rnd.randrange(-20, 30))
                if t > end:
                    break
                events.append(Event(cid, t, fam, graph.klass(fam), ctype, license_type="renewal", term_months=12))
            out = [e for e in graph.out.get(fam, ()) if e["type"] != "renewal" and graph.on_sale(e["dst"])]
            if out and rnd.random() < 0.35:                             # переход по связи графа
                e = rnd.choice(out)
                t2 = d + timedelta(days=rnd.randrange(30, 400))
                if t2 <= end:
                    events.append(Event(cid, t2, e["dst"], graph.klass(e["dst"]), ctype, term_months=12))
    return events
