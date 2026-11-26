"""Замена снятого с продажи зарубежного продукта: прямой аналог, замена по классу, срочность."""
from datetime import date

from recsys.recommend.engine import Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 26)


def graph() -> Graph:
    def node(fam, name, cls="c/editors", on_sale="1", ru="0", on_request="0"):
        return {"family": fam, "name": name, "vendor": fam.split("/")[0], "class": cls, "on_sale": on_sale,
                "ru": ru, "on_request": on_request, "kind": "software"}

    nodes = {n["family"]: n for n in [
        node("acme/suite", "Acme Suite", on_sale="0", on_request="1"),       # зарубежный пакет, снят с продажи
        node("acme/photo", "Acme Photo", on_sale="0", on_request="1"),       # его сосед, у которого есть прямой аналог
        node("rus/kraski", "Краски", ru="1"),                                # аналог Acme Photo по таблице замещения
        node("rus/plakat", "Плакат", ru="1"),                                # просто российский продукт того же класса
        node("rus/zakaz", "Заказной", ru="1", on_sale="0", on_request="1"),  # российский, цена по запросу
        node("other/tool", "Other Tool", cls="c/other", on_sale="0"),        # снят, российских в его классе нет
    ]}
    edges = [{"src": "acme/photo", "dst": "rus/kraski", "type": "analog_ru", "source": "таблица замещения АРПП",
              "confidence": "0.8", "evidence": "аналог по таблице (реестр № 123)"}]
    return Graph(nodes, edges, class_names={"c/editors": "Графические редакторы"})


def advise(family, bought, term=12, client_type="b2b", k=3, **opts):
    client = Client(client_type, [Purchase(family, bought, term)], today=TODAY)
    return Recommender(graph(), **opts).recommend(client, k)


def test_expired_licence_of_withdrawn_product_gets_replacement_first():
    recs = advise("acme/suite", date(2025, 11, 23))                 # истекла три дня назад, продлить нельзя
    first = recs[0]
    assert (first.family, first.slot, first.score) == ("rus/kraski", "replace", 0.75)
    assert "истекла 23.11.2026" in first.why and "Продлить её в магазине нельзя" in first.why
    assert "«Графические редакторы»" in first.why and "назван аналогом «Acme Photo»" in first.why
    assert [r.slot for r in recs].count("replace") == 1            # одна замена на один продукт


def test_withdrawn_product_without_due_date_gets_calm_replacement():
    recs = advise("acme/suite", date(2024, 1, 10), term=None)
    first = recs[0]
    assert first.slot == "replace" and first.score < 0.5
    assert first.why.startswith("«Acme Suite» — зарубежный продукт, который теперь продаётся только по запросу.")


def test_direct_analogue_wins_over_class_replacement():
    recs = advise("acme/photo", date(2025, 11, 23), k=5)
    assert [r.family for r in recs if r.slot == "replace"] == ["rus/kraski"]
    assert "Российский аналог: «Краски» (реестр российского ПО, запись № 123)" in recs[0].why
    assert "Продлить её в магазине нельзя" in recs[0].why and recs[0].score >= 0.72


def test_on_request_russian_product_is_only_for_organisations():
    b2b = {r.family for r in advise("other/tool", date(2025, 11, 23), k=5)}
    assert not b2b                                                   # в классе нет российских продуктов — совета нет
    fams = lambda t: [r.family for r in Recommender(graph()).recommend(        # noqa: E731
        Client(t, [Purchase("acme/suite", date(2025, 11, 23), 12), Purchase("rus/kraski", date(2026, 1, 1), 0),
                   Purchase("rus/plakat", date(2026, 1, 1), 0)], today=TODAY), 5)]
    assert fams("b2b") == ["rus/zakaz"] and fams("b2c") == []


def test_strict_mode_hides_class_replacement():
    assert advise("acme/suite", date(2025, 11, 23), min_confidence=0.6) == []
