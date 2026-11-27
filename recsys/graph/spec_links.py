"""Совместимость по характеристикам каталога: сокет платы и процессора, тип памяти, форм-фактор корпуса.

К оборудованию нельзя советовать «по классу»: процессор подходит не к любой плате. Но в фиде магазина
у товара есть строка характеристик — «mATX, 1×Socket AM4, AMD B550, DDR4, …». По ней совместимость
проверяется точно, без модели и без статистики. Какой класс к какому нужен, уже решила разметка классов
(knowledge/class_verdicts.csv: «к плате нужен процессор», «к корпусу — блок питания»); здесь выбирается,
какой именно товар подходит.

Основание связи — характеристики двух товаров из открытого фида, оно записано в evidence и показывается
клиенту. Связи лежат в knowledge/spec_links.csv и подключаются только в открытом движке.

Запуск: python -m recsys.graph.spec_links [--show]
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import re
import sys
from collections import defaultdict

from recsys.config import INTERIM, ROOT
from recsys.graph.name_links import load_nodes, rank

OUT = ROOT / "knowledge" / "spec_links.csv"
NEED, KIT = "характеристики каталога", "характеристики каталога: комплект"
PER_PRODUCT = 3                  # сколько товаров одного класса оставляем в связях; движок покажет один

SOCKET = re.compile(r"LGA\s?(\d{3,4}(?:-\d)?)|Socket\s+([A-Za-z]+\d+\+?)")
DESKTOP = {"LGA 1150", "LGA 1151", "LGA 1155", "LGA 1156", "LGA 1200", "LGA 1700", "LGA 1851", "AM3", "AM4", "AM5", "FM2"}
TOWER = ("MidiTower", "MiniTower", "Full-Tower", "SuperTower")
# Сокет совпал — ещё не всё: плата на чипсете B550 не работает с Ryzen 3200G, хотя сокет у обоих AM4.
# Здесь перечислено, процессоры какого поколения чипсет поддерживает без оговорок (по спискам совместимости
# производителей). Чипсета нет в таблице — процессор к плате не советуем; серверные сокеты не трогаем.
GENERATION = [
    ("AM4", r"A520|B550", r"Ryzen \d 5\d{3}"),
    ("AM5", r"A620A?|B650E?|X670E?", r"Ryzen \d 7\d{3}"),
    ("AM5", r"B840|B850|X870E?", r"Ryzen \d [789]\d{3}"),
    ("LGA 1700", r"H610|B660|H670|Z690", r"i\d-12\d{3}"),
    ("LGA 1700", r"B760|H770|Z790", r"i\d-1[234]\d{3}"),
    ("LGA 1851", r"H810|B860|Z890", r"Core Ultra"),
]
CHIPSET = re.compile(r"(?:AMD|Intel) ([A-Z]\d{3}[A-Z]?)")
WATTS = (450, 850)               # блок питания к корпусу: не самый слабый и не для рекордной сборки


def load_specs() -> dict[str, str]:
    """Строка характеристик каждого товара-оборудования из фида: ключ — адрес товара, как у узла графа."""
    out: dict[str, str] = {}
    with gzip.open(INTERIM / "feed_offers.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if o.get("kind") != "софт" and o.get("description"):
                out[o["url"].split("store.softline.ru/")[-1].strip("/")] = html.unescape(o["description"])
    return out


def sockets(spec: str) -> set[str]:
    return {f"LGA {a}" if a else b.upper() for a, b in SOCKET.findall(spec)}


def number(pattern: str, spec: str) -> int:
    m = re.search(pattern, spec)
    return int(m.group(1)) if m else 0


def ddr(spec: str) -> str:
    kinds = set(re.findall(r"DDR\d", spec))
    return kinds.pop() if len(kinds) == 1 else ""


def chipset(spec: str) -> str:
    m = CHIPSET.search(spec)
    return m.group(1) if m else ""


def fits(socket: str, board_spec: str, cpu_name: str) -> bool:
    """Процессор этого поколения плата на своём чипсете поддерживает."""
    chip = chipset(board_spec)
    return any(s == socket and re.fullmatch(chips, chip) and re.search(names, cpu_name)
               for s, chips, names in GENERATION)


def top(fams: list[str], nodes: dict[str, dict]) -> list[str]:
    return sorted(fams, key=lambda f: (rank(nodes[f]), nodes[f]["name"]))[:PER_PRODUCT]


def edge(src: str, dst: str, source: str, evidence: str) -> dict:
    return {"src": src, "dst": dst, "type": "complements", "source": source,
            "confidence": 0.8 if source == NEED else 0.5, "evidence": evidence}


def build(nodes: dict[str, dict], specs: dict[str, str]) -> dict[str, list[dict]]:
    by_class: dict[str, list[str]] = defaultdict(list)
    for k, n in nodes.items():
        if n["on_sale"] == "1" and k in specs:
            by_class[n["class"]].append(k)
    boards, cpus = by_class["parts/motherboard"], by_class["parts/cpu"]
    one = {k: next(iter(sockets(specs[k]))) for k in boards + cpus if len(sockets(specs[k])) == 1}
    parts: dict[str, list[dict]] = defaultdict(list)

    # плата → процессор с тем же сокетом; процессор → плата с тем же сокетом (комплект)
    cpu_by_socket, board_by_socket = defaultdict(list), defaultdict(list)
    for k in cpus:
        if k in one:
            cpu_by_socket[one[k]].append(k)
    for k in boards:
        if k in one:
            board_by_socket[one[k]].append(k)
    for b in boards:
        s = one.get(b)
        ok = [c for c in cpu_by_socket.get(s, []) if fits(s, specs[b], nodes[c]["name"])]
        for c in top(ok, nodes):
            parts["плата — процессор под её сокет и чипсет"].append(
                edge(b, c, NEED, f"У обоих в характеристиках каталога указан сокет {s}; чипсет {chipset(specs[b])} "
                                 f"рассчитан на процессоры этого поколения."))
    for c in cpus:
        s = one.get(c)
        ok = [b for b in board_by_socket.get(s, []) if fits(s, specs[b], nodes[c]["name"])]
        for b in top(ok, nodes):
            parts["процессор — плата под его сокет"].append(
                edge(c, b, KIT, f"Если платы ещё нет: у этой тот же сокет {s}, а её чипсет {chipset(specs[b])} "
                                f"рассчитан на процессоры этого поколения, — по характеристикам каталога"))

    # плата → память того же поколения: обычный модуль DIMM к плате под настольный сокет
    modules = defaultdict(list)
    for k in by_class["parts/ram"]:
        spec = specs[k]
        plain = "non-ECC Unbuffered" in spec or "On-Die ECC Unbuffered" in spec      # серверная память с ECC не подойдёт
        if ddr(spec) and "SO-DIMM" not in spec and "DIMM" in spec and plain:
            modules[ddr(spec)].append(k)
    for b in boards:
        gen = ddr(specs[b])
        if one.get(b) in DESKTOP and gen:
            for m in top(modules.get(gen, []), nodes):
                parts["плата — память того же типа"].append(
                    edge(b, m, KIT, f"Если памяти ещё нет: модуль {gen} DIMM подходит к слотам платы — "
                                    f"тип указан в характеристиках каталога"))

    # процессор → кулер, у которого назван сокет процессора и хватает отвода тепла
    coolers = [(k, sockets(specs[k]), number(r"(\d+) Вт", specs[k])) for k in by_class["parts/coolers"]]
    coolers = [x for x in coolers if x[1]]
    for c in cpus:
        s, heat = one.get(c), number(r"TDP (\d+)", specs[c])
        # с запасом в треть: под нагрузкой процессор выделяет больше паспортного; из подходящих — самые доступные
        fit = [k for k, ss, power in coolers if s in ss and heat and power >= 1.3 * heat]
        fit.sort(key=lambda k: (float(nodes[k].get("price_from") or 0) or 10 ** 9, nodes[k]["name"]))
        for k in fit[:PER_PRODUCT]:
            power = number(r"(\d+) Вт", specs[k])
            parts["процессор — кулер под его сокет"].append(
                edge(c, k, NEED, f"В характеристиках кулера указаны сокет {s} и отвод тепла до {power} Вт — "
                                 f"у процессора {heat} Вт."))

    # корпус без блока питания → блок питания стандарта ATX
    supplies = [k for k in by_class["parts/psu"] if specs[k].split(",")[0].strip() in ("ATX", "ATX12V")
                and WATTS[0] <= number(r"(\d+) Вт", specs[k]) <= WATTS[1]]
    best = top(supplies, nodes)
    for k in by_class["parts/chassis"]:
        spec = specs[k]
        if spec.split(",")[0].strip() in TOWER and "без БП" in spec:
            for p in best:
                parts["корпус без блока питания — блок ATX"].append(
                    edge(k, p, NEED, "В каталоге указано, что корпус продаётся без блока питания; этот блок — "
                                     "стандарта ATX, который ставят в корпуса такого типа. Мощность выбирают под сборку."))

    # сетевое хранилище → диск 3,5 дюйма SATA; сначала диски серий для хранилищ
    disks = [k for k in by_class["parts/hdd"] if specs[k].startswith("HDD") and "3.5in" in specs[k] and "SATA" in specs[k]]
    for_nas = [k for k in disks if re.search(r"ironwolf|\bnas\b|\bred\b|n300", nodes[k]["name"], re.I)] or disks
    for k in by_class["san-nas/nas"]:
        if "3.5in" in specs[k]:
            for d in top(for_nas, nodes):
                parts["хранилище — диск под его отсеки"].append(
                    edge(k, d, NEED, "В характеристиках хранилища указаны отсеки 3,5 дюйма, у диска — тот же размер "
                                     "и интерфейс SATA."))

    # диск SATA → кабель SATA (комплект)
    cables = [k for k, n in nodes.items() if n["on_sale"] == "1" and n["class"] == "parts/cables"
              and re.search(r"^кабель\b.*\bsata sata\b", n["name"], re.I)]
    for k in by_class["parts/hdd"]:
        if "SATA" in specs[k] and "USB" not in specs[k] and "SAS" not in nodes[k]["name"].upper():
            for c in top(cables, nodes)[:1]:
                parts["диск SATA — кабель SATA"].append(
                    edge(k, c, KIT, "Если ещё нет кабеля SATA: без него диск не подключить к материнской плате — "
                                    "у диска интерфейс SATA по характеристикам каталога"))
    return dict(parts)


def read() -> list[dict]:
    """Связи по характеристикам в том же виде, что строки графа каталога."""
    if not OUT.exists():
        return []
    with OUT.open(encoding="utf-8") as f:
        return [{**r, "confidence": float(r["confidence"])} for r in csv.DictReader(f)]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    nodes = load_nodes()
    parts = build(nodes, load_specs())
    edges = [e for es in parts.values() for e in es]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["src", "dst", "type", "source", "confidence", "evidence"], lineterminator="\n")
        w.writeheader()
        w.writerows(edges)
    for title, es in parts.items():
        print(f"{title}: связей {len(es)}, товаров с советом {len({e['src'] for e in es})}")
        if "--show" in sys.argv:
            for e in es[:: max(1, len(es) // 8)]:
                print(f"   {nodes[e['src']]['name'][:62]}  ->  {nodes[e['dst']]['name'][:62]} | {e['evidence'][:90]}")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
