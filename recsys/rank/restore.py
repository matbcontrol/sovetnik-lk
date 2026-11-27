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
        Прячутся все товары, которые агент разбирал, — и те, где он ответил «связи нет» (список берётся из его
        отчёта рядом со связями, то же имя с расширением .json). Отдельно считается четвёртое состояние: вместе
        со связями без дословной цитаты, которые ушли в очередь менеджера, — что вернётся после его согласия.

Широкая выборка. Шесть товаров очереди видели все, кто настраивал агента. Чтобы проверка не была подогнана
под них, есть вторая очередь:  python -m recsys.rank.restore --wide
В неё идут все остальные товары каталога, у которых в разметке есть хотя бы одно «нужно» на другой товар
каталога (агент может его назвать). Замер по ней:
        python -m recsys.rank.restore --links ПУТЬ --queue data/private/task/restore_wide.json
Он пишется в отчёт отдельным ключом measured_wide (свой ключ задаёт --key ИМЯ — для второго варианта того же
прогона, например с рассуждением). Владельцы этих товаров держат совет и без их вердиктов (у них есть варианты
своей линейки), поэтому число клиентов здесь не меняется; главное — сколько спрятанных «нужно» агент нашёл
и совпадают ли его решения с разметкой.
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
WIDE = TASK_DIR / "restore_wide.json"
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
    for k in ("measured", "queue_wide", "measured_wide"):   # замеры и вторая очередь сохраняются, пока не пересчитаны
        if k in old or k == "measured":
            summary[k] = old.get(k, {})
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"клиентов кабинета {summary['clients_with_access']}: {dict(state)}")
    q = summary["queue"]
    print(f"очередь: товаров {q['products']}, от них зависят {q['clients']} клиентов "
          f"(организаций {q['clients_b2b']}, частных лиц {q['clients_b2c']}); у первого товара {q['largest_product_clients']}")
    print(f"очередь: {QUEUE}\nотчёт: {REPORT}")


def build_wide() -> None:
    """Вторая очередь: товары каталога со спрятанным «нужно» на товар каталога, кроме шести из основной очереди."""
    task, attrs, last, cat, uni, verdicts, prices = stand_state()
    _, to_key = family_keys()
    main = {h["line"] for h in json.loads(QUEUE.read_text(encoding="utf-8"))["holes"]} if QUEUE.exists() else set()
    pairs: dict[str, Counter] = defaultdict(Counter)
    need_to: dict[str, list[str]] = defaultdict(list)
    for (a, b), v in verdicts.table.items():
        if (v.source or "").startswith(AGENT):
            continue
        pairs[a][v.verdict] += 1
        if v.verdict == "need" and b in to_key:
            need_to[a].append(b)
    sample = {}
    for item in sorted(task.names):
        sample.setdefault(attrs[item].line, item)
    owners = Counter()
    for c, h in uni.fold.history.items():
        if task.clients.get(c, {}).get("lk_access") == "да":
            owners.update({attrs[x].line for x in h})
    rows = []
    for ln in sorted(need_to, key=lambda x: (-owners[x], x)):
        if ln in main or ln not in to_key or ln not in sample:
            continue
        item = sample[ln]
        offer = cat.offer_of(item) or {}
        rows.append({
            "line": ln, "family_id": ln, "family": to_key[ln], "item": item, "name": task.names[item],
            "catalog_name": offer.get("name", ""), "url": offer.get("url", ""),
            "class_id": attrs[item].cls, "vendor": attrs[item].vendor, "clients": owners[ln],
            "kind": "закрывает агент", "hidden_pairs": dict(pairs[ln]),
            "need_to": [{"line": b, "family": to_key[b]} for b in sorted(need_to[ln])],
        })
    WIDE.write_text(json.dumps({"today": last.isoformat(), "holes": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    report["queue_wide"] = {"products": len(rows), "need_pairs_hidden": sum(len(r["need_to"]) for r in rows),
                            "owners_with_access": sum(r["clients"] for r in rows)}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    q = report["queue_wide"]
    print(f"широкая очередь: товаров {q['products']}, спрятанных «нужно» на товары каталога {q['need_pairs_hidden']}")
    print(f"очередь: {WIDE}")
    print(f"отчёт: {REPORT}")


def measure(path: Path) -> None:
    from recsys.graph import agent_links
    from recsys.llm.pair_verdicts import Verdict, Verdicts

    qfile = Path(sys.argv[sys.argv.index("--queue") + 1]) if "--queue" in sys.argv else QUEUE
    key = "measured" if qfile.resolve() == QUEUE.resolve() else "measured_wide"
    if "--key" in sys.argv:                                # второй заранее объявленный вариант той же проверки
        key = sys.argv[sys.argv.index("--key") + 1]
    if not qfile.exists():
        sys.exit(f"нет очереди ({qfile}): сначала python -m recsys.rank.restore" + (" --wide" if key != "measured" else ""))
    queue = {h["line"]: h for h in json.loads(qfile.read_text(encoding="utf-8"))["holes"]}
    task, attrs, last, cat, uni, verdicts, prices = stand_state()
    now = uni.fold
    to_id, to_key = family_keys()
    links = []                                # (линейка А, линейка Б, решение, подтверждена ли цитатой, фраза)
    off_graph = 0
    for r in agent_links.rows(path):
        a, b = to_id.get(r["src"], ""), to_id.get(r["dst"], "")
        if a not in queue:
            continue
        if not b:
            off_graph += 1
            continue
        links.append((a, b, "kit" if r["source"] == agent_links.KIT else "need", r.get("status") == "graph",
                      r.get("evidence", "")))
    # Разобранные товары — все из отчёта агента, включая те, где он ответил «связи нет»: спрятать нужно и их,
    # иначе замер считал бы только удачи. Отчёт лежит рядом со связями, с тем же именем и расширением .json.
    tried = {a for a, *_ in links}
    summary = {}
    side = path.with_suffix(".json")
    if side.exists():
        rep = json.loads(side.read_text(encoding="utf-8"))
        holes = rep.get("holes") or []
        tried |= {to_id.get(h.get("family", ""), "") for h in (holes.values() if isinstance(holes, dict) else holes)}
        summary = rep.get("summary") or {}
    tried = sorted(tried & set(queue))
    if not tried:
        sys.exit(f"в {path} нет товаров из очереди: агент ещё не разбирал их или ключи семейств другие")

    base = {k: v for k, v in verdicts.table.items() if not (v.source or "").startswith(AGENT)}
    hidden = {k: v for k, v in base.items() if k[0] not in tried}
    restored, proposed = dict(hidden), dict(hidden)
    for a, b, decision, confirmed, why in links:
        proposed[(a, b)] = Verdict(decision, why, AGENT, "")            # всё, что агент предложил
        if confirmed:
            restored[(a, b)] = Verdict(decision, why, AGENT, "")        # только с дословной цитатой

    owners = [c for c in sorted(now.history) if task.clients.get(c, {}).get("lk_access") == "да"
              and any(attrs[x].line in tried for x in now.history[c])]
    was = grounded(uni, Verdicts(base), prices, owners)
    gone = grounded(uni, Verdicts(hidden), prices, owners)
    back = grounded(uni, Verdicts(restored), prices, owners)
    maybe = grounded(uni, Verdicts(proposed), prices, owners) if proposed != restored else back

    def count(flags: dict, clients: list[str]) -> dict:
        g = Counter(task.clients[c]["client_group"] for c in clients if flags[c])
        return {"all": g["B2B"] + g["B2C"], "b2b": g["B2B"], "b2c": g["B2C"]}

    per = []
    for a in tried:
        own = [c for c in owners if any(attrs[x].line == a for x in now.history[c])]
        mine = [x for x in links if x[0] == a]
        need_was = {k[1] for k, v in base.items() if k[0] == a and v.verdict == "need"}
        need_sure = {x[1] for x in mine if x[2] == "need" and x[3]}
        need_any = {x[1] for x in mine if x[2] == "need"}
        per.append({"line": a, "name": queue[a]["name"], "owners": len(own),
                    "with_advice_before": count(was, own)["all"], "with_advice_hidden": count(gone, own)["all"],
                    "with_advice_restored": count(back, own)["all"], "with_advice_proposed": count(maybe, own)["all"],
                    "agent_links": len(mine), "agent_links_confirmed": sum(x[3] for x in mine),
                    "need_pairs_hidden": len(need_was), "need_pairs_found": len(need_was & need_sure),
                    "need_pairs_proposed": len(need_was & need_any),
                    # агент может назвать только товар открытого каталога: по ним полнота и считается
                    "need_pairs_hidden_in_catalog": sum(x in to_key for x in need_was)})

    # сверка решений агента со спрятанной разметкой по тем же парам
    agree = Counter()
    for a, b, decision, confirmed, _ in links:
        v = base.get((a, b))
        if v is None:
            agree["пары нет в разметке"] += 1
        else:
            agree["то же решение" if (v.verdict in SHOWN) == (decision in SHOWN) else "решение другое"] += 1
            agree["тот же вердикт"] += v.verdict == decision
    need_hidden = sum(x["need_pairs_hidden"] for x in per)
    need_found = sum(x["need_pairs_found"] for x in per)
    need_proposed = sum(x["need_pairs_proposed"] for x in per)
    need_in_catalog = sum(x["need_pairs_hidden_in_catalog"] for x in per)

    total = sum(task.clients.get(c, {}).get("lk_access") == "да" for c in now.history)
    w, g, b, m = count(was, owners), count(gone, owners), count(back, owners), count(maybe, owners)
    lost, returned, with_manager = w["all"] - g["all"], b["all"] - g["all"], m["all"] - g["all"]
    waiting = sum(not x[3] for x in links)                # связи без дословной цитаты: ждут менеджера
    silent = len(tried) - len({a for a, *_ in links})      # товары, по которым агент ответил «связи нет»
    note = (f"Разница между «вернул сам» и «с менеджером» — связи без дословной цитаты ({waiting}): агент их "
            f"назвал, но в граф не поставил. " if waiting else "В очередь менеджера агент ничего не отправил. ")
    note += f"По {silent} товарам из {len(tried)} агент ответил «обоснованной связи нет»."
    measured = {
        "links_file": path.name, "products": len(tried), "note": note,
        "products_with_agent_link": len({a for a, *_ in links}),
        "owners_with_access": len(owners), "clients_with_access": total,
        # restored — только связи с дословной цитатой (идут в граф сами); proposed — вместе с теми, что ждут менеджера
        "with_own_ground_advice": {"before": w, "hidden": g, "restored": b, "proposed": m},
        "lost_when_hidden": lost, "returned_by_agent": returned,
        "share_returned": returned / lost if lost else None,
        "returned_with_manager": with_manager,
        "share_returned_with_manager": with_manager / lost if lost else None,
        "lost_share_of_all_clients": lost / total, "returned_share_of_all_clients": returned / total,
        "agent_links": len(links), "agent_links_confirmed": sum(x[3] for x in links),
        "agent_links_to_products_outside_graph": off_graph,
        "pairs_vs_labelling": dict(agree),
        "need_pairs_hidden": need_hidden, "need_pairs_found_by_agent": need_found,
        "need_pairs_proposed_by_agent": need_proposed,
        "need_pairs_hidden_in_catalog": need_in_catalog,
        "share_of_hidden_need_found": need_found / need_in_catalog if need_in_catalog else None,
        "agent_run": {k: summary[k] for k in ("tool_calls_by_model", "tokens_in", "tokens_out", "seconds") if k in summary},
    }
    if "--dry-run" not in sys.argv:                        # пробный прогон отчёты не трогает
        report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
        report[key] = measured
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        private = json.loads(qfile.read_text(encoding="utf-8"))
        private["measured"] = per
        qfile.write_text(json.dumps(private, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"товаров, разобранных агентом: {len(tried)} (со связью {measured['products_with_agent_link']}); "
          f"их владельцев в кабинете {len(owners)} из {total}")
    print(f"обоснованный совет у владельцев: было {w['all']} -> спрятали {g['all']} -> вернул агент {b['all']} "
          f"-> с очередью менеджера {m['all']} (организации {w['b2b']} -> {g['b2b']} -> {b['b2b']} -> {m['b2b']}; "
          f"частные лица {w['b2c']} -> {g['b2c']} -> {b['b2c']} -> {m['b2c']})")
    if lost:
        print(f"потеряли совет {lost} клиентов ({lost / total:.1%} кабинета); агент вернул сам {returned} "
              f"({returned / lost:.0%}), вместе с предложенным менеджеру {with_manager} ({with_manager / lost:.0%})")
    print(f"связей агента {len(links)}, с цитатой {measured['agent_links_confirmed']}; сверка с разметкой: {dict(agree)}; "
          f"спрятанных «нужно» {need_hidden} (из них на товары каталога {need_in_catalog}): "
          f"найдено с цитатой {need_found}, предложено {need_proposed}")
    for x in per:
        print(f"   {x['name'][:52]:52} владельцев {x['owners']:>4}: {x['with_advice_before']} -> {x['with_advice_hidden']} "
              f"-> {x['with_advice_restored']} -> {x['with_advice_proposed']}; «нужно» {x['need_pairs_found']}"
              f"/{x['need_pairs_proposed']} из {x['need_pairs_hidden']}")
    print("пробный прогон: отчёты не записаны" if "--dry-run" in sys.argv else f"отчёт: {REPORT}")


def main() -> None:
    from recsys.stable import rerun_with_fixed_order

    rerun_with_fixed_order("recsys.rank.restore")
    sys.stdout.reconfigure(encoding="utf-8")
    if "--links" in sys.argv:
        measure(Path(sys.argv[sys.argv.index("--links") + 1]))
    elif "--wide" in sys.argv:
        build_wide()
    else:
        build_queue()


if __name__ == "__main__":
    main()
