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

Запуск:  python -m recsys.agent.exam --n 24 --seed 23              как в эксплуатации: код готовит кандидатов
         python -m recsys.agent.exam --n 24 --seed 23 --no-hints   без подсказок кода: модель ищет сама
Отчёт:   reports/agent_exam.json (reports/agent_exam_nohints.json), журналы — data/interim/agent_runs/
         (agent_runs_nohints/). В отчёте есть строка «только код»: какой класс дал бы код без модели.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict

from recsys.agent.agent import ALIKE, place
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
        "tokens_in": (res.get("tokens") or {}).get("in", 0), "tokens_out": (res.get("tokens") or {}).get("out", 0),
        # сколько раз модель сама вызвала инструмент (шаги кода без модели не считаются)
        "model_tool_calls": sum(1 for st in res["steps"] if st.get("ask", 0) >= 1 and st["tool"] != "итог"),
    }
    # класс, который дал бы один код без модели: соседи по каталогу, а если они не похожи — поиск класса
    card = res["card"]
    h = cat.neighbor_class(card.get("name", ""), card.get("vendor", ""))
    if h["class_id"] and h.get("similarity", 0.0) >= ALIKE:
        code_cls = h["class_id"]
    else:
        found = cat.find_classes(f"{card.get('name', '')} {(card.get('description') or '')[:300]}".strip())
        code_cls = found[0]["class_id"] if found else ""
    out["class_code_only"] = code_cls == node["class"]
    if task["kind"] in ("дополнение", "линейка"):
        out["found"] = bool(dsts & lines)                     # нашёл сам агент
        by_rule = {e["src"] if e["dst"] == "new" else e["dst"] for e in res.get("rule_edges", [])}
        out["found_with_rules"] = bool((dsts | by_rule) & lines)   # агент или правило по названию
        # был ли верный товар уже среди кандидатов, которые код нашёл до модели
        given = {r.get("id") for st in res["steps"] if st.get("ask", 0) == 0 and isinstance(st["result"], list)
                 for r in st["result"] if isinstance(r, dict)}
        out["truth_given_by_code"] = bool(given & lines)
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=11, help="номер выборки: другой номер — другие товары")
    ap.add_argument("--no-hints", action="store_true", help="без подсказок кода: класс и связи модель ищет сама")
    args = ap.parse_args()
    from recsys.llm.client import LLM, LLMError
    cat = Catalog()
    llm = LLM()
    suffix = "_nohints" if args.no_hints else ""
    runs_dir = INTERIM / f"agent_runs{suffix}"
    runs_dir.mkdir(exist_ok=True)
    rows = []
    for task in pick(cat, args.n, args.seed):
        cat.hidden = {task["id"]}
        try:
            res = place(llm, cat, cat.card(task["id"]), hints=not args.no_hints)
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
    fresh = [r for r in rows if r["tokens_in"] > 0]
    summary = {
        "runs": n, "seed": args.seed, "hints": not args.no_hints,
        "class_code_only": sum(r["class_code_only"] for r in rows) / n,
        "model_tool_calls_per_run": sum(r["model_tool_calls"] for r in rows) / n,
        "runs_with_model_tools": sum(r["model_tool_calls"] > 0 for r in rows) / n,
        "truth_given_by_code": (sum(r.get("truth_given_by_code", False) for r in rel) / len(rel)) if rel else None,
        "class_by_neighbors": sum(r["class_source"] == "соседи по каталогу" for r in rows) / n,
        "class_exact": sum(r["class_exact"] for r in rows) / n,
        "class_category": sum(r["class_category"] for r in rows) / n,
        "related_found": (sum(r["found"] for r in rel) / len(rel)) if rel else None,
        "related_found_with_rules": (sum(r.get("found_with_rules", r["found"]) for r in rel) / len(rel)) if rel else None,
        "related_runs": len(rel),
        "requests_per_run": sum(r["requests"] for r in rows) / n,
        "seconds_per_run": sum(r["seconds"] for r in rows) / n,
        "links_kept": sum(r["links"] for r in rows), "links_dropped": sum(r["dropped"] for r in rows),
        "seconds_median": sorted(r["seconds"] for r in rows)[n // 2],
        # расход токенов на товар: только по прогонам, где ответы модели не взяты из кеша
        "tokens_in_per_run": round(sum(r["tokens_in"] for r in fresh) / len(fresh)) if fresh else None,
        "tokens_out_per_run": round(sum(r["tokens_out"] for r in fresh) / len(fresh)) if fresh else None,
        "by_kind": {kind: {"runs": len(part), "agent": sum(r["found"] for r in part),
                           "agent_or_rule": sum(r["found_with_rules"] for r in part)}
                    for kind in ("дополнение", "линейка") if (part := [r for r in rel if r["kind"] == kind])},
    }
    summary["without_cache"] = journal_costs()
    print(f"\nпрогонов {n}: класс точно {summary['class_exact']:.0%}, в той же категории {summary['class_category']:.0%}; "
          + (f"связанный товар найден в {summary['related_found']:.0%} из {len(rel)}; " if rel else "")
          + f"в среднем {summary['requests_per_run']:.1f} запроса и {summary['seconds_per_run']:.0f} с на товар; "
          + f"класс одним кодом {summary['class_code_only']:.0%}; инструменты модель вызывала сама в "
          + f"{summary['runs_with_model_tools']:.0%} прогонов ({summary['model_tool_calls_per_run']:.1f} на товар)")
    out = ROOT / "reports" / f"agent_exam{suffix}.json"
    out.write_text(json.dumps({"summary": summary, "runs": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("отчёт:", out)


def journal_costs() -> dict:
    """Расход исходных вызовов агента без кеша — по журналу data/interim/llm_calls.jsonl.

    Повторный экзамен берёт ответы модели из кеша, поэтому время и токены его прогонов нулевые. Чтобы в отчёте
    стояла настоящая цена, здесь средние по всем некешированным вызовам шага агента и критика из журнала.
    """
    path = ROOT / "data" / "interim" / "llm_calls.jsonl"
    if not path.exists():
        return {}
    acc: dict[str, list[float]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            if c.get("cached") or c.get("purpose") not in ("агент: шаг", "агент: критик"):
                continue
            a = acc.setdefault(c["purpose"], [0, 0, 0, 0.0])
            a[0] += 1
            a[1] += c.get("tokens_in", 0)
            a[2] += c.get("tokens_out", 0)
            a[3] += c.get("seconds", 0) or 0
    out = {name: {"calls": int(n), "tokens_in": round(ti / n), "tokens_out": round(to / n), "seconds": round(s / n, 1)}
           for name, (n, ti, to, s) in acc.items() if n}
    out["note"] = ("Ответы экзамена взяты из кеша, поэтому время и токены прогонов в нём нулевые; здесь — средние "
                   "исходных вызовов без кеша по журналу llm_calls.jsonl")
    return out


if __name__ == "__main__":
    main()
