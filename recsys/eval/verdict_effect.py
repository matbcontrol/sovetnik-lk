"""Что вердикты пар делают с блоком кабинета: сколько карточек, сколько сбылось, кому что показано.

Клиенты официальной проверки (покупки до 31.08.2026, сбылось — осенние покупки). Сравниваются:
  без проверки пар           как было: первые три новых совета по оценке модели;
  только «нужно»             чужая линейка попадает в блок только с вердиктом «нужно» (так на стенде);
  «нужно», затем комплект    свободные места занимает не больше одного совета «комплект»;
  «нужно» и комплект         оба вердикта вперемешку по оценке модели.

Запуск:  python -m recsys.eval.verdict_effect      (отчёт: reports/verdict_effect.json)
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import Counter, defaultdict

from recsys.config import ROOT
from recsys.llm.pair_verdicts import RU, Verdicts
from recsys.rank.cabinet import cabinet
from recsys.rank.data import OFFICIAL, load_task, make_fold
from recsys.rank.demo import CACHE, item_prices
from recsys.rank.explain import short_name
from recsys.rank.features import FoldStats, build_attrs
from recsys.rank.unified import Catalog, Unified
from recsys.stable import rerun_with_fixed_order

REPORT = ROOT / "reports" / "verdict_effect.json"
VARIANTS = [
    ("без проверки пар", dict(verdicts=False)),
    ("только «нужно»", dict(kit_max=0)),
    ("«нужно», затем один комплект", dict(kit_max=1)),
    ("«нужно» и комплект вперемешку", dict(kit_first=True)),
]


def measure(uni: Unified, verdicts: Verdicts, prices: dict, opts: dict) -> dict:
    task, fold, at = uni.task, uni.fold, uni.at
    use = None if opts.get("verdicts") is False else verdicts
    kw = {k: v for k, v in opts.items() if k != "verdicts"}
    per: dict[str, Counter] = defaultdict(Counter)
    lines: dict[str, Counter] = defaultdict(Counter)
    for c in fold.group_a:
        cab = cabinet(uni, c, use, prices, **kw)
        tgt = fold.target[c]
        g = "A " + task.clients[c]["client_group"]
        r = per[g]
        r["клиентов"] += 1
        r["продлений"] += len(cab["renew"])
        r["продлений сбылось"] += sum(x.s.item in tgt for x in cab["renew"])
        r["новых советов"] += len(cab["new"])
        r["новых сбылось"] += sum(x.s.item in tgt for x in cab["new"])
        r["клиентов без новых советов"] += not cab["new"]
        r["строк «часто докупают»"] += len(cab["popular"])
        r["«часто докупают» сбылось"] += sum(x.s.item in tgt for x in cab["popular"])
        every = cab["new"] + cab["renew"] + cab["popular"]
        r["клиентов без карточек"] += not every
        r["клиентов, у кого сбылась хоть одна карточка"] += any(x.s.item in tgt for x in every)
        for x in cab["new"]:
            r[f"вид: {x.tier}"] += 1
            r[f"сбылось: {x.tier}"] += x.s.item in tgt
            ab = at.get(x.s.item)
            lines[g][ab.line if ab else x.s.item] += 1
    out = {}
    for g, r in per.items():
        top = lines[g].most_common(5)
        d = dict(r)
        d["доля сбывшихся новых советов"] = round(r["новых сбылось"] / r["новых советов"], 4) if r["новых советов"] else None
        d["самый частый товар: доля клиентов"] = round(top[0][1] / r["клиентов"], 3) if top else 0
        d["пять самых частых товаров: доля карточек"] = round(sum(n for _, n in top) / r["новых советов"], 3) \
            if r["новых советов"] else 0
        d["самые частые товары"] = [[k, n] for k, n in top]
        out[g] = d
    return out


def main() -> None:
    rerun_with_fixed_order("recsys.eval.verdict_effect")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    at = build_attrs(task)
    cat = Catalog(task)
    off = FoldStats(task, make_fold(task, *OFFICIAL), at)
    model = pickle.loads(CACHE.read_bytes())["test"]["full"]
    uni = Unified(off, model, cat)
    verdicts = Verdicts.load("line")
    prices = item_prices(task)
    name = {}
    for i in task.items:
        name.setdefault(at[i].line, short_name(task, i, 60))
    report = {"verdicts": dict(Counter(RU[v.verdict] for v in verdicts.table.values())), "variants": {},
              "stand": "только «нужно»"}
    for title, opts in VARIANTS:
        m = measure(uni, verdicts, prices, opts)
        for g in m.values():
            g["самые частые товары"] = [[name.get(k, k), n] for k, n in g["самые частые товары"]]
        report["variants"][title] = m
        for g in ("A B2B", "A B2C"):
            r = m.get(g)
            if not r:
                continue
            print(f"{title:32} {g}: новых советов {r['новых советов']:4}, сбылось {r['новых сбылось']:3} "
                  f"({(r['доля сбывшихся новых советов'] or 0):.1%}); без новых советов {r['клиентов без новых советов']:3} "
                  f"из {r['клиентов']}; самый частый товар у {r['самый частый товар: доля клиентов']:.0%} клиентов; "
                  f"«часто докупают» {r['строк «часто докупают»']} (сбылось {r['«часто докупают» сбылось']}); "
                  f"сбылась хоть одна карточка у {r['клиентов, у кого сбылась хоть одна карточка']}")
    report["pairs_without_verdict"] = len(verdicts.missing)
    print("пар без вердикта встретилось:", len(verdicts.missing))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {REPORT}")


if __name__ == "__main__":
    main()
