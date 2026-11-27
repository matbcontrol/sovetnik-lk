"""Пилот в кабинете: тест против контроля по журналу кабинета и расчёт, сколько дней пилот должен идти.

Разбиение: POST /api/advice с client_id и долей контроля (recsys/recommend/block.py, функция group) —
клиент навсегда в тесте или в контроле. Контроль видит нынешний блок кабинета, тест — советника; журнал
(recsys/events.py) пишет показы, переходы и запросы счёта обеих групп с меткой группы.

Отчёт: кликабельность (переходы к показам) и конверсия (запросы счёта к показам) по группам, разница долей
с 95 % интервалом и двусторонним z-тестом, и сколько показов на группу нужно, чтобы заметить такую разницу
(α = 0,05, мощность 80 %).

  python -m recsys.pilot                                  отчёт по журналу data/private/events.jsonl
  python -m recsys.pilot --plan [--daily 300] [--p0 0.05] [--p1 0.075] [--control 0.5]
                                                          сколько дней нужно при таком трафике; базовые доли —
                                                          допущение, первая неделя пилота их уточнит
"""
from __future__ import annotations

import argparse
import json
import math
import sys

from recsys import events

Z95, Z80 = 1.959964, 0.841621


def compare(hits_t: int, n_t: int, hits_c: int, n_c: int) -> dict | None:
    """Доли в тесте и контроле, их разница с 95 % интервалом и p двустороннего z-теста."""
    if not n_t or not n_c:
        return None
    pt, pc = hits_t / n_t, hits_c / n_c
    d = pt - pc
    se = math.sqrt(pt * (1 - pt) / n_t + pc * (1 - pc) / n_c)
    pooled = (hits_t + hits_c) / (n_t + n_c)
    se0 = math.sqrt(pooled * (1 - pooled) * (1 / n_t + 1 / n_c))
    p = math.erfc(abs(d / se0) / math.sqrt(2)) if se0 else 1.0
    return {"test": round(pt, 4), "control": round(pc, 4), "diff": round(d, 4),
            "ci95": [round(d - Z95 * se, 4), round(d + Z95 * se, 4)], "p": round(p, 6)}


def sample_size(p0: float, p1: float) -> int | None:
    """Показов на группу, чтобы заметить разницу p1 − p0: двусторонний тест, α = 0,05, мощность 80 %."""
    if not (0 < p0 < 1 and 0 < p1 < 1) or p0 == p1:
        return None
    pbar = (p0 + p1) / 2
    n = (Z95 * math.sqrt(2 * pbar * (1 - pbar)) + Z80 * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) ** 2 / (p1 - p0) ** 2
    return math.ceil(n)


def days(n_per_group: int, daily: int, control: float) -> int | None:
    """Сколько дней набирать n показов в меньшей группе при daily показах блока в сутки."""
    smaller = min(control, 1 - control)
    if not n_per_group or daily <= 0 or smaller <= 0:
        return None
    return math.ceil(n_per_group / (daily * smaller))


def report(log: list[dict]) -> dict:
    g = events.summarize_groups(log)
    out = {"groups": {k: {**v["total"], "views": v["views"]} for k, v in g.items()}}
    if "test" in g and "control" in g:
        t, c = g["test"]["total"], g["control"]["total"]
        out["ctr"] = compare(t["clicks"], t["shows"], c["clicks"], c["shows"])
        out["conversion"] = compare(t["invoices"], t["shows"], c["invoices"], c["shows"])
        if out["conversion"]:
            p0, p1 = out["conversion"]["control"], out["conversion"]["test"]
            out["needed_per_group"] = sample_size(p0, p1)
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--daily", type=int, default=300, help="показов блока в сутки (в ТЗ — 300 обращений)")
    ap.add_argument("--p0", type=float, default=0.05, help="доля сейчас (контроль)")
    ap.add_argument("--p1", type=float, default=0.075, help="доля, которую хотим заметить (тест)")
    ap.add_argument("--control", type=float, default=0.5, help="доля контроля")
    a = ap.parse_args()
    if a.plan:
        n = sample_size(a.p0, a.p1)
        print(f"заметить {a.p0:.1%} -> {a.p1:.1%}: {n} показов на группу; при {a.daily} в сутки и доле контроля "
              f"{a.control:.0%} — {days(n, a.daily, a.control)} дн.")
        return
    print(json.dumps(report(events.read()), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
