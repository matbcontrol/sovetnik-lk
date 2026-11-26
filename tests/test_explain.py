"""Тексты объяснений: названия без служебных слов, согласование чисел, отличие лицензий."""
from recsys.rank import explain as e


def test_clean_name_drops_article_and_legal_words():
    raw = "LHW-BK-12M-2-A3, Неисключительное право на использование ПО Dr.Web Security Space"
    assert e.clean_name(raw, 120) == "Dr.Web Security Space"
    assert e.clean_name("Право на использование программы для ЭВМ Платформа nanoCAD 25", 64) == "Платформа nanoCAD 25"
    assert e.clean_name("Программа КриптоПро CSP 5.0", 64) == "КриптоПро CSP 5.0"
    assert e.clean_name("Средство анализа защищенности «Сканер-ВС». НПЕШ.00606-01 Продление", 64) \
        == "Средство анализа защищенности «Сканер-ВС»"


def test_quotes_inside_name_become_inner_quotes():
    assert e.q("Эшелон «СКАНЕР-ВС»") == "«Эшелон „СКАНЕР-ВС“»"
    assert e.q("Dr.Web «Малый…") == "«Dr.Web „Малый“…»"


def test_verb_agrees_with_number():
    assert (e.took(1), e.took(21), e.took(11), e.took(5)) == ("взял", "взял", "взяли", "взяли")
    assert e.clients(21) == "21 клиент" and e.clients(3) == "3 клиента" and e.clients(12) == "12 клиентов"


def test_share_is_named_only_when_visible():
    assert e.after(3, 39, "линейки «X»", "«B»") == "3 из 39 клиентов с продуктами линейки «X» затем взяли «B»."
    assert e.after(4, 849, "класса «X»", "«B»") == "«B» затем взяли 4 клиента с продуктами класса «X»."


def test_difference_keeps_edition_names_and_drops_scraps():
    a = "Kaspersky Endpoint Security для бизнеса Стандартный 10-14 Node 1 year Base"
    assert e.difference(a, "Kaspersky Endpoint Security для бизнеса Расширенный 10-14 Node 1 year Renewal") == "Расширенный"
    assert e.difference(a, "Kaspersky Endpoint Security для бизнеса Стандартный 25-49 Node 2 year Renewal") == ""
    assert e.difference("Secret Net Studio 8 на рабочей станции", "Secret Net Studio 8 на сервере") == ""
    assert e.difference("Secret Net Studio 8", "Secret Net Studio 8 Модуль от НСД и контроля устройств") \
        == "Модуль от НСД и контроля устройств"


def test_ordinal_stops_after_third_place():
    assert e.ordinal(1) == "самая частая" and e.ordinal(3) == "третья по частоте" and e.ordinal(4) == ""
