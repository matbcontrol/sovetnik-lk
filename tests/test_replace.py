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
    """Совет глазами менеджера: вместе со связями по классу, которые ещё никто не проверил."""
    client = Client(client_type, [Purchase(family, bought, term)], today=TODAY)
    return Recommender(graph(), **{"unverified": True, **opts}).recommend(client, k)


def test_expired_licence_of_withdrawn_product_gets_replacement_first():
    recs = advise("acme/suite", date(2025, 11, 23))                 # истекла три дня назад, продлить нельзя
    first = recs[0]
    assert (first.family, first.slot, first.score) == ("rus/kraski", "replace", 0.75)
    assert "истекла 23.11.2026" in first.why and "только по запросу цены у менеджера" in first.why
    assert "Полной замены в таблице замещения нет" in first.why
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
    assert "только по запросу цены у менеджера" in recs[0].why and recs[0].score >= 0.72


def test_on_request_russian_product_is_only_for_organisations():
    b2b = {r.family for r in advise("other/tool", date(2025, 11, 23), k=5)}
    assert not b2b                                                   # в классе нет российских продуктов — совета нет
    fams = lambda t: [r.family for r in Recommender(graph(), unverified=True).recommend(        # noqa: E731
        Client(t, [Purchase("acme/suite", date(2025, 11, 23), 12), Purchase("rus/kraski", date(2026, 1, 1), 0),
                   Purchase("rus/plakat", date(2026, 1, 1), 0)], today=TODAY), 5)]
    assert fams("b2b") == ["rus/zakaz"] and fams("b2c") == []


def test_strict_mode_hides_class_replacement():
    assert advise("acme/suite", date(2025, 11, 23), min_confidence=0.6) == []


def test_client_does_not_see_class_replacement_until_manager_accepts():
    client = Client("b2b", [Purchase("acme/suite", date(2025, 11, 23), 12)], today=TODAY)
    recs, bans = Recommender(graph()).recommend(client, 3, explain_bans=True)
    assert recs == []                                                # клиенту ничего не показано
    assert [(b["family"], b["reason"]) for b in bans] == [("rus/kraski", "ждёт проверки менеджером")]
    key = bans[0]["key"]
    accepted = Recommender(graph(), approved={key}).recommend(client, 3)
    assert accepted[0].family == "rus/kraski" and accepted[0].label == "принято менеджером"
    assert Recommender(graph(), unverified=True, rejected={key}).recommend(client, 3)[0].family == "rus/plakat"


def test_class_link_is_shown_when_sales_confirm_it():
    nodes = {f: {"family": f, "name": f, "vendor": "v", "class": c, "on_sale": "1", "kind": "software"}
             for f, c in [("v/antivirus", "c/av"), ("v/backup", "c/backup")]}
    link = {"src_class": "c/av", "dst_class": "c/backup", "type": "complements", "audience": "any",
            "confidence": "0.8", "reason": "К антивирусу нужно резервное копирование", "source": "модель"}
    client = Client("b2b", [Purchase("v/antivirus", date(2026, 6, 1), 0)], today=TODAY)

    def advice(sales):
        g = Graph(nodes, [], [dict(link, sales=sales)], class_rank={"c/backup": ["v/backup"]})
        return Recommender(g).recommend(client, 3)

    assert advice(0) == []                                           # модель предложила, никто не подтвердил
    shown = advice(10)
    assert shown[0].family == "v/backup" and shown[0].label == "связь классов подтверждена покупками"
