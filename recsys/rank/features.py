"""Кандидаты и признаки для обучаемого выбора трёх товаров.

Кандидаты для клиента собираются из нескольких источников (каждый — возможная причина совета):
  «потом брали» по товару и по семейству, та же линейка, новая версия того же продукта,
  переход между классами каталога, один заказ, докупки клиентов того же сегмента, общие докупки.

Для пары «клиент — кандидат» считаются признаки: сила каждого источника, своевременность
(сколько месяцев прошло с покупки), соответствие лицензии (вид, диапазон мест, срок, период подписки,
версия), сведения о клиенте и о самом товаре. Всё считается только по данным до даты разреза.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from recsys.graph.textnorm import tokens
from recsys.rank.data import Fold, Task, months_between

MAX_GAP = 19
EMPTY: Counter = Counter()
KINDS = ["", "base", "renewal", "upgrade", "migration", "extension", "support", "media"]
TYPES = ["", "ЮЛ", "ИП", "ФЛ"]
SEGS = ["", "small", "medium", "corporate", "enterprise"]

FEATURES = [
    "pair_sum", "pair_max", "pair_n", "pair_t", "fam_sum", "fam_max", "fam_t", "cls_max", "co_sum",
    "seg_gt", "seg_ind", "seg_size", "pop_follow", "pop_all", "trend", "new_item",
    "same_line", "n_owned_in_line", "since_line", "due_same", "due_cross", "kind_b", "kind_a", "kind_tr",
    "band_rel", "band_ratio", "term_same", "term_b", "period_delta", "same_vkey", "version_delta",
    "name_sim", "share_in_line", "edu_b", "cert_b",
    "is_b2b", "ctype", "segment", "has_industry", "n_hist", "since_last", "since_first",
]


@dataclass
class Attr:
    line: str = ""            # семейство каталога, а если товара в каталоге нет — линейка по названию
    nline: str = ""           # линейка по названию без версии и параметров лицензии
    cls: str = ""             # класс (подкатегория каталога)
    kind: str = ""
    band_lo: int | None = None
    band_hi: int | None = None
    term: int | None = None
    period: int | None = None
    pkey: str = ""
    version: float | None = None
    edu: int = 0
    cert: int = 0
    toks: frozenset = frozenset()


def build_attrs(task: Task) -> dict[str, Attr]:
    out = {}
    for item, name in task.names.items():
        r = task.items.get(item) or {}
        low = name.lower()
        a = Attr()
        a.nline = r.get("line") or " ".join(tokens(name)[:6])
        a.line = r.get("family_id") or ("L:" + a.nline)
        a.cls = r.get("class_id") or ""
        a.kind = r.get("license_kind") or ""
        a.band_lo = int(r["band_lo"]) if r.get("band_lo") else None
        a.band_hi = int(r["band_hi"]) if r.get("band_hi") else None
        a.term = int(float(r["term_months"])) if r.get("term_months") not in ("", None) else None
        m = re.search(r"\((\d+)/(\d+)", name)
        if m:
            a.period = int(m.group(1))
            a.pkey = " ".join(tokens(re.sub(r"\([^)]*\)", " ", name))[:8])
        m = re.search(r"(?:nanocad|contentreader pdf|finereader|версии|версия|version)\s*(\d{1,2}(?:\.\d+)?)\b", low) \
            or re.search(r"\b(20[12]\d)\b", low)
        if m:
            a.version = float(m.group(1))
        a.edu = int(bool(re.search(r"education|академич|образоват", low)))
        a.cert = int(bool(re.search(r"сертифицирован|фстэк|фсб|\bcert", low)))
        a.toks = frozenset(tokens(name))
        out[item] = a
    return out


class FoldStats:
    """Всё, что можно посчитать по обучающей части разреза."""

    def __init__(self, task: Task, fold: Fold, attrs: dict[str, Attr]):
        self.task, self.fold, self.attrs = task, fold, attrs
        first, last = fold.first, fold.last
        at = attrs
        self.owners, self.line_owners = Counter(), Counter()
        self.line_items: dict[str, set[str]] = defaultdict(set)
        self.nline_items: dict[str, set[str]] = defaultdict(set)
        self.pair: dict[str, Counter] = defaultdict(Counter)
        self.fam: dict[str, Counter] = defaultdict(Counter)
        self.cls: dict[str, Counter] = defaultdict(Counter)
        self.cls_owners = Counter()
        self.follow = Counter()
        self.follow_seg: dict[tuple, Counter] = defaultdict(Counter)
        self.kind_tr: dict[str, Counter] = defaultdict(Counter)
        same_gap, cross_gap = Counter(), Counter()
        recent_from = fold.train_until.toordinal() - 84
        self.recent = Counter()
        self.first_seen: dict[str, int] = {}
        for r in fold.train:
            if r.week.toordinal() >= recent_from:
                self.recent[r.item] += 1
            self.first_seen.setdefault(r.item, r.week.toordinal())
        for c, d in first.items():
            seq = sorted(d.items(), key=lambda kv: kv[1])
            start = seq[0][1]
            keys = seg_keys(task.clients.get(c, {}))
            for i in d:
                self.owners[i] += 1
                self.line_items[at[i].line].add(i)
                self.nline_items[at[i].nline].add(i)
            for ln in {at[i].line for i in d}:
                self.line_owners[ln] += 1
            for cl in {at[i].cls for i in d if at[i].cls}:
                self.cls_owners[cl] += 1
            for x, (b, wb) in enumerate(seq):
                if wb > start:
                    self.follow[b] += 1
                    for k in keys:
                        self.follow_seg[k][b] += 1
                seen_l, seen_c = set(), set()
                for a, wa in seq[:x]:
                    if wa >= wb:
                        continue
                    self.pair[a][b] += 1
                    la, ca = at[a].line, at[a].cls
                    if la not in seen_l:
                        seen_l.add(la)
                        self.fam[la][b] += 1
                    if ca and ca not in seen_c:
                        seen_c.add(ca)
                        self.cls[ca][b] += 1
                    prev = max(w for w in (wa, last[c][a]) if w < wb)
                    gap = min(int(months_between(prev, wb)), MAX_GAP - 1)
                    if la == at[b].line:
                        same_gap[gap] += 1
                        self.kind_tr[at[a].kind][at[b].kind] += 1
                    else:
                        cross_gap[gap] += 1
        at_risk = Counter()
        for c, d in last.items():
            for a, wa in d.items():
                for m in range(min(int(months_between(wa, fold.train_until)), MAX_GAP - 1) + 1):
                    at_risk[m] += 1
        self.haz_same = hazard(same_gap, at_risk)
        self.haz_cross = hazard(cross_gap, at_risk)
        self.co: dict[str, Counter] = defaultdict(Counter)
        for items in fold.orders.values():
            for a in items:
                for b in items:
                    if a != b:
                        self.co[a][b] += 1
        self.n_clients = max(len(first), 1)
        self.n_follow = max(sum(self.follow.values()), 1)
        self.n_recent = max(sum(self.recent.values()), 1)
        self.n_rows = max(len(fold.train), 1)
        self.item_rows = Counter(r.item for r in fold.train)
        self.pop_top = [i for i, _ in self.follow.most_common(40)]
        self.trend_top = [i for i, _ in self.recent.most_common(25)]

    def due(self, since: float, same: bool) -> float:
        haz = self.haz_same if same else self.haz_cross
        m0 = int(max(since, 0))
        return sum(haz[min(m, MAX_GAP - 1)] for m in range(m0, m0 + 3)) / 3

    # ---- кандидаты и признаки ------------------------------------------------------------

    def rows(self, c: str) -> dict[str, list[float]]:
        fold, at = self.fold, self.attrs
        h = fold.history[c]
        owned = set(h)
        info = self.task.clients.get(c, {})
        keys = seg_keys(info)
        f: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        since = {a: months_between(fold.last[c][a], fold.test_from) for a in h}
        own_lines = defaultdict(list)
        for a in h:
            own_lines[at[a].line].append(a)
        own_nlines = defaultdict(list)
        for a in h:
            own_nlines[at[a].nline].append(a)

        for a in h:
            la = at[a].line
            na = self.owners[a] + 5
            ds, dc = self.due(since[a], True), self.due(since[a], False)
            for b, n in self.pair.get(a, EMPTY).most_common(40):
                if b in owned:
                    continue
                p = n / na
                fb = f[b]
                fb["pair_sum"] += p
                fb["pair_max"] = max(fb["pair_max"], p)
                fb["pair_n"] = max(fb["pair_n"], n)
                fb["pair_t"] += p * (ds if at[b].line == la else dc)
            for b, n in self.co.get(a, EMPTY).most_common(20):
                if b not in owned:
                    f[b]["co_sum"] += n / na
        for la, owned_here in own_lines.items():
            nl = self.line_owners[la] + 20
            s = min(since[a] for a in owned_here)
            ds, dc = self.due(s, True), self.due(s, False)
            for b, n in self.fam.get(la, EMPTY).most_common(40):
                if b in owned:
                    continue
                p = n / nl
                fb = f[b]
                fb["fam_sum"] += p
                fb["fam_max"] = max(fb["fam_max"], p)
                fb["fam_t"] += p * (ds if at[b].line == la else dc)
            for b in self.line_items.get(la, ()):
                if b not in owned:
                    f[b]["same_line"] = 1.0
        for nl_key, owned_here in own_nlines.items():        # тот же продукт в другой версии
            for b in self.nline_items.get(nl_key, ()):
                if b not in owned:
                    f[b]["same_vkey"] = 1.0
        for cl in {at[a].cls for a in h if at[a].cls}:
            nc = self.cls_owners[cl] + 30
            for b, n in self.cls.get(cl, EMPTY).most_common(30):
                if b not in owned:
                    f[b]["cls_max"] = max(f[b]["cls_max"], n / nc)
        for k in keys:
            cnt = self.follow_seg.get(k)
            if not cnt:
                continue
            tot = sum(cnt.values()) + 30
            name = {"gt": "seg_gt", "gi": "seg_ind", "gs": "seg_size"}.get(k[0])
            if not name:
                continue
            for b, n in cnt.most_common(30):
                if b not in owned:
                    f[b][name] = n / tot
        for b in self.pop_top + self.trend_top:
            if b not in owned:
                f[b]["pop_follow"] += 0.0                    # попадает в кандидаты; значения считаются ниже

        n_hist = len(h)
        since_last = min(since.values())
        since_first = months_between(min(fold.first[c].values()), fold.test_from)
        client = {
            "is_b2b": float(info.get("client_group") == "B2B"),
            "ctype": float(TYPES.index(info.get("client_type", "")) if info.get("client_type", "") in TYPES else 0),
            "segment": float(SEGS.index(info.get("segment", "")) if info.get("segment", "") in SEGS else 0),
            "has_industry": float(bool(info.get("industry"))),
            "n_hist": float(n_hist), "since_last": since_last, "since_first": since_first,
        }
        out = {}
        for b, fb in f.items():
            ab = at[b]
            fb.update(client)
            fb["pop_follow"] = self.follow[b] / self.n_follow
            fb["pop_all"] = self.owners[b] / self.n_clients
            fb["trend"] = (self.recent[b] / self.n_recent) / max(self.item_rows[b] / self.n_rows, 1e-9)
            fb["new_item"] = float(fold.train_until.toordinal() - self.first_seen.get(b, 0) <= 180)
            fb["kind_b"] = float(KINDS.index(ab.kind) if ab.kind in KINDS else 0)
            fb["term_b"] = float(ab.term) if ab.term is not None else -1.0
            fb["edu_b"], fb["cert_b"] = float(ab.edu), float(ab.cert)
            fb["share_in_line"] = self.owners[b] / max(self.line_owners[ab.line], 1)
            fb["since_line"], fb["band_rel"], fb["term_same"], fb["period_delta"] = -1.0, 0.0, -1.0, -99.0
            fb["version_delta"] = 0.0
            # ближайший по времени купленный товар той же линейки (или того же продукта другой версии)
            near = own_lines.get(ab.line) or own_nlines.get(ab.nline) or []
            if near:
                a = min(near, key=lambda x: since[x])
                aa = at[a]
                fb["n_owned_in_line"] = float(len(near))
                fb["since_line"] = since[a]
                fb["due_same"] = self.due(since[a], True)
                fb["kind_a"] = float(KINDS.index(aa.kind) if aa.kind in KINDS else 0)
                tr = self.kind_tr.get(aa.kind) or {}
                fb["kind_tr"] = (tr.get(ab.kind, 0) + 1) / (sum(tr.values()) + 10)
                if aa.band_lo and ab.band_lo:
                    fb["band_rel"] = 1.0 if aa.band_lo == ab.band_lo else (2.0 if ab.band_lo > aa.band_lo else 3.0)
                    fb["band_ratio"] = max(-3.0, min(3.0, math.log(ab.band_lo / aa.band_lo)))
                if aa.term is not None and ab.term is not None:
                    fb["term_same"] = float(aa.term == ab.term)
                if aa.period is not None and ab.period is not None and aa.pkey == ab.pkey:
                    best_k = max(at[x].period for x in near if at[x].period is not None and at[x].pkey == ab.pkey)
                    fb["period_delta"] = float(ab.period - best_k)
                if aa.version is not None and ab.version is not None:
                    fb["version_delta"] = max(-5.0, min(5.0, ab.version - max(at[x].version or 0 for x in near)))
            fb["due_cross"] = self.due(since_last, False)
            best = 0.0
            for a in h:
                ta = at[a].toks
                if ta and ab.toks:
                    j = len(ta & ab.toks) / len(ta | ab.toks)
                    if j > best:
                        best = j
            fb["name_sim"] = best
            out[b] = [fb.get(name, 0.0) for name in FEATURES]
        return out

    def fallback(self, c: str) -> list[str]:
        owned = set(self.fold.history[c])
        keys = seg_keys(self.task.clients.get(c, {}))
        cnt = self.follow_seg.get(keys[0]) or self.follow
        return [i for i, _ in cnt.most_common(30) if i not in owned] + [i for i in self.pop_top if i not in owned]


def seg_keys(info: dict) -> list[tuple]:
    g, t = info.get("client_group", ""), info.get("client_type", "")
    keys = [("g", g), ("gt", g, t)]
    if info.get("industry"):
        keys.append(("gi", g, info["industry"]))
    if info.get("segment"):
        keys.append(("gs", g, info["segment"]))
    return keys


def hazard(gaps: Counter, at_risk: Counter) -> list[float]:
    """Частота перехода на m-м месяце среди тех, за кем наблюдали не меньше m месяцев; сглажено и нормировано."""
    raw = [gaps[m] / max(at_risk[m], 30) for m in range(MAX_GAP)]
    mean = sum(raw) / len(raw) or 1.0
    sm = [(raw[max(m - 1, 0)] + 2 * raw[m] + raw[min(m + 1, MAX_GAP - 1)]) / 4 for m in range(MAX_GAP)]
    return [x / mean for x in sm]
