"""Агент «закрыть дыру» без модели: правила в коде и проверка цитаты критика."""
import json

import pytest

from recsys.agent.holes import HoleTools, close_hole, edges_of, norm

FAM = "nanosoft/platforma-nanocad"
SEARCH = {"thought": "ищу", "calls": [{"tool": "search_catalog", "args": {"query": "nanoCAD"}}]}


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
    vetoed = {b for b, _ in cat.by_a.get(FAM, [])} | {a["id"] for a in cat.advised_now(FAM)}
    for c in cat.call("class_companions", {"class_id": cat.nodes[FAM]["class"]}):
        for t in c.get("top", []):
            if t["id"] not in vetoed and t["id"] not in cat.line.get(FAM, ()) and t["id"] != FAM:
                return t["id"]
    pytest.skip("у класса нет спутников без вердикта")


def vetoed_one(cat):
    return next(b for b, v in cat.by_a.get(FAM, []) if v == "unrelated")


def run(cat, critic):
    ok, bad = candidate(cat), vetoed_one(cat)
    final = {"final": {"links": [{"dst": ok, "kind": "need", "reason": "Нужен для проверки"},
                                 {"dst": bad, "kind": "need", "reason": "лишнее"},
                                 {"dst": "nobody/nothing", "kind": "need", "reason": "выдумано"}]}}
    return ok, close_hole(FakeLLM([SEARCH, final, {"verdicts": [critic(ok)]}]), cat, FAM, persist=False)


def test_quote_found_in_text_goes_to_graph(cat):
    ok, res = run(cat, lambda d: {"i": 0, "keep": True, "quote": " ".join(cat.text(d).split()[:4]), "why": "ок"})
    assert res["outcome"] == "closed"
    assert [(l["dst"], l["status"]) for l in res["links"]] == [(ok, "graph")]
    whys = " ".join(d["why"] for d in res["dropped"])
    assert "уже рассужено" in whys and "нет в каталоге" in whys
    row = edges_of(res)[0]
    assert row["source"] == "агент каталога" and row["status"] == "graph" and row["quote"]


def test_invented_quote_goes_to_manager_queue(cat):
    _, res = run(cat, lambda d: {"i": 0, "keep": True, "quote": "этих слов нет ни в одном описании", "why": "ок"})
    assert res["outcome"] == "queue"
    assert res["links"][0]["status"] == "queue" and res["links"][0]["quote"] == ""


def test_critic_rejects(cat):
    _, res = run(cat, lambda d: {"i": 0, "keep": False, "quote": "", "why": "заменяет друг друга"})
    assert res["outcome"] == "no_grounded_link" and not res["links"]
    assert any(d["why"].startswith("критик") for d in res["dropped"])


def test_honest_no_link(cat):
    res = close_hole(FakeLLM([SEARCH, {"final": {"links": [], "none_reason": "совместимость зависит от модели"}}]), cat, FAM, persist=False)
    assert res["outcome"] == "no_grounded_link" and res["explanation"] == "совместимость зависит от модели"
    assert json.dumps(res, ensure_ascii=False)                  # результат пишется в отчёт как есть


def test_final_without_search_is_not_accepted(cat):
    none = {"final": {"links": [], "none_reason": "связи нет"}}
    res = close_hole(FakeLLM([none, SEARCH, none]), cat, FAM, persist=False)
    assert any(s["tool"] == "требование кода" for s in res["steps"])
    assert any(s["tool"] == "search_catalog" and s["ask"] for s in res["steps"])


def test_as_new_hides_labelling(cat):
    """Товар «как заведённый сегодня»: агент не видит его разметки, размеченная пара не отсекается как «уже рассужено»."""
    cat.as_new = {FAM}
    try:
        assert "note" in cat.judged(FAM)
        advised = {a["id"] for a in cat.advised_now(FAM)}
        bad = next((b for b, v in cat.by_a.get(FAM, []) if v == "unrelated" and b not in advised
                    and cat.nodes.get(b, {}).get("on_sale") == "1" and b not in cat.line.get(FAM, ())), None)
        if not bad:
            pytest.skip("нет размеченной пары «не связано» с товаром в продаже вне линейки")
        final = {"final": {"links": [{"dst": bad, "kind": "need", "reason": "проверка"}]}}
        critic = {"verdicts": [{"i": 0, "keep": True, "quote": " ".join(cat.text(bad).split()[:4]), "why": "ок"}]}
        res = close_hole(FakeLLM([SEARCH, final, critic]), cat, FAM, persist=False)
    finally:
        cat.as_new = set()
    assert res["as_new"] and [l["dst"] for l in res["links"]] == [bad]
    assert not any("уже рассужено" in d["why"] for d in res["dropped"])


def test_norm_ignores_quotes_case_and_spaces():
    assert norm("  «Позволяет  выполнять» Перевод. ") == "позволяет выполнять перевод"
