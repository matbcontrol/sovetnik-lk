"""Скачивает карту сайта магазина и собирает списки: вендоры, семейства, товары, категории.

Запуск:  python -m recsys.crawl.sitemap
Результат: data/interim/sitemap_urls.csv  (kind, url, vendor_slug, slug)
"""
import csv
import re
from urllib.parse import urlparse

from recsys.config import INTERIM, RAW, SITEMAP_INDEX
from recsys.crawl.http import PoliteSession

LOC = re.compile(r"<loc>([^<]+)</loc>")


def kind_of(sitemap_url: str) -> str:
    name = sitemap_url.rsplit("/", 1)[-1].removesuffix(".xml")
    return re.sub(r"-\d+$", "", name)  # softwareProducts-3 -> softwareProducts


def main() -> None:
    raw_dir = RAW / "sitemaps"
    raw_dir.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)
    http = PoliteSession()

    index = http.get(SITEMAP_INDEX)
    if index is None or index.status_code != 200:
        raise SystemExit("Не удалось получить индекс карты сайта")
    sitemaps = LOC.findall(index.text)
    print(f"карт в индексе: {len(sitemaps)}")

    rows = []
    for sm in sitemaps:
        r = http.get(sm)
        if r is None or r.status_code != 200:
            print(f"  пропуск {sm}")
            continue
        (raw_dir / sm.rsplit("/", 1)[-1]).write_bytes(r.content)
        kind = kind_of(sm)
        urls = LOC.findall(r.text)
        for u in urls:
            parts = [p for p in urlparse(u).path.split("/") if p]
            vendor = parts[0] if parts else ""
            slug = parts[1] if len(parts) > 1 else ""
            rows.append((kind, u, vendor, slug))
        print(f"  {kind}: {len(urls)}")

    out = INTERIM / "sitemap_urls.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "url", "vendor_slug", "slug"])
        w.writerows(rows)
    print(f"записано строк: {len(rows)} -> {out}")


if __name__ == "__main__":
    main()
