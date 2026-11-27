"""Агент «закрыть дыру» без модели: правила в коде и проверка цитаты критика."""
import json

import pytest

from recsys.agent.holes import HoleTools, close_hole, edges_of, norm

FAM = "nanosoft/platforma-nanocad"


class FakeLLM:
    """Отвечает заранее заданными JSON по очереди; ведёт счёт как настоящий клиент."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.spent = [0, 0, 0]
        self.model = "fake"

    def json(self, system, user, purpose, **kw):
        return self.answers.pop(0) if self.answers else {}


@pytest.fixture(scope="module")
def cat():
    return HoleTools()


def candidate(cat):
    """Товар из спутников класса, по которому у FAM нет вердикта и который не из его линейки."""
    vetoed = {b for b, _ in cat.by_a.get(FAM, [])}
    for c in cat.call("class_companions", {"class_id": cat.nodes[FAM]["class"]}):
        for t in c.get("top", []):
            if t["id"] not in vetoed and t["id"] not in cat.line.get(FAM, ()) and t["id"] != FAM:
                return t["id"]
    pytest.skip("у класса нет спутников без вердикта")


def vetoed_one(cat):
    return next(b for b, v in cat.by_a.get(FAM, []) if v == "unrelated")


def run(cat, critic):
    ok, bad = candidate(cat), vetoed_one(cat)
    final = {"final": {"links": [{"dst": ok, "kind": "kit", "reason": "Если ещё нет: проверка"},
                                 {"dst": bad, "kind": "need", "reason": "лишнее"},
                                 {"dst": "nobody/nothing", "kind": "need", "reason": "выдумано"}]}}
    return ok, close_hole(FakeLLM([final, {"verdicts": [critic(ok)]}]), cat, FAM, persist=False)


def test_quote_found_in_text_goes_to_graph(cat):
    ok, res = run(cat, lambda d: {"i": 0, "keep": True, "quote": " ".join(cat.text(d).split()[:4]), "why": "ок"})
    assert res["outcome"] == "closed"
    assert [(l["dst"], l["status"]) for l in res["links"]] == [(ok, "graph")]
    whys = " ".join(d["why"] for d in res["dropped"])
    assert "уже рассужено" in whys and "нет в каталоге" in whys
    row = edges_of(res)[0]
    assert row["source"] == "агент каталога: комплект" and row["status"] == "graph" and row["quote"]


def test_invented_quote_goes_to_manager_queue(cat):
    _, res = run(cat, lambda d: {"i": 0, "keep": True, "quote": "этих слов нет ни в одном описании", "why": "ок"})
    assert res["outcome"] == "queue"
    assert res["links"][0]["status"] == "queue" and res["links"][0]["quote"] == ""


def test_critic_rejects(cat):
    _, res = run(cat, lambda d: {"i": 0, "keep": False, "quote": "", "why": "заменяет друг друга"})
    assert res["outcome"] == "no_grounded_link" and not res["links"]
    assert any(d["why"].startswith("критик") for d in res["dropped"])


def test_honest_no_link(cat):
    res = close_hole(FakeLLM([{"final": {"links": [], "none_reason": "совместимость зависит от модели"}}]), cat, FAM, persist=False)
    assert res["outcome"] == "no_grounded_link" and res["explanation"] == "совместимость зависит от модели"
    assert json.dumps(res, ensure_ascii=False)                  # результат пишется в отчёт как есть


def test_norm_ignores_quotes_case_and_spaces():
    assert norm("  «Позволяет  выполнять» Перевод. ") == "позволяет выполнять перевод"
