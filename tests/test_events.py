"""Журнал кабинета: какие события принимаются и как считаются показы, переходы и запросы счёта."""
from recsys import events


def test_clean_rejects_foreign_events():
    assert events.clean({"kind": "show", "slot": "missing", "pos": 1, "item": "КриптоПро CSP", "view": "ab12"})["slot"] == "missing"
    assert events.clean({"kind": "buy", "slot": "missing"}) is None          # чужой вид события
    assert events.clean({"kind": "click", "slot": "ads"}) is None            # чужой вид совета
    assert events.clean(["show"]) is None
    ev = events.clean({"kind": "click", "slot": "renew", "pos": "999", "item": "x" * 500, "view": "AB-12<script>"})
    assert ev["pos"] == 20 and len(ev["item"]) == 200 and ev["view"] == "ab12script"


def test_summarize_counts_each_card_once_per_visit():
    log = [
        {"kind": "show", "slot": "next", "item": "A", "view": "v1"},
        {"kind": "show", "slot": "next", "item": "A", "view": "v1"},       # та же карточка в тот же заход
        {"kind": "show", "slot": "next", "item": "A", "view": "v2"},       # новый заход — новый показ
        {"kind": "show", "slot": "popular", "item": "B", "view": "v2"},
        {"kind": "click", "slot": "next", "item": "A", "view": "v2"},
        {"kind": "invoice", "slot": "next", "item": "A", "view": "v2"},
    ]
    s = events.summarize(log)
    assert s["by_slot"]["next"] == {"shows": 2, "clicks": 1, "invoices": 1}
    assert s["total"] == {"shows": 3, "clicks": 1, "invoices": 1}
    assert s["views"] == 2 and "renew" not in s["by_slot"]


def test_append_and_read(tmp_path):
    path = tmp_path / "events.jsonl"
    events.append(events.clean({"kind": "show", "slot": "kit", "item": "Т", "view": "v"}), path)
    events.append(events.clean({"kind": "invoice", "slot": "kit", "item": "Т", "view": "v"}), path)
    assert events.summarize(events.read(path))["total"] == {"shows": 1, "clicks": 0, "invoices": 1}
