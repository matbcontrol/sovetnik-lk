"""Таблица замещения из открытого каталога совместимости АРПП «Отечественный софт».

Источник: https://catalog.arppsoft.ru/replacement_download (robots.txt сайта разрешает обход).
Строка таблицы: зарубежный продукт -> российский продукт, номер в реестре, классы ПО.

Запуск:  python -m recsys.sources.arpp
Результат: data/interim/arpp_replacements.csv
"""
import csv

import openpyxl

from recsys.config import INTERIM, RAW
from recsys.crawl.http import PoliteSession

URL = "https://catalog.arppsoft.ru/replacement_download"


def download() -> None:
    target = RAW / "arpp" / "replacement.xlsx"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    r = PoliteSession().get(URL)
    if r is None or r.status_code != 200 or r.content[:2] != b"PK":
        raise SystemExit("Не удалось получить таблицу замещения")
    target.write_bytes(r.content)


def main() -> None:
    download()
    wb = openpyxl.load_workbook(RAW / "arpp" / "replacement.xlsx", read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        foreign, domestic, registry_no, *classes = row
        if not foreign or not domestic:
            continue
        cls = [str(c).strip() for c in classes if c]
        rows.append((str(foreign).strip(), str(domestic).strip(), str(registry_no or "").strip(), "|".join(cls)))
    INTERIM.mkdir(parents=True, exist_ok=True)
    out = INTERIM / "arpp_replacements.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["foreign_product", "domestic_product", "registry_no", "classes"])
        w.writerows(rows)
    foreign = {r[0] for r in rows}
    domestic = {r[1] for r in rows}
    classes = {c for r in rows for c in r[3].split("|") if c}
    print(f"пар замещения: {len(rows)}; зарубежных продуктов: {len(foreign)}; "
          f"российских: {len(domestic)}; классов ПО: {len(classes)} -> {out}")


if __name__ == "__main__":
    main()
