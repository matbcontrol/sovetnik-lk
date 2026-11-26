"""Вердикты по парам «у клиента есть А — советовать ли Б».

Зачем. Статистика продаж не отличает «нужно» от «и так есть»: после антивируса чаще всего покупают
Windows и офисный пакет просто потому, что их покупают все. Поэтому каждую пару заранее судит модель
по правилам хорошего менеджера:

  need       нужно        Б работает вместе с А, защищает или расширяет его; у владельца А его может не быть
  kit        комплект     прямой связи нет, но Б входит в тот же типовой набор и мог быть ещё не куплен
  has        уже есть     без Б товар А не работал бы (к антивирусу — Windows)
  same       то же самое  Б решает ту же задачу: замена, а не дополнение
  unrelated  не связано   Б просто часто покупают

В советы идут только «нужно» и «комплект», каждый с фразой «зачем». На обращение клиента модель
не вызывается: вердикт — строка таблицы.

Три таблицы:
  knowledge/class_verdicts.csv           классы каталога (открытые данные, в репозитории)
  knowledge/family_verdicts.csv          семейства открытого каталога (открытые данные, в репозитории)
  data/private/task/family_verdicts.csv  линейки из продаж (названия из датасета, не публикуется)
Правила для разметчика, пачки пар и ответы по открытому каталогу лежат в knowledge/labelling.

Первую разметку сделал ИИ-ассистент во время разработки — по тем же правилам, что записаны ниже
в RULES (в таблицах источник «ассистент»). В эксплуатации новые пары размечает модель по API:

  python -m recsys.llm.pair_verdicts merge                 собрать файлы первой разметки в таблицы
  python -m recsys.llm.pair_verdicts label --level class   разметить через API пары, у которых вердикта ещё нет
  python -m recsys.llm.pair_verdicts check                 сверить модель по API с готовой разметкой на выборке

Датасет организаторов нельзя отправлять в сервисы, которые обучаются на переданных данных, поэтому
разметка линеек из продаж через API включается только флагом --private: его ставят там, где модель
работает по договору без обучения на данных (в расчёте стоимости — Qwen в Yandex AI Studio).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from recsys.config import INTERIM, ROOT, TASK_DIR

CLASS_FILE = ROOT / "knowledge" / "class_verdicts.csv"
FAMILY_FILE = ROOT / "knowledge" / "family_verdicts.csv"
LINE_FILE = TASK_DIR / "family_verdicts.csv"
WORK = TASK_DIR / "pairs"                       # пачки первой разметки линеек из продаж (закрытая папка)
OPEN_WORK = ROOT / "knowledge" / "labelling"     # первая разметка открытого каталога: правила, пачки, ответы
TODO = {"class": INTERIM / "class_pairs_todo.json", "family": INTERIM / "family_pairs_todo.json",
        "line": WORK / "todo.json"}
REPORT = ROOT / "reports" / "pair_verdicts_check.json"

CODES = {"N": "need", "K": "kit", "H": "has", "S": "same", "U": "unrelated"}
LETTER = {v: k for k, v in CODES.items()}
RU = {"need": "нужно", "kit": "комплект", "has": "уже есть", "same": "то же самое", "unrelated": "не связано"}
SHOWN = ("need", "kit")
FIRST = "ассистент"                              # первая разметка: ИИ-ассистент во время разработки
CONFIDENCE = {"need": 0.85, "kit": 0.5}          # сила совета в графе: «нужно» идёт раньше «комплекта»
REASON_MAX = 140
# Вердикту модели по API клиент верит сразу, только если на сверке с первой разметкой она дала не меньше
# TRUST одинаковых решений «советовать или нет» и не меньше TRUST точности среди того, что показала бы сама.
# Иначе её «нужно» и «комплект» ждут менеджера.
TRUST = 0.85

SYSTEM = ("Ты — опытный менеджер по продажам ИТ-дистрибьютора в России: программы и оборудование для организаций "
          "и частных лиц. Клиенты ценят тебя за то, что ты советуешь только нужное и никогда не предлагаешь то, "
          "что у клиента заведомо есть. Отвечай строго в формате JSON.")
RULES = """У клиента уже есть товар А. Система хочет посоветовать ему товар Б. Вынеси вердикт по каждой паре — одна буква.

N — нужно. Прямая связь: Б работает вместе с А или поверх А, защищает либо обслуживает именно А, расширяет его возможности или это следующий шаг владельца А. И при этом у владельца А его вполне может не быть. Сюда же: старшая редакция или новая версия того же продукта, если она добавляет возможности; техподдержка и расширение лицензии к своему продукту.
K — комплект. Прямой связи нет, но Б входит в тот же типовой набор (рабочее место, сервер, защита организации), и владелец А мог его ещё не купить. Пример: к офисному пакету — антивирус.
H — уже есть. У владельца А это почти наверняка есть: без Б товар А не работал бы или не имел смысла. Примеры: к антивирусу — операционная система; к дополнительному модулю — базовая программа; к программе — компьютер под неё.
S — то же самое. Б решает ту же задачу, что А: конкурент, аналог, младшая или равная редакция, предыдущая версия, тот же продукт в другой упаковке. Это замена, а не дополнение. Пример: к Kaspersky — Dr.Web.
U — не связано. Прямой связи нет, Б просто часто покупают. Пример: к КриптоПро CSP — Microsoft Office.

Как решать спорное:
1. Направление важно: А уже есть, Б предлагаем. «Операционная система → антивирус» — N, «антивирус → операционная система» — H.
2. Сомневаешься между N и K — ставь K. Между K и U — ставь U. Лучше не посоветовать, чем посоветовать лишнее.
3. Несовместимое не советуем: если Б не работает с А или на его платформе — U.
4. Дистрибутив, документация, техподдержка, продление, расширение лицензии имеют смысл только к своему продукту; к чужому — U.
5. Средства защиты информации дополняют друг друга, только когда закрывают разные задачи; два продукта одной задачи — S.
6. Товары для дома и для организаций не смешивай, если только их не берут и те и другие.
7. Не знаешь товар и по названию понять нельзя — U.

Причина (поле r) — только для N и K: одна фраза клиенту по-русски, до 110 знаков, без рекламных слов и восклицаний, без точки в конце. Что именно Б даёт владельцу А — конкретно, чтобы фраза не подошла к любой другой паре. Для K начинай с условия: «Если ещё нет …: …». Для H, S, U — пустая строка."""
WHAT = {
    "class": "А и Б — классы каталога магазина. Суди по типичному товару класса; в скобках — примеры товаров.",
    "family": "А и Б — товары каталога магазина; в скобках — класс каталога.",
    "line": "А и Б — позиции прайс-листа; через « | » дано семейство и раздел каталога.",
}
ASK = """{rules}

{what}

Пары:
{pairs}

Перед вердиктом ответь по каждой паре на два вопроса:
w — работает ли А и имеет ли смысл без Б: "да" или "нет". Если "нет" (Б — основа, без которой А не купили бы), вердикт только H.
s — решают ли А и Б одну и ту же задачу: "да" или "нет". Если "да", вердикт только S.

Ответ — JSON: {{"pairs": [{{"i": 1, "w": "да", "s": "нет", "v": "N", "r": "…"}}]}} — по одному объекту на каждую пару, в том же порядке."""

LINE_ROW = re.compile(r"^\s*(\d+)\s+([NKHSU])(?:\s+(.*))?$")
PAIR_ROW = re.compile(r"^\s*(\S+)\s+(\S+)\s+([NKHSU])(?:\s+(.*))?$")


@dataclass(frozen=True)
class Verdict:
    verdict: str            # need / kit / has / same / unrelated
    reason: str = ""        # фраза клиенту — для need и kit; для остальных — пометка менеджеру или пусто
    source: str = FIRST     # «ассистент» или «api:<модель>»
    audience: str = ""      # b2b / b2c / пусто — всем

    @property
    def shown(self) -> bool:
        return self.verdict in SHOWN


@dataclass(frozen=True)
class Judged:
    verdict: str            # итог по всем покупкам клиента; пусто — вердикта нет
    reason: str = ""
    by: str = ""            # покупка клиента, которая дала итог
    source: str = ""


def clean(reason) -> str:
    r = " ".join(str(reason or "").split()).strip().rstrip(".").strip()
    return r[:REASON_MAX]


# ---- таблицы ---------------------------------------------------------------------------------

def read(path: Path) -> dict[tuple[str, str], Verdict]:
    out: dict[tuple[str, str], Verdict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[(r["a"], r["b"])] = Verdict(r["verdict"], r.get("reason", ""), r.get("source") or FIRST,
                                            r.get("audience", ""))
    return out


def write(path: Path, table: dict[tuple[str, str], Verdict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["a", "b", "verdict", "reason", "audience", "source"])
        for (a, b), v in sorted(table.items()):
            w.writerow([a, b, v.verdict, v.reason, v.audience, v.source])


class Verdicts:
    """Таблица вердиктов одного уровня и правило, как свести вердикты по всем покупкам клиента."""

    def __init__(self, table: dict[tuple[str, str], Verdict]):
        self.table = table
        self.missing: Counter = Counter()        # пары, которые спросили, а вердикта нет: очередь на разметку

    @classmethod
    def load(cls, level: str, pending: bool = False) -> "Verdicts":
        """pending=True — вместе с вердиктами моделей, которые ещё не прошли сверку (вид менеджера)."""
        table = read({"class": CLASS_FILE, "family": FAMILY_FILE, "line": LINE_FILE}[level])
        if not pending:
            ok = trusted_models()
            table = {p: v for p, v in table.items()
                     if v.source == FIRST or not v.shown or v.source.removeprefix("api:") in ok}
        return cls(table)

    def get(self, a: str, b: str) -> Verdict | None:
        return self.table.get((a, b))

    def judge(self, owned: Iterable[str], cand: str, audience: str = "") -> Judged:
        """Итог по кандидату для клиента с покупками owned (свежие — первыми).

        «Уже есть» или «то же самое» хотя бы по одной покупке убирает кандидата; иначе хватает одного
        «нужно», затем одного «комплект»; если по всем покупкам «не связано» — не советуем. Пара без
        вердикта не советуется и попадает в очередь на разметку.
        """
        found: list[tuple[str, Verdict]] = []
        unknown = False
        for a in owned:
            if a == cand:
                continue
            v = self.table.get((a, cand))
            if v is None:
                self.missing[(a, cand)] += 1
                unknown = True
            elif not v.audience or not audience or v.audience == audience:
                found.append((a, v))
        for kind in ("has", "same", "need", "kit"):
            for a, v in found:
                if v.verdict == kind:
                    return Judged(kind, v.reason, a, v.source)
        if unknown:
            return Judged("")
        return Judged("unrelated") if found else Judged("")


def trusted_models() -> set[str]:
    """Модели, чьи вердикты по API показываются клиенту без менеджера: прошли сверку с первой разметкой."""
    if not REPORT.exists():
        return set()
    runs = json.loads(REPORT.read_text(encoding="utf-8")).get("runs", {})
    return {m for m, r in runs.items() if r.get("trusted")}


def as_class_links(table: dict[tuple[str, str], Verdict]) -> list[dict]:
    """Вердикты «нужно» и «комплект» в виде связей классов для графа открытого каталога."""
    return [{"src_class": a, "dst_class": b, "type": "complements", "audience": v.audience or "any",
             "confidence": CONFIDENCE[v.verdict], "reason": v.reason, "verdict": v.verdict,
             "source": "разметка" if v.source == FIRST else "разметка по API"}
            for (a, b), v in sorted(table.items()) if v.shown]


def as_family_edges(table: dict[tuple[str, str], Verdict], same_line=None) -> list[dict]:
    """Вердикты «нужно» и «комплект» по парам семейств каталога в виде связей графа.

    same_line(a, b) — товары одной линейки: «нужно» между ними — это следующая ступень (старшая редакция),
    а не дополнение.
    """
    ok = trusted_models()
    out = []
    for (a, b), v in sorted(table.items()):
        if not v.shown or not (v.source == FIRST or v.source.removeprefix("api:") in ok):
            continue
        step = v.verdict == "need" and same_line is not None and same_line(a, b)
        out.append({"src": a, "dst": b, "type": "upgrade" if step else "complements",
                    "confidence": CONFIDENCE[v.verdict], "evidence": v.reason,
                    "source": "разметка: нужно" if v.verdict == "need" else "разметка: комплект"})
    return out


def class_picks(table: dict[tuple[str, str], Verdict], nodes: dict[str, dict]) -> dict[str, list[str]]:
    """«класс А|класс Б» -> товары класса Б, которые подошли хотя бы одному проверенному товару класса А.

    Связь классов знает только «к такому классу нужен такой класс»; какой именно товар подойдёт, она не знает.
    Первый по витрине бывает не тем: антивирус только для Windows — владельцу Linux, корпоративная система —
    домашнему пользователю. Поэтому товар по связи классов подбирается из тех, что прошли проверку пары
    с ходовым товаром класса А. Ключ с пустым списком значит: товары класса Б судили, ни один не подошёл.
    """
    picks: dict[str, set[str]] = defaultdict(set)
    for (a, b), v in table.items():
        ca, cb = (nodes.get(a) or {}).get("class"), (nodes.get(b) or {}).get("class")
        if not ca or not cb or ca == cb:
            continue
        key = f"{ca}|{cb}"
        picks[key] |= {b} if v.shown else set()
    return {k: sorted(v) for k, v in sorted(picks.items())}


def class_blocks(table: dict[tuple[str, str], Verdict], nodes: dict[str, dict]) -> list[list[str]]:
    """Пары «товар — класс», где связь классов для этого товара не работает.

    Если по товару А проверены не меньше двух первых товаров класса и ни один не подошёл, брать следующий
    по витрине вслепую нельзя: совет из этого класса владельцу А не показываем вовсе.
    """
    seen: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for (a, b), v in table.items():
        cls = (nodes.get(b) or {}).get("class")
        if a in nodes and cls and cls != nodes[a].get("class"):
            seen[(a, cls)].append(v.shown)
    return sorted([a, cls] for (a, cls), flags in seen.items() if len(flags) >= 2 and not any(flags))


# ---- первая разметка: собрать пачки в таблицы --------------------------------------------------

def _rows(path: Path) -> list[str]:
    return [x for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]


def _numbered(folder: Path, ids_name: str, pattern: str) -> tuple[dict[tuple[str, str], Verdict], Counter, int]:
    """Пачки вида «номер, буква, причина»; номера расшифровывает файл ids_name."""
    ids = {int(k): tuple(v) for k, v in json.loads((folder / ids_name).read_text(encoding="utf-8")).items()}
    got: dict[int, Verdict] = {}
    bad: Counter = Counter()
    for f in sorted(folder.glob(pattern)):
        for raw in _rows(f):
            parts = raw.split("\t")
            m = LINE_ROW.match(raw)
            if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip() in CODES:
                k, code, reason = int(parts[0]), parts[1].strip(), parts[2] if len(parts) > 2 else ""
            elif m:
                k, code, reason = int(m.group(1)), m.group(2), m.group(3) or ""
            else:
                bad["строка не разобрана"] += 1
                continue
            if k not in ids:
                bad["лишний номер"] += 1
                continue
            if k in got:
                bad["номер повторён"] += 1
            kind = CODES[code]
            if kind in SHOWN and not clean(reason):
                bad["нет причины у N или K"] += 1
                continue
            got[k] = Verdict(kind, clean(reason) if kind in SHOWN else "")
    left = sorted(set(ids) - set(got))
    (folder / ("left_" + ids_name)).write_text(json.dumps(left), encoding="utf-8")
    return {ids[k]: v for k, v in got.items()}, bad, len(ids)


def _fixes(path: Path, table: dict[tuple[str, str], Verdict]) -> int:
    """Правки после просмотра разметки: «ключ А, ключ Б, буква, причина»."""
    n = 0
    if path.exists():
        for raw in _rows(path):
            a, b, code, *rest = raw.split("\t")
            if (a, b) in table and code in CODES:
                table[(a, b)] = Verdict(CODES[code], clean(rest[0]) if rest and CODES[code] in SHOWN else "")
                n += 1
    return n


def merge() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    # линейки из продаж (закрыто) и семейства открытого каталога: нумерованные пачки
    for title, folder, ids_name, pattern, path in (
            ("линейки из продаж", WORK, "ids.json", "out_*.tsv", LINE_FILE),
            ("семейства каталога", OPEN_WORK, "fam_ids.json", "fout_*.tsv", FAMILY_FILE)):
        if not (folder / ids_name).exists():
            continue
        got, bad, total = _numbered(folder, ids_name, pattern)
        table = {p: v for p, v in read(path).items() if v.source != FIRST}
        table.update(got)
        fixed = _fixes(folder / "fixes.tsv", table)
        write(path, table)
        print(f"{title}: размечено {len(got)} из {total}; правок после просмотра {fixed}; замечания: {dict(bad)}")
        print("  по вердиктам:", dict(Counter(RU[v.verdict] for v in table.values())))
    # классы каталога: «ключ А, ключ Б, буква, причина, кому»
    files = sorted(OPEN_WORK.glob("class_out_*.tsv"))
    if files:
        known = set(json.loads((INTERIM / "class_names.json").read_text(encoding="utf-8")))
        table = {p: v for p, v in read(CLASS_FILE).items() if v.source != FIRST}
        bad = Counter()
        fresh: dict[tuple[str, str], Verdict] = {}
        for f in files:
            for raw in _rows(f):
                parts = [x.strip() for x in raw.split("\t")]
                m = PAIR_ROW.match(raw)
                if len(parts) >= 3 and parts[2] in CODES:
                    a, b, code = parts[0], parts[1], parts[2]
                    reason, aud = (parts[3] if len(parts) > 3 else ""), (parts[4] if len(parts) > 4 else "")
                elif m:
                    a, b, code, reason, aud = m.group(1), m.group(2), m.group(3), m.group(4) or "", ""
                else:
                    bad["строка не разобрана"] += 1
                    continue
                if a not in known or b not in known:
                    bad["неизвестный ключ"] += 1
                    continue
                if a == b:
                    bad["Б равно А"] += 1
                    continue
                kind = CODES[code]
                if kind in SHOWN and not clean(reason):
                    bad["нет причины у N или K"] += 1
                    continue
                fresh[(a, b)] = Verdict(kind, clean(reason), FIRST, aud if aud in ("b2b", "b2c") else "")
        table.update(fresh)
        fixed = _fixes(OPEN_WORK / "class_fixes.tsv", table)
        write(CLASS_FILE, table)
        srcs = {a for (a, _), v in table.items() if v.shown}
        print(f"классы: строк {len(fresh)}, источников с советом {len(srcs)}; правок {fixed}; замечания: {dict(bad)}")
        print("  по вердиктам:", dict(Counter(RU[v.verdict] for v in table.values())))


# ---- путь через API ---------------------------------------------------------------------------

def describer(level: str) -> Callable[[str], str]:
    """Как назвать сторону пары в запросе к модели."""
    if level == "line":
        info = json.loads((WORK / "line_info.json").read_text(encoding="utf-8"))
        return lambda k: info[k]["name"] + (f" | {info[k]['cat']}" if info[k].get("cat") else "") if k in info else k
    names = json.loads((INTERIM / "class_names.json").read_text(encoding="utf-8"))
    nodes = {}
    with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            nodes[r["family"]] = r
    if level == "family":
        return lambda k: f"{nodes[k]['name']} ({nodes[k].get('class_name') or names.get(nodes[k].get('class', ''), '')})" \
            if k in nodes else k
    rank = json.loads((INTERIM / "class_rank.json").read_text(encoding="utf-8"))

    def one(k: str) -> str:
        ex = [nodes[f]["name"][:48] for f in rank.get(k, []) if f in nodes and nodes[f]["on_sale"] == "1"][:3]
        return f"{names.get(k, k)} ({'; '.join(ex)})" if ex else names.get(k, k)
    return one


def ask(llm, level: str, pairs: list[tuple[str, str]], describe: Callable[[str], str], batch: int = 40,
        progress: bool = False) -> dict[tuple[str, str], Verdict]:
    """Вердикты модели по API: пары группируются по товару А, по batch пар в одном запросе."""
    groups: dict[str, list[str]] = defaultdict(list)
    for a, b in pairs:
        groups[a].append(b)
    chunks: list[list[tuple[str, list[str]]]] = [[]]
    size = 0
    for a, bs in groups.items():
        for i in range(0, len(bs), batch):
            part = bs[i:i + batch]
            if size and size + len(part) > batch:
                chunks.append([])
                size = 0
            chunks[-1].append((a, part))
            size += len(part)
    out: dict[tuple[str, str], Verdict] = {}
    for n, chunk in enumerate(chunks, 1):
        index: list[tuple[str, str]] = []
        lines = []
        for a, bs in chunk:
            lines.append(f"А: {describe(a)}")
            for b in bs:
                index.append((a, b))
                lines.append(f"  {len(index)}. Б: {describe(b)}")
        if not index:
            continue
        data = llm.json(SYSTEM, ASK.format(rules=RULES, what=WHAT[level], pairs="\n".join(lines)),
                        purpose=f"вердикты пар: {level}")
        rows = data.get("pairs") if isinstance(data, dict) else data
        for x in rows or []:
            try:
                i, code = int(x.get("i")), str(x.get("v", "")).strip().upper()[:1]
            except (TypeError, ValueError, AttributeError):
                continue
            if 1 <= i <= len(index) and code in CODES:
                kind = CODES[code]
                # ответы на проверочные вопросы сильнее вердикта: «без Б не работает» — уже есть, «та же задача» — то же самое
                if str(x.get("w", "")).strip().lower().startswith("н"):
                    kind = "has"
                elif str(x.get("s", "")).strip().lower().startswith("д"):
                    kind = "same"
                reason = clean(x.get("r", "")) if kind in SHOWN else ""
                if kind in SHOWN and not reason:
                    continue                             # совет без «зачем» не принимаем
                out[index[i - 1]] = Verdict(kind, reason, f"api:{llm.model}")
        if progress:
            print(f"  запрос {n} из {len(chunks)}: пар {len(index)}, всего вердиктов {len(out)}", flush=True)
    return out


def label(level: str, limit: int, private: bool) -> None:
    from recsys.llm.client import LLM

    sys.stdout.reconfigure(encoding="utf-8")
    if level == "line" and not private:
        sys.exit("Линейки из продаж — данные организаторов. Через API их можно размечать только там, где сервис "
                 "не обучается на переданных данных; тогда добавьте флаг --private.")
    path = {"class": CLASS_FILE, "family": FAMILY_FILE, "line": LINE_FILE}[level]
    table = read(path)
    todo_file = TODO[level]
    if not todo_file.exists():
        sys.exit(f"Очереди пар без вердикта нет: {todo_file}")
    todo = [tuple(p[:2]) for p in json.loads(todo_file.read_text(encoding="utf-8")) if tuple(p[:2]) not in table]
    todo = todo[:limit] if limit else todo
    print(f"пар без вердикта: {len(todo)}")
    if not todo:
        return
    llm = LLM()
    got = ask(llm, level, todo, describer(level), progress=True)
    table.update(got)
    write(path, table)
    print(f"размечено по API: {len(got)} из {len(todo)}; модель {llm.model}; вердикты:",
          dict(Counter(RU[v.verdict] for v in got.values())))


def check(n: int, seed: int, level: str = "class") -> None:
    """Сверка модели по API с первой разметкой: согласие по вердикту и по решению «советовать / не советовать»."""
    from recsys.llm.client import LLM

    sys.stdout.reconfigure(encoding="utf-8")
    table = read({"class": CLASS_FILE, "family": FAMILY_FILE}[level])
    mine = {p: v for p, v in table.items() if v.source == FIRST}
    by: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p, v in sorted(mine.items()):
        by[v.verdict].append(p)
    rnd = random.Random(seed)
    sample: list[tuple[str, str]] = []
    for kind in CODES.values():
        rnd.shuffle(by[kind])
        sample += by[kind][:n]
    # Отвергнутые пары первой разметки — те, что когда-то предложила облегчённая модель: для неё это заведомо
    # трудная выборка. Поэтому добавляем столько же случайных пар, которых в карте нет вовсе: их ждём «не советовать».
    keys = sorted({a for a, _ in mine} | {b for _, b in mine})
    srcs = sorted({a for a, v in ((p[0], v) for p, v in mine.items()) if v.shown})
    implicit: list[tuple[str, str]] = []
    while srcs and len(implicit) < n:
        p = (rnd.choice(srcs), rnd.choice(keys))
        if p[0] != p[1] and p not in table and p not in implicit:
            implicit.append(p)
    sample += implicit
    llm = LLM()
    describe = describer(level)
    got = ask(llm, level, sample, describe, progress=True)
    matrix: dict[str, Counter] = defaultdict(Counter)
    same = show_same = 0
    tp = fp = fn = 0
    easy_n = easy_ok = 0
    risky, lost = [], []
    for p in sample:
        if p not in got:
            continue
        m, a = (mine[p].verdict if p in mine else "unrelated"), got[p].verdict
        if p not in mine:
            easy_n += 1
            easy_ok += a not in SHOWN
        matrix[m][a] += 1
        same += m == a
        show_same += (m in SHOWN) == (a in SHOWN)
        tp += m in SHOWN and a in SHOWN
        fp += m not in SHOWN and a in SHOWN
        fn += m in SHOWN and a not in SHOWN
        row = {"a": describe(p[0]), "b": describe(p[1]), "assistant": RU[m], "api": RU[a], "api_reason": got[p].reason}
        if m not in SHOWN and a in SHOWN:
            risky.append(row)
        elif m in SHOWN and a not in SHOWN:
            lost.append(row)
    done = sum(sum(c.values()) for c in matrix.values())
    report = {
        "level": level, "model": llm.model, "pairs": done, "asked": len(sample),
        "same_verdict": round(same / done, 3) if done else None,
        "same_decision": round(show_same / done, 3) if done else None,
        "api_would_show": {"agrees_with_assistant": tp, "assistant_says_no": fp,
                           "precision": round(tp / (tp + fp), 3) if tp + fp else None},
        "assistant_would_show": {"api_agrees": tp, "api_says_no": fn,
                                 "recall": round(tp / (tp + fn), 3) if tp + fn else None},
        "matrix_assistant_rows_api_columns": {RU[m]: {RU[a]: c for a, c in row.items()} for m, row in matrix.items()},
        "api_shows_what_assistant_rejects": risky[:40], "api_rejects_what_assistant_shows": lost[:40],
        "random_pairs_outside_the_map": {"pairs": easy_n, "api_also_rejects": easy_ok},
        "note": "Отвергнутые пары выборки когда-то предложила облегчённая модель, поэтому для слабых моделей выборка "
                "заведомо трудная; случайные пары вне карты добавлены для равновесия и ожидаются как «не советовать».",
        "tokens": getattr(llm, "spent", None),
    }
    report["trusted"] = bool(done and report["same_decision"] >= TRUST
                             and (report["api_would_show"]["precision"] or 0) >= TRUST)
    runs = json.loads(REPORT.read_text(encoding="utf-8")).get("runs", {}) if REPORT.exists() else {}
    runs[llm.model] = report
    REPORT.write_text(json.dumps({"trust_threshold": TRUST, "runs": runs}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"модель {llm.model}: пар {done}; тот же вердикт {report['same_verdict']}, "
          f"то же решение «советовать или нет» {report['same_decision']}")
    print(f"  из того, что модель по API показала бы клиенту, первая разметка согласна в {tp} случаях из {tp + fp}")
    print(f"  из того, что показывает первая разметка, модель по API оставила бы {tp} из {tp + fn}")
    print(f"отчёт: {REPORT}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Вердикты по парам «есть А — советовать ли Б»")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("merge")
    p = sub.add_parser("label")
    p.add_argument("--level", choices=["class", "family", "line"], default="class")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--private", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--level", choices=["class", "family"], default="class")
    p.add_argument("-n", type=int, default=30, help="пар каждого вердикта в выборке")
    p.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    if a.cmd == "merge":
        merge()
    elif a.cmd == "label":
        label(a.level, a.limit, a.private)
    else:
        check(a.n, a.seed, a.level)


if __name__ == "__main__":
    main()
