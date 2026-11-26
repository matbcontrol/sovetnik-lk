"""Каталог магазина из открытого фида (формат YML), на который указали организаторы.

Фид: https://store.softline.ru/feeds/Mindbox.yml.gz — около 136 тысяч товаров, обновляется ежедневно.
Дерево категорий в фиде трёхуровневое: категория -> подкатегория -> СЕМЕЙСТВО товара. У товара
categoryId указывает на подкатегорию, group_id — на семейство, поэтому привязка
«товар -> семейство -> класс» есть сразу для всего каталога, без обхода страниц.

Запуск:  python -m recsys.catalog.feed            скачать (если нет) и разобрать
         python -m recsys.catalog.feed --stats    показать устройство фида
Результат: data/interim/feed_categories.csv, data/interim/feed_offers.jsonl.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from recsys.config import INTERIM, RAW, USER_AGENT

FEED_URL = "https://store.softline.ru/feeds/Mindbox.yml.gz"
FEED = RAW / "feed" / "Mindbox.yml.gz"
OFFERS = INTERIM / "feed_offers.jsonl.gz"
CATEGORIES = INTERIM / "feed_categories.csv"

# параметры товара, которые нужны для связей (остальные в выгрузку не берём)
KEEP_PARAMS = {
    "Тип продукта": "kind", "Пользователь": "user", "Тип организации": "org",
    "Тип лицензии": "license", "Срок действия": "term", "Совместимая архитектура/платформа": "platform",
    "Номер записи в реестре отечественного ПО": "registry",
    "Версия": "version", "К-во узлов": "nodes", "К-во пользователей до": "users_to",
    "Лицензионная программа": "program", "Модель лицензирования": "lic_model",
    "Размер организации": "org_size", "Компоненты пакетов": "components",
    "Сертификаты и стандарты": "certs", "Тип поставки": "delivery_type",
}
EMPTY = {"", "другое или отсутствует", "не указано", "нет"}


def norm_code(s: str) -> str:
    """Артикул к одному виду, как советуют организаторы: верхний регистр, только буквы и цифры."""
    return re.sub(r"[^0-9A-ZА-ЯЁ]", "", (s or "").upper())


def download() -> None:
    import requests

    FEED.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(FEED_URL, headers={"User-Agent": USER_AGENT}, timeout=120)
    r.raise_for_status()
    FEED.write_bytes(r.content)


def iter_feed(path: Path = FEED):
    """Потоковое чтение: ('category', id, parent, name) и ('offer', словарь)."""
    with gzip.open(path, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "category":
                yield "category", el.get("id"), el.get("parentId") or "", (el.text or "").strip()
                el.clear()
            elif el.tag == "offer":
                o = {"id": el.get("id"), "available": el.get("available") == "true"}
                params = {}
                for ch in el:
                    if ch.tag == "param":
                        params[ch.get("name") or ""] = (ch.text or "").strip()
                    elif ch.tag not in ("picture",):
                        o[ch.tag] = (ch.text or "").strip()
                o["params"] = params
                yield "offer", o
                el.clear()


def parse() -> None:
    cats, n, avail = {}, 0, 0
    INTERIM.mkdir(parents=True, exist_ok=True)
    with gzip.open(OFFERS, "wt", encoding="utf-8") as out:
        for rec in iter_feed():
            if rec[0] == "category":
                cats[rec[1]] = (rec[2], rec[3])
                continue
            o = rec[1]
            url = o.get("url", "")
            m = re.search(r"store\.softline\.ru/([^/]+)/([^/?]*?)-?(\d+)/?(?:\?|$)", url)
            row = {
                "offer_id": o["id"], "available": int(o["available"]), "name": o.get("name", ""),
                "vendor": o.get("vendor", ""), "vendor_code": o.get("vendorCode", ""),
                # в фиде categoryId — подкатегория (класс товара), а group_id — семейство (третий уровень дерева)
                "class_id": o.get("categoryId", ""), "family_id": o.get("group_id", ""), "url": url.split("?")[0],
                "vendor_slug": m.group(1) if m else "", "url_code": m.group(2) if m else "",
                "product_id": m.group(3) if m else "",
                "price": o.get("price", ""),
            }
            for src, dst in KEEP_PARAMS.items():
                v = o["params"].get(src, "")
                if v.strip().lower() not in EMPTY:
                    row[dst] = v
            desc = re.sub(r"<[^>]+>", " ", o.get("description", ""))
            row["description"] = re.sub(r"\s+", " ", desc).strip()[:400]
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
            avail += row["available"]
    with CATEGORIES.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "parent_id", "name", "level"])
        for cid, (parent, name) in cats.items():
            level, p = 1, parent
            while p and p in cats and level < 6:
                level, p = level + 1, cats[p][0]
            w.writerow([cid, parent, name, level])
    print(f"категорий {len(cats)}, товаров {n}, из них в наличии {avail} -> {OFFERS.name}, {CATEGORIES.name}")


def stats() -> None:
    tags, pnames, levels = Counter(), Counter(), Counter()
    cats, sample, n = {}, None, 0
    for rec in iter_feed():
        if rec[0] == "category":
            cats[rec[1]] = rec[2]
            continue
        o = rec[1]
        n += 1
        tags.update(k for k in o if k != "params")
        pnames.update(o["params"].keys())
        if sample is None and len(o["params"]) >= 5:
            sample = o
    for cid, parent in cats.items():
        level, p = 1, parent
        while p and p in cats and level < 6:
            level, p = level + 1, cats[p]
        levels[level] += 1
    print("товаров:", n, "| категорий по уровням:", dict(sorted(levels.items())))
    print("поля товара:", dict(tags.most_common(20)))
    print("параметры (сколько товаров):")
    for k, v in pnames.most_common(40):
        print(f"   {v:>7}  {k}")
    print("пример товара:", json.dumps({**sample, "description": (sample.get('description') or '')[:200]}, ensure_ascii=False)[:1500])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if not FEED.exists():
        download()
    stats() if args.stats else parse()


if __name__ == "__main__":
    main()
