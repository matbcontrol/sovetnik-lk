"""ИИ-тест «слепая оценка»: виртуальные клиенты сравнивают наш набор советов с базовым, не зная, где чей.

Зачем. Precision@3 считает только то, что клиент потом купил. Он не видит, полезен ли совет, который не сбылся:
продление ли это нужной лицензии или случайный товар из чужой корзины. Слепая оценка закрывает этот пробел
так, как это сделал бы опрос клиентов: каждый виртуальный клиент видит свою историю покупок и два набора
«Вам может подойти» на 31.08.2026 — нашу тройку по протоколу и «совместные покупки» из baseline.py организаторов
(ближе всего к нынешним ручным связкам) — в случайном порядке и только по названиям товаров: объяснения есть
только у нас, они подсказали бы ответ.

Шаги:
  python -m recsys.eval.ai_judge --cases   выборка 60 клиентов группы A (40 организаций, 20 частных лиц) из
                                           site/data/private.json -> data/private/task/ai_judge/{cases.json,input.md}
  оценка                                    два независимых ИИ-оценщика (агенты в среде разработки, те же условия,
                                           что и вся работа с закрытыми данными; модели по API данные задачи не
                                           получают) читают input.md с правилами JUDGE и пишут judge_1.json, judge_2.json
  python -m recsys.eval.ai_judge           сводка -> reports/ai_judge.json (только числа, без товаров)
"""
from __future__ import annotations

import json
import random
import sys

from recsys.config import ROOT, TASK_DIR

WORK = TASK_DIR / "ai_judge"
OUT = ROOT / "reports" / "ai_judge.json"
PRIVATE = ROOT / "site" / "data" / "private.json"
SEED, N_B2B, N_B2C = 11, 40, 20
JUDGE = """Для каждого товара в обоих наборах — оценка от лица клиента:
2 — стоит купить или продлить в ближайшие месяцы: нужно для того, что уже есть, продление подходящей по сроку
    лицензии, явный следующий шаг;
1 — может пригодиться, но не обязательно;
0 — не нужно: не по профилю, дублирует уже купленное и продлевать рано, неподходящий вариант той же лицензии
    или непонятно зачем.
Затем выбор набора: «А», «Б» или «=»; пустой набор полезен быть не может. Одна фраза почему.
Какой набор чей, оценщик не знает; второй оценщик идёт по случаям в обратном порядке."""


def name(items: dict, i: str) -> str:
    it = items.get(i) or {}
    return (it.get("name") or it.get("title") or i).strip()


def persona(c: dict) -> str:
    if c["group"] == "B2C":
        return "частное лицо, покупает программы для себя и семьи"
    seg = {"small": "небольшая", "medium": "средняя", "large": "крупная",
           "corporate": "крупная корпоративная"}.get(c.get("segment", ""), "")
    ind = c.get("industry") or "отрасль не указана"
    return f"{seg + ' ' if seg else ''}организация, отрасль: {ind}; вы отвечаете за закупку программ и оборудования"


def build_cases() -> None:
    d = json.loads(PRIVATE.read_text(encoding="utf-8"))
    items = d["items"]
    test = [c for c in d["clients"] if c["mode"] == "test"]
    rnd = random.Random(SEED)
    b2b = [c for c in test if c["group"] == "B2B"]
    b2c = [c for c in test if c["group"] == "B2C"]
    rnd.shuffle(b2b)
    rnd.shuffle(b2c)
    sample = b2b[:N_B2B] + b2c[:N_B2C]
    rnd.shuffle(sample)
    cases, lines = [], ["# Слепая оценка блока «Вам может подойти»\n"]
    for n, c in enumerate(sample, 1):
        ours = [x["item"] for x in c["protocol"]]
        base = [x["item"] for x in c["baseline"]["copurchase"]]
        a_is_ours = rnd.random() < 0.5
        a, b = (ours, base) if a_is_ours else (base, ours)
        hist = sorted(c["history"], key=lambda h: h["last"], reverse=True)[:8]
        cases.append({"n": n, "client": c["id"], "group": c["group"], "a_is_ours": a_is_ours,
                      "ours": c["protocol"], "base": c["baseline"]["copurchase"], "target": c["target"]})
        lines += [f"## Случай {n}", f"Кто вы: {persona(c)}.",
                  "Что вы уже покупали в этом магазине (последняя покупка — дата):"]
        lines += [f"- {name(items, h['item'])} — {h['last']}" for h in hist]
        for label, block in (("А", a), ("Б", b)):
            lines.append(f"Набор {label}:" + ("" if block else " пусто — блок ничего не показывает"))
            lines += [f"  {k}. {name(items, i)}" for k, i in enumerate(block, 1)]
        lines.append("")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    (WORK / "input.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"случаев: {len(cases)}; записано: {WORK}")


def verdicts() -> tuple[list[dict], list[dict]]:
    """Случаи и оценки обоих оценщиков, переведённые из «А/Б» в «наш/базовый»."""
    cases = json.loads((WORK / "cases.json").read_text(encoding="utf-8"))
    by_n = {c["n"]: c for c in cases}
    out = []
    for k, path in enumerate(sorted(WORK.glob("judge_*.json")), 1):
        for v in json.loads(path.read_text(encoding="utf-8")):
            c = by_n.get(int(v.get("n", 0)))
            if not c:
                continue
            ch = str(v.get("choice", "")).strip()
            mine, theirs = ("А", "Б") if c["a_is_ours"] else ("Б", "А")
            a, b = list(v.get("a") or []), list(v.get("b") or [])
            out.append({"judge": k, "n": c["n"], "client": c["client"], "group": c["group"],
                        "winner": "ours" if ch == mine else "base" if ch == theirs else "tie",
                        "ours": a if c["a_is_ours"] else b, "base": b if c["a_is_ours"] else a,
                        "why": str(v.get("why", ""))[:200]})
    return cases, out


def summary() -> dict:
    cases, vs = verdicts()
    if not vs:
        sys.exit("Оценок нет: нужны data/private/task/ai_judge/judge_*.json")

    def block(xs: list[dict]) -> dict:
        n = len(xs)
        pre = {w: sum(x["winner"] == w for x in xs) for w in ("ours", "base", "tie")}
        flat = lambda key: [s for x in xs for s in x[key] if isinstance(s, (int, float))]  # noqa: E731
        o, b = flat("ours"), flat("base")
        return {"judgments": n, "prefer": pre,
                "scores": {"ours": sum(o) / len(o) if o else None, "base": sum(b) / len(b) if b else None},
                "useless": {"ours": sum(s == 0 for s in o) / len(o) if o else None,
                            "base": sum(s == 0 for s in b) / len(b) if b else None}}

    judges = sorted({v["judge"] for v in vs})
    by_case: dict[int, dict[int, str]] = {}
    for v in vs:
        by_case.setdefault(v["n"], {})[v["judge"]] = v["winner"]
    both = [w for w in by_case.values() if len(w) == len(judges)]
    agree = sum(len(set(w.values())) == 1 for w in both) / len(both) if len(judges) > 1 and both else None
    # совпала ли оценка с тем, что клиент купил потом: случаи, где сбылся ровно один из наборов
    real = {c["n"]: ("ours" if any(x["hit"] for x in c["ours"]) else "") + ("base" if any(x["hit"] for x in c["base"]) else "")
            for c in cases}
    decided = [v for v in vs if real.get(v["n"]) in ("ours", "base")]
    total = block(vs)
    out = {
        "cases": len(vs), "clients": len(cases), "judges": len(judges),
        "groups": {g: sum(c["group"] == g for c in cases) for g in ("B2B", "B2C")},
        "prefer": total["prefer"], "scores": total["scores"], "useless": total["useless"],
        "by_group": {g: block([v for v in vs if v["group"] == g]) for g in ("B2B", "B2C")},
        "agreement": agree,
        "matches_purchase": {"cases": len(decided),
                             "share": sum(v["winner"] == real[v["n"]] for v in decided) / len(decided) if decided else None},
        "how": (f"{len(cases)} клиентов группы A ({sum(c['group'] == 'B2B' for c in cases)} организаций и "
                f"{sum(c['group'] == 'B2C' for c in cases)} частных лиц), {len(judges)} независимых ИИ-оценщика в роли "
                "этих клиентов. Каждый видит историю покупок до 31.08.2026 и два набора по три товара — нашу тройку "
                "по протоколу и «совместные покупки» из baseline.py — в случайном порядке и только названиями: "
                "какой набор чей, он не знает. Оценка — польза каждого товара от 0 до 2 и выбор набора."),
        "note": ("Это не замена проверке на истории, а её дополнение: она меряет, что купили, а здесь — полезен ли "
                 "совет, который не сбылся. Оценщики — языковые модели, а не живые клиенты."),
    }
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--cases" in sys.argv:
        return build_cases()
    out = summary()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    p, s = out["prefer"], out["scores"]
    print(f"оценок {out['cases']}: наш набор {p['ours']}, совместные покупки {p['base']}, равно {p['tie']}; "
          f"польза {s['ours']:.2f} против {s['base']:.2f}; согласие оценщиков {out['agreement']}")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
