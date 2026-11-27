"""Тест «спрятали — вернул»: может ли агент каталога заново вывести связки товара, заведённого как новый.

Настоящих «дыр», которые закрываются связкой, в блоке почти не осталось (recsys.rank.holes): нужные связки
уже сделаны разметкой. Поэтому работу агента проверяем там, где правильный ответ известен. У товара прячутся
все вердикты «купил его — советовать ли Б», как будто товар завели сегодня: его владельцы теряют личный совет.
Агент (recsys.agent.holes) выводит связи заново по открытому каталогу. Считаем, скольким клиентам совет
вернулся, и сверяем решения агента со спрятанной разметкой.

Шаг 1. Очередь:   python -m recsys.rank.restore
        Товары каталога, на вердиктах которых держится обоснованный совет клиентов кабинета, по убыванию
        числа таких клиентов. Клиент «держится» на товаре, если все его обоснованные карточки — «нужно»
        от покупки этого товара (нет ни варианта своей линейки, ни связи графа, ни «нужно» от другой покупки).
        Результат: data/private/task/restore.json (с названиями, не публикуется), reports/restore.json (сводка).
Шаг 2. Замер:     python -m recsys.rank.restore --links ПУТЬ        (с --dry-run отчёты не перезаписываются)
        ПУТЬ — связи агента в формате knowledge/agent_links.csv (src и dst — ключи семейств «вендор/семейство»).
        Берутся товары из очереди, для которых агент что-то написал. Три состояния блока у их владельцев:
        было (вся разметка), спрятали (без вердиктов этих товаров), вернул агент (спрятанное плюс его связи
        с подтверждающей цитатой). Связь агента «комплект» обоснованным советом не считается, как и в блоке.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from recsys.config import INTERIM, ROOT, TASK_DIR
from recsys.rank.fill import AGENT, OWN, stand_state, strongest

QUEUE = TASK_DIR / "restore.json"
REPORT = ROOT / "reports" / "restore.json"
MIN_CLIENTS = 2                      # в очередь идут товары, от которых зависят хотя бы два клиента кабинета
SHOWN = ("need", "kit")              # решения «советовать»


def family_keys() -> tuple[dict[str, str], dict[str, str]]:
    """Ключ узла графа «вендор/семейство» -> номер семейства в фиде (он же ключ линейки) и обратно."""
    to_id, to_key = {}, {}
    with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("family_id"):
                to_id[r["family"]] = r["family_id"]
                to_key.setdefault(r["family_id"], r["family"])
    return to_id, to_key


def grounded(uni, verdicts, prices: dict, clients: list[str]) -> dict[str, bool]:
    """Есть ли у клиента совет с собственным основанием при данной таблице вердиктов."""
    from recsys.rank.cabinet import cabinet

    return {c: strongest(cabinet(uni, c, verdicts, prices)) in OWN for c in clients}


def build_queue() -> None:
    from recsys.rank.cabinet import cabinet

    task, attrs, last, cat, uni, verdicts, prices = stand_state()
    now = uni.fold
    _, to_key = family_keys()
    dep: dict[str, list[str]] = defaultdict(list)
    state = Counter()
    sample: dict[str, str] = {}
    for c in sorted(now.history):
        info = task.clients.get(c, {})
        if info.get("lk_access") != "да":
            continue
        cab = cabinet(uni, c, verdicts, prices)
        tiers = Counter(x.tier for x in cab["new"])
        if tiers["line"] or tiers["graph"]:
            state["совет не зависит от вердиктов"] += 1
            continue
        if not tiers["need"]:
            state["обоснованного совета нет"] += 1
            continue
        by = {attrs[x.by].line for x in cab["new"] if x.tier == "need" and x.by in attrs}
        if len(by) != 1:
            state["«нужно» от нескольких покупок"] += 1
            continue
        ln = next(iter(by))
        dep[ln].append(c)
        state["держится на вердиктах одного товара"] += 1
        sample.setdefault(ln, next(x.by for x in cab["new"] if x.tier == "need" and x.by in attrs))

    pairs: dict[str, Counter] = defaultdict(Counter)
    need_to: dict[str, list[str]] = defaultdict(list)
    for (a, b), v in verdicts.table.items():
        if (v.source or "").startswith(AGENT):
            continue
        pairs[a][v.verdict] += 1
        if v.verdict == "need":
            need_to[a].append(b)

    rows = []
    for ln, clients in sorted(dep.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if ln.startswith("L:") or len(clients) < MIN_CLIENTS:
            continue                                   # товара нет в каталоге — агенту по API он не передаётся
        item = sample[ln]
        offer = cat.offer_of(item) or {}
        groups = Counter(task.clients[c]["client_group"] for c in clients)
        rows.append({
            "line": ln, "family_id": ln, "family": to_key.get(ln, ""), "item": item, "name": task.names[item],
            "catalog_name": offer.get("name", ""), "url": offer.get("url", ""),
            "class_id": attrs[item].cls, "vendor": attrs[item].vendor,
            "clients": len(clients), "clients_b2b": groups["B2B"], "clients_b2c": groups["B2C"],
            "kind": "закрывает агент", "hidden_pairs": dict(pairs.get(ln, {})),
            "need_to": [{"line": b, "family": to_key.get(b, "")} for b in sorted(need_to.get(ln, []))],
        })
    QUEUE.write_text(json.dumps({"today": last.isoformat(), "holes": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {
        "today": last.isoformat(), "clients_with_access": sum(state.values()), "clients_by_state": dict(state),
        "queue": {"products": len(rows), "clients": sum(r["clients"] for r in rows),
                  "clients_b2b": sum(r["clients_b2b"] for r in rows), "clients_b2c": sum(r["clients_b2c"] for r in rows),
                  "largest_product_clients": rows[0]["clients"] if rows else 0},
        "how": "клиент «держится» на товаре, если все его обоснованные карточки — «нужно» от покупки этого товара",
    }
    old = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    summary["measured"] = old.get("measured", {})          # замер шага 2 сохраняется, пока не пересчитан
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"клиентов кабинета {summary['clients_with_access']}: {dict(state)}")
    q = summary["queue"]
    print(f"очередь: товаров {q['products']}, от них зависят {q['clients']} клиентов "
          f"(организаций {q['clients_b2b']}, частных лиц {q['clients_b2c']}); у первого товара {q['largest_product_clients']}")
    print(f"очередь: {QUEUE}\nотчёт: {REPORT}")


def measure(path: Path) -> None:
    from recsys.graph import agent_links
    from recsys.llm.pair_verdicts import Verdict, Verdicts

    if not QUEUE.exists():
        sys.exit(f"нет очереди ({QUEUE}): сначала python -m recsys.rank.restore")
    queue = {h["line"]: h for h in json.loads(QUEUE.read_text(encoding="utf-8"))["holes"]}
    task, attrs, last, cat, uni, verdicts, prices = stand_state()
    now = uni.fold
    to_id, _ = family_keys()
    links = []                                             # (линейка А, линейка Б, решение, подтверждена ли цитатой)
    for r in agent_links.rows(path):
        a, b = to_id.get(r["src"], ""), to_id.get(r["dst"], "")
        if a in queue and b:
            links.append((a, b, "kit" if r["source"] == agent_links.KIT else "need", r.get("status") == "graph",
                          r.get("evidence", "")))
    tried = sorted({a for a, *_ in links})
    if not tried:
        sys.exit(f"в {path} нет связей для товаров из очереди: агент ещё не разбирал их или ключи семейств другие")

    base = {k: v for k, v in verdicts.table.items() if not (v.source or "").startswith(AGENT)}
    hidden = {k: v for k, v in base.items() if k[0] not in tried}
    restored = dict(hidden)
    for a, b, decision, confirmed, why in links:
        if confirmed:
            restored[(a, b)] = Verdict(decision, why, AGENT, "")

    owners = [c for c in sorted(now.history) if task.clients.get(c, {}).get("lk_access") == "да"
              and any(attrs[x].line in tried for x in now.history[c])]
    was = grounded(uni, Verdicts(base), prices, owners)
    gone = grounded(uni, Verdicts(hidden), prices, owners)
    back = grounded(uni, Verdicts(restored), prices, owners)

    def count(flags: dict, clients: list[str]) -> dict:
        g = Counter(task.clients[c]["client_group"] for c in clients if flags[c])
        return {"all": g["B2B"] + g["B2C"], "b2b": g["B2B"], "b2c": g["B2C"]}

    per = []
    for a in tried:
        own = [c for c in owners if any(attrs[x].line == a for x in now.history[c])]
        w, g, b = count(was, own), count(gone, own), count(back, own)
        mine = [x for x in links if x[0] == a]
        need_was = {k[1] for k, v in base.items() if k[0] == a and v.verdict == "need"}
        need_now = {x[1] for x in mine if x[2] == "need" and x[3]}
        per.append({"line": a, "name": queue[a]["name"], "owners": len(own), "with_advice_before": w["all"],
                    "with_advice_hidden": g["all"], "with_advice_restored": b["all"],
                    "agent_links": len(mine), "agent_need_confirmed": len(need_now),
                    "need_pairs_hidden": len(need_was), "need_pairs_found": len(need_was & need_now)})

    # сверка решений агента со спрятанной разметкой по тем же парам
    agree = Counter()
    for a, b, decision, confirmed, _ in links:
        v = base.get((a, b))
        if v is None:
            agree["пары нет в разметке"] += 1
        else:
            agree["то же решение" if (v.verdict in SHOWN) == (decision in SHOWN) else "решение другое"] += 1
            agree["тот же вердикт"] += v.verdict == decision
    need_hidden = sum(p["need_pairs_hidden"] for p in per)
    need_found = sum(p["need_pairs_found"] for p in per)

    total = sum(task.clients.get(c, {}).get("lk_access") == "да" for c in now.history)
    w, g, b = count(was, owners), count(gone, owners), count(back, owners)
    lost, returned = w["all"] - g["all"], b["all"] - g["all"]
    measured = {
        "links_file": path.name, "products": len(tried), "owners_with_access": len(owners),
        "clients_with_access": total,
        "with_own_ground_advice": {"before": w, "hidden": g, "restored": b},
        "lost_when_hidden": lost, "returned_by_agent": returned,
        "share_returned": returned / lost if lost else None,
        "lost_share_of_all_clients": lost / total, "returned_share_of_all_clients": returned / total,
        "agent_links": len(links), "agent_links_confirmed": sum(x[3] for x in links),
        "pairs_vs_labelling": dict(agree),
        "need_pairs_hidden": need_hidden, "need_pairs_found_by_agent": need_found,
    }
    if "--dry-run" not in sys.argv:                        # пробный прогон отчёты не трогает
        report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
        report["measured"] = measured
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        private = json.loads(QUEUE.read_text(encoding="utf-8"))
        private["measured"] = per
        QUEUE.write_text(json.dumps(private, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"товаров, разобранных агентом: {len(tried)}; их владельцев в кабинете {len(owners)} из {total}")
    print(f"обоснованный совет у владельцев: было {w['all']} -> спрятали {g['all']} -> вернул агент {b['all']} "
          f"(организации {w['b2b']} -> {g['b2b']} -> {b['b2b']}; частные лица {w['b2c']} -> {g['b2c']} -> {b['b2c']})")
    if lost:
        print(f"потеряли совет {lost} клиентов ({lost / total:.1%} кабинета), агент вернул {returned} ({returned / lost:.0%})")
    print(f"связей агента {len(links)}, с цитатой {measured['agent_links_confirmed']}; сверка с разметкой: {dict(agree)}; "
          f"спрятанных «нужно» {need_hidden}, агент нашёл {need_found}")
    for p in per:
        print(f"   {p['name'][:56]:56} владельцев {p['owners']:>4}: {p['with_advice_before']} -> {p['with_advice_hidden']} "
              f"-> {p['with_advice_restored']}; «нужно» найдено {p['need_pairs_found']} из {p['need_pairs_hidden']}")
    print("пробный прогон: отчёты не записаны" if "--dry-run" in sys.argv else f"отчёт: {REPORT}")


def main() -> None:
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.restore")
    sys.stdout.reconfigure(encoding="utf-8")
    if "--links" in sys.argv:
        measure(Path(sys.argv[sys.argv.index("--links") + 1]))
    else:
        build_queue()


if __name__ == "__main__":
    main()
