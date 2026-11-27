"""Согласие двух моделей как фильтр перед менеджером: какие вердикты пар можно принимать без человека.

Модели по API размечают пары «купил А — советовать ли Б» ниже порога доверия — 85 % одинаковых решений
«советовать / не советовать» с первой разметкой (python -m recsys.llm.pair_verdicts check): gemini-3.6-flash
76,5 %, deepseek-v4-pro с рассуждением 81,3 %. Здесь проверяется, что даёт их согласие. Пара решается без
менеджера, только если обе модели дали одно и то же решение, и только для того решения, которое на сверке
держит тот же порог доверия. Остальные пары идут менеджеру — первыми те, где обе модели за «советовать».

Выборка та же, что у сверки: 166 пар уровня классов открытого каталога (seed 11, до 30 пар на каждый вердикт
первой разметки и 30 случайных пар вне карты). Ответы моделей берутся из кеша сверки — ни одного нового вызова
API. Если кеша или ключей нет (чистая копия репозитория), берутся ответы, сохранённые в отчёте.

Сравнение с тем, как работает сейчас. Вердикт модели без доверия «советовать» ждёт менеджера, а «не советовать»
применяется сразу (recsys.llm.pair_verdicts.Verdicts.load): пара молча выпадает из советов. Поэтому отчёт считает,
сколько пар, которые первая разметка советует, так теряется без человека — у одной модели и при согласии двух.

Запуск:  python -m recsys.eval.consensus
Отчёт:   reports/pair_consensus.json — правило, числа и ответы моделей по каждой паре (пары классов открытого
         каталога, данных клиентов там нет).
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import defaultdict
from itertools import combinations

from recsys.config import ROOT

REPORT = ROOT / "reports" / "pair_consensus.json"
LEVEL, N, SEED = "class", 30, 11                  # как у python -m recsys.llm.pair_verdicts check -n 30
# модели — под теми же именами, что в reports/pair_verdicts_check.json
MODELS = {
    "gemini-3.6-flash": {"LLM_PROVIDER": "google", "LLM_MODEL": "gemini-3.6-flash", "LLM_THINKING": "off"},
    "deepseek-v4-pro, без рассуждения": {"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "deepseek-v4-pro",
                                         "LLM_THINKING": "off"},
    "deepseek-v4-pro, с рассуждением": {"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "deepseek-v4-pro",
                                        "LLM_THINKING": "on"},
}
RULE_PAIR = ("gemini-3.6-flash", "deepseek-v4-pro, с рассуждением")   # пара, которую предлагаем для работы
SHOW, SKIP = "советовать", "не советовать"


def check_sample() -> tuple[list[tuple[str, str]], dict]:
    """Выборка сверки — повтор кода recsys.llm.pair_verdicts.check, чтобы ответы совпали с её кешем."""
    from recsys.llm import pair_verdicts as pv

    table = pv.read(pv.CLASS_FILE)
    mine = {p: v for p, v in table.items() if v.source == pv.FIRST}
    by: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p, v in sorted(mine.items()):
        by[v.verdict].append(p)
    rnd = random.Random(SEED)
    sample: list[tuple[str, str]] = []
    for kind in pv.CODES.values():
        rnd.shuffle(by[kind])
        sample += by[kind][:N]
    keys = sorted({a for a, _ in mine} | {b for _, b in mine})
    srcs = sorted({a for a, v in ((p[0], v) for p, v in mine.items()) if v.shown})
    implicit: list[tuple[str, str]] = []
    while srcs and len(implicit) < N:
        p = (rnd.choice(srcs), rnd.choice(keys))
        if p[0] != p[1] and p not in table and p not in implicit:
            implicit.append(p)
    sample += implicit
    labelling = {p: (mine[p].verdict if p in mine else "unrelated") for p in sample}
    return sample, labelling


def from_cache(sample: list[tuple[str, str]]) -> dict[str, dict]:
    """Ответы каждой модели по выборке — только из кеша: при промахе сеть не трогается, модель пропускается."""
    from recsys.llm import client as cl
    from recsys.llm import pair_verdicts as pv

    class CacheOnly(cl.LLM):
        def _call(self, *a, **k):
            raise cl.LLMFatal("ответа нет в кеше")

    names = list(os.environ.get(k) for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_MODELS", "LLM_THINKING"))
    describe = pv.describer(LEVEL)
    out: dict[str, dict] = {}
    try:
        for name, env in MODELS.items():
            os.environ.update(env)
            os.environ["LLM_MODELS"] = env["LLM_MODEL"]
            try:
                got = pv.ask(CacheOnly(), LEVEL, sample, describe)
            except Exception as e:  # noqa: BLE001 — нет ключа или кеша: эту модель берём из отчёта
                print(f"  {name}: из кеша не взять ({type(e).__name__}: {str(e)[:80]})")
                continue
            out[name] = {p: got[p].verdict for p in sample if p in got}
    finally:
        for k, v in zip(("LLM_PROVIDER", "LLM_MODEL", "LLM_MODELS", "LLM_THINKING"), names):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if not n:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - r) / d, 3), round((c + r) / d, 3)]


def score(sample, truth: dict, decide) -> dict:
    """decide(пара) -> True («советовать») / False / None (к менеджеру)."""
    auto = [p for p in sample if decide(p) is not None]
    out = {"pairs": len(sample), "decided": len(auto), "to_manager": len(sample) - len(auto)}
    for title, want in ((SHOW, True), (SKIP, False)):
        rows = [p for p in auto if decide(p) is want]
        ok = sum(truth[p] is want for p in rows)
        out[title] = {"pairs": len(rows), "agrees_with_labelling": ok,
                      "accuracy": round(ok / len(rows), 3) if rows else None, "ci95": wilson(ok, len(rows))}
    ok = sum(decide(p) is truth[p] for p in auto)
    out["accuracy"] = round(ok / len(auto), 3) if auto else None
    out["ci95"] = wilson(ok, len(auto))
    return out


def main() -> None:
    from recsys.llm import pair_verdicts as pv

    sys.stdout.reconfigure(encoding="utf-8")
    sample, labelling = check_sample()
    truth = {p: labelling[p] in pv.SHOWN for p in sample}
    answers = from_cache(sample)
    saved = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    if len(answers) < len(MODELS) and saved.get("answers"):
        rows = {(r["a"], r["b"]): r for r in saved["answers"]}
        for name in MODELS:
            if name not in answers and all(name in rows.get(p, {}) for p in sample):
                answers[name] = {p: rows[p][name] for p in sample}
                print(f"  {name}: ответы взяты из сохранённого отчёта")
    missing = [m for m in RULE_PAIR if m not in answers]
    if missing:
        sys.exit(f"нет ответов моделей {missing}: сначала python -m recsys.llm.pair_verdicts check с этой моделью")

    shows = {m: {p: v in pv.SHOWN for p, v in a.items()} for m, a in answers.items()}
    check = json.loads(pv.REPORT.read_text(encoding="utf-8")).get("runs", {}) if pv.REPORT.exists() else {}
    report: dict = {
        "how": "выборка сверки recsys.llm.pair_verdicts check (уровень классов, seed 11, -n 30); ответы из кеша "
               "сверки, без новых вызовов API; эталон — первая разметка",
        "trust_threshold": pv.TRUST, "pairs": len(sample),
        "labelling_shows": sum(truth.values()), "models": {}, "pairs_of_models": {},
    }
    print(f"пар в выборке {len(sample)}, из них первая разметка советует {sum(truth.values())}")
    for m, s in shows.items():
        r = score(sample, truth, lambda p, s=s: s.get(p))
        same = check.get(m, {}).get("same_decision")
        r["same_decision_in_check_report"] = same
        report["models"][m] = r
        mark = "" if same is None else ("; совпадает со сверкой" if abs(same - r["accuracy"]) < 1e-3
                                         else f"; В СВЕРКЕ {same} — выборки разошлись")
        print(f"  одна модель {m}: то же решение {r['accuracy']:.1%}{mark}")

    def agree(a: str, b: str):
        return lambda p: shows[a][p] if p in shows[a] and p in shows[b] and shows[a][p] == shows[b][p] else None

    for a, b in combinations(shows, 2):
        report["pairs_of_models"][f"{a} + {b}"] = score(sample, truth, agree(a, b))

    # правило: решение, на котором две модели сходятся, принимается без менеджера, только если его доля
    # совпадений с первой разметкой держит порог доверия; иначе пара идёт менеджеру
    both = score(sample, truth, agree(*RULE_PAIR))
    auto = [t for t in (SHOW, SKIP) if (both[t]["accuracy"] or 0) >= pv.TRUST]
    rule = agree(*RULE_PAIR)
    decided = [p for p in sample if rule(p) is not None and (SHOW if rule(p) else SKIP) in auto]
    errors = [p for p in decided if rule(p) is not truth[p]]
    describe = pv.describer(LEVEL)
    report["rule"] = {
        "models": list(RULE_PAIR), "automated_decisions": auto,
        "decided_without_manager": len(decided), "share_without_manager": round(len(decided) / len(sample), 3),
        "to_manager": len(sample) - len(decided),
        "to_manager_first": sum(1 for p in sample if rule(p) is True and SHOW not in auto),
        "errors_without_manager": len(errors),
        "errors": [{"a": describe(a), "b": describe(b), "labelling": pv.RU[labelling[(a, b)]],
                    **{m: pv.RU[answers[m][(a, b)]] for m in RULE_PAIR}} for a, b in errors],
        "agreement": both,
        "note": "доли — на выборке сверки: она нарочно трудная (половина отвергнутых пар когда-то предложена "
                "облегчённой моделью); первая разметка — не истина: два независимых разметчика сходятся в 86 %",
    }
    # сколько хороших пар (первая разметка советует) отсекается без человека: одна модель против согласия двух
    def lost(decide) -> dict:
        rows = [p for p in sample if decide(p) is False and truth[p]]
        return {"pairs": len(rows), "need": sum(labelling[p] == "need" for p in rows),
                "kit": sum(labelling[p] == "kit" for p in rows)}

    report["silently_rejected_good_pairs"] = {
        **{f"одна модель: {m}": lost(lambda p, s=s: s.get(p)) for m, s in shows.items()},
        "согласие двух: " + " + ".join(RULE_PAIR): lost(lambda p: rule(p) if rule(p) is False else None),
    }
    report["answers"] = [{"a": a, "b": b, "labelling": labelling[(a, b)],
                          **{m: answers[m][(a, b)] for m in answers if (a, b) in answers[m]}} for a, b in sample]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nсогласие {RULE_PAIR[0]} и {RULE_PAIR[1]}: решено {both['decided']} пар из {both['pairs']}, "
          f"совпало с разметкой {both['accuracy']:.1%} {both['ci95']}")
    for t in (SHOW, SKIP):
        x = both[t]
        print(f"  обе за «{t}»: {x['pairs']} пар, верно {x['agrees_with_labelling']} ({(x['accuracy'] or 0):.0%}, "
              f"95 % интервал {x['ci95'][0]:.0%}–{x['ci95'][1]:.0%})"
              + ("  -> без менеджера" if t in auto else "  -> менеджеру, первыми в очереди"))
    r = report["rule"]
    print(f"без менеджера решается {r['decided_without_manager']} пар из {len(sample)} ({r['share_without_manager']:.0%}), "
          f"ошибок среди них {r['errors_without_manager']}; менеджеру {r['to_manager']} пар, "
          f"из них первыми — {r['to_manager_first']}")
    for name, x in report["silently_rejected_good_pairs"].items():
        print(f"  {name}: без человека отсекает хороших пар {x['pairs']} из {sum(truth.values())} "
              f"(«нужно» {x['need']}, «комплект» {x['kit']})")
    for name, x in report["pairs_of_models"].items():
        print(f"  согласие {name}: решено {x['decided']}, верно {(x['accuracy'] or 0):.1%}; "
              f"«не советовать» верно {(x[SKIP]['accuracy'] or 0):.0%} из {x[SKIP]['pairs']}")
    print(f"отчёт: {REPORT}")


if __name__ == "__main__":
    main()
