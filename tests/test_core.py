"""Проверки ядра: разбор названий, запреты, продление, счёт мерок. Запуск: python -m pytest -q"""
from datetime import date

from recsys.eval.backtest import Advice, Context, Event, metrics, run
from recsys.graph.textnorm import newer, split_edition, tokens, version_of
from recsys.normalize.config import features
from recsys.recommend.engine import Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 26)


def node(key, name, **kw):
    base = {"family": key, "name": name, "vendor": "В", "on_sale": "1", "b2b_only": "0", "client_types": "",
            "platforms": "", "license_types": "", "typical_term": "", "edition_rank": "", "class": "к/а", "url": ""}
    base.update({k: str(v) for k, v in kw.items()})
    return base


def edge(src, dst, type_, conf=0.9, source="таблица сравнения магазина"):
    return {"src": src, "dst": dst, "type": type_, "source": source, "confidence": conf, "evidence": ""}


def small_graph():
    nodes = {n["family"]: n for n in [
        node("v/std", "Продукт Standard", edition_rank=2),
        node("v/pro", "Продукт Professional", edition_rank=4),
        node("v/ent", "Продукт Enterprise", edition_rank=6, b2b_only=1),
        node("x/old", "Зарубежный продукт", on_sale=0),
        node("r/analog", "Российский аналог"),
        node("r/gone", "Снятый аналог", on_sale=0),
    ]}
    edges = [
        edge("v/std", "v/pro", "upgrade"), edge("v/std", "v/ent", "upgrade"), edge("v/pro", "v/ent", "upgrade"),
        edge("v/std", "v/pro", "same_line"), edge("v/std", "v/ent", "same_line"), edge("v/pro", "v/ent", "same_line"),
        edge("x/old", "r/analog", "analog_ru", source="таблица замещения АРПП"),
        edge("x/old", "r/gone", "analog_ru", source="таблица замещения АРПП"),
    ]
    return Graph(nodes, edges)


def test_names():
    assert tokens("Kaspersky Endpoint Security для бизнеса Расширенный") == ["kaspersky", "endpoint", "security", "расширенный"]
    assert split_edition("Microsoft 365 Business Standard")[1:] == (2.0, "standard")      # «Business» — линейка
    assert split_edition("Microsoft Office Professional Plus 2021")[1] == 4.5             # «плюс» поднимает редакцию
    assert version_of("ContentReader PDF 15 Standard") == "15"
    assert version_of("3-Heights PDF Security") == ""                                     # число в начале — часть марки
    assert newer("15", "16") and newer("2021", "2024")
    assert not newer("15", "2024") and not newer("23", "365")                             # год с версией не сравниваем


def test_config():
    f = features("x", "Срок действия: 12 мес. • Платформа: Windows • Тип лицензии: продление • Тип клиента: юрлицо")
    assert (f["license_type"], f["client_type"], f["term_months"], f["platform"]) == ("renewal", "b2b", 12, "Windows")
    # «домашняя» в названии лицензии — редакция, а не тип покупателя
    assert features("Обновление с Домашней до Профессиональной версии")["client_type"] == ""


def test_upgrade_and_bans():
    g = small_graph()
    rec = Recommender(g)
    got = rec.recommend(Client("b2b", [Purchase("v/std", date(2026, 1, 10))], today=TODAY))
    assert [r.family for r in got] == ["v/pro"] or got[0].slot == "next"       # одна линейка — один совет
    assert len({r.family for r in got}) == len(got) == 1
    # частному лицу не предлагаем то, что продаётся только организациям
    got, bans = rec.recommend(Client("b2c", [Purchase("v/pro", date(2026, 1, 10))], today=TODAY), explain_bans=True)
    assert not got and bans[0]["reason"] == "продаётся только юридическим лицам"
    # куплена старшая редакция — младшую не советуем
    got = rec.recommend(Client("b2b", [Purchase("v/std", date(2026, 1, 10)), Purchase("v/ent", date(2026, 2, 1))], today=TODAY))
    assert all(r.family != "v/pro" for r in got)


def test_replacement_only_what_is_on_sale():
    got, bans = Recommender(small_graph()).recommend(
        Client("b2b", [Purchase("x/old", date(2023, 5, 1))], today=TODAY), explain_bans=True)
    assert [r.family for r in got] == ["r/analog"] and got[0].slot == "replace"
    assert {b["family"]: b["reason"] for b in bans} == {"r/gone": "нет в продаже"}


def test_renewal_window():
    rec = Recommender(small_graph())
    due = rec.recommend(Client("b2b", [Purchase("v/std", date(2025, 12, 20), 12)], today=TODAY))
    assert due[0].slot == "renew" and due[0].family == "v/std"
    assert due[0].also_family == "v/pro"                                       # продление и переход — один совет
    far = rec.recommend(Client("b2b", [Purchase("v/std", date(2026, 10, 1), 12)], today=TODAY))
    assert all(r.slot != "renew" for r in far)
    forever = rec.recommend(Client("b2b", [Purchase("v/std", date(2025, 12, 20), 0)], today=TODAY))
    assert all(r.slot != "renew" for r in forever)                             # бессрочную не продлеваем


class _Fixed:
    name = "проверочный"

    def fit(self, train):
        pass

    def recommend(self, ctx: Context, k: int):
        return [Advice("a"), Advice("zzz", fallback=True)]


def test_backtest_metrics():
    ev = [Event("c1", date(2026, 1, 1), "x"), Event("c1", date(2026, 3, 1), "a"),
          Event("c2", date(2026, 1, 5), "x"), Event("c2", date(2026, 3, 9), "b")]
    rows = run(_Fixed(), ev, cutoff=date(2026, 2, 1), horizon_days=90)
    m = metrics(rows)
    assert m["clients"] == 2 and m["hit@3"] == 0.5
    assert abs(m["precision@3"] - 1 / 6) < 1e-9          # одно попадание на два клиента по три места
    assert m["fill_rate"] == 1.0 and m["precision_shown"] == 0.5
