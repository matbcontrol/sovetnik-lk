"""Пилот: разбиение на тест и контроль, отчёт по журналу и расчёт длительности."""
import json

from recsys import pilot
from recsys.config import ROOT
from recsys.recommend.block import advice, group


def test_group_is_stable_and_follows_share():
    assert group("C1", 0.0) == "test" and group("", 0.5) == "test"
    assert group("C1", 1.0) == "control"
    assert group("C42", 0.5) == group("C42", 0.5)                     # один клиент — всегда одна группа
    share = sum(group(f"client-{i}", 0.3) == "control" for i in range(4000)) / 4000
    assert 0.27 < share < 0.33


def test_control_keeps_current_block():
    client = json.loads((ROOT / "site" / "data" / "examples.json").read_text(encoding="utf-8"))[0]["client"]
    res = advice({**client, "client_id": "C1", "control_share": 1})
    assert res["status"] == "control" and res["group"] == "control" and res["data"] == [] and res["popular"]
    assert advice({**client, "client_id": "C1"})["group"] == "test"   # без доли контроля пилот выключен


def test_report_and_plan():
    log = [{"kind": "show", "slot": "next", "item": f"t{i}", "view": "a", "group": "test"} for i in range(100)]
    log += [{"kind": "click", "slot": "next", "item": "t0", "view": "a", "group": "test"}] * 20
    log += [{"kind": "show", "slot": "popular", "item": f"c{i}", "view": "b", "group": "control"} for i in range(100)]
    log += [{"kind": "click", "slot": "popular", "item": "c0", "view": "b", "group": "control"}] * 10
    r = pilot.report(log)
    assert r["groups"]["test"]["shows"] == 100 and r["groups"]["control"]["clicks"] == 10
    assert r["ctr"]["diff"] == 0.1 and r["ctr"]["ci95"][0] < 0.1 < r["ctr"]["ci95"][1] and r["ctr"]["p"] < 0.06
    assert pilot.sample_size(0.05, 0.075) == 1471 and pilot.days(1471, 300, 0.5) == 10
    assert pilot.compare(1, 0, 1, 10) is None and pilot.sample_size(0.05, 0.05) is None
