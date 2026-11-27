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

«Было → стало». Связи, которые добавил агент каталога (knowledge/agent_links.csv), попадают в таблицу
вердиктов с источником «агент каталога…». Если такие есть, блок считается дважды: без них — это
before_holes, состояние до работы агента, — и с ними. Нет связей агента — оба состояния совпадают.

Запуск:  python -m recsys.rank.fill            клиенты с доступом в кабинет (около двух минут на состояние)
         python -m recsys.rank.fill --all      ещё и все клиенты с покупками (около десяти минут)
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
AGENT = "агент каталога"                                 # так начинается источник вердикта, добавленного агентом


def without_agent(verdicts):
    """Таблица вердиктов без связей агента каталога — состояние «до» — и число убранных связей."""
    from recsys.llm.pair_verdicts import Verdicts

    table = {k: v for k, v in verdicts.table.items() if not (v.source or "").startswith(AGENT)}
    return Verdicts(table), len(verdicts.table) - len(table)


def strongest(cab: dict) -> str:
    """Самый сильный вид совета в блоке клиента."""
    tiers = {x.tier for x in cab["new"]}
    best = next((t for t in ORDER if t in tiers), None)
    if best is None:
        best = "renew" if cab["renew"] else "popular" if cab["popular"] else "none"
    return best


def measure(uni: Unified, verdicts, prices: dict, everyone: bool) -> tuple[dict, dict]:
    """Клиенты по самому сильному виду совета; вторым значением — личные карточки по видам (кабинет)."""
    from recsys.rank.cabinet import cabinet

    task, now = uni.task, uni.fold
    per: dict[str, Counter] = defaultdict(Counter)
    cards = Counter()
    for c in sorted(now.history):
        info = task.clients.get(c, {})
        access = info.get("lk_access") == "да"
        if not access and not everyone:
            continue
        cab = cabinet(uni, c, verdicts, prices)
        best = strongest(cab)
        g = info.get("client_group", "")
        keys = [f"с доступом в кабинет: {g}", "с доступом в кабинет: все"] if access else []
        if everyone:
            keys += [f"все клиенты с покупками: {g}", "все клиенты с покупками: все"]
        for k in keys:
            per[k][best] += 1
        if access:
            cards.update(x.tier for x in cab["new"])
    groups = {}
    for k in sorted(per):
        n = sum(per[k].values())
        own = sum(per[k][t] for t in OWN)
        groups[k] = {
            "clients": n, "by_strongest_advice": {t: per[k][t] for t in KINDS},
            "share_with_own_ground_advice": own / n, "share_with_any_personal_advice": (own + per[k]["kit"]) / n,
            "share_with_any_card": 1 - per[k]["none"] / n,
        }
    return groups, dict(cards)


def stand_state():
    """Задача, блок «сегодня» и вердикты — то, на чём считают и этот замер, и очередь дыр."""
    from recsys.llm.pair_verdicts import Verdicts
    from recsys.rank.demo import CACHE, item_prices

    if not CACHE.exists():
        sys.exit(f"нет моделей стенда ({CACHE}): сначала python -m recsys.rank.demo")
    task = load_task()
    attrs = build_attrs(task)
    last = max(r.week for r in task.rows)
    now = make_fold(task, "сегодня", last.isoformat(), last.isoformat(), last.isoformat())
    cat = Catalog(task)
    uni = Unified(FoldStats(task, now, attrs), pickle.loads(CACHE.read_bytes())["today"]["full"], cat)
    verdicts = Verdicts.load("line")
    if not verdicts.table:
        sys.exit("таблица вердиктов по линейкам пуста: без неё блок считается иначе, чем на стенде")
    return task, attrs, last, cat, uni, verdicts, item_prices(task)


def main() -> None:
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.fill")
    sys.stdout.reconfigure(encoding="utf-8")
    everyone = "--all" in sys.argv
    task, attrs, last, cat, uni, verdicts, prices = stand_state()
    before_table, n_agent = without_agent(verdicts)
    groups, cards = measure(uni, verdicts, prices, everyone)
    before = measure(uni, before_table, prices, everyone)[0] if n_agent else groups

    report = {
        "today": last.isoformat(), "current_block_fill_by_visits": CURRENT,
        "how": "доля клиентов по самому сильному виду совета в блоке стенда; по клиентам, а не по визитам",
        "kinds": {k: TITLE[k] for k in KINDS}, "own_ground": list(OWN),
        "personal_cards_by_kind_with_access": cards,
        "agent_links_in_verdict_table": n_agent,
        "groups": groups,
        # состояние до связей агента каталога: тот же расчёт без вердиктов с источником «агент каталога…»
        "before_holes": before,
    }
    for k, r in groups.items():
        n = r["clients"]
        print(f"\n{k}: клиентов {n}")
        acc = 0
        for t in KINDS:
            acc += r["by_strongest_advice"][t]
            print(f"   {TITLE[t]:44} {r['by_strongest_advice'][t]:>6} ({r['by_strongest_advice'][t] / n:.1%}), "
                  f"нарастающим итогом {acc / n:.1%}")
        was = before[k]["share_with_own_ground_advice"]
        print(f"   совет с собственным основанием: {r['share_with_own_ground_advice']:.1%}"
              + (f" (до связей агента {was:.1%})" if n_agent else "")
              + f"; с «комплектом»: {r['share_with_any_personal_advice']:.1%}; сейчас блок показан в {CURRENT:.1%} визитов")
    print(f"\nсвязей агента каталога в таблице вердиктов: {n_agent}")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {REPORT}")


if __name__ == "__main__":
    main()
