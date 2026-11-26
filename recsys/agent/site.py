"""Журналы прогонов агента для стенда: data/interim/agent_runs/*.json -> site/data/agent_runs.json.

В журналах только открытый каталог магазина: карточка товара, шаги агента, найденные связи.
Данных клиентов в них нет, поэтому файл можно публиковать.

Запуск:  python -m recsys.agent.site
"""
from __future__ import annotations

import json
import sys

from recsys.config import INTERIM, ROOT

# Стоимость постановки одного нового товара, ₽ без НДС: 1,04 вызова агента (1 947 / 196 токенов)
# и 0,75 вызова критика (771 / 90) по ценам Qwen3.6 35B в Yandex AI Studio — 163,93 и 245,90 ₽ за 1 млн.
COST_PER_PRODUCT = 0.49


def short(result: object) -> object:
    """Результат инструмента в сжатом виде — как его показать на стенде."""
    if isinstance(result, list):
        out = []
        for r in result[:6]:
            if isinstance(r, dict):
                out.append({k: (v if not isinstance(v, list) else v[:3]) for k, v in r.items()
                            if k in ("id", "name", "class", "class_id", "sale", "why", "note", "examples", "top", "type")})
        return out
    if isinstance(result, dict):
        return {k: (str(v)[:260] if isinstance(v, str) else v) for k, v in result.items()
                if k in ("id", "name", "vendor", "class", "sale", "description", "links", "error")}
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    runs = []
    for p in sorted((INTERIM / "agent_runs").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        card = dict(r["card"])
        card["description"] = (card.get("description") or "")[:600]
        runs.append({
            "card": card, "truth": r.get("truth"), "passport": r["passport"],
            "steps": [{"ask": s.get("ask", 0), "tool": s["tool"], "args": s["args"], "thought": s.get("thought", ""),
                       "result": short(s["result"])} for s in r["steps"]],
            "links": r["links"], "edges": r.get("edges", []), "dropped": r["dropped"],
            "seconds": r["seconds"], "requests": r.get("model_requests"),
        })
    exam = {}
    p = ROOT / "reports" / "agent_exam.json"
    if p.exists():
        exam = json.loads(p.read_text(encoding="utf-8")).get("summary", {})
    exam_nohints = {}
    p = ROOT / "reports" / "agent_exam_nohints.json"
    if p.exists():                                    # тот же экзамен без подсказок кода: модель ищет сама
        exam_nohints = json.loads(p.read_text(encoding="utf-8")).get("summary", {})
    # Стоимость одного нового товара — по полному замеру без кеша (26.11.2026, 25 вызовов агента и 18 вызовов
    # критика на 24 товарах; токены посчитаны токенизатором Gemini), цены — Qwen3.6 35B без НДС, см. submission/cost.md.
    # Повторные прогоны экзамена берут ответы из кеша и расхода не показывают.
    per_run = {"price": "оценка по тарифу Qwen3.6 35B без НДС; токены замерены на Gemini", "rub": COST_PER_PRODUCT}
    out = ROOT / "site" / "data" / "agent_runs.json"
    out.write_text(json.dumps({"exam": exam, "exam_nohints": exam_nohints, "per_run": per_run, "runs": runs},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано: {out}, прогонов {len(runs)}, расход на прогон: {per_run}")


if __name__ == "__main__":
    main()
