"""Связи, прочитанные в названиях: серия курсов, серия расходных материалов, модель в названии принадлежности."""
from datetime import date

from recsys.graph import name_links as nl
from recsys.recommend.engine import Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 27)


def node(family: str, name: str, cls: str, kind: str = "software") -> dict:
    return {"family": family, "name": name, "vendor": family.split("/")[0], "vendor_slug": family.split("/")[0],
            "class": cls, "class_name": cls, "kind": kind, "on_sale": "1", "on_request": "0", "b2b_only": "0",
            "client_types": "", "platforms": "", "price_from": "1000", "store_rank": "1", "category": "", "subcategory": ""}


def test_courses_of_one_series_are_linked_and_topics_are_not():
    nodes = {k: node(k, name, nl.COURSES) for k, name in {
        "sike/a": "Sike. Электронный курс «Слесарь-ремонтник. Подшипники – общие сведения»",
        "sike/b": "Sike. Электронный курс «Слесарь-ремонтник: инженерная графика»",
        "sike/c": "Sike. Электронный курс «Гидравлические насосы»",
        "sike/d": "Sike. Электронный курс «Диагностика гидравлических насосов»",
        "other/a": "Слесарь-ремонтник. Редукторы"}.items()}
    pairs = {(e["src"], e["dst"]) for e in nl.course_series(nodes)}
    # серия названа в самих названиях и издатель один; общая тема без названия серии — не основание
    assert pairs == {("sike/a", "sike/b"), ("sike/b", "sike/a")}


def test_cartridges_of_one_series_differ_only_by_colour():
    nodes = {k: node(k, name, nl.SUPPLIES, "hardware") for k, name in {
        "c/y": "Картридж желтый Cactus CS-CLI471XLY", "c/m": "Картридж пурпурный Cactus CS-CLI471XLM",
        "c/k": "Картридж черный Cactus CS-CLI471XLBK", "c/cf1": "Картридж голубой Cactus CS-CF211A",
        "c/cf2": "Картридж желтый Cactus CS-CF212A", "c/tk1": "Картридж черный Cactus CS-TK1110",
        "c/tk2": "Картридж черный Cactus CS-TK1120", "c/set": "Картридж голубой, пурпурный, желтый Cactus CS-CLI471XLC/M/Y"}.items()}
    pairs = {(e["src"], e["dst"]) for e in nl.colour_series(nodes)}
    assert {("c/y", "c/m"), ("c/y", "c/k"), ("c/m", "c/k"), ("c/cf1", "c/cf2")} <= pairs
    # два чёрных картриджа с соседними номерами — разные принтеры; набор цветов владельцу одного из них не нужен
    assert not {p for p in pairs if "c/tk1" in p or "c/tk2" in p}
    assert ("c/y", "c/set") not in pairs and ("c/set", "c/m") not in pairs


def test_accessory_names_the_model_of_the_device():
    nodes = {"y/t46u": node("y/t46u", "IP-телефон Yealink SIP-T46U", "c/phones", "hardware"),
             "y/t31": node("y/t31", "IP-телефон Yealink SIP-T31", "c/phones", "hardware"),
             "y/ehs": node("y/ehs", "EHS40 адаптер для беспроводных гарнитур для SIP-T41S/T42S/T46U(S)/T48U(S), шт", "c/acc", "hardware"),
             "z/ehs": node("z/ehs", "Адаптер для гарнитур для SIP-T46U", "c/acc", "hardware")}
    edges = nl.named_models(nodes)
    # модель названа в перечне; чужой производитель и не названная модель — не основание
    assert [(e["src"], e["dst"]) for e in edges] == [("y/t46u", "y/ehs")]
    assert edges[0]["evidence"] == "В названии принадлежности прямо названа модель T46U."


def test_series_link_is_shown_as_a_kit_and_can_be_rejected():
    nodes = {"c/y": node("c/y", "Картридж желтый Cactus CS-CLI471XLY", nl.SUPPLIES, "hardware"),
             "c/m": node("c/m", "Картридж пурпурный Cactus CS-CLI471XLM", nl.SUPPLIES, "hardware"),
             "c/k": node("c/k", "Картридж черный Cactus CS-CLI471XLBK", nl.SUPPLIES, "hardware")}
    g = Graph(nodes, nl.colour_series(nodes), [], {})
    client = Client("b2b", [Purchase("c/y", date(2026, 6, 1), 0)], today=TODAY)
    recs = Recommender(g).recommend(client, 3)
    # серия — «комплект»: одна карточка с условием «Если…», а не три совета «нужно»
    assert [(r.family, r.slot) for r in recs] == [("c/k", "kit")] or [(r.family, r.slot) for r in recs] == [("c/m", "kit")]
    assert recs[0].why.startswith("Если заканчиваются и другие цвета")
    rejected = Recommender(g, rejected={f"pk|c/y|{recs[0].family}"}).recommend(client, 3)
    assert recs[0].family not in [r.family for r in rejected]
