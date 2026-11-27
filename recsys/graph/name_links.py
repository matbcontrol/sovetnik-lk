"""Связи, которые прямо читаются в названиях товаров открытого каталога.

1. Серия. Курсы одной серии одного издателя («Слесарь-ремонтник. Подшипники» и «Слесарь-ремонтник. Редукторы»)
   и расходные материалы одной серии разных цветов (CLI471XL голубой и CLI471XL жёлтый). Это совет-«комплект»:
   он начинается с условия «Если…», стоит последней карточкой и не больше одного. Сверка с разметкой пар:
   из 12 пар курсов одной серии, которые разметчик судил сам, он показал бы 11, а пары «по общей теме» отвергал —
   поэтому серией считается только то, что названо серией в самих названиях.
2. Модель. Принадлежность, в названии которой прямо названа модель устройства того же производителя
   («EHS40 адаптер … для SIP-T46U/T48U» — к телефону SIP-T46U). Это факт каталога, а не вывод.

Основание каждой связи — два названия; оно записано в evidence и показывается клиенту. Связи лежат
в knowledge/name_links.csv, отдельно от графа каталога (data/interim/graph_edges.csv): на блок кабинета
по данным задачи они не влияют. Пара, по которой есть вердикт разметки, подчиняется вердикту.

Запуск: python -m recsys.graph.name_links [--show]
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict

from recsys.config import INTERIM, ROOT

OUT = ROOT / "knowledge" / "name_links.csv"
SERIES, NAMED = "серия в названии", "название товара"
PER_PRODUCT = 3                  # сколько связей одного вида оставляем товару
COURSES = "obrazovanie-i-nauka/obuchayuschie-programmi"
SUPPLIES = "office/expendables"

# ---- курсы одной серии ---------------------------------------------------------------------------------

LEAD = re.compile(r"^(?:sike|ос3|oc3|осӡ|осз)\s*\.?\s*", re.I)
COURSE_WORD = re.compile(r"^электронный\s+курс\s+(?:sike\s+)?", re.I)
QUOTES = str.maketrans("", "", "«»\"“”‎")
GRADES = re.compile(r"\d+\s*[-–]\s*\d+\s+класс", re.I)
CHAPTER = re.compile(r"\s+глава\s+\d+$", re.I)


def course_name(name: str) -> str:
    """Название курса без имени издателя, слов «Электронный курс» и кавычек."""
    s = name.translate(QUOTES).strip()
    return COURSE_WORD.sub("", LEAD.sub("", s)).strip()


def course_key(name: str) -> str:
    """Серия курса — то, что стоит в названии до двоеточия или до первой точки: «Слесарь-ремонтник»."""
    s = course_name(name)
    for sep in (":", ". "):
        if sep in s:
            head, tail = s.split(sep, 1)
            head = CHAPTER.sub("", head.strip())
            if len(head) >= 6 and tail.strip() and not GRADES.search(head):
                return head
    return ""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip()


def course_series(nodes: dict[str, dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    title: dict[tuple[str, str], str] = {}
    for k, n in nodes.items():
        if n["class"] == COURSES and n["on_sale"] == "1":
            key = course_key(n["name"])
            if key:
                g = (n["vendor_slug"], norm(key))
                groups[g].append(k)
                title.setdefault(g, key)
    edges = []
    for g, fams in sorted(groups.items()):
        if len(fams) < 2:
            continue
        fams.sort(key=lambda f: (rank(nodes[f]), nodes[f]["name"]))
        for i, a in enumerate(fams):
            near = fams[i + 1:] + fams[:i]               # соседи по серии: следующие по витрине, затем по кругу
            for b in near[:PER_PRODUCT]:
                edges.append({"src": a, "dst": b, "type": "complements", "source": SERIES, "confidence": 0.5,
                              "evidence": f"Если серию «{title[g]}» продолжают: это ещё одна её часть от того же издателя"})
    return edges


# ---- расходные материалы одной серии разных цветов -----------------------------------------------------

COLOURS = ("светло-голубой", "светло-пурпурный", "голубой", "пурпурный", "желтый", "жёлтый", "черный", "чёрный",
           "серый", "красный", "синий", "зеленый", "зелёный", "оранжевый", "белый")
COLOUR_RE = re.compile(r"(?<![\w-])(" + "|".join(COLOURS) + r")(?![\w-])", re.I)
CODE = re.compile(r"[A-Za-z]*\d[\w\-]*")
SUFFIXES = ("PGBK", "MBK", "PBK", "BK", "GY", "LC", "LM", "C", "M", "Y", "K")
VOLUME = re.compile(r"-\d+$")
KINDS = ("картридж", "чернильница", "тонер-картридж", "тонер", "чернила")


def colours(name: str) -> frozenset[str]:
    return frozenset(x.lower().replace("ё", "е") for x in COLOUR_RE.findall(name))


def supply_keys(n: dict) -> list[str]:
    """Ключи серии: название без цвета и артикула («Canon GI-490»), код без цветового окончания
    (CLI471XLC и CLI471XLY — серия CLI471XL) и код без последней цифры (CF211A и CF212A — серия CF21·A)."""
    name = n["name"]
    kind = name.split()[0].lower()
    if kind not in KINDS:
        return []
    head = re.sub(r"\s+", " ", COLOUR_RE.sub("", name.split(",")[0]).replace(" ,", ",")).strip(" ,")
    codes = CODE.findall(head)
    part = name.split(",")[-1].strip() if "," in name else ""
    keys = []
    if part and codes:
        keys.append("имя|" + norm(head))                # в названии есть номер серии: «картридж canon gi-490»
    code = (CODE.findall(part) or codes or [""])[-1].upper()
    if code:
        vol = VOLUME.search(code)
        base, tail = (code[:vol.start()], vol.group(0)) if vol else (code, "")
        for s in SUFFIXES:                              # CLI471XLM: и «…XL» + M, и «…X» + LM — берём оба ключа
            if base.endswith(s) and len(base) - len(s) >= 4:
                keys.append(f"код|{kind}|{base[:-len(s)]}{tail}")
        d = re.search(r"\d(?=\D*$)", base)
        if d:
            keys.append(f"цифра|{kind}|{base[:d.start()]}#{base[d.end():]}{tail}")
    return keys


def colour_series(nodes: dict[str, dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for k, n in nodes.items():
        if n["class"] == SUPPLIES and n["on_sale"] == "1" and colours(n["name"]):
            for key in supply_keys(n):
                groups[(n["vendor_slug"], key)].append(k)
    by_src: dict[str, set[str]] = defaultdict(set)
    for fams in groups.values():
        for a in fams:
            for b in fams:
                # серию опознаём по разному цвету; набор из нескольких цветов владельцу одного из них не советуем
                if a != b and colours(nodes[a]["name"]).isdisjoint(colours(nodes[b]["name"])):
                    by_src[a].add(b)
    edges = []
    for a, bs in sorted(by_src.items()):
        seen: set[frozenset[str]] = set()
        for b in sorted(bs, key=lambda f: (rank(nodes[f]), nodes[f]["name"])):
            c = colours(nodes[b]["name"])
            if c in seen:
                continue                                # одного цвета в блоке хватит одного
            seen.add(c)
            edges.append({"src": a, "dst": b, "type": "complements", "source": SERIES, "confidence": 0.5,
                          "evidence": "Если заканчиваются и другие цвета: та же серия расходных материалов, "
                                      "подходит к тому же принтеру"})
            if len(seen) >= PER_PRODUCT:
                break
    return edges


# ---- принадлежность, в названии которой названа модель устройства --------------------------------------

FOR = re.compile(r"(?:^|\s)(?:для|for)\s+(.+)$", re.I)
TOKEN = re.compile(r"[A-Za-zА-Яа-я0-9][\w\-+]*")
PAREN = re.compile(r"\(([^()]*)\)")
VARIANT = re.compile(r"^(.*?)([\w\-]+)\((\w)\)(\w*)$")


def is_code(t: str) -> bool:
    return len(t) >= 3 and bool(re.search(r"\d", t)) and bool(re.search(r"[A-ZА-Я]", t))


def codes(text: str) -> set[str]:
    """Коды моделей: слова, где есть и буква, и цифра, от трёх знаков; «SIP-T46U» даёт и «T46U»."""
    out = {t.strip("-").upper() for t in TOKEN.findall(text)}
    out = {t for t in out if is_code(t)}
    return out | {t.split("-", 1)[1] for t in out if "-" in t and is_code(t.split("-", 1)[1])}


def mention_codes(tail: str) -> set[str]:
    """Коды из перечня после «для»: «SIP-T41S/T42S/T46U(S)» — скобка даёт вариант с добавленной буквой."""
    out: set[str] = set()
    for chunk in re.split(r"[/,;&]|\s+и\s+|\s+and\s+", tail):
        chunk = chunk.strip()
        m = VARIANT.match(chunk)
        if m:
            out |= codes(m.group(2) + m.group(4)) | codes(m.group(2) + m.group(3) + m.group(4))
        out |= codes(PAREN.sub(" ", chunk))
    return out


def named_models(nodes: dict[str, dict]) -> list[dict]:
    sale = [n for n in nodes.values() if n.get("kind") == "hardware" and n["on_sale"] == "1"]
    devices: dict[str, list[tuple[dict, set[str]]]] = defaultdict(list)
    for n in sale:
        name = PAREN.sub(" ", n["name"])
        if FOR.search(name) or "ASSY" in name.upper():
            continue                                    # само устройство — принадлежность к чему-то
        cs = codes(name)
        if cs:
            devices[n["vendor_slug"]].append((n, cs))
    by_dev: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for acc in sale:
        m = FOR.search(acc["name"])
        if not m:
            continue
        mentioned = mention_codes(m.group(1))
        for dev, cs in devices.get(acc["vendor_slug"], ()) if mentioned else ():
            hit = sorted(mentioned & cs, key=len, reverse=True)
            if hit and dev["family"] != acc["family"] and dev["class"] != acc["class"]:
                by_dev[dev["family"]].append((acc["family"], hit[0]))
    edges = []
    for d, lst in sorted(by_dev.items()):
        lst.sort(key=lambda x: (rank(nodes[x[0]]), nodes[x[0]]["name"]))
        for a, code in lst[:PER_PRODUCT]:
            edges.append({"src": d, "dst": a, "type": "complements", "source": NAMED, "confidence": 0.75,
                          "evidence": f"В названии принадлежности прямо названа модель {code}."})
    return edges


def rank(n: dict) -> int:
    try:
        return int(float(n.get("store_rank") or 10 ** 6))
    except ValueError:
        return 10 ** 6


def load_nodes() -> dict[str, dict]:
    with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
        return {r["family"]: r for r in csv.DictReader(f)}


def read() -> list[dict]:
    """Связи из названий в том же виде, что строки графа каталога."""
    if not OUT.exists():
        return []
    with OUT.open(encoding="utf-8") as f:
        return [{**r, "confidence": float(r["confidence"])} for r in csv.DictReader(f)]


def build(nodes: dict[str, dict]) -> dict[str, list[dict]]:
    return {"курсы одной серии": course_series(nodes), "расходные материалы одной серии": colour_series(nodes),
            "модель названа в названии принадлежности": named_models(nodes)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    nodes = load_nodes()
    parts = build(nodes)
    edges = [e for es in parts.values() for e in es]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["src", "dst", "type", "source", "confidence", "evidence"], lineterminator="\n")
        w.writeheader()
        w.writerows(edges)
    for title, es in parts.items():
        print(f"{title}: связей {len(es)}, товаров с советом {len({e['src'] for e in es})}")
        if "--show" in sys.argv:
            for e in es[:: max(1, len(es) // 25)]:
                print(f"   {nodes[e['src']]['name'][:66]}  ->  {nodes[e['dst']]['name'][:66]} | {e['evidence'][:60]}")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
