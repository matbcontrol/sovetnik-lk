"""Общие пути и настройки."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"

# Датасет организаторов и всё производное от него лежит здесь и в репозиторий не попадает.
# Версия 4 от 26.11.2026 заменила версию 3 (пороги обезличивания пересчитаны по item_id, товаров 817):
# цифры, посчитанные на v3, с базовыми способами организаторов не сопоставимы.
TASK_DIR = DATA / "private" / "task"
DATASET = TASK_DIR / "dataset_v4.xlsx"

STORE = "https://store.softline.ru"
SITEMAP_INDEX = f"{STORE}/https-sitemap.xml"

# Обход вежливый: один поток, пауза между запросами, всё кешируется на диск.
USER_AGENT = "Mozilla/5.0 (compatible; research-crawler/0.1)"
REQUEST_DELAY_SEC = 1.0
REQUEST_TIMEOUT_SEC = 30
