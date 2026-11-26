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
from collections import Counter
import pickle
import random
import sys
from datetime import date, datetime, timezone

from recsys.config import DATA, INTERIM, ROOT
from recsys.eval.value import Prep, in_regime
from recsys.rank.data import OFFICIAL, Fold, load_task, make_fold
from recsys.llm.pair_verdicts import Verdicts
from recsys.rank.cabinet import Card, cabinet
from recsys.rank.explain import advice, card_title, clean_name, describe, q, short_name
from recsys.rank.features import FoldStats, build_attrs
from recsys.rank.ltr import copies, fit
from recsys.rank.unified import Catalog, Unified
from recsys.stable import rerun_with_fixed_order

OUT = ROOT / "site" / "data" / "private.json"
CACHE = DATA / "private" / "task" / "demo_models.pkl"      # обученные модели; пересчёт: --refit
REGIMES = ["full", "new_item"]
TODAY_SAMPLE = 240


PRICE_CAP = 5.0        # во сколько раз совет может быть дороже самой дорогой покупки клиента (режим кабинета)


def item_prices(task) -> dict[str, float]:
    """Цена товара по витрине магазина; товары без цены в словарь не попадают."""
    offer = {}
    with gzip.open(INTERIM / "feed_offers.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            try:
                offer[o["offer_id"]] = float(o["price"])
            except (TypeError, ValueError):
                continue
    out = {}
    for item, r in task.items.items():
        p = offer.get((r or {}).get("offer_id") or "")
        if p and p > 0:
            out[item] = p
    return out


TIER = {      # вид карточки по вердикту пары: слот (цвет), заголовок, подпись источника
    "need": ("missing", "К вашему продукту", "пара проверена заранее: нужно"),
    "kit": ("kit", "Типовой набор", "пара проверена заранее: комплект"),
}
REJECT = {"has": "уже есть у владельца {a}", "same": "то же самое, что {a}", "unrelated": "не связано с покупками клиента"}
EXTRA: dict[str, dict] = {}      # товары каталога вне продаж, попавшие в карточки: описание для справочника стенда


def sentence(text: str) -> str:
    text = (text or "").strip()
    return (text[:1].upper() + text[1:]).rstrip(".") + "." if text else ""


def graph_card(uni: Unified, card: Card) -> dict:
    """Карточка товара, пришедшего по проверенной связи графа или занявшего место снятого с продажи."""
    task, s = uni.task, card.s
    src = card.by or s.src or s.replaces
    qa = q(short_name(task, src)) if src in task.items else "ваш продукт"
    name = q(clean_name(s.title, 80))
    ev = s.evidence or ""
    if s.basis == "старшая редакция":
        gained = ev.split("добавляет:")[1].split("; http")[0].strip().rstrip(".") if "добавляет:" in ev else ""
        why = f"У вас есть {qa}. Старшая редакция — {name}" + (f": добавляет {gained}." if gained else ".")
        slot, title = "next", "Следующая ступень"
        label = "каталог: таблица сравнения магазина" if "колонка" in ev else "каталог: названия редакций"
    elif s.basis == "новая версия":
        why = f"У вас есть {qa}. В продаже есть более новая версия — {name}."
        slot, title, label = "next", "Следующая ступень", "каталог: номера версий"
    elif s.basis == "замена":
        reg = ev.split("(реестр № ")[1].split(")")[0] if "(реестр № " in ev else ""
        tail = f" (реестр российского ПО, запись № {reg})" if reg else ""
        why = f"{qa[:1].upper() + qa[1:]} больше не продаётся. Российский аналог — {name}{tail}."
        slot, title, label = "replace", "Замена", "реестр российского ПО"
    elif s.basis == "дополнение":
        why = f"К {qa} подходит {name}: это сказано в названии товара."
        slot, title, label = "missing", "К вашему продукту", "каталог: название товара"
    else:                                                # тот же товар под актуальным артикулом
        why = f"Прежний артикул {qa} снят с продажи. Это тот же товар под актуальным артикулом."
        slot, title, label = "next", "Тот же товар", "каталог магазина"
    if s.item not in task.items:
        offer = uni.cat.offers.get(s.item[2:], {}) if s.item.startswith("K:") else {}
        EXTRA[s.item] = {"name": s.title, "title": clean_name(s.title, 110), "short": clean_name(s.title, 64), "desc": "",
                         "vendor": offer.get("vendor", ""), "cls": offer.get("subcategory", ""), "url": s.url,
                         "price": s.price if s.price and s.price > 0 else None}
    return {"item": s.item, "slot": slot, "title": title, "why": why, "also": "", "label": label, "kind": "граф",
            "strength": 2.0, "desc": "", "tier": "graph",
            "paths": [{"src": src, "type": s.basis, "kind": "граф", "text": why}] if src in task.items else []}


def render(uni: Unified, c: str, card: Card) -> dict:
    """Карточка стенда: объяснение из каталога и покупок, а для чужой линейки — фраза «зачем» из вердикта пары."""
    st, s = uni.st, card.s
    if card.tier == "graph" or s.item not in uni.task.items:
        return graph_card(uni, card)
    d = advice(st, c, s.item)
    d.setdefault("also", "")
    d.setdefault("desc", describe(uni.task, s.item, uni.at[s.item]))
    if card.tier in TIER:
        slot, title, label = TIER[card.tier]
        stat = d["why"] if d.get("kind") in ("следом", "семейство", "класс") else ""   # покупки других — вторым основанием
        d.update(slot=slot, title=title, label=label, why=sentence(card.reason), also=stat, kind=card.tier)
        d["paths"] = [{"src": card.by, "type": "нужно" if card.tier == "need" else "комплект", "kind": card.tier,
                       "text": d["why"]}] if card.by else []
    d["tier"] = card.tier
    return d


def popular_card(uni: Unified, c: str, card: Card) -> dict:
    """Строка «Часто докупают»: не личный совет, а продукт, который часто берут клиенты того же типа."""
    s = card.s
    who = "организаций" if uni.task.clients.get(c, {}).get("client_group") == "B2B" else "частных покупателей"
    share = f"{s.share * 100:.0f} %" if s.share >= 0.095 else f"{s.share * 100:.1f} %".replace(".", ",")
    if s.item not in uni.task.items:
        graph_card(uni, card)                            # заносит товар каталога в справочник стенда
    return {"item": s.item, "slot": "popular", "title": "Часто докупают", "tier": "popular", "also": "",
            "why": f"Не личный совет: этот продукт впервые берут {share} {who}, вернувшихся за покупкой.",
            "label": "покупки других клиентов", "kind": "общее", "strength": 0.3, "share": round(s.share, 4),
            "desc": describe(uni.task, s.item, uni.at[s.item]) if s.item in uni.task.items else "", "paths": []}


def rejected(uni: Unified, rows: list[dict], limit: int = 6) -> list[dict]:
    """Что предлагала статистика, а вердикт пары убрал: товар и причина словами."""
    task, at = uni.task, uni.at
    out, seen = [], set()
    for r in rows:
        name = short_name(task, r["item"]) if r["item"] in task.items else ""
        if r["verdict"] not in REJECT or not name or name in seen:
            continue
        seen.add(name)                                   # разные линейки одного продукта называются одинаково
        by = q(short_name(task, r["by"])) if r["by"] in task.items else "этого продукта"
        out.append({"item": r["item"], "verdict": r["verdict"], "why": REJECT[r["verdict"]].format(a=by)})
    # сначала самое наглядное: «уже есть» и «то же самое», затем «не связано»
    out.sort(key=lambda x: x["verdict"] == "unrelated")
    return out[:limit]


def client_card(unis: dict[str, Unified], verdicts: Verdicts, c: str, mode: str, cold: bool,
                prices: dict[str, float]) -> dict:
    st = unis["full"].st
    task, fold = st.task, st.fold
    info = task.clients.get(c, {})
    out = {
        "id": c, "group": info.get("client_group", ""), "type": info.get("client_type", ""),
        "segment": info.get("segment", ""), "industry": info.get("industry", ""), "lk": info.get("lk_access", ""),
        "mode": mode, "cold": cold,
        "history": [{"item": a, "first": fold.first[c][a].isoformat(), "last": fold.last[c][a].isoformat()}
                    for a in fold.history[c]],
        "target": sorted(fold.target.get(c, ())) if mode == "test" else [],
        "recs": {}, "renew": {}, "popular": {}, "rejected": {}, "unlabelled": {},
        # тройка по протоколу организаторов: из неё считается Precision@3, блок кабинета к ней не сводится
        "protocol": [{"item": i, "hit": i in fold.target.get(c, ())} for i in unis["full"].protocol(c)]
        if mode == "test" else [],
    }
    for regime in REGIMES:
        uni = unis[regime]
        cab = cabinet(uni, c, verdicts, prices)
        with in_regime(st, fold.history[c], regime):
            out["renew"][regime] = [render(uni, c, x) for x in cab["renew"]]
            out["recs"][regime] = [render(uni, c, x) for x in cab["new"]]
        out["popular"][regime] = [popular_card(uni, c, x) for x in cab["popular"]]
        out["rejected"][regime] = rejected(uni, cab["rejected"])
        out["unlabelled"][regime] = sum(r["verdict"] == "unknown" for r in cab["rejected"])
        for card in out["renew"][regime] + out["recs"][regime] + out["popular"][regime]:
            card["hit"] = card["item"] in fold.target.get(c, ()) if mode == "test" else None
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
    cat = Catalog(task)
    verdicts = Verdicts.load("line")
    prices = item_prices(task)
    unis = {r: Unified(off, models[r], cat, r) for r in REGIMES}
    cold = set(off.fold.group_b)
    cards = [client_card(unis, verdicts, c, "test", c in cold, prices) for c in clients]
    every = lambda card: card["recs"]["full"] + card["renew"]["full"] + card["popular"]["full"]  # noqa: E731
    hits = sum(any(x["hit"] for x in every(card)) for card in cards)
    shown = sum(len(every(card)) for card in cards)
    print(f"проверка на истории: клиентов {len(cards)}, карточек {shown}, клиентов с хотя бы одной сбывшейся карточкой {hits}")

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
    unis_now = {r: Unified(now_stats, models_now[r], cat, r) for r in REGIMES}
    cards_now = [client_card(unis_now, verdicts, c, "today", len(now.history[c]) == 1, prices) for c in today]
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
            used |= {x["item"] for x in card["recs"][regime] + card["renew"][regime] + card["popular"][regime]
                     + card["rejected"][regime] + card["protocol"]}
    items = dict(EXTRA)
    for i in used - set(EXTRA):
        r = task.items.get(i) or {}
        off_id = r.get("offer_id") or ""
        try:
            p = float(price.get(off_id) or 0)
        except ValueError:
            p = 0.0
        items[i] = {
            # заголовок карточки — название продукта из каталога (параметры лицензии идут строкой ниже);
            # полное название из продаж остаётся в подсказке. Так карточка и объяснение называют товар одинаково.
            "name": task.names.get(i, i),
            "title": card_title(task, i),
            "short": short_name(task, i),
            "desc": describe(task, i, attrs[i]),
            "vendor": r.get("vendor", ""), "cls": r.get("subcategory", ""), "url": url.get(off_id, r.get("url", "")),
            "price": p if p > 0 else None,
        }
    # пары, которые встретились у клиентов стенда, а вердикта по ним нет: очередь для разметки по API
    from recsys.llm.pair_verdicts import TODO
    TODO["line"].write_text(json.dumps(sorted([a, b, n] for (a, b), n in verdicts.missing.items()), ensure_ascii=False),
                            encoding="utf-8")
    print(f"пар без вердикта в пределах горизонта: {len(verdicts.missing)} (очередь: {TODO['line']})")
    data = {
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Обезличенный датасет организаторов хакатона. Не публиковать.",
        "test_from": OFFICIAL[2], "train_until": OFFICIAL[1], "today": last.isoformat(),
        "items": items, "clients": cards + cards_now,
        "verdicts": dict(Counter(v.verdict for v in verdicts.table.values())),
        "unlabelled_pairs": len(verdicts.missing),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано: {OUT} ({OUT.stat().st_size / 1e6:.1f} МБ), товаров {len(items)}")
    # несколько примеров в консоль — посмотреть, как читаются объяснения
    for card in rnd.sample([c for c in cards if c["group"] == "B2B"], 4):
        print(f"\n— {card['id']} {card['group']} {card['type']}, покупок {len(card['history'])}, холодный старт: {card['cold']}")
        for h in card["history"][-3:]:
            print(f"    было: {items[h['item']]['short']} ({items[h['item']]['desc']}), {h['last']}")
        for x in card["renew"]["full"] + card["recs"]["full"] + card["popular"]["full"]:
            print(f"    [{'сбылось' if x['hit'] else '—':7}] {x['title']}: {items[x['item']]['short']} ({x.get('desc', '')}) | {x['label']}")
            print(f"              {x['why']}")
        for x in card["rejected"]["full"][:3]:
            print(f"    убрано: {items[x['item']]['short']} — {x['why']}")
        print("    на самом деле купил:", "; ".join(items[t]["short"] for t in card["target"]))


if __name__ == "__main__":
    main()
