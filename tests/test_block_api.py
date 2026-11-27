"""Встраивание: запрос кабинета -> блок советника в формате нынешнего блока (POST /api/advice)."""
import json

import pytest

from recsys.config import ROOT
from recsys.recommend.block import advice, link, parse


def test_parse_rejects_bad_requests():
    with pytest.raises(ValueError):
        parse({"type": "vip"})
    with pytest.raises(ValueError):
        parse({"purchases": [{"date": "2026-01-01"}]})               # нет семейства
    with pytest.raises(ValueError):
        parse({"purchases": [{"family": "a/b", "date": "вчера"}]})   # дата не в формате ГГГГ-ММ-ДД
    c = parse({"type": "B2C", "today": "2026-11-26", "purchases": [{"family": "a/b", "date": "2026-05-11", "term": "12"}]})
    assert c.client_type == "b2c" and c.purchases[0].term_months == 12


def test_link_keeps_shop_tags():
    assert link("https://store.softline.ru/x/", "next", 1).endswith("utm_content=sovetnik_next&utm_term=1")
    assert "?a=1&utm_source=" in link("https://store.softline.ru/x/?a=1", "kit", 3)
    assert link("", "kit", 1) == ""


def test_test_client_gets_the_expected_block():
    ex = json.loads((ROOT / "site" / "data" / "examples.json").read_text(encoding="utf-8"))
    res = advice(ex[0]["client"])
    assert res["model_calls"] == 0 and res["status"] == "shown"
    assert [c["slot"] for c in res["data"]] == ["next", "missing", "kit"]
    assert set(res["data"][0]) == {"link", "name", "vendorName", "price", "slot", "reason"}
    assert all("utm_content=sovetnik_" in c["link"] for c in res["data"] + res["popular"])
    empty = advice({"type": "b2b", "today": "2026-11-26", "purchases": []})
    assert empty["status"] == "hidden" and len(empty["popular"]) == 3
