"""Обходит страницы семейств магазина и сохраняет разобранные записи.

Запуск:  python -m recsys.crawl.families [--limit N]
Сырые страницы: data/raw/families/*.html.gz (кеш, повторный запуск ничего не качает заново)
Результат:      data/interim/families.jsonl
"""
import argparse
import csv
import gzip
import json
import time
import zlib

from recsys.config import INTERIM, RAW
from recsys.crawl.http import PoliteSession
from recsys.parse.family_page import parse_family


def family_urls() -> list[tuple[str, str, str]]:
    with (INTERIM / "sitemap_urls.csv").open(encoding="utf-8") as f:
        return [(r["url"], r["vendor_slug"], r["slug"]) for r in csv.DictReader(f) if r["kind"] == "families"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="сколько страниц обойти (0 — все)")
    args = ap.parse_args()

    cache = RAW / "families"
    cache.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "families.jsonl"
    http = PoliteSession()

    urls = family_urls()
    if args.limit:
        urls = urls[: args.limit]
    done = fetched = failed = streak = 0
    t0 = time.time()
    tmp_path = out_path.with_suffix(".jsonl.tmp")           # пишем во временный файл: сбой не обнулит готовый результат
    with tmp_path.open("w", encoding="utf-8") as out:
        for url, vendor, slug in urls:
            path = cache / f"{vendor}__{slug}.html.gz"
            html = None
            if path.exists():
                try:
                    html = gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
                except (OSError, EOFError, zlib.error):
                    path.unlink()                       # файл кеша повреждён (запись оборвалась): качаем заново
                    print(f"  повреждён кеш, качаю заново: {url}", flush=True)
            if html is None:
                r = http.get(url)
                if r is not None and r.status_code == 200:
                    html = r.text
                    path.write_bytes(gzip.compress(html.encode("utf-8"), 6))
                    fetched += 1
                    streak = 0
                else:
                    failed += 1
                    streak += 1
                    code = r.status_code if r is not None else "нет ответа"
                    print(f"  не получено ({code}): {url}", flush=True)
                    if streak >= 8:
                        print("Восемь отказов подряд — останавливаюсь, чтобы не нагружать сайт.", flush=True)
                        break
            if html:
                rec = parse_family(html, url)
                rec["vendor_slug"], rec["family_slug"] = vendor, slug
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done += 1
            if (done + failed) % 100 == 0:
                print(f"{done + failed}/{len(urls)} обработано, скачано {fetched}, отказов {failed}, "
                      f"{(time.time() - t0) / 60:.1f} мин", flush=True)
    tmp_path.replace(out_path)
    print(f"Готово: записей {done}, скачано {fetched}, отказов {failed} -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
