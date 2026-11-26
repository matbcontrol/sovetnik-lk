"""Проверка на истории продаж: честный разрез по времени, одна и та же мерка для всех способов.

Порядок:
  1. Берём дату разреза T. Всё, что куплено до T включительно, — «прошлое», способ учится только на нём.
  2. Для каждого клиента, который что-то купил в окне (T, T + H], способ выдаёт до трёх советов,
     зная только его покупки до T.
  3. Совет засчитан, если клиент купил это семейство в окне.

Мерки (считаются по клиентам, у которых в окне была покупка):
  hit@3        — доля клиентов, которым угадали хотя бы одну покупку из трёх советов
  precision@3  — угаданные советы / 3, в среднем по клиентам (пустой блок даёт ноль)
  precision_shown — угаданные / показанные советы, только по клиентам, которым блок показан
  fill_rate    — доля клиентов, которым способ показал хотя бы один совет, выведенный из данных
                 (подстановка «самое популярное» сюда не входит и считается отдельно: fill_with_fallback)

Уровень сравнения: «family» (семейство, основной) или «class» (класс продукта, мягче).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Event:
    client_id: str
    date: date
    family: str                  # ключ семейства; если не сопоставлено — идентификатор товара
    klass: str = ""
    client_type: str = ""        # b2b / b2c
    industry: str = ""
    order_id: str = ""
    qty: float = 1.0
    license_type: str = ""
    term_months: int | None = None
    sku: str = ""


@dataclass
class Context:
    client_id: str
    client_type: str
    industry: str
    history: list[Event]
    today: date


@dataclass
class Advice:
    family: str
    fallback: bool = False       # подстановка «самое популярное», а не вывод из покупок клиента
    slot: str = ""


class Model(Protocol):
    name: str

    def fit(self, train: list[Event]) -> None: ...
    def recommend(self, ctx: Context, k: int) -> list[Advice]: ...


@dataclass
class ClientResult:
    client_id: str
    client_type: str
    history_size: int
    n_target: int
    n_recs: int
    n_derived: int               # советов без подстановки
    hits: int
    hits_derived: int
    hit_slots: list[str] = field(default_factory=list)


def _bucket(n: int) -> str:
    return "без истории" if n == 0 else ("1–2 покупки" if n <= 2 else "3 и больше")


def run(model: Model, events: list[Event], cutoff: date, horizon_days: int = 180, k: int = 3,
        level: str = "family", only_new: bool = False) -> list[ClientResult]:
    """Прогон одного способа на одном разрезе. Возвращает по строке на клиента из окна проверки."""
    end = cutoff + timedelta(days=horizon_days)
    train = [e for e in events if e.date <= cutoff]
    test = [e for e in events if cutoff < e.date <= end]
    klass_of = {e.family: e.klass for e in events if e.klass}

    def key(family: str) -> str:
        return klass_of.get(family, family) if level == "class" else family

    model.fit(train)
    history: dict[str, list[Event]] = defaultdict(list)
    for e in train:
        history[e.client_id].append(e)
    targets: dict[str, set[str]] = defaultdict(set)
    meta: dict[str, Event] = {}
    for e in test:
        targets[e.client_id].add(key(e.family))
        meta[e.client_id] = e

    out = []
    for client_id, tgt in targets.items():
        h = sorted(history.get(client_id, []), key=lambda e: e.date)
        if only_new:
            tgt = tgt - {key(e.family) for e in h}
            if not tgt:
                continue
        m = h[-1] if h else meta[client_id]
        ctx = Context(client_id, m.client_type, m.industry, h, cutoff)
        recs = model.recommend(ctx, k)[:k]
        seen, hits, hits_d, slots = set(), 0, 0, []
        for r in recs:
            kk = key(r.family)
            if kk in seen:
                continue
            seen.add(kk)
            if kk in tgt:
                hits += 1
                slots.append(r.slot or ("popular" if r.fallback else ""))
                hits_d += 0 if r.fallback else 1
        out.append(ClientResult(client_id, m.client_type, len(h), len(tgt), len(recs),
                                sum(1 for r in recs if not r.fallback), hits, hits_d, slots))
    return out


def metrics(rows: list[ClientResult], k: int = 3) -> dict:
    n = len(rows)
    if not n:
        return {"clients": 0}
    shown = [r for r in rows if r.n_derived > 0]
    return {
        "clients": n,
        "hit@3": sum(1 for r in rows if r.hits > 0) / n,
        "precision@3": sum(r.hits for r in rows) / (k * n),
        "precision_shown": (sum(r.hits_derived for r in shown) / sum(r.n_derived for r in shown)) if shown else 0.0,
        "fill_rate": len(shown) / n,
        "fill_with_fallback": sum(1 for r in rows if r.n_recs > 0) / n,
        "full_block": sum(1 for r in rows if r.n_recs >= k) / n,
    }


def slices(rows: list[ClientResult], k: int = 3) -> dict[str, dict]:
    groups: dict[str, list[ClientResult]] = defaultdict(list)
    for r in rows:
        groups[f"тип: {r.client_type or 'не указан'}"].append(r)
        groups[f"история: {_bucket(r.history_size)}"].append(r)
    return {name: metrics(g, k) for name, g in sorted(groups.items())}


def _per_client(rows: list[ClientResult], metric: str, k: int) -> np.ndarray:
    """hit@3 и precision@3 — средние по клиентам, поэтому интервал считается по вектору значений."""
    if metric == "hit@3":
        return np.array([1.0 if r.hits > 0 else 0.0 for r in rows])
    if metric == "precision@3":
        return np.array([r.hits / k for r in rows])
    raise ValueError(f"интервал не считается для мерки {metric}")


def _resampled_means(values: np.ndarray, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = []
    for start in range(0, n, 50):                      # частями, чтобы не держать большую матрицу в памяти
        b = min(50, n - start)
        idx = rng.integers(0, len(values), size=(b, len(values)))
        out.append(values[idx].mean(axis=1))
    return np.concatenate(out)


def bootstrap(rows: list[ClientResult], metric: str = "hit@3", k: int = 3, n: int = 1000, seed: int = 7) -> tuple[float, float]:
    """95% интервал для мерки: пересобираем выборку клиентов с возвращением."""
    if not rows:
        return 0.0, 0.0
    means = _resampled_means(_per_client(rows, metric, k), n, seed)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def paired_gain(a: list[ClientResult], b: list[ClientResult], metric: str = "hit@3", k: int = 3,
                n: int = 1000, seed: int = 7) -> tuple[float, float, float]:
    """Разница мерки «способ a минус способ b» на одних и тех же клиентах и её 95% интервал."""
    bi = {r.client_id: r for r in b}
    pairs = [(r, bi[r.client_id]) for r in a if r.client_id in bi]
    if not pairs:
        return 0.0, 0.0, 0.0
    diff = _per_client([p[0] for p in pairs], metric, k) - _per_client([p[1] for p in pairs], metric, k)
    means = _resampled_means(diff, n, seed)
    return float(diff.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def compare(models: list[Model], events: list[Event], cutoff: date, horizon_days: int = 180, k: int = 3,
            level: str = "family", only_new: bool = False) -> dict:
    """Все способы на одном разрезе. Последний в списке сравнивается с каждым попарно."""
    results = {m.name: run(m, events, cutoff, horizon_days, k, level, only_new) for m in models}
    last = models[-1].name
    report = {"cutoff": cutoff.isoformat(), "horizon_days": horizon_days, "level": level,
              "only_new": only_new, "models": {}}
    for name, rows in results.items():
        entry = {**metrics(rows, k), "slices": slices(rows, k)}
        entry["hit@3_ci"] = bootstrap(rows, "hit@3", k)
        entry["precision@3_ci"] = bootstrap(rows, "precision@3", k)
        if name != last:
            entry["gain_of_last_hit@3"] = paired_gain(results[last], rows, "hit@3", k)
            entry["gain_of_last_precision@3"] = paired_gain(results[last], rows, "precision@3", k)
        report["models"][name] = entry
    return report


def table(report: dict) -> str:
    cols = ["hit@3", "precision@3", "precision_shown", "fill_rate", "fill_with_fallback"]
    head = f"{'способ':28}" + "".join(f"{c:>20}" for c in cols) + f"{'клиентов':>10}"
    lines = [f"разрез {report['cutoff']}, окно {report['horizon_days']} дн., уровень {report['level']}"
             + (", только новые для клиента семейства" if report["only_new"] else ""), head]
    for name, m in report["models"].items():
        if not m.get("clients"):
            lines.append(f"{name:28} нет клиентов в окне")
            continue
        lines.append(f"{name:28}" + "".join(f"{m[c]:>20.1%}" for c in cols) + f"{m['clients']:>10}")
    return "\n".join(lines)
