"""Разбор страницы подкатегории магазина: товары в порядке витрины «По рекомендации Softline».

Порядок витрины — открытый признак того, что магазин сам считает ходовым в категории.
Он нужен для клиентов без истории и как запасной уровень, когда у товара нет своих связей.
"""
import json
import re

from bs4 import BeautifulSoup


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xad", "")).strip()


def parse_category(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    h1 = _clean(soup.h1.get_text()) if soup.h1 else ""
    total = None
    pag = soup.select_one(".js-pagination")
    if pag:
        m = re.search(r"из\s+(\d+)\s+товар", pag.get_text(" "))
        if m:
            total = int(m.group(1))
    items = []
    for card in soup.select(".b-product-item"):
        a = card.select_one(".b-product-item__title a") or card.select_one("a[href]")
        if not a or not a.get("href"):
            continue
        title = card.select_one(".b-product-item__title")
        vendor = card.select_one(".b-product-item__vendor")
        rate = card.select_one(".b-product-item__rate")
        rev = card.select_one(".b-product-item__reviews")
        eco = {}
        try:
            eco = json.loads(card.get("data-ecommerce") or "{}")
        except json.JSONDecodeError:
            pass
        m_rev = re.search(r"\d+", rev.get_text()) if rev else None
        price_el = card.select_one(".b-product-item__current-price")
        price_txt = re.sub(r"[^\d,.]", "", price_el.get_text()) if price_el else ""
        try:
            price = float(price_txt.replace(",", ".")) if price_txt else None
        except ValueError:
            price = None
        items.append({
            # «buy» — на карточке есть цена и товар можно купить; «request» — только «Запросить цену»
            "sale": "buy" if price else "request",
            "price": price,
            "href": a["href"].split("?")[0],
            "title": _clean(title.get_text(" ")) if title else "",
            "vendor": _clean(vendor.get_text(" ")) if vendor else "",
            "rating": float(rate.get_text(strip=True).replace(",", ".")) if rate and rate.get_text(strip=True) else None,
            "reviews": int(m_rev.group()) if m_rev else 0,
            # в метках бывают условия перехода: «скидка 50% при переходе», «−40% за миграцию»
            "tags": [_clean(t.get_text(" "))[:200] for t in card.select(".b-product-item__single-tag")],
            "item_id": eco.get("item_id"),
            "item_category": eco.get("item_category") or "",
            "item_category2": eco.get("item_category2") or "",
        })
    return {"name": h1, "total": total, "items": items}
