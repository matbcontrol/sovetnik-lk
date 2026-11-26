"""Общие пути и настройки."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"

STORE = "https://store.softline.ru"
SITEMAP_INDEX = f"{STORE}/https-sitemap.xml"

# Обход вежливый: один поток, пауза между запросами, всё кешируется на диск.
USER_AGENT = "Mozilla/5.0 (compatible; research-crawler/0.1)"
REQUEST_DELAY_SEC = 1.0
REQUEST_TIMEOUT_SEC = 30
