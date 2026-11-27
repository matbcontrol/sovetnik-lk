"""Агент каталога: товар «X для Y» — дополнение к Y, даже если модель назвала связь только «требует Y»."""
from recsys.agent.agent import addon_edges, named_for


class Cat:
    nodes = {"nanosoft/platforma-nanocad": {"name": "Платформа nanoCAD 26"},
             "microsoft/windows-11": {"name": "Microsoft Windows 11 Pro"}}


def test_named_for_matches_the_product_after_dlya():
    assert named_for("СПДС GraphiCS для nanoCAD — модуль оформления чертежей", "Платформа nanoCAD 26")
    assert named_for("V-Ray for Blender", "Blender Studio")
    assert not named_for("СПДС GraphiCS для nanoCAD", "Microsoft Windows 11 Pro")
    assert not named_for("Модуль оформления чертежей", "Платформа nanoCAD 26")     # «для» в названии нет


def test_requires_link_to_the_named_product_also_gives_its_owners_an_addon():
    kept = [{"dst": "nanosoft/platforma-nanocad", "type": "requires", "reason": "Требуется Платформа nanoCAD"},
            {"dst": "microsoft/windows-11", "type": "requires", "reason": "Работает в Windows"}]
    edges = addon_edges("СПДС GraphiCS для nanoCAD — модуль оформления чертежей", Cat(), kept)
    assert [(e["src"], e["dst"], e["type"]) for e in edges] == [("nanosoft/platforma-nanocad", "new", "complements")]
    assert edges[0]["why"].startswith("Дополнение к «Платформа nanoCAD 26»")
