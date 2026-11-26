"""Экзамен агента — «вынь и вставь».

Берём товар, про который в каталоге всё известно, прячем его от инструментов и даём агенту только
карточку (название, вендор, описание) — как будто товар только что завели. Потом сравниваем ответ
агента с тем, что было спрятано:
  класс        — совпал ли с классом, в который товар поставил сам магазин (точно или в той же
                 крупной категории);
  базовый товар — для дополнений и курсов: нашёл ли агент продукт, к которому они относятся;
  линейка      — для редакций и версий: связал ли агент товар с соседом по линейке.

Так проверяется именно то, что нужно для новых и «соло»-товаров: место в графе по одной карточке.
Работает только на открытом каталоге; данных клиентов здесь нет.

Запуск:  python -m recsys.agent.exam --n 12        (каждый прогон — 3–4 запроса к модели)
Отчёт:   reports/agent_exam.json, журналы прогонов — data/interim/agent_runs/
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict

from recsys.agent.agent import place
from recsys.agent.tools import Catalog
from recsys.config import INTERIM, ROOT


def pick(cat: Catalog, n: int, seed: int = 11) -> list[dict]:
    """Товары для экзамена: дополнения, члены линеек и просто случайные — поровну."""
    rnd = random.Random(seed)
    base_of: dict[str, set[str]] = defaultdict(set)      # дополнение -> его базовые продукты (по названиям)
    line_of: dict[str, set[str]] = defaultdict(set)      # товар -> соседи по линейке
    with (INTERIM / "graph_edges.csv").open(encoding="utf-8") as f:
        for e in csv.DictReader(f):
            if e["source"] == "название товара":
                base_of[e["dst"]].add(e["src"])
            if e["type"] in ("upgrade", "new_version", "same_line"):
                line_of[e["src"]].add(e["dst"])
                line_of[e["dst"]].add(e["src"])

    def ok(k: str) -> bool:
        n_ = cat.nodes.get(k) or {}
        return (n_.get("kind") == "software" and n_.get("class") and n_.get("on_sale") == "1"
                and len((cat.desc.get(k) or {}).get("description") or "") >= 300)

    addons = sorted(k for k in base_of if ok(k))
    ladder = sorted(k for k in line_of if ok(k) and k not in base_of)
    plain = sorted(k for k in cat.nodes if ok(k) and k not in base_of and k not in line_of)
    for lst in (addons, ladder, plain):
        rnd.shuffle(lst)
    third = max(1, n // 3)
    out = [{"id": k, "kind": "дополнение", "truth": sorted(base_of[k])} for k in addons[:third]]
    out += [{"id": k, "kind": "линейка", "truth": sorted(line_of[k])} for k in ladder[:third]]
    out += [{"id": k, "kind": "обычный", "truth": []} for k in plain[: n - len(out)]]
    return out


def grade(cat: Catalog, task: dict, res: dict) -> dict:
    node = cat.nodes[task["id"]]
    cls = res["passport"]["class_id"]
    dsts = {link["dst"] for link in res["links"]}
    lines = set()
    for t in task["truth"]:
        lines.add(t)
    out = {
        "id": task["id"], "name": node["name"], "kind": task["kind"],
        "class_true": node.get("class_name", ""), "class_agent": res["passport"]["class_name"],
        "class_named": not res["passport"].get("class_note"),
        "class_source": res["passport"].get("class_source", "агент"),
        "class_exact": cls == node["class"],
        "class_category": bool(cls) and cls.split("/")[0] == node["class"].split("/")[0],
        "links": len(res["links"]), "dropped": len(res["dropped"]),
        "requests": res["model_requests"], "seconds": res["seconds"],
    }
    if task["kind"] in ("дополнение", "линейка"):
        out["found"] = bool(dsts & lines)                     # нашёл сам агент
        by_rule = {e["src"] if e["dst"] == "new" else e["dst"] for e in res.get("rule_edges", [])}
        out["found_with_rules"] = bool((dsts | by_rule) & lines)   # агент или правило по названию
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=11, help="номер выборки: другой номер — другие товары")
    args = ap.parse_args()
    from recsys.llm.client import LLM, LLMError
    cat = Catalog()
    llm = LLM()
    runs_dir = INTERIM / "agent_runs"
    runs_dir.mkdir(exist_ok=True)
    rows = []
    for task in pick(cat, args.n, args.seed):
        cat.hidden = {task["id"]}
        try:
            res = place(llm, cat, cat.card(task["id"]))
        except LLMError as e:
            print(f"остановлено: {str(e)[:160]}")
            break
        res["truth"] = {"id": task["id"], "class": cat.nodes[task["id"]].get("class_name", ""), "kind": task["kind"],
                        "related": [cat.nodes[t]["name"] for t in task["truth"] if t in cat.nodes][:6]}
        (runs_dir / f"{task['id'].replace('/', '__')}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        g = grade(cat, task, res)
        rows.append(g)
        mark = "" if "found" not in g else (" | связь найдена" if g["found"] else " | связь не найдена")
        print(f"[{g['kind']:10}] {g['name'][:44]:44} класс: {'точно' if g['class_exact'] else ('та же категория' if g['class_category'] else 'мимо'):15}"
              f"{mark} | запросов {g['requests']}, {g['seconds']} с", flush=True)
    if not rows:
        return
    n = len(rows)
    rel = [r for r in rows if "found" in r]
    summary = {
        "runs": n, "seed": args.seed,
        "class_by_neighbors": sum(r["class_source"] == "соседи по каталогу" for r in rows) / n,
        "class_exact": sum(r["class_exact"] for r in rows) / n,
        "class_category": sum(r["class_category"] for r in rows) / n,
        "related_found": (sum(r["found"] for r in rel) / len(rel)) if rel else None,
        "related_found_with_rules": (sum(r.get("found_with_rules", r["found"]) for r in rel) / len(rel)) if rel else None,
        "related_runs": len(rel),
        "requests_per_run": sum(r["requests"] for r in rows) / n,
        "seconds_per_run": sum(r["seconds"] for r in rows) / n,
        "links_kept": sum(r["links"] for r in rows), "links_dropped": sum(r["dropped"] for r in rows),
    }
    print(f"\nпрогонов {n}: класс точно {summary['class_exact']:.0%}, в той же категории {summary['class_category']:.0%}; "
          + (f"связанный товар найден в {summary['related_found']:.0%} из {len(rel)}; " if rel else "")
          + f"в среднем {summary['requests_per_run']:.1f} запроса и {summary['seconds_per_run']:.0f} с на товар")
    out = ROOT / "reports" / "agent_exam.json"
    out.write_text(json.dumps({"summary": summary, "runs": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("отчёт:", out)


if __name__ == "__main__":
    main()
