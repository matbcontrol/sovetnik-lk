"""Блок советника для кабинета одним запросом: история покупок -> ответ в формате нынешнего блока.

Так кабинет встраивает советника: шлёт тип клиента, дату и покупки (семейство каталога, дата, срок),
получает то же, что показывает макет кабинета (site/cabinet.js): продления — в строку лицензии,
до трёх личных советов (поля link, name, vendorName, price — как сейчас; slot и reason — новые)
и строку «Часто докупают». Движок тот же, что у страницы, языковая модель на запрос не вызывается.

  POST /api/advice  {"type": "b2b", "today": "2026-11-26",
                     "purchases": [{"family": "kaspersky/…", "date": "2026-05-11", "term": 12}]}
"""
from __future__ import annotations

import time
from datetime import date

from recsys.recommend.engine import Client, Graph, Purchase, Rec, Recommender

UTM = "utm_source=lk.softline.ru&utm_medium=internal&utm_campaign=reco&utm_content=sovetnik_{slot}&utm_term={pos}"
_GRAPH: Graph | None = None


def graph() -> Graph:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = Graph.load()
    return _GRAPH


def link(url: str, slot: str, pos: int) -> str:
    """Ссылка в магазин с теми же метками, что ставит блок кабинета: по ним считаются переходы и покупки."""
    if not url:
        return ""
    return url + ("&" if "?" in url else "?") + UTM.format(slot=slot, pos=pos)


def parse(body: dict) -> Client:
    """Запрос кабинета -> клиент движка. Ошибка в данных — ValueError с понятным текстом."""
    if not isinstance(body, dict):
        raise ValueError("ожидается JSON-объект")
    kind = str(body.get("type", "b2b")).lower()
    if kind not in ("b2b", "b2c"):
        raise ValueError("type — b2b или b2c")
    today = date.fromisoformat(str(body.get("today") or date.today().isoformat())[:10])
    purchases = []
    for p in (body.get("purchases") or [])[:50]:
        if not isinstance(p, dict) or not str(p.get("family", "")).strip():
            raise ValueError("у покупки нужно поле family — семейство каталога «вендор/семейство»")
        d = p.get("date")
        term = p.get("term")
        purchases.append(Purchase(family=str(p["family"]).strip(), date=date.fromisoformat(str(d)[:10]) if d else None,
                                  term_months=int(term) if term not in (None, "") else None))
    return Client(client_type=kind, purchases=purchases, today=today)


def card(g: Graph, r: Rec, pos: int) -> dict:
    price = Recommender._price(g.nodes.get(r.family) or {})
    return {"link": link(r.url, r.slot, pos), "name": r.name, "vendorName": r.vendor, "price": price or None,
            "slot": r.slot, "reason": r.why}


def advice(body: dict) -> dict:
    """Тот же состав, что в макете кабинета: продления отдельно, до трёх личных советов, «Часто докупают»."""
    t0 = time.perf_counter()
    client = parse(body)
    g = graph()
    recs = Recommender(g).recommend(client, 6)
    renew = [r for r in recs if r.slot == "renew"]
    personal = [r for r in recs if r.slot != "renew"][:3]
    popular = [r for r in Recommender(g, fill=True).recommend(client, 8) if r.slot == "popular"][: 1 if client.purchases else 3]
    return {"status": "shown" if personal else "hidden",
            "data": [card(g, r, k + 1) for k, r in enumerate(personal)],
            "renew": [card(g, r, k + 1) for k, r in enumerate(renew)],
            "popular": [card(g, r, k + 1) for k, r in enumerate(popular)],
            "model_calls": 0, "ms": round((time.perf_counter() - t0) * 1000, 2)}
