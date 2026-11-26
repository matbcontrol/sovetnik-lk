"""Базовые способы для сравнения. Все учатся только на «прошлом» и не знают ничего о магазине.

  Popular          — самое покупаемое вообще
  PopularSegment   — самое покупаемое среди клиентов того же типа (и отрасли, если она есть)
  SameAgain        — то, что клиент сам покупал последним (в подписочных продажах очень сильный способ)
  CoPurchase       — «покупают вместе»: совместная встречаемость семейств в истории клиентов
  Sequential       — «покупают следом»: что берут в следующих заказах после уже купленного
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from recsys.eval.backtest import Advice, Context, Event


def _fill(recs: list[Advice], extra: list[str], k: int, skip: set[str]) -> list[Advice]:
    have = {r.family for r in recs} | skip
    for f in extra:
        if len(recs) >= k:
            break
        if f not in have:
            recs.append(Advice(f, fallback=True, slot="popular"))
            have.add(f)
    return recs


class Popular:
    name = "популярное"

    def fit(self, train: list[Event]) -> None:
        clients = defaultdict(set)
        for e in train:
            clients[e.family].add(e.client_id)
        self.top = [f for f, _ in Counter({f: len(c) for f, c in clients.items()}).most_common(200)]

    def recommend(self, ctx: Context, k: int) -> list[Advice]:
        return [Advice(f, fallback=True, slot="popular") for f in self.top[:k]]


class PopularSegment(Popular):
    name = "популярное в сегменте"

    def fit(self, train: list[Event]) -> None:
        super().fit(train)
        seg = defaultdict(lambda: defaultdict(set))
        for e in train:
            seg[(e.client_type, e.industry)][e.family].add(e.client_id)
            seg[(e.client_type, "")][e.family].add(e.client_id)
        self.seg = {s: [f for f, _ in Counter({f: len(c) for f, c in fams.items()}).most_common(200)]
                    for s, fams in seg.items()}

    def top_for(self, ctx: Context) -> list[str]:
        return self.seg.get((ctx.client_type, ctx.industry)) or self.seg.get((ctx.client_type, "")) or self.top

    def recommend(self, ctx: Context, k: int) -> list[Advice]:
        return [Advice(f, fallback=True, slot="popular") for f in self.top_for(ctx)[:k]]


class SameAgain(PopularSegment):
    name = "то же, что брал"

    def recommend(self, ctx: Context, k: int) -> list[Advice]:
        recs, seen = [], set()
        for e in sorted(ctx.history, key=lambda e: e.date, reverse=True):
            if e.family not in seen:
                seen.add(e.family)
                recs.append(Advice(e.family, slot="renew"))
            if len(recs) >= k:
                break
        return _fill(recs, self.top_for(ctx), k, set())


class CoPurchase(PopularSegment):
    name = "покупают вместе"

    def fit(self, train: list[Event]) -> None:
        super().fit(train)
        by_client = defaultdict(set)
        for e in train:
            by_client[e.client_id].add(e.family)
        freq = Counter()
        pair = defaultdict(Counter)
        for fams in by_client.values():
            fams = list(fams)[:200]
            for a in fams:
                freq[a] += 1
                for b in fams:
                    if a != b:
                        pair[a][b] += 1
        # косинусная близость: общие клиенты / корень из произведения числа клиентов
        self.sim = {a: {b: n / math.sqrt(freq[a] * freq[b]) for b, n in nb.items() if n >= 2}
                    for a, nb in pair.items()}

    def recommend(self, ctx: Context, k: int) -> list[Advice]:
        owned = {e.family for e in ctx.history}
        score = Counter()
        for a in owned:
            for b, s in self.sim.get(a, {}).items():
                if b not in owned:
                    score[b] += s
        recs = [Advice(f, slot="missing") for f, _ in score.most_common(k)]
        return _fill(recs, self.top_for(ctx), k, owned)


class Sequential(PopularSegment):
    name = "покупают следом"

    def __init__(self, max_gap_days: int = 365):
        self.max_gap_days = max_gap_days

    def fit(self, train: list[Event]) -> None:
        super().fit(train)
        by_client = defaultdict(list)
        for e in train:
            by_client[e.client_id].append(e)
        trans = defaultdict(Counter)
        starts = Counter()
        for evs in by_client.values():
            evs.sort(key=lambda e: e.date)
            seen_pairs = set()
            for i, a in enumerate(evs):
                for b in evs[i + 1:]:
                    gap = (b.date - a.date).days
                    if gap <= 0:
                        continue
                    if gap > self.max_gap_days:
                        break
                    if (a.family, b.family) not in seen_pairs:
                        seen_pairs.add((a.family, b.family))
                        trans[a.family][b.family] += 1
            for f in {e.family for e in evs}:
                starts[f] += 1
        self.trans = {a: {b: n / starts[a] for b, n in nb.items() if n >= 2} for a, nb in trans.items()}

    def recommend(self, ctx: Context, k: int) -> list[Advice]:
        score = Counter()
        for e in ctx.history:
            age = (ctx.today - e.date).days
            w = math.exp(-max(age, 0) / 365)
            for b, p in self.trans.get(e.family, {}).items():
                score[b] += w * p
        recs = [Advice(f, slot="renew" if f in {e.family for e in ctx.history} else "missing")
                for f, _ in score.most_common(k)]
        return _fill(recs, self.top_for(ctx), k, set())


ALL = [Popular, PopularSegment, SameAgain, CoPurchase, Sequential]
