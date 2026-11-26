"""Журналы прогонов агента для стенда: data/interim/agent_runs/*.json -> site/data/agent_runs.json.

В журналах только открытый каталог магазина: карточка товара, шаги агента, найденные связи.
Данных клиентов в них нет, поэтому файл можно публиковать.

Запуск:  python -m recsys.agent.site
"""
from __future__ import annotations

import json
import sys

from recsys.config import INTERIM, ROOT

# цены модели, доступной в России, для оценки стоимости прогона: рублей за 1000 токенов (вход, выход)
PRICE = {"name": "Qwen3.6 35B в Yandex AI Studio", "in": 0.2, "out": 0.3}


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
    # средний расход токенов на товар — из экзамена: каждый прогон сам считает свои токены
    per_run = None
    if exam.get("tokens_in_per_run"):
        t_in, t_out = exam["tokens_in_per_run"], exam["tokens_out_per_run"]
        per_run = {"tokens_in": t_in, "tokens_out": t_out, "price": PRICE["name"],
                   "rub": round(t_in / 1000 * PRICE["in"] + t_out / 1000 * PRICE["out"], 2)}
    out = ROOT / "site" / "data" / "agent_runs.json"
    out.write_text(json.dumps({"exam": exam, "per_run": per_run, "runs": runs}, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"записано: {out}, прогонов {len(runs)}, расход на прогон: {per_run}")


if __name__ == "__main__":
    main()
