"""Разбор страницы семейства товаров магазина в плоскую запись.

Берём только то, что опубликовано на странице: название, вендор, категорию,
варианты лицензии, признак «только юрлицам», таблицу сравнения редакций,
товары семейства с их конфигурацией и ссылки на другие страницы магазина.
"""
import json
import re

from bs4 import BeautifulSoup

CFG_RE = re.compile(r"window\.temporaryFrontConfig\s*=\s*(\{.*?\});?\s*</script>", re.S)
SOFT_HYPHEN = "\xad"


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(SOFT_HYPHEN, "")).strip()


def _front_config(html: str) -> dict:
    m = CFG_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _json_ld(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
        except json.JSONDecodeError:
            continue
        items.extend(d.get("@graph", [d]) if isinstance(d, dict) else [])
    return items


def _option_groups(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Группы выбора в шапке товара: «Тип лицензии», «Тип организации», «Срок действия»."""
    groups: dict[str, list[str]] = {}
    head = soup.select_one(".b-product__head-info")
    if not head:
        return groups
    for box in head.select("div.flex-column.gap-4"):
        texts = [_clean(t) for t in box.stripped_strings]
        texts = [t for t in texts if t]
        if len(texts) >= 2 and len(texts[0]) < 40:
            groups[texts[0]] = texts[1:]
    return groups


def _compare_table(soup: BeautifulSoup) -> dict | None:
    """Таблица сравнения редакций: заголовок — лестница, строки — функции по редакциям."""
    tb = soup.find("table")
    if not tb:
        return None
    rows = tb.find_all("tr")
    if not rows:
        return None
    header = [_clean(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
    if len(header) < 3:
        return None
    body = []
    for r in rows[1:]:
        cells = [_clean(c.get_text(" ")) for c in r.find_all(["th", "td"])]
        if len(cells) == len(header):
            body.append(cells)
    title = ""
    h = tb.find_previous(["h2", "h3"])
    if h:
        title = _clean(h.get_text(" "))
    return {"title": title, "columns": header[1:], "rows": body}


def parse_family(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    cfg = _front_config(html)
    ld = _json_ld(soup)
    product_ld = next((x for x in ld if x.get("@type") == "Product"), {})
    offers = product_ld.get("offers") or {}

    crumbs = [
        {"name": _clean(a.get_text()), "href": a.get("href", "")}
        for a in soup.select("[class*=breadcrumb] a, [class*=bread] a")
        if _clean(a.get_text())
    ]

    def price_of(item: dict) -> float | None:
        """Публичная цена товара на витрине (со скидкой, если она объявлена)."""
        pr = item.get("price")
        if isinstance(pr, dict):
            pr = pr.get("with_discount") or pr.get("without_discount")
        try:
            return float(str(pr).replace(" ", "").replace(",", ".")) if pr not in (None, "") else None
        except ValueError:
            return None

    def id_of(item: dict) -> str:
        """Номер товара на витрине: из поля id или из адреса /вендор/артикул-номер/."""
        if item.get("id"):
            return str(item["id"])
        m = re.search(r"-(\d+)/?$", item.get("link") or "")
        return m.group(1) if m else ""

    products = []
    # Вёрстка А: страница с конфигуратором — товары лежат в familyFilteredProducts.
    for p in cfg.get("familyFilteredProducts") or []:
        products.append({
            "id": id_of(p),
            "price": price_of(p),
            "sku": p.get("sku"),
            "name": _clean(p.get("name")),
            "config": _clean(p.get("shortConfiguration")),
            "link": p.get("link") or "",
            "min_qty": p.get("minQuantity"),
            "max_qty": p.get("maxQuantity"),
            "vendor": _clean(p.get("vendorName")),
            "category": _clean(p.get("categoryName")),
            "subcategory": _clean(p.get("subCategoryName")),
        })
    # Вёрстка Б: страница со списком лицензий — товары лежат в models[].licenses.
    for m in cfg.get("models") or []:
        licenses = m.get("licenses") or {}
        for lic in (licenses.values() if isinstance(licenses, dict) else licenses):
            products.append({
                "id": id_of(lic),
                "price": price_of(lic),
                "sku": lic.get("sku"),
                "name": _clean(lic.get("name") or m.get("name")),
                "config": "",
                "link": lic.get("link") or "",
                "min_qty": lic.get("min_quantity"),
                "max_qty": lic.get("max_quantity"),
                "vendor": "",
                "category": "",
                "subcategory": "",
            })

    main = soup.select_one("article.b-product__main") or soup.select_one(".b-product__description")
    description = _clean(main.get_text(" "))[:3000] if main else ""
    headings = [_clean(h.get_text(" ")) for h in (main.find_all(["h2", "h3"]) if main else [])][:30]
    store_links = []
    if main:
        for a in main.find_all("a", href=True):
            href = a["href"]
            if "store.softline.ru" in href or href.startswith("/"):
                store_links.append({"text": _clean(a.get_text())[:80], "href": href.split("#")[0]})

    h1 = _clean(soup.h1.get_text()) if soup.h1 else ""
    first = products[0] if products else {}
    brand = (product_ld.get("brand") or {}).get("name") if isinstance(product_ld.get("brand"), dict) else None

    return {
        "url": url,
        "family_id": cfg.get("familyId"),
        "name": _clean(cfg.get("familyName")) or h1,
        "h1": h1,
        "vendor": _clean(brand) or first.get("vendor", ""),
        "category": first.get("category") or (crumbs[0]["name"] if crumbs else ""),
        "subcategory": first.get("subcategory") or (crumbs[1]["name"] if len(crumbs) > 1 else ""),
        "breadcrumbs": crumbs,
        # Признак берём только из видимой плашки в шапке товара, а не из любого места страницы.
        "b2b_only": any(
            "только юридическим лицам" in n.get_text(" ")
            for n in soup.select(".b-product__head .b-notification")
        ),
        "options": _option_groups(soup),
        "offer_count": offers.get("offerCount"),
        "products": products,
        "compare": _compare_table(soup),
        "headings": headings,
        "description": description,
        "store_links": store_links[:20],
    }
