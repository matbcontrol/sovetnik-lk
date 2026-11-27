"""Разметка новых пар двумя моделями: что действует сразу, а что ждёт менеджера. Модели поддельные."""
import json
import os

import recsys.llm.pair_verdicts as pv
from recsys.llm.pair_verdicts import BOTH_FOR, CONSENSUS, FIRST, SPLIT, Verdict, applied, client_for, combine

P1, P2, P3, P4 = ("a1", "b1"), ("a1", "b2"), ("a2", "b3"), ("a2", "b4")


def test_combine_and_applied():
    gemini = {P1: Verdict("unrelated", "", "api:g"), P2: Verdict("need", "нужен", "api:g"),
              P3: Verdict("has", "", "api:g"), P4: Verdict("kit", "если нет", "api:g")}
    deepseek = {P1: Verdict("has", "уже есть", "api:d"), P2: Verdict("kit", "если ещё нет", "api:d"),
                P3: Verdict("need", "нужен", "api:d")}                        # на P4 вторая модель не ответила
    got = combine(gemini, deepseek)
    assert got[P1].source == CONSENSUS and got[P1].verdict == "has"          # обе против: вердикт второй модели
    assert got[P2].source == BOTH_FOR and got[P2].verdict == "kit"           # обе за: менеджеру первыми
    assert got[P3].source == SPLIT and got[P3].verdict == "need"             # расходятся: предложенный совет
    assert P4 not in got                                                     # одна модель — пара остаётся в очереди
    first = {("x", "y"): Verdict("need", "нужен", FIRST)}
    single_no = {("x", "z"): Verdict("unrelated", "", "api:gemini-3.6-flash")}
    live = applied({**got, **first, **single_no})
    assert set(live) == {P1, ("x", "y")}       # клиенту — первая разметка и согласие «не советовать»; одиночное «нет» ждёт


def test_client_for_restores_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "было")
    seen = {}

    class Fake:
        def __init__(self):
            seen["model"] = os.environ["LLM_MODEL"]

    import recsys.llm.client as client
    monkeypatch.setattr(client, "LLM", Fake)
    client_for({"LLM_MODEL": "deepseek-v4-pro", "LLM_THINKING": "on"})
    assert seen["model"] == "deepseek-v4-pro" and os.environ["LLM_MODEL"] == "было"


def test_label_asks_both_models(tmp_path, monkeypatch):
    table, todo = tmp_path / "class.csv", tmp_path / "todo.json"
    pv.write(table, {("c0", "c9"): Verdict("need", "нужен", FIRST)})
    todo.write_text(json.dumps([list(P1), list(P2)]), encoding="utf-8")
    monkeypatch.setattr(pv, "CLASS_FILE", table)
    monkeypatch.setitem(pv.TODO, "class", todo)
    monkeypatch.setattr(pv, "describer", lambda level: (lambda k: k))

    class Fake:
        def __init__(self, cfg):
            self.model = cfg["LLM_MODEL"]

    monkeypatch.setattr(pv, "client_for", Fake)
    answers = {"gemini-3.6-flash": {P1: Verdict("unrelated"), P2: Verdict("need", "нужен")},
               "deepseek-v4-pro": {P1: Verdict("unrelated"), P2: Verdict("same")}}
    monkeypatch.setattr(pv, "ask", lambda llm, level, pairs, describe, progress=False: answers[llm.model])
    pv.label("class", 0, False)
    got = pv.read(table)
    assert got[P1].source == CONSENSUS and got[P2].source == SPLIT and got[("c0", "c9")].source == FIRST
