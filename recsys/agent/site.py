"""Журналы прогонов агента для стенда: data/interim/agent_runs/*.json -> site/data/agent_runs.json.

В журналах только открытый каталог магазина: карточка товара, шаги агента, найденные связи.
Данных клиентов в них нет, поэтому файл можно публиковать.

Запуск:  python -m recsys.agent.site
"""
from __future__ import annotations

import json
import sys

from recsys.config import INTERIM, ROOT

# Стоимость постановки одного нового товара, ₽ без НДС: 3,2 вызова агента (3 077 / 200 токенов) и 1 вызов критика
# (963 / 123) — экзамен на deepseek-v4-pro без рассуждения (reports/agent_exam_deepseek.json), дневной тариф DeepSeek
# $1,32 и $3,96 за 1 млн по курсу ЦБ 82,1265 ₽ — 108,41 и 325,22 ₽ за 1 млн (submission/cost.md).
COST_PER_PRODUCT = 1.42


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
    # Стоимость одного нового товара — замер на deepseek-v4-pro (27.11.2026, 77 вызовов агента и 24 вызова критика
    # на 24 товарах), дневной тариф DeepSeek без НДС, см. submission/cost.md. Прогоны ниже — экзамен на Gemini.
    # Повторные прогоны экзамена берут ответы из кеша и расхода не показывают.
    # модель экзамена — по журналу вызовов без кеша: почти все шаги и вызовы критика шли на облегчённых моделях
    if exam:
        exam.setdefault("model", "облегчённая Gemini Flash-Lite (3.5 и 3.1); из 295 вызовов обоих экзаменов на 3.6 Flash — 5")
    if exam_nohints:
        exam_nohints.setdefault("model", "облегчённая Gemini Flash-Lite")
    per_run = {"price": "замер на deepseek-v4-pro, дневной тариф без НДС", "rub": COST_PER_PRODUCT}
    out = ROOT / "site" / "data" / "agent_runs.json"
    out.write_text(json.dumps({"exam": exam, "exam_nohints": exam_nohints, "per_run": per_run, "runs": runs},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано: {out}, прогонов {len(runs)}, расход на прогон: {per_run}")


if __name__ == "__main__":
    main()
