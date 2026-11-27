"""Агент «закрыть дыру»: обоснованный совет для товара, к которому блок кабинета ничего не советует.

Дыра — товар в продаже, владелец которого не получает совета с собственным основанием. Агент получает товар
каталога и то, что по нему уже рассужено (вердикты пар), сам ищет связи инструментами — каталог, страница
товара, характеристики из фида, линейка, дополнения «для X», спутники класса — и предлагает до трёх связей
с видом и фразой «зачем». Критик проверяет каждую по цитате: дословный фрагмент описания или характеристик
товара А или Б. Код проверяет, что цитата действительно есть в тексте, — выдуманную цитату не пропустит.

Связь с проверенной цитатой идёт в граф (knowledge/agent_links.csv, status=graph): клиент видит её сразу,
менеджер может отклонить. Без цитаты — в очередь менеджера (status=queue). «Обоснованной связи нет» — тоже
результат: агент объясняет почему, дыра помечается разобранной (reports/agent_holes.json).

Данных клиентов агент не видит: в запросы идут только названия, описания и характеристики из открытого
каталога. Дыры из линеек продаж без товара каталога по API не отправляются без флага --private.

Запуск:
  python -m recsys.agent.holes --family vendor/slug [--family ...]
  python -m recsys.agent.holes --holes data/private/task/holes.json --top 20
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from recsys.agent.tools import TOOLS_HELP, Catalog
from recsys.config import INTERIM
from recsys.llm.pair_verdicts import FAMILY_FILE, LINE_FILE, RULES, RU, read

ROOT = Path(__file__).resolve().parents[2]
LINKS_FILE = ROOT / "knowledge" / "agent_links.csv"
REPORT = ROOT / "reports" / "agent_holes.json"
FIELDS = ["src", "dst", "type", "source", "confidence", "evidence", "quote", "status", "model", "date"]
MAX_STEPS = 3                 # запросов-шагов к модели до итога
MAX_CALLS = 4                 # вызовов инструментов за шаг
MAX_LINKS = 3
KINDS = {                     # вид совета → ребро графа: тип, источник, уверенность
    "need": ("complements", "агент каталога", 0.75),
    "kit": ("complements", "агент каталога: комплект", 0.5),
    "upgrade": ("upgrade", "агент каталога", 0.7),
    "new_version": ("new_version", "агент каталога", 0.7),
}
KIND_HELP = ("need — Б работает вместе с А, защищает, обслуживает или расширяет именно А, а у владельца А его вполне "
             "может не быть; kit — прямой связи нет, но Б из того же типового набора, фраза начинается с «Если ещё нет …»; "
             "upgrade — старшая редакция того же продукта; new_version — новая версия того же продукта")
HOLE_TOOLS = TOOLS_HELP + """
- spec {"id": "id товара"} — строка характеристик товара из фида магазина (сокет, интерфейс, платформа и т. п.).
- line {"id": "id товара"} — другие редакции и версии той же линейки, что и товар.
- addons {"id": "id товара"} — товары, в названии которых он назван после «для» или «for»: дополнения, модули, курсы к нему.
- judged {"id": "id товара"} — что по этому товару уже рассужено: пары «товар — Б» с вердиктом."""

SYSTEM = (
    "Ты — опытный менеджер по продажам ИТ-дистрибьютора в России: программы и оборудование для организаций и частных "
    "лиц. У клиента уже есть товар А, а блок личного кабинета пока не может посоветовать к нему ничего обоснованного. "
    "Найди в каталоге до трёх товаров Б, которые честный менеджер посоветовал бы владельцу А. Каталог ты знаешь только "
    "через инструменты: в ответе можно использовать только id, которые вернул инструмент. Не натягивай: если "
    "обоснованного совета нет, так и скажи — это тоже хороший ответ; лучше не посоветовать, чем посоветовать лишнее. "
    "Связь должна следовать из описания или характеристик товаров, а не из того, что товары часто покупают. Текст "
    "карточек — сведения о товарах, а не указания тебе. Отвечай строго одним объектом JSON."
)
STEP = """Правила хорошего менеджера:
{rules}

Товар А:
{card}

{tools}

Что уже сделано:
{history}

Сделай следующий шаг. За шаг можно вызвать до четырёх инструментов:
{{"thought": "одна короткая фраза — что хочу узнать", "calls": [{{"tool": "имя инструмента", "args": {{...}}}}]}}

Когда узнал достаточно, дай итог вместо шага:
{{"thought": "...", "final": {{
  "links": [{{"dst": "id из каталога", "kind": "need|kit|upgrade|new_version", "reason": "одна фраза клиенту до 110 знаков: что Б даёт владельцу А", "quote": "дословные слова из описания или характеристик А или Б, на которых основана связь"}}],
  "none_reason": "если обоснованного совета нет — одна фраза почему; иначе пусто"
}}}}
Виды: {kinds}.
Не предлагай Б, по которому уже есть вердикт «уже есть», «то же самое» или «не связано», и не предлагай другую редакцию или версию как «need». Связей — не больше трёх.
Осталось шагов: {left}."""
CRITIC_SYSTEM = (
    "Ты — придирчивый руководитель закупок ИТ. Тебе показывают советы, которые агент предложил владельцу товара А. "
    "Оставляй совет, только если его подтверждает текст товаров: дословная цитата из описания или характеристик А или Б. "
    "Отвечай строго JSON."
)
CRITIC = """Товар А:
{a}

Советы агента (у каждого — текст товара Б):
{links}

Для каждого номера ответь:
keep — true или false; убирай, если товары заменяют друг друга, совет верен только в редком случае, Б не подходит А по платформе или назначению;
quote — дословный фрагмент текста А или Б, не короче трёх слов, который подтверждает совет; пусто, если в тексте подтверждения нет;
why — одна короткая фраза.
Ответ: {{"verdicts": [{{"i": 0, "keep": true, "quote": "...", "why": "..."}}]}}"""


def norm(s: str) -> str:
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'“”„]", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,;:")


class HoleTools(Catalog):
    """Инструменты агента каталога плюс то, что нужно для существующего товара."""

    def __init__(self) -> None:
        super().__init__()
        from recsys.graph.spec_links import load_specs
        self.specs = load_specs()
        self.verdicts = read(FAMILY_FILE)
        self.by_a: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for (a, b), v in self.verdicts.items():
            self.by_a[a].append((b, v.verdict))
        # закрытая таблица вердиктов линеек: ключ линейки у товаров, привязанных к каталогу, совпадает с семейством.
        # Её вердикт главнее связи агента, поэтому пары «уже есть / то же самое / не связано» агент не предлагает.
        # В запросы к модели она не идёт: только фильтр в коде.
        self.line_veto: dict[str, set[str]] = defaultdict(set)
        if LINE_FILE.exists():
            for (a, b), _ in read(LINE_FILE).items():          # любая уже размеченная пара: повторять её незачем
                self.line_veto[a].add(b)
        self.line: dict[str, set[str]] = defaultdict(set)
        with (INTERIM / "graph_edges.csv").open(encoding="utf-8") as f:
            for e in csv.DictReader(f):
                if e["type"] in ("same_line", "upgrade", "new_version"):
                    self.line[e["src"]].add(e["dst"])
                    self.line[e["dst"]].add(e["src"])
        self._rec = None

    def advised_now(self, fam: str) -> list[dict]:
        """Что блок кабинета уже советует владельцу одного этого товара — тем же движком, что на стенде."""
        from datetime import date, timedelta

        from recsys.recommend.engine import Client, Graph, Purchase, Recommender
        if self._rec is None:
            self._rec = Recommender(Graph.load())
        n = self.nodes.get(fam, {})
        home = (n.get("client_types") == "b2c" or "для дома" in n.get("class_name", "").lower()) and n.get("b2b_only") != "1"
        today = date.today()
        recs = self._rec.recommend(Client("b2c" if home else "b2b", [Purchase(fam, today - timedelta(days=183))], today=today), k=3)
        return [{"id": r.family, "name": r.name[:70], "kind": "kit" if r.slot == "kit" else r.slot}
                for r in recs if not r.fallback and r.slot != "renew"]

    def text(self, k: str, limit: int = 1200) -> str:
        """Всё, что о товаре известно из открытого каталога: название, класс, описание, характеристики."""
        n = self.nodes.get(k, {})
        d = (self.desc.get(k) or {}).get("description") or ""
        spec = self.specs.get(k, "")
        return " ".join(x for x in (n.get("name", ""), n.get("class_name", ""), d[:limit], spec[:600]) if x)

    def spec(self, id: str) -> dict:
        if id not in self.nodes:
            return {"error": "такого id нет в каталоге"}
        return {"id": id, "name": self.nodes[id]["name"][:90], "spec": self.specs.get(id, "")[:600] or "характеристик в фиде нет"}

    def line_of(self, id: str) -> list[dict]:
        out = [self.brief(k) | {"edition": self.nodes[k].get("edition", ""), "version": self.nodes[k].get("version", "")}
               for k in sorted(self.line.get(id, ())) if k in self.nodes][:12]
        return out or [{"note": "других редакций и версий этой линейки в каталоге нет"}]

    def addons(self, id: str) -> list[dict]:
        n = self.nodes.get(id)
        if not n:
            return [{"error": "такого id нет в каталоге"}]
        # опорные слова — самые редкие в каталоге слова названия: у «Платформа nanoCAD 26» это nanoCAD
        words = sorted({w for w in re.findall(r"[\wЁё+-]{3,}", n["name"]) if not w.isdigit()},
                       key=lambda w: -self.idf.get(w.lower(), 0.0))[:2]
        if not words:
            return [{"note": "у названия нет опорных слов"}]
        pat = re.compile(r"(?:для|for)\s+[^,;()]*(?:" + "|".join(re.escape(w) for w in words) + ")", re.I)
        out = [self.brief(k) for k, m in self.nodes.items()
               if k != id and pat.search(m.get("name", "")) and m.get("on_sale") == "1"][:10]
        return out or [{"note": f"товаров «для {' / '.join(words)}» в каталоге нет"}]

    def judged(self, id: str) -> dict:
        rows = self.by_a.get(id, [])
        return {"verdicts": dict(Counter(RU[v] for _, v in rows)),
                "pairs": [{"id": b, "name": self.nodes.get(b, {}).get("name", b)[:60], "verdict": RU[v]} for b, v in rows[:25]]}

    def call(self, tool: str, args: dict) -> object:
        try:
            if tool == "spec":
                return self.spec(str(args.get("id", "")))
            if tool == "line":
                return self.line_of(str(args.get("id", "")))
            if tool == "addons":
                return self.addons(str(args.get("id", "")))
            if tool == "judged":
                return self.judged(str(args.get("id", "")))
        except Exception as e:                                   # ошибка инструмента — тоже ответ агенту
            return {"error": f"{type(e).__name__}: {e}"}
        return super().call(tool, args)


def close_hole(llm, cat: HoleTools, fam: str, verbose: bool = False, persist: bool = True) -> dict:
    """Ищет до трёх обоснованных советов к товару fam. Возвращает исход, связи, журнал шагов и расход.

    persist=True — сразу дописывает результат в knowledge/agent_links.csv и reports/agent_holes.json: живой запуск
    со стенда и пакетный оставляют один и тот же след.
    """
    t0 = time.time()
    spent0 = list(getattr(llm, "spent", [0, 0, 0]))
    n = cat.nodes[fam]
    card = {"id": fam, "name": n["name"], "vendor": n.get("vendor", ""), "class": n.get("class_name", ""),
            "class_id": n.get("class", ""), "for": "организации" if n.get("b2b_only") == "1" else "организации и частные лица",
            "description": ((cat.desc.get(fam) or {}).get("description") or "")[:900], "spec": cat.specs.get(fam, "")[:400]}
    steps: list[dict] = []
    seen: set[str] = set()
    # что владельцу уже показывают: второй комплект там, где комплект есть, клиенту не виден (в блоке он один)
    now = cat.advised_now(fam)
    strong = [a for a in now if a["kind"] != "kit"]
    card["сейчас_советуем"] = [f"{a['name']} ({'комплект' if a['kind'] == 'kit' else 'совет по делу'})" for a in now] or "ничего"
    goal = ("У владельца А уже есть совет по делу. Ищи только то, чего в нём нет, и только need, upgrade или new_version." if strong else
            "Сейчас владельцу А показывается только комплект. Второй комплект клиент не увидит — ищи need, upgrade или new_version." if now else
            "Сейчас владельцу А не показывается ничего. Подойдёт и need, и честный комплект «Если ещё нет …».")

    def run(tool: str, args: dict, thought: str, ask: int) -> None:
        result = cat.call(tool, args)
        for r in result if isinstance(result, list) else [result]:
            if isinstance(r, dict):
                if r.get("id"):
                    seen.add(r["id"])
                for t in r.get("top", []) if isinstance(r.get("top"), list) else []:
                    seen.add(t.get("id", ""))
                for p in r.get("pairs", []) if isinstance(r.get("pairs"), list) else []:
                    seen.add(p.get("id", ""))
        steps.append({"tool": tool, "args": args, "thought": thought, "result": result, "ask": ask})
        if verbose:
            print(f"  {'код' if not ask else f'шаг {ask}'}: {tool} {json.dumps(args, ensure_ascii=False)[:80]}", flush=True)

    # без модели: то, с чего начал бы любой менеджер — уже рассуженное, линейка, дополнения, спутники класса
    for tool, args in (("judged", {"id": fam}), ("line", {"id": fam}), ("addons", {"id": fam}),
                       ("class_companions", {"class_id": n.get("class", "")})):
        run(tool, args, "без модели: исходные сведения", 0)
    final, asks, searched, nudged = None, 0, 0, False
    for _ in range(MAX_STEPS + 2):
        history = "\n".join(f"{i + 1}. {s['tool']} {json.dumps(s['args'], ensure_ascii=False)} -> "
                            f"{json.dumps(s['result'], ensure_ascii=False)[:1200]}" for i, s in enumerate(steps))
        left = MAX_STEPS - asks
        prompt = STEP.format(rules=RULES, card=json.dumps(card, ensure_ascii=False, indent=1), tools=HOLE_TOOLS,
                             history=history, kinds=KIND_HELP, left=left) + "\nЦель: " + goal
        if left <= 0:
            prompt += "\nШаги закончились: дай итог сейчас."
        ans = llm.json(SYSTEM, prompt, purpose="агент: дыра, шаг")
        asks += 1
        if not isinstance(ans, dict):
            continue
        if "final" in ans and not searched and not nudged and left > 0:
            # итог без единого поиска не принимаем: облегчённые модели отвечают «связи нет», не заглянув в каталог
            nudged = True
            steps.append({"tool": "требование кода", "args": {}, "thought": "итог без поиска не принят",
                          "result": "Сначала поищи сам: вызови хотя бы один инструмент (search_catalog, addons, line, spec, "
                                    "get_product), потом дай итог.", "ask": asks})
            continue
        if "final" in ans or left <= 0:
            final = ans.get("final") if isinstance(ans.get("final"), dict) else {}
            steps.append({"tool": "итог", "args": {}, "thought": ans.get("thought", ""), "result": "советы", "ask": asks})
            break
        calls = ans.get("calls") if isinstance(ans.get("calls"), list) else []
        for c in calls[:MAX_CALLS]:
            if isinstance(c, dict) and c.get("tool"):
                run(str(c["tool"]), c.get("args") if isinstance(c.get("args"), dict) else {}, str(ans.get("thought", "")), asks)
                searched += 1
    final = final or {}

    # ---- правила в коде, потом критик по цитате
    vetoed = dict(cat.by_a.get(fam, []))                 # любая уже размеченная пара: повторять известное незачем
    advised = {a["id"] for a in now}
    proposed, dropped = [], []
    for link in (final.get("links") or [])[:6]:
        if not isinstance(link, dict):
            continue
        dst, kind = str(link.get("dst", "")), str(link.get("kind", ""))
        why = ("такого товара нет в каталоге" if dst not in cat.nodes else
               "это сам товар" if dst == fam else
               "агент не получал этот товар от инструментов" if dst not in seen else
               "товара нет в продаже" if cat.nodes[dst].get("on_sale") != "1" else
               "неизвестный вид совета" if kind not in KINDS else
               f"уже рассужено: «{RU[vetoed[dst]]}»" if dst in vetoed else
               "уже рассужено" if dst in cat.line_veto.get(fam, ()) else
               "это уже советуется владельцу" if dst in advised else
               "второй комплект клиент не увидит" if kind == "kit" and any(a["kind"] == "kit" for a in now) else
               "редакция или версия не из линейки товара" if kind in ("upgrade", "new_version") and dst not in cat.line.get(fam, ()) else
               "другая редакция той же линейки — это не «нужно»" if kind in ("need", "kit") and dst in cat.line.get(fam, ()) else "")
        reason = re.sub(r"\s+", " ", str(link.get("reason", ""))).strip().rstrip(".")[:160]
        if kind == "kit" and reason and not reason.lower().startswith("если"):
            reason = "Если ещё нет: " + reason[:1].lower() + reason[1:]       # комплект — всегда с условием
        rec = {"dst": dst, "kind": kind, "type": KINDS.get(kind, ("",))[0], "reason": reason, "evidence": reason,
               "quote_agent": str(link.get("quote", ""))[:200]}
        if why:
            dropped.append({**rec, "why": why})
        elif len(proposed) < MAX_LINKS:
            proposed.append({**rec, "dst_name": cat.nodes[dst]["name"]})
    kept = []
    if proposed:
        a_text = cat.text(fam)
        listing = "\n".join(f"{i}. [{l['kind']}] Б = «{l['dst_name']}». Довод: {l['reason']}\n   Текст Б: {cat.text(l['dst'], 700)}"
                            for i, l in enumerate(proposed))
        verdict = llm.json(CRITIC_SYSTEM, CRITIC.format(a=a_text, links=listing), purpose="агент: дыра, критик")
        vs = {int(v["i"]): v for v in (verdict.get("verdicts", []) if isinstance(verdict, dict) else [])
              if str(v.get("i", "")).isdigit()}
        for i, l in enumerate(proposed):
            v = vs.get(i) or {}
            quote = str(v.get("quote", "")).strip()
            q = norm(quote)
            found = len(q) >= 12 and len(q.split()) >= 3 and (q in norm(a_text) or q in norm(cat.text(l["dst"], 1200)))
            if not v.get("keep"):
                dropped.append({**l, "why": "критик: " + str(v.get("why", "нет вердикта"))[:160]})
            else:
                kept.append({**l, "quote": quote if found else "", "critic": str(v.get("why", ""))[:160],
                             "status": "graph" if found else "queue",
                             "quote_note": "" if found else ("цитаты в тексте нет" if quote else "критик не привёл цитату")})
    # «закрыта» — только если появилась связь сильнее комплекта: замер показа блока комплект обоснованным советом не считает
    graph_kinds = {k["kind"] for k in kept if k["status"] == "graph"}
    outcome = ("closed" if graph_kinds - {"kit"} else "kit_only" if graph_kinds else "queue" if kept else "no_grounded_link")
    explanation = "найден только комплект: обоснованного совета сильнее комплекта нет" if outcome == "kit_only" else ""
    if outcome == "no_grounded_link":
        explanation = str(final.get("none_reason") or "").strip() or "; ".join(d["why"] for d in dropped[:3]) or \
            "агент не нашёл в каталоге товара, который подходит именно к этому"
    spent = getattr(llm, "spent", [0, 0, 0])
    res = {"family": fam, "name": n["name"], "class": n.get("class_name", ""), "outcome": outcome,
           "explanation": explanation, "links": kept, "dropped": dropped, "steps": steps,
           "model": getattr(llm, "model", ""), "model_requests": asks + (1 if proposed else 0),
           "tokens": {"in": spent[0] - spent0[0], "out": spent[1] - spent0[1]}, "seconds": round(time.time() - t0, 1),
           "date": time.strftime("%Y-%m-%d %H:%M")}
    if persist:
        save([res])
    return res


def edges_of(res: dict) -> list[dict]:
    """Строки knowledge/agent_links.csv из результата: связь с цитатой — в граф, без цитаты — в очередь менеджера."""
    out = []
    for l in res["links"]:
        typ, source, conf = KINDS[l["kind"]]
        out.append({"src": res["family"], "dst": l["dst"], "type": typ, "source": source, "confidence": conf,
                    "evidence": l["reason"], "quote": l.get("quote", ""), "status": l["status"],
                    "model": res.get("model", ""), "date": time.strftime("%Y-%m-%d")})
    return out


def save(results: list[dict]) -> None:
    done = {r["family"] for r in results}
    rows = []
    if LINKS_FILE.exists():
        with LINKS_FILE.open(encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["src"] not in done]     # перезапуск дыры заменяет её строки
    for r in results:
        rows += edges_of(r)
    with LINKS_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {"holes": {}}
    for r in results:
        report["holes"][r["family"]] = r
    holes = list(report["holes"].values())
    report["summary"] = {
        "holes": len(holes), "by_outcome": dict(Counter(h["outcome"] for h in holes)),
        "links_to_graph": sum(l["status"] == "graph" for h in holes for l in h["links"]),
        "links_to_queue": sum(l["status"] == "queue" for h in holes for l in h["links"]),
        "links_dropped": sum(len(h["dropped"]) for h in holes),
        "tool_calls_by_model": sum(1 for h in holes for s in h["steps"] if s.get("ask")),
        "tokens_in": sum(h["tokens"]["in"] for h in holes), "tokens_out": sum(h["tokens"]["out"] for h in holes),
        "seconds": round(sum(h["seconds"] for h in holes), 1),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def families_from(path: Path, top: int, private: bool, cat: HoleTools) -> list[str]:
    """Дыры из очереди recsys.rank.holes по убыванию числа клиентов: только kind «закрывает агент» и только товары
    каталога (по ключу семейства, family_id фида или адресу). Линейку без товара каталога — только с --private."""
    data = json.loads(path.read_text(encoding="utf-8"))
    holes = data["holes"] if isinstance(data, dict) else data
    by_id = {r.get("family_id"): k for k, r in cat.nodes.items() if r.get("family_id")}
    out, skipped, renew = [], 0, 0
    for h in sorted(holes, key=lambda x: -(x.get("clients") or 0)):
        if h.get("kind") and h["kind"] != "закрывает агент":
            renew += 1                                   # «нужно продление, а не совет» — агенту закрывать нечего
            continue
        k = h.get("family") or ""
        k = k if k in cat.nodes else by_id.get(str(h.get("family_id") or ""), "")
        if not k and h.get("url"):
            k = h["url"].split("store.softline.ru/")[-1].strip("/")
        if k in cat.nodes and cat.nodes[k].get("on_sale") == "1":
            out.append(k)
        else:
            skipped += 1
        if len(out) >= top:
            break
    print(f"дыр к разбору: {len(out)}; пропущено: «нужно продление» {renew}, без товара каталога в продаже {skipped}"
          + ("" if private else " (названия из продаж по API без --private не отправляем)"))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--family", action="append", default=[], help="ключ товара каталога «вендор/семейство»")
    ap.add_argument("--holes", type=Path, help="очередь дыр, например data/private/task/holes.json")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    from recsys.llm.client import LLM

    cat = HoleTools()
    fams = list(args.family) + (families_from(args.holes, args.top, args.private, cat) if args.holes else [])
    fams = [f for f in dict.fromkeys(fams) if f in cat.nodes]
    if not fams:
        sys.exit("Нет товаров каталога для разбора: укажите --family или --holes.")
    llm, results = LLM(), []
    for fam in fams:
        print(f"дыра: {cat.nodes[fam]['name']}", flush=True)
        res = close_hole(llm, cat, fam, verbose=args.verbose, persist=False)
        results.append(res)
        for l in res["links"]:
            print(f"   {l['status']:5} [{l['kind']}] {l['dst_name'][:60]} — {l['reason'][:80]}" + (f" | цитата: «{l['quote'][:60]}»" if l.get("quote") else ""))
        if res["outcome"] == "no_grounded_link":
            print(f"   обоснованной связи нет: {res['explanation'][:140]}")
        print(f"   запросов {res['model_requests']}, токенов {res['tokens']['in']}/{res['tokens']['out']}, {res['seconds']} с", flush=True)
    save(results)
    print(f"записано: {LINKS_FILE.relative_to(ROOT)}, {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
