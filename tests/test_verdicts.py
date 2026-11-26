"""Вердикты пар «купил А — советовать ли Б»: что видит клиент, а что ждёт менеджера."""
from datetime import date

from recsys.llm.pair_verdicts import Verdict, Verdicts, as_class_links, as_family_edges, class_blocks
from recsys.recommend.engine import PENDING, Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 27)


def node(f, c):
    return {"family": f, "name": f, "vendor": "v", "class": c, "on_sale": "1", "kind": "software"}


NODES = {f: node(f, c) for f, c in [
    ("v/antivirus", "c/av"), ("v/backup", "c/backup"), ("v/backup2", "c/backup"), ("v/office", "c/office"),
    ("v/firewall", "c/fw"), ("v/os", "c/os")]}
RANK = {"c/backup": ["v/backup", "v/backup2"], "c/office": ["v/office"], "c/fw": ["v/firewall"], "c/os": ["v/os"]}
CLIENT = Client("b2b", [Purchase("v/antivirus", date(2026, 6, 1), 0)], today=TODAY)
CLASS_TABLE = {("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика"),
               ("c/av", "c/office"): ("kit", "Если ещё нет офисного пакета: документы и таблицы"),
               ("c/av", "c/fw"): ("kit", "Если ещё нет защиты периметра: фильтрует трафик")}


def links(table):
    return as_class_links({k: Verdict(*v) for k, v in table.items()})


def test_only_need_and_kit_become_class_links():
    got = links({**CLASS_TABLE, ("c/av", "c/os"): ("has", "без неё антивирус не поставить")})
    assert [(x["dst_class"], x["verdict"]) for x in got] == [("c/backup", "need"), ("c/fw", "kit"), ("c/office", "kit")]


def test_class_link_is_a_hint_for_the_manager_not_advice_for_the_client():
    # связь классов проверена, но пара с товаром клиента — нет: клиент такой совет не видит
    recs, bans = Recommender(Graph(NODES, [], links(CLASS_TABLE), RANK)).recommend(CLIENT, 3, explain_bans=True)
    assert recs == [] and {b["reason"] for b in bans} == {PENDING}
    # менеджер видит всё: «нужно» первым, «комплект» — после и не больше одного
    seen = Recommender(Graph(NODES, [], links(CLASS_TABLE), RANK), unverified=True).recommend(CLIENT, 3)
    assert [r.slot for r in seen] == ["missing", "kit"]
    assert seen[0].family == "v/backup" and seen[0].label == "пара проверена заранее: нужно"
    # принятая менеджером связь начинает работать у клиента
    ok = Recommender(Graph(NODES, [], links(CLASS_TABLE), RANK), approved={"cl|c/av|c/backup"}).recommend(CLIENT, 3)
    assert [r.family for r in ok] == ["v/backup"] and ok[0].label == "принято менеджером"


def test_already_owned_base_is_never_advised():
    # «к антивирусу — операционная система»: вердикт «уже есть», связи нет вовсе — даже для менеджера
    g = Graph(NODES, [], links({("c/av", "c/os"): ("has", "")}), RANK)
    assert Recommender(g, unverified=True).recommend(CLIENT, 3) == []


def test_client_sees_a_checked_product_pair_with_verdict_need():
    table = {("v/antivirus", "v/backup"): Verdict("unrelated"), ("v/antivirus", "v/backup2"): Verdict("same"),
             ("v/antivirus", "v/firewall"): Verdict("need", "Фильтрует трафик до рабочих мест"),
             ("v/antivirus", "v/office"): Verdict("kit", "Если ещё нет офисного пакета: документы и таблицы")}
    edges = as_family_edges(table)
    assert [(e["dst"], e["source"]) for e in edges] == [("v/firewall", "разметка: нужно"), ("v/office", "разметка: комплект")]
    assert class_blocks(table, NODES) == [["v/antivirus", "c/backup"]]
    recs, bans = Recommender(Graph(NODES, [dict(e) for e in edges], [], RANK)).recommend(CLIENT, 3, explain_bans=True)
    assert [r.family for r in recs] == ["v/firewall"] and recs[0].why == "Фильтрует трафик до рабочих мест."
    assert [(b["family"], b["reason"]) for b in bans] == [("v/office", PENDING)]       # комплект ждёт менеджера
    ok = Recommender(Graph(NODES, [dict(e) for e in edges], [], RANK), approved={"pk|v/antivirus|v/office"})
    assert [r.slot for r in ok.recommend(CLIENT, 3)] == ["missing", "kit"]


def test_judged_pair_overrides_class_pick():
    table = {("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика")}
    g = Graph(NODES, [], links(table), RANK, veto=[("v/antivirus", "v/backup")])
    assert Recommender(g, unverified=True).recommend(CLIENT, 3)[0].family == "v/backup2"   # первый отвергнут парой
    g = Graph(NODES, [], links(table), RANK, class_block=[("v/antivirus", "c/backup")])
    assert Recommender(g, unverified=True).recommend(CLIENT, 3) == []                      # оба первых не подошли


def test_class_link_takes_only_products_that_passed_a_pair_check():
    table = {("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика")}
    g = Graph(NODES, [], links(table), RANK, class_picks={"c/av|c/backup": ["v/backup2"]})
    assert Recommender(g, unverified=True).recommend(CLIENT, 3)[0].family == "v/backup2"
    g = Graph(NODES, [], links(table), RANK, class_picks={"c/av|c/backup": []})
    assert Recommender(g, unverified=True).recommend(CLIENT, 3) == []                      # судили, не подошёл ни один


def test_consumables_only_from_the_same_vendor():
    nodes = {"hp/printer": dict(node("hp/printer", "c/print"), kind="hardware"),
             "kyo/toner": dict(node("kyo/toner", "c/toner"), kind="hardware"),
             "hp/toner": dict(node("hp/toner", "c/toner"), kind="hardware")}
    table = {("c/print", "c/toner"): ("need", "Картриджи заканчиваются, запасной нужен заранее")}
    g = Graph(nodes, [], links(table), {"c/toner": ["kyo/toner", "hp/toner"]})
    client = Client("b2b", [Purchase("hp/printer", date(2026, 6, 1), 0)], today=TODAY)
    assert [r.family for r in Recommender(g, unverified=True).recommend(client, 3)] == ["hp/toner"]


def test_home_product_is_recognised_by_its_class():
    # у домашних антивирусов в каталоге бывает пустой признак «для дома»: организации их не советуем по классу
    nodes = {"ms/windows": node("ms/windows", "c/os"), "dr/home": node("dr/home", "c/home-av")}
    edge = {"src": "ms/windows", "dst": "dr/home", "type": "complements", "confidence": 0.85,
            "evidence": "Защищает систему от вирусов", "source": "разметка: нужно"}

    def advice(kind):
        g = Graph(nodes, [dict(edge)], [], {}, class_names={"c/home-av": "Антивирусы для дома", "c/os": "ОС"})
        return Recommender(g).recommend(Client(kind, [Purchase("ms/windows", date(2026, 6, 1), 0)], today=TODAY), 3)

    assert [r.family for r in advice("b2c")] == ["dr/home"]
    assert advice("b2b") == []


def test_judge_over_all_purchases():
    v = Verdicts({("a", "x"): Verdict("need", "зачем"), ("b", "x"): Verdict("has"), ("a", "y"): Verdict("kit", "если нет"),
                  ("b", "y"): Verdict("unrelated"), ("a", "z"): Verdict("unrelated")})
    assert v.judge(["a", "b"], "x").verdict == "has"             # «уже есть» по одной покупке убирает совет
    assert v.judge(["a"], "x").verdict == "need"
    assert v.judge(["a", "b"], "y").verdict == "kit"
    assert v.judge(["a"], "z").verdict == "unrelated"
    assert v.judge(["a", "b"], "z").verdict == ""                # пара (b, z) не размечена: не советуем, ждёт разметки
    assert ("b", "z") in v.missing
