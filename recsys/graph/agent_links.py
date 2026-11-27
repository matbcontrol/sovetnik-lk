"""Связи, найденные агентом каталога, когда он закрывает «дыру» — товар, владельцу которого нечего посоветовать.

Агент (recsys.agent.holes) пишет таблицу knowledge/agent_links.csv:
    src, dst, type, source, confidence, evidence, quote, status, model, date
src и dst — семейства каталога. source — «агент каталога» (личный совет) или «агент каталога: комплект»
(последней карточкой, с условием «Если ещё нет…»). evidence — готовая фраза клиенту, quote — дословная
цитата из описания или характеристик товара, которой критик подтвердил связь.

status = graph  — цитата есть: связь идёт в граф, клиент видит совет сразу, менеджер может отклонить.
status = queue  — модель предложила, а цитаты нет: связь ждёт менеджера и в граф не идёт.

Вердикт разметки по той же паре товаров остаётся главнее связи агента.
"""
from __future__ import annotations

import csv

from recsys.config import ROOT

PATH = ROOT / "knowledge" / "agent_links.csv"
NEED, KIT = "агент каталога", "агент каталога: комплект"
TYPES = ("complements", "requires", "upgrade", "new_version")


def rows(path=PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            if r.get("src") and r.get("dst") and r["src"] != r["dst"] and r.get("type") in TYPES \
                    and r.get("source") in (NEED, KIT):
                out.append(r)
        return out


def read(path=PATH) -> list[dict]:
    """Подтверждённые связи в том же виде, что строки графа каталога."""
    return [{"src": r["src"], "dst": r["dst"], "type": r["type"], "source": r["source"],
             "confidence": float(r.get("confidence") or (0.5 if r["source"] == KIT else 0.75)),
             "evidence": r.get("evidence", "")}
            for r in rows(path) if r.get("status") == "graph"]


def queue(path=PATH) -> list[dict]:
    """Предложения без подтверждающей цитаты: только в очередь менеджера."""
    return [{"src": r["src"], "dst": r["dst"], "type": r["type"], "source": r["source"],
             "confidence": float(r.get("confidence") or 0.5), "evidence": r.get("evidence", ""),
             "quote": r.get("quote", ""), "model": r.get("model", ""), "date": r.get("date", "")}
            for r in rows(path) if r.get("status") == "queue"]


def quotes(path=PATH) -> dict[str, str]:
    """Цитата, которой подтверждена связь: «src|dst|type» -> цитата (для очереди менеджера на стенде)."""
    return {f"{r['src']}|{r['dst']}|{r['type']}": r.get("quote", "") for r in rows(path) if r.get("quote")}


REPORT = ROOT / "reports" / "agent_holes.json"
SITE_REPORT = ROOT / "site" / "data" / "agent_holes.json"


def publish(report=REPORT, out=SITE_REPORT) -> int:
    """Копия отчёта агента для страницы стенда: что он сделал с каждой разобранной дырой.

    Дыры идут по алфавиту: порядок разбора — это порядок очереди по числу клиентов, а он считается
    по закрытым данным и на открытую страницу попадать не должен. Отчёта нет — пустой список,
    чтобы страница не спотыкалась о «не найдено».
    """
    import json

    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    holes = data.get("holes") or []
    holes = sorted(holes.values() if isinstance(holes, dict) else holes, key=lambda h: str(h.get("name", "")).lower())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"holes": holes, "summary": data.get("summary", {})}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return len(holes)
