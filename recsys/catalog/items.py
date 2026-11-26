"""Товары из продаж -> товары каталога (фид магазина): семейство, класс и параметры лицензии.

Привязка — как советуют организаторы: артикул ищем в поле vendorCode и в адресе товара, обе строки
приводим к виду «верхний регистр, только буквы и цифры». Чего так не нашли — ищем по названию.
Признаки лицензии (тип, срок, число мест) берём из параметров каталога, а где товара в каталоге
нет — из самого названия.

Запуск:  python -m recsys.catalog.items
Результат: data/private/task/items.csv (производное от датасета организаторов — не публикуется)
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache

import pandas as pd

from recsys.catalog.feed import CATEGORIES, OFFERS, norm_code
from recsys.config import DATA, DATASET
from recsys.graph.textnorm import tokens

ITEMS = DATA / "private" / "task" / "items.csv"

LICENSE_WORDS = [
    ("renewal", r"renewal|продлени|продление|\bпродл\b"),
    ("upgrade", r"upgrade|апгрейд|обновлени[ея]\b.*(верси|лиценз)|переход"),
    ("migration", r"cross-?grade|миграци|migration"),
    ("extension", r"расширение лицензии|add-?on|дополнительн"),
    ("support", r"техническ\w+ поддержк|техподдержк|сертификат\w* (активации )?(на )?(техническ|поддержк)|support|maintenance"),
    ("media", r"медиа-?комплект|медиа-?пакет|media ?pack|установочный комплект|дистрибутив|формуляр"),
    ("base", r"\bbase\b|базов\w+ лиценз|new license|новая лицензия"),
]


def license_kind(name: str, param: str = "") -> str:
    text = f"{param} {name}".lower()
    for kind, pat in LICENSE_WORDS:
        if re.search(pat, text):
            return kind
    return ""


def seat_band(name: str) -> tuple[int | None, int | None, str]:
    """Диапазон мест из названия: «10-14 Node», «50-99 MailAddress», «5-Device», «на 25 пользователей»."""
    low = name.lower()
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*(node|user|mailaddress|mailbox|device|mobile device|пользовател|узл|рабочих мест|устройств)", low)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3)
    m = re.search(r"(\d+)\s*[-–]?\s*(node|user|device|mobile device|desktop|пк\b|пользовател|узл|устройств|рабочих мест|рабочем месте)", low)
    if m:
        return int(m.group(1)), int(m.group(1)), m.group(2)
    return None, None, ""


def term_months(name: str, param: str = "") -> int | None:
    low = f"{param} {name}".lower()
    if re.search(r"бессрочн|perpetual|постоянн\w+ лиценз", low):
        return 0
    m = re.search(r"(\d+)\s*(мес|month)", low)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*[- ]?(year|yr|год|года|лет)\b", low)
    if m:
        return int(m.group(1)) * 12
    if re.search(r"годов|на год|1yr", low):
        return 12
    return None


def line_key(name: str) -> str:
    """Название без признаков конкретной лицензии: вид, число мест, срок, язык — остаётся сама линейка."""
    s = name.lower().replace("ё", "е")
    s = re.sub(r"\(\d+/\d+[^)]*\)", " ", s)                                  # «(2/12 первый год …)»
    s = re.sub(r"\d+\s*[-–]\s*\d+\s*(node|user|mailaddress|mailbox|device|mobile device|пользовател\w*|узл\w*|устройств\w*)", " ", s)
    s = re.sub(r"\d+\s*[-–]?\s*(node|user|device|mobile device|desktop|мес\w*|month\w*|year\w*|yr|год\w*|лет)\b", " ", s)
    s = re.sub(r"renewal|base|license|лицензия|лицензии|download pack|russian edition|cross-?grade|educational|"
               r"продление|продлени\w+|new|sub\b|subscription|подписк\w+|alllng|all lng|pk ?lic|online|"
               r"право на использование|неисключительное право|на право использования|программы для эвм|по\b", " ", s)
    return " ".join(tokens(s)[:6])


def load_feed():
    cats = {}
    with CATEGORIES.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cats[r["id"]] = r
    offers, by_code, by_url, by_name = [], defaultdict(list), defaultdict(list), defaultdict(list)
    with gzip.open(OFFERS, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            o = json.loads(line)
            offers.append(o)
            if o["vendor_code"]:
                by_code[norm_code(o["vendor_code"])].append(i)
            if o["url_code"]:
                by_url[norm_code(o["url_code"])].append(i)
            by_name[norm_code(o["name"])].append(i)
    return cats, offers, by_code, by_url, by_name


def category_path(cats: dict, family_id: str, class_id: str) -> tuple[str, str, str]:
    """(семейство, подкатегория, категория): семейство — по group_id товара, класс — по его categoryId."""
    fam = cats.get(family_id) or {}
    sub = cats.get(class_id) or cats.get(fam.get("parent_id", "")) or {}
    top = cats.get(sub.get("parent_id", "")) or {}
    return fam.get("name", ""), sub.get("name", ""), top.get("name", "")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sales = pd.read_excel(DATASET, sheet_name="Продажи", dtype=str, engine="openpyxl").fillna("")
    items = sales.drop_duplicates("item_id")[["item_id", "sku", "item_name"]].to_dict("records")
    cats, offers, by_code, by_url, by_name = load_feed()

    # для поиска по названию: слова товара -> товары каталога
    df = Counter()
    otok = {}
    index = defaultdict(set)
    for i, o in enumerate(offers):
        t = set(tokens(o["name"]))
        otok[i] = t
        for x in t:
            df[x] += 1
    idf = {x: math.log(1 + len(offers) / c) for x, c in df.items()}
    for i, t in otok.items():
        for x in sorted(t, key=lambda x: -idf[x])[:4]:       # индексируем по самым редким словам
            index[x].add(i)

    @lru_cache(maxsize=None)
    def by_tokens(name: str) -> tuple[int | None, float]:
        t = set(tokens(name))
        if not t:
            return None, 0.0
        cand = Counter()
        for x in sorted(t, key=lambda x: -idf.get(x, 0))[:5]:
            for i in index.get(x, ()):
                cand[i] += 1
        best, best_s = None, 0.0
        wt = sum(idf.get(x, 8.0) for x in t)
        for i, _ in cand.most_common(300):
            inter = sum(idf.get(x, 8.0) for x in t & otok[i])
            union = wt + sum(idf.get(x, 8.0) for x in otok[i] - t)
            s = inter / union
            if s > best_s:
                best, best_s = i, s
        return best, best_s

    def pick(ids: list[int]) -> int:
        return sorted(ids, key=lambda i: (-offers[i]["available"], i))[0]

    rows, how = [], Counter()
    for it in items:
        o, match, score = None, "", 1.0
        code = norm_code(it["sku"])
        if code and code in by_code:
            o, match = offers[pick(by_code[code])], "артикул"
        elif code and code in by_url:
            o, match = offers[pick(by_url[code])], "адрес"
        elif norm_code(it["item_name"]) in by_name:
            o, match = offers[pick(by_name[norm_code(it["item_name"])])], "название точно"
        else:
            i, score = by_tokens(it["item_name"])
            if i is not None and score >= 0.6:
                o, match = offers[i], "название похоже"
        how[match or "не найден"] += 1
        fam, sub, top = category_path(cats, o["family_id"], o["class_id"]) if o else ("", "", "")
        lo, hi, unit = seat_band(it["item_name"])
        rows.append({
            "item_id": it["item_id"], "sku": it["sku"], "name": it["item_name"],
            "match": match, "match_score": round(score, 2) if o else "",
            "offer_id": o["offer_id"] if o else "", "offer_name": o["name"] if o else "",
            "family_id": o["family_id"] if o else "", "family": fam, "subcategory": sub, "category": top,
            "class_id": o["class_id"] if o else "", "vendor": o["vendor"] if o else "",
            "available": o["available"] if o else "", "url": o["url"] if o else "",
            "p_license": o.get("license", "") if o else "", "p_term": o.get("term", "") if o else "",
            "p_user": o.get("user", "") if o else "", "p_org": o.get("org", "") if o else "",
            "p_platform": o.get("platform", "") if o else "", "p_registry": o.get("registry", "") if o else "",
            "p_version": o.get("version", "") if o else "", "p_nodes": o.get("nodes", "") if o else "",
            "p_users_to": o.get("users_to", "") if o else "", "p_kind": o.get("kind", "") if o else "",
            "license_kind": license_kind(it["item_name"], o.get("license", "") if o else ""),
            "band_lo": lo if lo is not None else "", "band_hi": hi if hi is not None else "", "band_unit": unit,
            "term_months": (lambda t: "" if t is None else t)(term_months(it["item_name"], o.get("term", "") if o else "")),
            "line": line_key(it["item_name"]),
        })
    ITEMS.parent.mkdir(parents=True, exist_ok=True)
    with ITEMS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    lines_sold = sales.merge(pd.DataFrame(rows)[["item_id", "match"]], on="item_id")
    print(f"товаров в продажах: {n}")
    for k, v in how.most_common():
        share_rows = (lines_sold["match"] == (k if k != "не найден" else "")).mean()
        print(f"  {k:18} {v:>5} ({v / n:.0%} товаров, {share_rows:.0%} строк продаж)")
    fams = Counter(r["family"] for r in rows if r["family"])
    print(f"семейств каталога среди проданных товаров: {len(fams)}; подкатегорий: {len({r['subcategory'] for r in rows if r['subcategory']})}")
    print("вид лицензии:", Counter(r["license_kind"] or "—" for r in rows).most_common())
    print("срок, мес.:", Counter(r["term_months"] for r in rows).most_common(6))
    print("с диапазоном мест:", sum(1 for r in rows if r["band_lo"] != ""), "| линеек по названию:", len({r["line"] for r in rows}))


if __name__ == "__main__":
    main()
