"""Наш способ в проверке на истории: граф из открытых источников + то, чему учат продажи.

Из «прошлого» (и только из него) берём три вещи:
  1. связи «покупают следом» и «покупают вместе» между семействами — с порогом поддержки и подъёма,
     чтобы не записать в связи просто популярные товары;
  2. обычный интервал повторной покупки по семейству — чтобы знать, когда наступает продление;
  3. веса видов связей: как часто связь каждого вида подтверждается покупкой. Это и есть сверка
     открытых источников с продажами: одна цифра на каждый вид связи.
"""
from __future__ import annotations

import copy
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from recsys.eval.backtest import Advice, Context, Event
from recsys.eval.baselines import PopularSegment
from recsys.recommend.engine import WEIGHT, Client, Graph, Purchase, Recommender


def sales_edges(train: list[Event], min_support: int = 3, min_lift: float = 1.5, shrink: float = 5.0) -> list[dict]:
    """Связи из продаж. Уверенность — сглаженная доля клиентов, купивших dst после src."""
    by_client: dict[str, list[Event]] = defaultdict(list)
    for e in train:
        by_client[e.client_id].append(e)
    n_clients = len(by_client)
    owners = Counter()
    after: dict[str, Counter] = defaultdict(Counter)
    for evs in by_client.values():
        evs.sort(key=lambda e: e.date)
        first: dict[str, date] = {}
        for e in evs:
            first.setdefault(e.family, e.date)
        for f in first:
            owners[f] += 1
        fams = list(first.items())[:200]
        for a, da in fams:
            for b, db in fams:
                if a != b and db >= da:
                    after[a][b] += 1
    edges = []
    for a, nb in after.items():
        for b, n in nb.items():
            if n < min_support:
                continue
            base = owners[b] / n_clients
            conf = n / owners[a]
            if conf / base < min_lift:
                continue
            p = (n + shrink * base) / (owners[a] + shrink)
            edges.append({
                "src": a, "dst": b, "type": "next_purchase", "source": "продажи",
                "confidence": round(p, 4),
                "evidence": f"{n} из {owners[a]} клиентов, купивших первое, купили и второе",
            })
    return edges


def repeat_cycles(train: list[Event], min_pairs: int = 3) -> dict[str, int]:
    """Обычный интервал повторной покупки семейства одним клиентом, в днях."""
    last: dict[tuple[str, str], date] = {}
    gaps: dict[str, list[int]] = defaultdict(list)
    for e in sorted(train, key=lambda e: e.date):
        k = (e.client_id, e.family)
        if k in last and (e.date - last[k]).days >= 60:
            gaps[e.family].append((e.date - last[k]).days)
        if k not in last or (e.date - last[k]).days >= 60:
            last[k] = e.date
    return {f: int(statistics.median(g)) for f, g in gaps.items() if len(g) >= min_pairs}


def calibrate(graph: Graph, train: list[Event], cutoff: date, horizon_days: int) -> tuple[dict[str, float], dict[str, dict]]:
    """Как часто связь каждого вида подтверждается покупкой: клиент владел src до даты T′ и купил dst после.

    T′ = cutoff − horizon: сверка идёт внутри «прошлого», окно проверки не затрагивается.
    Возвращает веса видов связей и таблицу сверки (сколько раз связь могла сработать и сколько сработала).
    """
    inner = cutoff - timedelta(days=horizon_days)
    before: dict[str, set[str]] = defaultdict(set)
    later: dict[str, set[str]] = defaultdict(set)
    for e in train:
        (before if e.date <= inner else later)[e.client_id].add(e.family)
    tried, hit, conf_sum = Counter(), Counter(), Counter()
    for client, owned in before.items():
        bought = later.get(client, set())
        for src in owned:
            for e in graph.out.get(src, ()):
                if e["type"] == "renewal" or not graph.on_sale(e["dst"]):
                    continue
                if e["dst"] in owned and e["type"] != "renewal":
                    continue
                t = e["type"]
                tried[t] += 1
                conf_sum[t] += e["confidence"]
                hit[t] += e["dst"] in bought
    weights, table = {}, {}
    for t, n in tried.items():
        rate = (hit[t] + 1) / (n + 20)                     # сглаживание: мало наблюдений — осторожная оценка
        mean_conf = conf_sum[t] / n
        table[t] = {"tried": n, "confirmed": hit[t], "rate": hit[t] / n}
        if n >= 30:
            weights[t] = min(1.0, rate / max(mean_conf, 1e-6))
    return weights, table


class GraphModel(PopularSegment):
    name = "граф связей"

    def __init__(self, graph: Graph, use_sales: bool = True, horizon_days: int = 180,
                 min_confidence: float = 0.0, fallback: bool = True, diversify: bool = True):
        self.base_graph = graph
        self.use_sales = use_sales
        self.horizon_days = horizon_days
        self.min_confidence = min_confidence
        self.fallback = fallback
        self.diversify = diversify
        self.calibration: dict[str, dict] = {}

    def fit(self, train: list[Event]) -> None:
        super().fit(train)
        g = copy.copy(self.base_graph)
        g.out = defaultdict(list, {k: list(v) for k, v in self.base_graph.out.items()})
        g.nodes = {k: dict(v) for k, v in self.base_graph.nodes.items()}
        weights = dict(WEIGHT)
        cutoff = max(e.date for e in train) if train else date.today()
        # «В продаже» на дату разреза: сегодняшняя витрина не знает, что продавалось тогда.
        # Считаем продаваемым всё, что покупали за два года перед разрезом (продление приходит через год и позже).
        recent = cutoff - timedelta(days=730)
        for e in train:
            n = g.nodes.get(e.family)
            if n is None:                              # товара нет в графе магазина: узел из самих продаж
                n = g.nodes[e.family] = {"family": e.family, "name": e.family, "class": e.klass, "on_sale": "0"}
            if e.date >= recent:
                n["on_sale"] = "1"
        self.cycles: dict[str, int] = {}
        if self.use_sales and train:
            for e in sales_edges(train):
                if e["src"] in g.nodes and e["dst"] in g.nodes:
                    g.out[e["src"]].append(e)
            self.cycles = repeat_cycles(train)
            learned, self.calibration = calibrate(g, train, cutoff, self.horizon_days)
            weights.update(learned)
            weights["next_purchase"] = 1.0            # уверенность такой связи уже есть доля покупок
        self.rec = Recommender(g, weights=weights, min_confidence=self.min_confidence,
                               diversify=self.diversify, renew_window=(-45, self.horizon_days))
        self.graph = g

    def recommend(self, ctx: Context, k: int) -> list[Advice]:
        purchases = []
        for e in ctx.history:
            term = e.term_months
            if term is None and e.family in self.cycles:
                term = max(1, round(self.cycles[e.family] / 30.4))
            purchases.append(Purchase(e.family, e.date, term, e.license_type, sku=e.sku))
        client = Client(ctx.client_type or "b2b", purchases, ctx.industry, ctx.today)
        recs = [Advice(r.family, slot=r.slot) for r in self.rec.recommend(client, k)]
        if not self.fallback:
            return recs
        have = {r.family for r in recs}
        owned = {e.family for e in ctx.history}
        for f in self.top_for(ctx):
            if len(recs) >= k:
                break
            n = self.graph.nodes.get(f)
            if f in have or f in owned:
                continue
            if n is not None and (not self.graph.on_sale(f)
                                  or (ctx.client_type == "b2c" and str(n.get("b2b_only", "0")) == "1")):
                continue
            recs.append(Advice(f, fallback=True, slot="popular"))
            have.add(f)
        return recs
