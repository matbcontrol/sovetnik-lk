"""Связи агента каталога: подтверждённые цитатой идут в граф, остальные — только в очередь менеджера."""
from datetime import date

from recsys.graph import agent_links
from recsys.recommend.engine import Client, Graph, Purchase, Recommender

TODAY = date(2026, 11, 27)
HEAD = "src,dst,type,source,confidence,evidence,quote,status,model,date\n"


def node(family: str, cls: str) -> dict:
    return {"family": family, "name": family, "vendor": family.split("/")[0], "vendor_slug": family.split("/")[0],
            "class": cls, "class_name": cls, "kind": "software", "on_sale": "1", "on_request": "0", "b2b_only": "0",
            "client_types": "", "platforms": "", "price_from": "1000", "store_rank": "1", "category": "", "subcategory": ""}


def table(tmp_path, body: str):
    p = tmp_path / "agent_links.csv"
    p.write_text(HEAD + body, encoding="utf-8")
    return p


def test_only_links_confirmed_by_a_quote_reach_the_graph(tmp_path):
    p = table(tmp_path, (
        "v/cad,v/viewer,complements,агент каталога,0.75,Открывает чертежи без лицензии САПР,«просмотр DWG»,graph,m,2026-11-27\n"
        "v/cad,v/archive,complements,агент каталога: комплект,0.5,Если ещё нет архива: хранит версии чертежей,«архив проектов»,graph,m,2026-11-27\n"
        "v/cad,v/render,complements,агент каталога,0.75,Строит картинку по модели,,queue,m,2026-11-27\n"
        "v/cad,v/cad,complements,агент каталога,0.75,сам к себе,,graph,m,2026-11-27\n"
        "v/cad,v/x,complements,чужой источник,0.75,не наш,,graph,m,2026-11-27\n"))
    assert [(e["dst"], e["source"]) for e in agent_links.read(p)] == [
        ("v/viewer", "агент каталога"), ("v/archive", "агент каталога: комплект")]
    assert [e["dst"] for e in agent_links.queue(p)] == ["v/render"]
    assert agent_links.quotes(p)["v/cad|v/viewer|complements"] == "«просмотр DWG»"


def test_agent_link_is_shown_with_its_own_reason_and_kit_goes_last(tmp_path):
    p = table(tmp_path, (
        "v/cad,v/viewer,complements,агент каталога,0.75,Открывает чертежи без лицензии САПР,«просмотр DWG»,graph,m,2026-11-27\n"
        "v/cad,v/archive,complements,агент каталога: комплект,0.5,Если ещё нет архива: хранит версии чертежей,«архив»,graph,m,2026-11-27\n"))
    nodes = {k: node(k, c) for k, c in (("v/cad", "c/cad"), ("v/viewer", "c/view"), ("v/archive", "c/arch"))}
    client = Client("b2b", [Purchase("v/cad", date(2026, 6, 1), 0)], today=TODAY)
    recs = Recommender(Graph(nodes, agent_links.read(p), [], {})).recommend(client, 3)
    assert [(r.family, r.slot) for r in recs] == [("v/viewer", "missing"), ("v/archive", "kit")]
    assert recs[0].why == "Открывает чертежи без лицензии САПР." and recs[0].label == "найдено агентом каталога, подтверждено цитатой"
    assert recs[1].why.startswith("Если ещё нет архива")
    # вердикт разметки главнее: пара, которую разметчик назвал «то же самое», клиенту не идёт
    vetoed = Graph(nodes, agent_links.read(p), [], {}, veto=[("v/cad", "v/viewer", "same")])
    assert [r.family for r in Recommender(vetoed).recommend(client, 3)] == ["v/archive"]
