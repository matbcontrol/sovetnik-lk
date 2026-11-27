"""Совместимость по характеристикам каталога: сокет и чипсет, тип памяти, отвод тепла, форм-фактор."""
from datetime import date

from recsys.graph import spec_links as sl
from recsys.recommend.engine import Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 27)


def node(family: str, name: str, cls: str, price: str = "10000", rank: str = "1") -> dict:
    return {"family": family, "name": name, "vendor": family.split("/")[0], "vendor_slug": family.split("/")[0],
            "class": cls, "class_name": cls, "kind": "hardware", "on_sale": "1", "on_request": "0", "b2b_only": "0",
            "client_types": "", "platforms": "", "price_from": price, "store_rank": rank, "category": "", "subcategory": ""}


NODES = {
    "a/b550": node("a/b550", "Материнская плата ASUS AMD B550 PRIME B550M-A", "parts/motherboard"),
    "a/h610": node("a/h610", "Материнская плата ASUS Intel H610 PRIME H610M-K", "parts/motherboard"),
    "amd/3200g": node("amd/3200g", "Процессор AMD Ryzen 3 3200G OEM", "parts/cpu", rank="1"),
    "amd/5600": node("amd/5600", "Процессор AMD Ryzen 5 5600 OEM", "parts/cpu", rank="2"),
    "i/12400": node("i/12400", "Процессор Intel Core i5-12400F OEM", "parts/cpu"),
    "i/14600": node("i/14600", "Процессор Intel Core i5-14600K OEM", "parts/cpu"),
    "k/ddr4": node("k/ddr4", "Оперативная память Kingston DDR4 8GB", "parts/ram"),
    "k/sodimm": node("k/sodimm", "Оперативная память Kingston Laptop DDR4 8GB", "parts/ram"),
    "k/ecc": node("k/ecc", "Оперативная память Kingston Server DDR4 16GB", "parts/ram"),
    "c/small": node("c/small", "Кулер Процессорный Intel E97379", "parts/coolers", price="500"),
    "c/tower": node("c/tower", "Кулер Процессорный Deepcool AK400", "parts/coolers", price="2500"),
    "z/case": node("z/case", "Корпус Zalman S3", "parts/chassis"),
    "f/case": node("f/case", "Корпус Foxline FL-816", "parts/chassis"),
    "p/atx": node("p/atx", "Блок питания Chieftec APB 500B8", "parts/psu"),
    "p/sfx": node("p/sfx", "Блок питания Thermalright TR-TPFX", "parts/psu"),
    "s/nas": node("s/nas", "Сетевое хранилище QNAP TS-464", "san-nas/nas", price="180000"),
    "w/red": node("w/red", "Жесткий диск Western Digital Red Plus 3.5 4TB SATA3", "parts/hdd"),
    "w/sas": node("w/sas", "Жесткий диск Western Digital Ultrastar 3.5 4TB SAS3", "parts/hdd"),
    "b/cable": node("b/cable", "Кабель Buro SATA SATA 0.5м (BU-SATA-SATA0.5M) красный", "parts/cables", price="115"),
}
SPECS = {
    "a/b550": "mATX, 1×Socket AM4, AMD B550, DDR4, GbE, USB 3.0, SATA3, HDMI",
    "a/h610": "mATX, 1×LGA 1700, Intel H610, DDR4, GbE, USB 3.0, SATA3, HDMI",
    "amd/3200g": "Частота 3,6–4,0 ГГц • Socket AM4 • техпроцесс 12 нм • TDP 65 Вт • OEM",
    "amd/5600": "Частота 3,5–4,4 ГГц • Socket AM4 • техпроцесс 7 нм • TDP 65 Вт • OEM",
    "i/12400": "Частота 2,5–4,4 ГГц • LGA 1700 • техпроцесс 10 нм • TDP 65 Вт • OEM",
    "i/14600": "Частота 3,5–5,3 ГГц • LGA 1700 • техпроцесс 10 нм • TDP 125 Вт • OEM",
    "k/ddr4": "8 ГБ, DDR4, 3200 МГц, DIMM 288-pin, non-ECC Unbuffered, 22-22-22, 1.2 В",
    "k/sodimm": "8 ГБ, DDR4, 3200 МГц, SO-DIMM 260-pins, non-ECC Unbuffered, 22-22-22, 1.2 В",
    "k/ecc": "16 ГБ, DDR4, 3200 МГц, DIMM 288-pin, ECC Registered, 22-22-22, 1.2 В",
    "c/small": "Al, LGA 1200, LGA 1700, Socket AM4, 90x90мм, 65 Вт, 1200–2800 об/мин, 65 Вт, BOX",
    "c/tower": "AlCu, LGA 1200, LGA 1700, Socket AM4, Socket AM5, 120x120мм, 220 Вт, 500–1850 об/мин, 220 Вт, BOX",
    "z/case": "MidiTower, ATX, 2 × 3.5\", 2 × 2.5\", без БП, черный",
    "f/case": "MidiTower, ATX, 3 × 5.25\", 4 × 3.5\", с БП 450 Вт, черный",
    "p/atx": "ATX12V, 500 Вт, Active PFC, 2 × IDE, 3 × SATA, 1 × ATX, 80 PLUS",
    "p/sfx": "SFX, 600 Вт, Active PFC, 3 × IDE, 6 × SATA, 1 × ATX, 80 PLUS Platinum",
    "s/nas": "2.5in (in 3.5in carrier), SATA3, отсеки:· 4, размер ФС до 88 ТБ, Intel Celeron, ОЗУ: 8 ГБ",
    "w/red": "HDD • 3.5in • 5400 об/мин • SATA3 • 4 ТБ",
    "w/sas": "HDD • 3.5in • 7200 об/мин • SAS 12Gb/s • 4 ТБ",
}


def pairs(title: str) -> set[tuple[str, str]]:
    return {(e["src"], e["dst"]) for e in sl.build(NODES, SPECS).get(title, [])}


def test_socket_alone_is_not_enough_the_chipset_must_support_the_cpu_generation():
    # B550 не работает с Ryzen 3200G, хотя сокет у обоих AM4; H610 без обновления не знает процессоров 14-го поколения
    assert pairs("плата — процессор под её сокет и чипсет") == {("a/b550", "amd/5600"), ("a/h610", "i/12400")}
    assert pairs("процессор — плата под его сокет") == {("amd/5600", "a/b550"), ("i/12400", "a/h610")}


def test_memory_must_be_the_same_generation_desktop_module_without_server_ecc():
    assert pairs("плата — память того же типа") == {("a/b550", "k/ddr4"), ("a/h610", "k/ddr4")}


def test_cooler_must_name_the_socket_and_take_the_heat_with_a_margin():
    got = pairs("процессор — кулер под его сокет")
    # кулер на 65 Вт к процессору на 65 Вт — без запаса, не советуем; к 125 Вт нужен башенный
    assert ("i/12400", "c/tower") in got and ("i/14600", "c/tower") in got
    assert not {p for p in got if p[1] == "c/small"}


def test_case_without_power_supply_gets_an_atx_unit_and_sata_disk_gets_a_cable():
    assert pairs("корпус без блока питания — блок ATX") == {("z/case", "p/atx")}
    assert pairs("хранилище — диск под его отсеки") == {("s/nas", "w/red")}
    assert pairs("диск SATA — кабель SATA") == {("w/red", "b/cable")}


def test_engine_shows_the_cpu_as_advice_and_the_memory_as_a_kit():
    edges = [e for es in sl.build(NODES, SPECS).values() for e in es]
    client = Client("b2b", [Purchase("a/b550", date(2026, 6, 1), 0)], today=TODAY)
    recs = Recommender(Graph(NODES, edges, [], {})).recommend(client, 3)
    assert [(r.family, r.slot) for r in recs] == [("amd/5600", "missing"), ("k/ddr4", "kit")]
    assert recs[0].label == "совместимо по характеристикам каталога"
    assert "сокет AM4" in recs[0].why and "чипсет B550" in recs[0].why
    assert recs[1].why.startswith("Если памяти ещё нет")
