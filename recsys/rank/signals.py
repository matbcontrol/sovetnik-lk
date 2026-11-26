"""Сигналы для выбора трёх товаров клиенту. Каждый сигнал — отдельная причина посоветовать товар,
и каждую можно показать клиенту словами.

  pair   — «купившие A потом брали B»: считается по всем клиентам обучения, на уровне товара
  fam    — то же, но от семейства купленного товара: работает, когда у самого товара следов мало
           (семейство берётся из открытого каталога магазина)
  line   — следующий шаг в той же линейке: продление после базовой лицензии, соседний диапазон мест
  co     — «покупают в одном заказе»
  seg    — что докупают клиенты того же типа, отрасли и размера
  pop    — что докупают чаще всего вообще

Сигналы pair, fam и line умножаются на «своевременность»: насколько часто следующую покупку делают
именно через столько месяцев после предыдущей (продление — через 11–12 месяцев, дополнения — сразу).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from recsys.rank.data import Fold, Task, months_between

SIGNALS = ["pair", "pair_t", "fam", "fam_t", "line", "co", "seg", "pop"]
MAX_GAP = 19


class Signals:
    def __init__(self, task: Task, fold: Fold):
        self.task, self.fold = task, fold
        it = task.items
        self.meta = it
        self.line = {}
        for i in {r.item for r in fold.train}:
            r = it.get(i) or {}
            self.line[i] = r.get("family_id") or ("L:" + (r.get("line") or i))
        first, last = fold.first, fold.last

        self.owners = Counter()
        self.line_owners = Counter()
        self.line_items: dict[str, set[str]] = defaultdict(set)
        for c, d in first.items():
            for i in d:
                self.owners[i] += 1
                self.line_items[self.line[i]].add(i)
            for ln in {self.line[i] for i in d}:
                self.line_owners[ln] += 1

        # «потом брали»: товар b появился у клиента позже товара a
        self.pair: dict[str, Counter] = defaultdict(Counter)
        self.fam: dict[str, Counter] = defaultdict(Counter)
        same_gap, cross_gap = Counter(), Counter()
        self.kind_tr: dict[str, Counter] = defaultdict(Counter)
        self.band_tr = Counter()
        self.follow = Counter()
        self.follow_seg: dict[tuple, Counter] = defaultdict(Counter)
        for c, d in first.items():
            seq = sorted(d.items(), key=lambda kv: kv[1])
            start = seq[0][1]
            info = task.clients.get(c, {})
            keys = self._seg_keys(info)
            for x, (b, wb) in enumerate(seq):
                if wb > start:                              # докупка: не из первой недели клиента
                    self.follow[b] += 1
                    for k in keys:
                        self.follow_seg[k][b] += 1
                seen_lines = set()
                for a, wa in seq[:x]:
                    if wa >= wb:
                        continue
                    self.pair[a][b] += 1
                    la = self.line[a]
                    if la not in seen_lines:
                        seen_lines.add(la)
                        self.fam[la][b] += 1
                    # сколько месяцев прошло от последней покупки a до появления b
                    gap = min(int(months_between(max(w for w in (wa, last[c][a]) if w < wb), wb)), MAX_GAP - 1)
                    if la == self.line[b]:
                        same_gap[gap] += 1
                        ka, kb = self._kind(a), self._kind(b)
                        self.kind_tr[ka][kb] += 1
                        self.band_tr[self._band_rel(a, b)] += 1
                    else:
                        cross_gap[gap] += 1

        # своевременность: частота перехода на m-м месяце среди тех, за кем наблюдали не меньше m месяцев
        at_risk = Counter()
        for c, d in last.items():
            for a, wa in d.items():
                seen = int(months_between(wa, fold.train_until))
                for m in range(min(seen, MAX_GAP - 1) + 1):
                    at_risk[m] += 1
        self.haz_same = self._hazard(same_gap, at_risk)
        self.haz_cross = self._hazard(cross_gap, at_risk)

        self.co: dict[str, Counter] = defaultdict(Counter)
        for items in fold.orders.values():
            for a in items:
                for b in items:
                    if a != b:
                        self.co[a][b] += 1
        self.pop_top = [i for i, _ in self.follow.most_common(60)]
        self.n_follow = max(sum(self.follow.values()), 1)

    # ---- вспомогательное ---------------------------------------------------------------

    @staticmethod
    def _seg_keys(info: dict) -> list[tuple]:
        g, t = info.get("client_group", ""), info.get("client_type", "")
        keys = [("g", g), ("gt", g, t)]
        if info.get("industry"):
            keys.append(("gi", g, info["industry"]))
        if info.get("segment"):
            keys.append(("gs", g, info["segment"]))
        return keys

    def _kind(self, i: str) -> str:
        return (self.meta.get(i) or {}).get("license_kind", "") or "—"

    def _band_rel(self, a: str, b: str) -> str:
        ra, rb = self.meta.get(a) or {}, self.meta.get(b) or {}
        if not ra.get("band_lo") or not rb.get("band_lo"):
            return "нет"
        la, lb = int(ra["band_lo"]), int(rb["band_lo"])
        return "тот же" if la == lb else ("больше" if lb > la else "меньше")

    @staticmethod
    def _hazard(gaps: Counter, at_risk: Counter) -> list[float]:
        raw = [gaps[m] / max(at_risk[m], 30) for m in range(MAX_GAP)]
        mean = sum(raw) / len(raw) or 1.0
        # сглаживаем соседними месяцами: данных по отдельному месяцу мало
        sm = [(raw[max(m - 1, 0)] + 2 * raw[m] + raw[min(m + 1, MAX_GAP - 1)]) / 4 for m in range(MAX_GAP)]
        return [x / mean for x in sm]

    def due(self, months_since: float, same: bool) -> float:
        """Своевременность для окна проверки (три месяца после конца обучения)."""
        haz = self.haz_same if same else self.haz_cross
        m0 = int(max(months_since, 0))
        return sum(haz[min(m, MAX_GAP - 1)] for m in range(m0, m0 + 3)) / 3

    # ---- признаки кандидатов для клиента ------------------------------------------------

    def features(self, c: str) -> dict[str, dict[str, float]]:
        fold = self.fold
        h = fold.history[c]
        owned = set(h)
        f: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for a in h:
            la = self.line.get(a, a)
            since = months_between(fold.last[c][a], fold.test_from)
            d_same, d_cross = self.due(since, True), self.due(since, False)
            na = self.owners[a] + 5
            for b, n in self.pair.get(a, {}).items():
                if b not in owned:
                    p = n / na
                    f[b]["pair"] += p
                    f[b]["pair_t"] += p * (d_same if self.line.get(b) == la else d_cross)
            nl = self.line_owners[la] + 20
            for b, n in self.fam.get(la, {}).items():
                if b not in owned:
                    p = n / nl
                    f[b]["fam"] += p
                    f[b]["fam_t"] += p * (d_same if self.line.get(b) == la else d_cross)
            # следующий шаг в линейке: вид лицензии и диапазон мест
            ka = self._kind(a)
            tr = self.kind_tr.get(ka) or {}
            tot = sum(tr.values()) + 10
            btot = sum(self.band_tr.values()) + 4
            for b in self.line_items.get(la, ()):
                if b in owned:
                    continue
                pk = (tr.get(self._kind(b), 0) + 1) / tot
                pb = (self.band_tr[self._band_rel(a, b)] + 1) / btot
                s = pk * pb * d_same
                if s > f[b]["line"]:
                    f[b]["line"] = s
            for b, n in self.co.get(a, {}).items():
                if b not in owned:
                    f[b]["co"] += n / na
        keys = self._seg_keys(self.task.clients.get(c, {}))
        for k in keys[1:]:                                   # тип, отрасль, размер — без общей группы
            cnt = self.follow_seg.get(k)
            if not cnt:
                continue
            tot = sum(cnt.values()) + 30
            for b, n in cnt.most_common(40):
                if b not in owned:
                    f[b]["seg"] += n / tot / (len(keys) - 1)
        gcnt = self.follow_seg.get(keys[0]) or self.follow
        gtot = sum(gcnt.values()) + 30
        for b, n in gcnt.most_common(40):
            if b not in owned:
                f[b]["pop"] += n / gtot
        return f

    def fallback(self, c: str) -> list[str]:
        keys = self._seg_keys(self.task.clients.get(c, {}))
        owned = set(self.fold.history[c])
        cnt = self.follow_seg.get(keys[0]) or self.follow
        return [i for i, _ in cnt.most_common(30) if i not in owned] + [i for i in self.pop_top if i not in owned]
