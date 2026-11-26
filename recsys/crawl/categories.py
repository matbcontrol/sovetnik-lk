"""Обходит подкатегории магазина и сохраняет порядок товаров на витрине.

Запуск:  python -m recsys.crawl.categories [--limit N] [--max-pages N]
Сырые страницы: data/raw/categories/*.html.gz (кеш)
Результат:      data/interim/category_listing.jsonl — по строке на товар в подкатегории:
                подкатегория, место на витрине, семейство, рейтинг, отзывы, метки условий
"""
import argparse
import csv
import gzip
import json
import math
import time
from urllib.parse import urlparse

from recsys.config import INTERIM, RAW
from recsys.crawl.http import PoliteSession
from recsys.parse.category_page import parse_category

PER_PAGE = 24


def leaf_categories() -> list[tuple[str, str, str]]:
    """Подкатегории (два уровня в адресе) и те категории верхнего уровня, у которых подкатегорий нет."""
    paths = []
    with (INTERIM / "sitemap_urls.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["kind"] == "categories":
                parts = [p for p in urlparse(r["url"]).path.split("/") if p]
                if 1 <= len(parts) <= 2:
                    paths.append((r["url"], parts))
    parents = {p[0] for _, p in paths if len(p) == 2}
    out = []
    for url, p in paths:
        if len(p) == 2:
            out.append((url, p[0], p[1]))
        elif p[0] not in parents:
            out.append((url, p[0], ""))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="сколько подкатегорий обойти (0 — все)")
    ap.add_argument("--max-pages", type=int, default=12, help="не больше стольких страниц на подкатегорию")
    ap.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, секунд")
    args = ap.parse_args()

    cache = RAW / "categories"
    cache.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "category_listing.jsonl"
    http = PoliteSession(delay=args.delay)

    cats = leaf_categories()
    if args.limit:
        cats = cats[: args.limit]
    fetched = failed = streak = rows = 0
    t0 = time.time()

    def page_html(url: str, cat: str, sub: str, page: int) -> str | None:
        nonlocal fetched, failed, streak
        path = cache / f"{cat}__{sub or '_'}__p{page}.html.gz"
        if path.exists():
            return gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
        r = http.get(url if page == 1 else f"{url}?page={page}")
        if r is not None and r.status_code == 200:
            path.write_bytes(gzip.compress(r.text.encode("utf-8"), 6))
            fetched += 1
            streak = 0
            return r.text
        failed += 1
        streak += 1
        print(f"  не получено ({r.status_code if r is not None else 'нет ответа'}): {url} стр. {page}", flush=True)
        return None

    with out_path.open("w", encoding="utf-8") as out:
        for n, (url, cat, sub) in enumerate(cats, 1):
            if streak >= 8:
                print("Восемь отказов подряд — останавливаюсь, чтобы не нагружать сайт.", flush=True)
                break
            html = page_html(url, cat, sub, 1)
            if not html:
                continue
            first = parse_category(html)
            total = first["total"] or len(first["items"])
            pages = min(max(1, math.ceil(total / PER_PAGE)), args.max_pages)
            position = 0
            for page in range(1, pages + 1):
                parsed = first if page == 1 else None
                if parsed is None:
                    h = page_html(url, cat, sub, page)
                    if not h:
                        break
                    parsed = parse_category(h)
                if not parsed["items"]:
                    break
                for it in parsed["items"]:
                    position += 1
                    out.write(json.dumps({
                        "category_slug": cat, "subcategory_slug": sub, "subcategory": first["name"],
                        "total": total, "position": position, **it,
                    }, ensure_ascii=False) + "\n")
                    rows += 1
            if n % 20 == 0:
                print(f"{n}/{len(cats)} подкатегорий, строк {rows}, скачано {fetched}, отказов {failed}, "
                      f"{(time.time() - t0) / 60:.1f} мин", flush=True)
    print(f"Готово: подкатегорий {len(cats)}, строк {rows}, скачано {fetched}, отказов {failed} -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
