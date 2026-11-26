"""Вердикты пар «купил А — советовать ли Б»: что попадает в блок и в каком порядке."""
from datetime import date

from recsys.llm.pair_verdicts import Verdict, Verdicts, as_class_links, as_family_edges, class_blocks
from recsys.recommend.engine import Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 27)


def node(f, c):
    return {"family": f, "name": f, "vendor": "v", "class": c, "on_sale": "1", "kind": "software"}


NODES = {f: node(f, c) for f, c in [
    ("v/antivirus", "c/av"), ("v/backup", "c/backup"), ("v/backup2", "c/backup"), ("v/office", "c/office"),
    ("v/firewall", "c/fw"), ("v/os", "c/os")]}
RANK = {"c/backup": ["v/backup", "v/backup2"], "c/office": ["v/office"], "c/fw": ["v/firewall"], "c/os": ["v/os"]}
CLIENT = Client("b2b", [Purchase("v/antivirus", date(2026, 6, 1), 0)], today=TODAY)


def links(table):
    return as_class_links({k: Verdict(*v) for k, v in table.items()})


def test_only_need_and_kit_become_class_links():
    got = links({("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика"),
                 ("c/av", "c/office"): ("kit", "Если ещё нет офисного пакета: документы и таблицы"),
                 ("c/av", "c/os"): ("has", "без неё антивирус не поставить")})
    assert [(x["dst_class"], x["verdict"]) for x in got] == [("c/backup", "need"), ("c/office", "kit")]


def test_need_goes_first_and_only_one_kit_is_shown():
    table = {("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика"),
             ("c/av", "c/office"): ("kit", "Если ещё нет офисного пакета: документы и таблицы"),
             ("c/av", "c/fw"): ("kit", "Если ещё нет защиты периметра: фильтрует трафик")}
    recs = Recommender(Graph(NODES, [], links(table), RANK)).recommend(CLIENT, 3)
    assert [r.slot for r in recs] == ["missing", "kit"]
    assert recs[0].family == "v/backup" and recs[0].label == "пара проверена заранее: нужно"
    assert recs[1].label == "пара проверена заранее: комплект"


def test_already_owned_base_is_never_advised():
    # «к антивирусу — операционная система»: вердикт «уже есть», связи нет вовсе
    recs = Recommender(Graph(NODES, [], links({("c/av", "c/os"): ("has", "")}), RANK)).recommend(CLIENT, 3)
    assert recs == []


def test_api_verdict_waits_for_manager_until_model_is_trusted():
    link = {"src_class": "c/av", "dst_class": "c/backup", "type": "complements", "audience": "any",
            "confidence": 0.85, "reason": "Вернёт файлы", "verdict": "need", "source": "разметка по API"}
    g = Graph(NODES, [], [dict(link)], RANK)
    recs, bans = Recommender(g).recommend(CLIENT, 3, explain_bans=True)
    assert recs == [] and bans[0]["reason"] == "ждёт проверки менеджером"
    g = Graph(NODES, [], [dict(link)], RANK)
    assert Recommender(g, approved={"cl|c/av|c/backup"}).recommend(CLIENT, 3)[0].label == "принято менеджером"


def test_judged_pair_overrides_class_pick():
    table = {("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика")}
    g = Graph(NODES, [], links(table), RANK, veto=[("v/antivirus", "v/backup")])
    assert Recommender(g).recommend(CLIENT, 3)[0].family == "v/backup2"       # первый товар класса отвергнут парой
    g = Graph(NODES, [], links(table), RANK, class_block=[("v/antivirus", "c/backup")])
    assert Recommender(g).recommend(CLIENT, 3) == []                          # оба первых не подошли: класс не советуем


def test_family_verdicts_become_edges_and_blocks():
    table = {("v/antivirus", "v/backup"): Verdict("unrelated"), ("v/antivirus", "v/backup2"): Verdict("same"),
             ("v/antivirus", "v/firewall"): Verdict("need", "Фильтрует трафик до рабочих мест")}
    edges = as_family_edges(table)
    assert [(e["dst"], e["source"]) for e in edges] == [("v/firewall", "разметка: нужно")]
    assert class_blocks(table, NODES) == [["v/antivirus", "c/backup"]]
    g = Graph(NODES, edges, [], RANK)
    rec = Recommender(g).recommend(CLIENT, 3)[0]
    assert rec.family == "v/firewall" and rec.why == "Фильтрует трафик до рабочих мест."


def test_judge_over_all_purchases():
    v = Verdicts({("a", "x"): Verdict("need", "зачем"), ("b", "x"): Verdict("has"), ("a", "y"): Verdict("kit", "если нет"),
                  ("b", "y"): Verdict("unrelated"), ("a", "z"): Verdict("unrelated")})
    assert v.judge(["a", "b"], "x").verdict == "has"             # «уже есть» по одной покупке убирает совет
    assert v.judge(["a"], "x").verdict == "need"
    assert v.judge(["a", "b"], "y").verdict == "kit"
    assert v.judge(["a"], "z").verdict == "unrelated"
    assert v.judge(["a", "b"], "z").verdict == ""                # пара (b, z) не размечена: не советуем, ждёт разметки
    assert ("b", "z") in v.missing


def test_class_link_takes_only_products_that_passed_a_pair_check():
    table = {("c/av", "c/backup"): ("need", "Вернёт файлы после шифровальщика")}
    g = Graph(NODES, [], links(table), RANK, class_picks={"c/av|c/backup": ["v/backup2"]})
    assert Recommender(g).recommend(CLIENT, 3)[0].family == "v/backup2"       # первый по витрине проверку не прошёл
    g = Graph(NODES, [], links(table), RANK, class_picks={"c/av|c/backup": []})
    assert Recommender(g).recommend(CLIENT, 3) == []                          # судили, не подошёл ни один


def test_consumables_only_from_the_same_vendor():
    nodes = {"hp/printer": dict(node("hp/printer", "c/print"), kind="hardware"),
             "kyo/toner": dict(node("kyo/toner", "c/toner"), kind="hardware"),
             "hp/toner": dict(node("hp/toner", "c/toner"), kind="hardware")}
    table = {("c/print", "c/toner"): ("need", "Картриджи заканчиваются, запасной нужен заранее")}
    g = Graph(nodes, [], links(table), {"c/toner": ["kyo/toner", "hp/toner"]})
    client = Client("b2b", [Purchase("hp/printer", date(2026, 6, 1), 0)], today=TODAY)
    assert [r.family for r in Recommender(g).recommend(client, 3)] == ["hp/toner"]
