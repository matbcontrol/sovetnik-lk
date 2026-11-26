"""Привязка товара из продаж или каталога к семейству магазина.

Три ступени, от надёжной к приблизительной:
  1. по артикулу — страницы семейств публикуют артикулы своих товаров;
  2. по адресу товара на витрине — в адресе стоит артикул и вендор;
  3. по названию — среди семейств того же вендора ищем то, чьи слова полнее всего входят в название товара.
Модель не используется.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from recsys.config import INTERIM
from recsys.graph.textnorm import tokens


def norm_sku(sku: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", (sku or "").lower())


def norm_vendor(name: str) -> str:
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'()]", " ", s)
    s = re.sub(r"\b(ооо|зао|оао|ао|пао|нпо|нпп|гк|inc|ltd|llc|corp|corporation|gmbh|software|company|co|pty|ранее)\b", " ", s)
    return re.sub(r"[^0-9a-zа-я]+", "", s)


class FamilyMatcher:
    def __init__(self, families: list[dict]):
        """families — записи из families.jsonl (нужны vendor_slug, family_slug, name, vendor, products)."""
        self.by_sku: dict[str, str] = {}
        self.by_id: dict[str, str] = {}                 # номер товара на витрине -> семейство
        self.names: dict[str, list[str]] = {}
        self.by_vendor: dict[str, list[str]] = defaultdict(list)
        self.vendor_alias: dict[str, str] = {}
        df = Counter()
        for r in families:
            key = f"{r['vendor_slug']}/{r['family_slug']}"
            t = tokens(r["name"])
            self.names[key] = t
            self.by_vendor[r["vendor_slug"]].append(key)
            for tok in set(t):
                df[tok] += 1
            for alias in (r.get("vendor") or "", r["vendor_slug"].replace("-", " ")):
                if norm_vendor(alias):
                    self.vendor_alias.setdefault(norm_vendor(alias), r["vendor_slug"])
            for p in r.get("products") or []:
                sku, pid = p.get("sku") or "", str(p.get("id") or "")
                m = re.search(r"/([^/]*?)-?(\d+)/?$", p.get("link") or "")
                if m:                                   # адрес товара: /вендор/артикул-номер/
                    sku, pid = sku or m.group(1), pid or m.group(2)
                if sku:
                    self.by_sku.setdefault(norm_sku(sku), key)
                if pid:
                    self.by_id.setdefault(pid, key)
        n = max(len(self.names), 1)
        self.idf = {tok: math.log(1 + n / c) for tok, c in df.items()}

    @classmethod
    def load(cls, folder: Path = INTERIM) -> "FamilyMatcher":
        rows = []
        with (folder / "families.jsonl").open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return cls(rows)

    def vendor_slug(self, vendor: str) -> str:
        v = norm_vendor(vendor)
        if v in self.vendor_alias:
            return self.vendor_alias[v]
        for alias, slug in self.vendor_alias.items():       # «Лаборатория Касперского» ~ «kaspersky» не сойдутся,
            if len(v) >= 4 and (v in alias or alias in v):  # но «microsoft corporation» ~ «microsoft» — да
                return slug
        return ""

    def by_name(self, name: str, vendor_slug: str = "") -> tuple[str, float]:
        """Лучшее семейство по названию и оценка от 0 до 1: доля «веса» слов семейства, найденных в названии."""
        t = set(tokens(name))
        if not t:
            return "", 0.0
        pool = self.by_vendor.get(vendor_slug) if vendor_slug else None
        if pool is None:
            pool = list(self.names)
        best, best_score = "", 0.0
        for key in pool:
            ft = self.names[key]
            if not ft:
                continue
            total = sum(self.idf.get(x, 1.0) for x in set(ft))
            found = sum(self.idf.get(x, 1.0) for x in set(ft) if x in t)
            score = found / total
            # при равной полноте выигрывает более длинное (более точное) название семейства
            if score > best_score + 1e-9 or (abs(score - best_score) < 1e-9 and best and len(ft) > len(self.names[best])):
                best, best_score = key, score
        return best, best_score

    def match(self, sku: str = "", name: str = "", vendor: str = "", min_score: float = 0.8,
              product_id: str = "") -> tuple[str, str, float]:
        """Возвращает (семейство, способ, оценка). Способ: «артикул», «номер товара», «название» или пусто."""
        if sku and norm_sku(sku) in self.by_sku:
            return self.by_sku[norm_sku(sku)], "артикул", 1.0
        if product_id and str(product_id) in self.by_id:
            return self.by_id[str(product_id)], "номер товара", 1.0
        vs = self.vendor_slug(vendor) if vendor else ""
        key, score = self.by_name(name, vs)
        if key and score >= min_score:
            return key, "название", round(score, 3)
        return "", "", round(score, 3)


def sku_vendor_index(folder: Path = INTERIM) -> dict[str, str]:
    """Артикул -> вендор по адресам товаров из карты сайта (адрес: /вендор/артикул-номер/)."""
    out = {}
    with (folder / "sitemap_urls.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["kind"] in ("softwareProducts", "hardwareProducts"):
                sku = re.sub(r"-\d+$", "", r["slug"])
                out.setdefault(norm_sku(sku), r["vendor_slug"])
    return out
