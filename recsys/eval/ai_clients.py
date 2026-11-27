"""Симуляция ИИ-клиентов на всех клиентах группы A: нажали бы, купили бы, какой набор полезнее.

Продолжение слепой оценки (recsys.eval.ai_judge, 60 клиентов) на всех 337 клиентах группы A и с двумя вопросами,
которые задал бы A/B-тест блока: нажал бы клиент на совет и купил бы его. Каждый виртуальный клиент видит свою
историю покупок до 31.08.2026 и два набора «Вам может подойти» — нашу тройку по протоколу и «совместные покупки»
из baseline.py организаторов — в случайном порядке и только названиями, не зная, какой набор чей.

Шаги:
  python -m recsys.eval.ai_clients --cases   случаи -> data/private/task/ai_clients/ (cases.json и пачки для оценщиков)
  оценка                                      ИИ-оценщики — агенты в среде разработки, как и в ai_judge: модели по API
                                             данные задачи не получают; пишут judge_<оценщик>_<пачка>.json
  python -m recsys.eval.ai_clients           сводка -> reports/ai_clients.json (только числа, без товаров)
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter

from recsys.config import ROOT, TASK_DIR
from recsys.eval.ai_judge import name, persona

WORK = TASK_DIR / "ai_clients"
OUT = ROOT / "reports" / "ai_clients.json"
PRIVATE = ROOT / "site" / "data" / "private.json"
SEED, BATCHES = 23, 3
RULES = """Вы — этот клиент. В личном кабинете магазина блок «Вам может подойти» в один день показал вам набор А,
в другой — набор Б. Для каждого набора ответьте честно, как ответил бы занятой закупщик или покупатель:
click — номер товара, по которому вы нажали бы «Подробнее», или 0, если не нажали бы ни на один
        (большинство клиентов ни на что не нажимает — нажимайте, только если правда заинтересовались);
buy   — номер товара, который вы купили бы в ближайшие три месяца, или 0 (только если правда купили бы);
оценки — польза каждого товара: 2 — стоит купить или продлить в ближайшие месяцы (нужно к тому, что есть,
        продление подходящей по сроку лицензии, явный следующий шаг); 1 — может пригодиться; 0 — не нужно
        (не по профилю, дублирует купленное и продлевать рано, неподходящий вариант той же лицензии, непонятно зачем).
Пустой набор: click и buy — 0, оценок нет. Затем choice — какой набор полезнее: «А», «Б» или «=». И одна фраза why.
Какой набор чей, вы не знаете и не угадываете."""


def build_cases() -> None:
    d = json.loads(PRIVATE.read_text(encoding="utf-8"))
    items = d["items"]
    test = [c for c in d["clients"] if c["mode"] == "test"]
    rnd = random.Random(SEED)
    rnd.shuffle(test)
    cases, blocks = [], []
    for n, c in enumerate(test, 1):
        ours = [x["item"] for x in c["protocol"]]
        base = [x["item"] for x in c["baseline"]["copurchase"]]
        a_is_ours = rnd.random() < 0.5
        a, b = (ours, base) if a_is_ours else (base, ours)
        hist = sorted(c["history"], key=lambda h: h["last"], reverse=True)[:8]
        cases.append({"n": n, "client": c["id"], "group": c["group"], "a_is_ours": a_is_ours,
                      "ours": c["protocol"], "base": c["baseline"]["copurchase"]})
        lines = [f"## Случай {n}", f"Кто вы: {persona(c)}.", "Что вы уже покупали в этом магазине (последняя покупка — дата):"]
        lines += [f"- {name(items, h['item'])} — {h['last']}" for h in hist]
        for label, block in (("А", a), ("Б", b)):
            lines.append(f"Набор {label}:" + ("" if block else " пусто — блок ничего не показывает"))
            lines += [f"  {k}. {name(items, i)}" for k, i in enumerate(block, 1)]
        blocks.append("\n".join(lines) + "\n")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    size = -(-len(blocks) // BATCHES)
    head = "# Симуляция клиентов: блок «Вам может подойти»\n\n" + RULES + "\n\n"
    for k in range(BATCHES):
        part = blocks[k * size:(k + 1) * size]
        (WORK / f"batch_{k + 1}.md").write_text(head + "\n".join(part), encoding="utf-8")
        (WORK / f"batch_{k + 1}_reversed.md").write_text(head + "\n".join(reversed(part)), encoding="utf-8")
    print(f"случаев: {len(cases)}, пачек: {BATCHES} по {size}; записано: {WORK}")


def _pos(v) -> int:
    try:
        k = int(v)
    except (TypeError, ValueError):
        return 0
    return k if 1 <= k <= 3 else 0


def answers() -> tuple[list[dict], list[dict]]:
    """Случаи и ответы всех оценщиков, переведённые из «А/Б» в «наш/базовый»."""
    cases = json.loads((WORK / "cases.json").read_text(encoding="utf-8"))
    by_n = {c["n"]: c for c in cases}
    out = []
    for path in sorted(WORK.glob("judge_*_*.json")):
        judge = int(path.stem.split("_")[1])
        for v in json.loads(path.read_text(encoding="utf-8")):
            c = by_n.get(int(v.get("n", 0) or 0))
            if not c:
                continue
            mine, theirs = ("А", "Б") if c["a_is_ours"] else ("Б", "А")
            ch = str(v.get("choice", "")).strip()
            side = {"ours": "a" if c["a_is_ours"] else "b", "base": "b" if c["a_is_ours"] else "a"}
            row = {"judge": judge, "n": c["n"], "group": c["group"],
                   "winner": "ours" if ch == mine else "base" if ch == theirs else "tie"}
            for arm, s in side.items():
                block = c[arm]
                click, buy = _pos(v.get(f"click_{s}")), _pos(v.get(f"buy_{s}"))
                click = click if click <= len(block) else 0
                buy = buy if buy <= len(block) else 0
                row[arm] = {"scores": list(v.get(s) or []), "click": click, "buy": buy,
                            "buy_hit": bool(buy and block[buy - 1]["hit"]), "shown": len(block)}
            out.append(row)
    return cases, out


def summary() -> dict:
    cases, rows = answers()
    if not rows:
        sys.exit("Ответов нет: нужны data/private/task/ai_clients/judge_<оценщик>_<пачка>.json")

    def part(sel: list[dict]) -> dict:
        n = len(sel)
        res = {"judgments": n, "prefer": dict(Counter(r["winner"] for r in sel))}
        for arm in ("ours", "base"):
            a = [r[arm] for r in sel]
            scores = [s for x in a for s in x["scores"] if isinstance(s, (int, float))]
            buys = [x for x in a if x["buy"]]
            res[arm] = {
                "click": round(sum(1 for x in a if x["click"]) / n, 3) if n else None,
                "buy": round(len(buys) / n, 3) if n else None,
                "buy_came_true": round(sum(x["buy_hit"] for x in buys) / len(buys), 3) if buys else None,
                "usefulness": round(sum(scores) / len(scores), 2) if scores else None,
                "useless_share": round(sum(1 for s in scores if s == 0) / len(scores), 3) if scores else None,
                "empty_sets": sum(1 for x in a if not x["shown"]),
            }
        return res

    judges = sorted({r["judge"] for r in rows})
    by_case: dict[int, dict[int, str]] = {}
    for r in rows:
        by_case.setdefault(r["n"], {})[r["judge"]] = r["winner"]
    both = [w for w in by_case.values() if len(w) == len(judges)]
    groups = {g: part([r for r in rows if r["group"] == g]) for g in ("B2B", "B2C")}
    out = {
        "clients": len(cases), "judges": len(judges), "judgments": len(rows),
        "groups": {g: sum(c["group"] == g for c in cases) for g in ("B2B", "B2C")},
        "all": part(rows), "by_group": groups,
        "agreement": round(sum(len(set(w.values())) == 1 for w in both) / len(both), 3) if len(judges) > 1 and both else None,
        "how": (f"{len(cases)} клиентов группы A, {len(judges)} независимых ИИ-оценщика в роли этих клиентов "
                "(агенты в среде разработки; модели по API данные задачи не получают). Каждый видит историю покупок "
                "до 31.08.2026 и два набора по три товара — нашу тройку по протоколу и «совместные покупки» из "
                "baseline.py — в случайном порядке и только названиями. Вопросы: нажал бы на совет, купил бы его "
                "в ближайшие три месяца, польза каждого товара 0–2 и какой набор полезнее."),
        "note": ("click и buy — доля оценок, где ИИ-клиент нажал бы хотя бы на один совет набора или купил бы его; "
                 "buy_came_true — доля таких «купил бы», которые клиент на деле купил с 07.09 по 23.11.2026. "
                 "Это намерения языковой модели в роли клиента, а не поведение живых клиентов."),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--cases" in sys.argv:
        return build_cases()
    out = summary()
    for g, v in out["by_group"].items():
        o, b = v["ours"], v["base"]
        print(f"{g}: оценок {v['judgments']}; нажал бы: наш {o['click']}, базовый {b['click']}; купил бы: наш {o['buy']}, "
              f"базовый {b['buy']}; из «купил бы» сбылось: наш {o['buy_came_true']}, базовый {b['buy_came_true']}; "
              f"выбор {v['prefer']}")
    print(f"согласие оценщиков {out['agreement']}; записано: {OUT}")


if __name__ == "__main__":
    main()
