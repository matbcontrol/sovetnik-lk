"""Клиенты и советы для стенда: что показала бы модель и почему.

Два набора клиентов:
  «проверка на истории» — клиенты официальной проверки: советы даны по покупкам до 31.08.2026,
                          рядом показано, что клиент на самом деле купил осенью;
  «сегодня»             — клиенты с доступом в личный кабинет: советы по всей истории.

Для каждого клиента советы посчитаны в двух режимах: с полной историей и «товар-новичок»
(история купленных товаров спрятана, работают только знания каталога) — чтобы показать вживую,
что совет не пропадает, когда о товаре нет ни одной продажи.

Результат: site/data/private.json. В нём строки клиентов из датасета организаторов, поэтому файл
не публикуется и в репозиторий не попадает; стенд без него работает на открытых данных.

Запуск:  python -m recsys.rank.demo
"""
from __future__ import annotations

import gzip
import json
import pickle
import random
import sys
from datetime import date, datetime, timezone

from recsys.config import DATA, INTERIM, ROOT
from recsys.eval.value import Prep, in_regime
from recsys.rank.data import OFFICIAL, Fold, load_task, make_fold
from recsys.rank.explain import advice, describe, short_name, title_name
from recsys.rank.features import FoldStats, build_attrs
from recsys.rank.ltr import copies, fit
from recsys.stable import rerun_with_fixed_order

OUT = ROOT / "site" / "data" / "private.json"
CACHE = DATA / "private" / "task" / "demo_models.pkl"      # обученные модели; пересчёт: --refit
REGIMES = ["full", "new_item"]
TODAY_SAMPLE = 240


def recommend(st: FoldStats, model, clients: list[str], regime: str, diverse: bool = False) -> dict[str, list[str]]:
    """Три товара каждому клиенту из списка (клиент должен иметь покупки в обучающей части разреза).

    diverse=False — ровно как в протоколе проверки: три лучших по оценке.
    diverse=True  — как в блоке кабинета: не больше одного товара из одной линейки, чтобы клиент
                    не видел три платежа одной подписки подряд.
    """
    fold, at = st.fold, st.attrs
    keep = fold.group_a
    fold.group_a = clients                       # Prep строит строки для клиентов из group_a
    try:
        prep = Prep(st, regime)
        if not len(prep.y):
            return {c: prep.fallback[c][:3] for c in clients}
        ranked = prep.recommend(model.predict_proba(prep.X)[:, 1], top=3 if not diverse else 15)
    finally:
        fold.group_a = keep
    if not diverse:
        return ranked
    out = {}
    for c, items in ranked.items():
        seen, picked = set(), []
        for b in items:
            line = at[b].pkey or at[b].line
            if line in seen:
                continue
            seen.add(line)
            picked.append(b)
            if len(picked) == 3:
                break
        out[c] = picked
    return out


def client_card(st: FoldStats, c: str, recs: dict[str, dict[str, list[str]]], mode: str, cold: bool) -> dict:
    task, fold = st.task, st.fold
    info = task.clients.get(c, {})
    out = {
        "id": c, "group": info.get("client_group", ""), "type": info.get("client_type", ""),
        "segment": info.get("segment", ""), "industry": info.get("industry", ""), "lk": info.get("lk_access", ""),
        "mode": mode, "cold": cold,
        "history": [{"item": a, "first": fold.first[c][a].isoformat(), "last": fold.last[c][a].isoformat()}
                    for a in fold.history[c]],
        "target": sorted(fold.target.get(c, ())) if mode == "test" else [],
        "recs": {},
    }
    for regime in REGIMES:
        with in_regime(st, fold.history[c], regime):
            cards = [advice(st, c, b) for b in recs[regime][c]]
        for card in cards:
            card["hit"] = card["item"] in fold.target.get(c, ()) if mode == "test" else None
        out["recs"][regime] = cards
    return out


def main() -> None:
    rerun_with_fixed_order("recsys.rank.demo")
    sys.stdout.reconfigure(encoding="utf-8")
    task = load_task()
    attrs = build_attrs(task)
    rnd = random.Random(7)

    # ---- проверка на истории: официальный разрез
    specs = copies(date.fromisoformat(OFFICIAL[1]))
    train_stats = [FoldStats(task, make_fold(task, *s), attrs) for s in specs]
    off = FoldStats(task, make_fold(task, *OFFICIAL), attrs)
    cached = pickle.loads(CACHE.read_bytes()) if CACHE.exists() and "--refit" not in sys.argv else {}
    models = cached.get("test") or {r: fit([Prep(st, r) for st in train_stats]) for r in REGIMES}
    clients = list(off.fold.group_a)
    recs = {r: recommend(off, models[r], clients, r) for r in REGIMES}
    cold = set(off.fold.group_b)
    cards = [client_card(off, c, recs, "test", c in cold) for c in clients]
    hits = sum(any(x["hit"] for x in card["recs"]["full"]) for card in cards)
    print(f"проверка на истории: клиентов {len(cards)}, из них с хотя бы одним угаданным советом {hits}")

    # ---- сегодня: вся история, клиенты с доступом в кабинет
    last = max(r.week for r in task.rows)
    spec_now = ("сегодня", last.isoformat(), (last.replace(day=last.day)).isoformat(), last.isoformat())
    now: Fold = make_fold(task, *spec_now)
    now_stats = FoldStats(task, now, attrs)
    all_specs = copies(last)
    models_now = cached.get("today")
    if not models_now:
        more = [FoldStats(task, make_fold(task, *s), attrs) for s in all_specs[len(specs):]]
        models_now = {r: fit([Prep(st, r) for st in train_stats + more]) for r in REGIMES}
        CACHE.write_bytes(pickle.dumps({"test": models, "today": models_now}))
    pool = [c for c in now.history if task.clients.get(c, {}).get("lk_access") == "да"]
    rnd.shuffle(pool)
    b2b = [c for c in pool if task.clients[c]["client_group"] == "B2B"][: TODAY_SAMPLE // 2]
    b2c = [c for c in pool if task.clients[c]["client_group"] == "B2C"][: TODAY_SAMPLE // 2]
    today = b2b + b2c
    recs_now = {r: recommend(now_stats, models_now[r], today, r, diverse=True) for r in REGIMES}
    cards_now = [client_card(now_stats, c, recs_now, "today", len(now.history[c]) == 1) for c in today]
    print(f"сегодня ({last}): клиентов {len(cards_now)} (организаций {len(b2b)}, частных лиц {len(b2c)})")

    # ---- справочник товаров, которые встречаются в карточках
    price, url = {}, {}
    with gzip.open(INTERIM / "feed_offers.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            price[o["offer_id"]], url[o["offer_id"]] = o["price"], o["url"]
    used = set()
    for card in cards + cards_now:
        used |= {h["item"] for h in card["history"]} | set(card["target"])
        for regime in REGIMES:
            used |= {x["item"] for x in card["recs"][regime]}
    items = {}
    for i in used:
        r = task.items.get(i) or {}
        off_id = r.get("offer_id") or ""
        try:
            p = float(price.get(off_id) or 0)
        except ValueError:
            p = 0.0
        items[i] = {
            "name": task.names.get(i, i), "title": title_name(task, i), "short": short_name(task, i),
            "desc": describe(task, i, attrs[i]),
            "vendor": r.get("vendor", ""), "cls": r.get("subcategory", ""), "url": url.get(off_id, r.get("url", "")),
            "price": p if p > 0 else None,
        }
    data = {
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Обезличенный датасет организаторов хакатона. Не публиковать.",
        "test_from": OFFICIAL[2], "train_until": OFFICIAL[1], "today": last.isoformat(),
        "items": items, "clients": cards + cards_now,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано: {OUT} ({OUT.stat().st_size / 1e6:.1f} МБ), товаров {len(items)}")
    # несколько примеров в консоль — посмотреть, как читаются объяснения
    for card in rnd.sample([c for c in cards if c["group"] == "B2B"], 4):
        print(f"\n— {card['id']} {card['group']} {card['type']}, покупок {len(card['history'])}, холодный старт: {card['cold']}")
        for h in card["history"][-3:]:
            print(f"    было: {items[h['item']]['short']} ({items[h['item']]['desc']}), {h['last']}")
        for x in card["recs"]["full"]:
            print(f"    [{'сбылось' if x['hit'] else '—':7}] {x['title']}: {items[x['item']]['short']} ({x.get('desc', '')}) | {x['label']}")
            print(f"              {x['why']}")
        print("    на самом деле купил:", "; ".join(items[t]["short"] for t in card["target"]))


if __name__ == "__main__":
    main()
