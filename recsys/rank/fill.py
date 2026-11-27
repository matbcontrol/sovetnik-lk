"""Показ блока в единицах заказчика: какой доле клиентов кабинета блок сегодня даст совет и какой именно.

Заказчик считает Fill Rate по визитам: блок «Вам может подойти» показан в 55,6 % визитов, потому что
для остальных клиентов нет ручных связок. Здесь та же мера по клиентам: берётся вся история продаж
(дата «сегодня» — последняя неделя данных) и блок в том виде, в каком его показывает стенд
(recsys.rank.cabinet: проверка пар, затем строка «Часто докупают»).

Клиент отнесён к самому сильному виду совета, который у него есть:
  нужно          — пара «купил А — нужно Б» проверена заранее, у совета есть фраза «зачем»;
  связь графа    — новая версия, старшая редакция или замена снятого с продажи;
  своя линейка   — другой вариант продукта, который у клиента уже есть (не продление);
  комплект       — слабый совет «если ещё нет…»: на истории такие карточки не сбывались;
  дальше — только продление, только «Часто докупают», пусто.
Первые три вида — совет с собственным основанием; его долю и стоит сравнивать с 55,6 %.
Это доля клиентов, а не визитов: активные клиенты заходят чаще, поэтому сравнение приблизительное.

Запуск:  python -m recsys.rank.fill            клиенты с доступом в кабинет (около трёх минут)
         python -m recsys.rank.fill --all      все клиенты с покупками (около десяти минут)
Нужны модели стенда: data/private/task/demo_models.pkl (их пишет python -m recsys.rank.demo).
Отчёт:   reports/block_fill.json (только сводные числа)
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import Counter, defaultdict

from recsys.config import ROOT
from recsys.rank.data import load_task, make_fold
from recsys.rank.features import FoldStats, build_attrs
from recsys.rank.unified import Catalog, Unified

REPORT = ROOT / "reports" / "block_fill.json"
ORDER = ("need", "graph", "line", "kit")                 # от сильного основания к слабому
TITLE = {"need": "есть совет «нужно»", "graph": "связь графа: версия, редакция или замена",
         "line": "другой вариант своей линейки", "kit": "только «комплект»",
         "renew": "только продление", "popular": "только «Часто докупают»", "none": "пусто"}
KINDS = ORDER + ("renew", "popular", "none")
OWN = ("need", "graph", "line")                          # совет с собственным основанием
CURRENT = 0.556                                          # показ блока сейчас, по визитам (лист «Текущий_блок»)


def main() -> None:
    from recsys.llm.pair_verdicts import Verdicts
    from recsys.rank.cabinet import cabinet
    from recsys.rank.demo import CACHE, item_prices
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.fill")
    sys.stdout.reconfigure(encoding="utf-8")
    everyone = "--all" in sys.argv
    if not CACHE.exists():
        sys.exit(f"нет моделей стенда ({CACHE}): сначала python -m recsys.rank.demo")
    task = load_task()
    attrs = build_attrs(task)
    last = max(r.week for r in task.rows)
    now = make_fold(task, "сегодня", last.isoformat(), last.isoformat(), last.isoformat())
    uni = Unified(FoldStats(task, now, attrs), pickle.loads(CACHE.read_bytes())["today"]["full"], Catalog(task))
    verdicts = Verdicts.load("line")
    if not verdicts.table:
        sys.exit("таблица вердиктов по линейкам пуста: без неё блок считается иначе, чем на стенде")
    prices = item_prices(task)

    per: dict[str, Counter] = defaultdict(Counter)
    cards = Counter()
    for c in sorted(now.history):
        info = task.clients.get(c, {})
        access = info.get("lk_access") == "да"
        if not access and not everyone:
            continue
        cab = cabinet(uni, c, verdicts, prices)
        tiers = {x.tier for x in cab["new"]}
        best = next((t for t in ORDER if t in tiers), None)
        if best is None:
            best = "renew" if cab["renew"] else "popular" if cab["popular"] else "none"
        g = info.get("client_group", "")
        keys = [f"с доступом в кабинет: {g}", "с доступом в кабинет: все"] if access else []
        if everyone:
            keys += [f"все клиенты с покупками: {g}", "все клиенты с покупками: все"]
        for k in keys:
            per[k][best] += 1
        if access:
            cards.update(x.tier for x in cab["new"])

    report = {
        "today": last.isoformat(), "current_block_fill_by_visits": CURRENT,
        "how": "доля клиентов по самому сильному виду совета в блоке стенда; по клиентам, а не по визитам",
        "kinds": {k: TITLE[k] for k in KINDS}, "own_ground": list(OWN),
        "personal_cards_by_kind_with_access": dict(cards), "groups": {},
    }
    for k in sorted(per):
        n = sum(per[k].values())
        own = sum(per[k][t] for t in OWN)
        report["groups"][k] = {
            "clients": n, "by_strongest_advice": {t: per[k][t] for t in KINDS},
            "share_with_own_ground_advice": own / n, "share_with_any_personal_advice": (own + per[k]["kit"]) / n,
            "share_with_any_card": 1 - per[k]["none"] / n,
        }
        print(f"\n{k}: клиентов {n}")
        acc = 0
        for t in KINDS:
            acc += per[k][t]
            print(f"   {TITLE[t]:44} {per[k][t]:>6} ({per[k][t] / n:.1%}), нарастающим итогом {acc / n:.1%}")
        print(f"   совет с собственным основанием: {own / n:.1%}; с «комплектом»: {(own + per[k]['kit']) / n:.1%}; "
              f"сейчас блок показан в {CURRENT:.1%} визитов")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nотчёт: {REPORT}")


if __name__ == "__main__":
    main()
