"""Запрет по платформе в блоке кабинета: платформу читаем только у товаров, сделанных «для неё»."""
from recsys.rank.cabinet import target


def test_product_made_for_a_platform_names_it():
    assert target("Кибер Бэкап Расширенная редакция для рабочей станции Windows") == {"Windows"}
    assert target("Kaspersky Endpoint Security for Linux, 1 год") == {"Linux"}
    assert target("Dr.Web Desktop Security Suite для macOS") == {"macOS"}


def test_operating_system_itself_is_not_bound_to_a_platform():
    # саму систему и советуют тому, у кого сейчас другая: под запрет она не попадает
    assert target("Astra Linux Special Edition") == set()
    assert target("Microsoft Windows 11 Pro") == set()
    assert target("Офисный пакет, лицензия на 1 год") == set()
