"""Агент каталога: ставит новый товар на его место в графе связей.

Вход — карточка товара, как её вводит контент-менеджер: название, вендор, описание.
Агент работает по шагам. На каждом шаге модель выбирает инструмент (поиск класса, поиск по каталогу,
карточка товара, спутники класса), код выполняет его и возвращает результат; так агент сам решает,
что ему ещё нужно узнать. В конце он выдаёт паспорт товара (класс, что делает, что нужно для работы,
для кого) и связи с товарами каталога — только с теми, которые вернул инструмент.

Затем отдельный запрос-критик проверяет каждую связь, а код отбрасывает связи с несуществующими
товарами. Журнал шагов сохраняется: по нему видно, как агент пришёл к ответу и сколько это стоило.

Данные клиентов агенту не передаются: он видит только открытый каталог магазина.

Запуск:  python -m recsys.agent.agent --id vendor/family      поставить заново товар из каталога
         python -m recsys.agent.agent --name "..." --vendor "..." --text "..."   новый товар
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

from recsys.agent.tools import TOOLS_HELP, Catalog
from recsys.config import INTERIM
from recsys.graph.textnorm import newer, split_edition, version_of

MAX_STEPS = 3            # шагов-запросов к модели до итога; за шаг — до четырёх вызовов инструментов
MAX_CALLS = 4
SURE = 0.7               # при такой уверенности соседей класс берём у них, без решения модели
LINK_TYPES = {
    "addon_for": "это дополнение или курс к товару dst",
    "requires": "для работы этого товара нужен dst",
    "complements": "покупателю этого товара стоит предложить dst",
    "upgrade_of": "это старшая редакция товара dst",
    "new_version_of": "это новая версия товара dst",
    "analog_of": "это российский аналог зарубежного товара dst",
}
SYSTEM = (
    "Ты — агент каталога интернет-магазина программ и оборудования. Тебе дают карточку товара, который "
    "только что завели в магазин. Твоя задача — поставить товар на его место в графе связей: определить "
    "его класс, понять, что он делает и что ему нужно для работы, и найти в каталоге товары, с которыми "
    "он связан. Каталог ты знаешь только через инструменты. Не выдумывай товары и коды: в ответе можно "
    "использовать только те id и class_id, которые вернул инструмент. Если связи нет — не придумывай её. "
    "Как искать связи: если в названии есть «for X» или «для X» — это дополнение, найди сам продукт X "
    "через search_catalog и свяжи addon_for; если товар — редакция или версия продукта, найди через "
    "search_catalog другие редакции и версии этого продукта и свяжи upgrade_of или new_version_of; "
    "товары-заменители дополнениями не называй. Класс обязателен: возьми class_id из ответа find_classes. "
    "Отвечай строго одним объектом JSON."
)
STEP = """Карточка нового товара:
{card}

{tools}

Что уже сделано:
{history}

Сделай следующий шаг. За один шаг можно вызвать сразу несколько инструментов (до четырёх) — так дешевле. Формат шага:
{{"thought": "одна короткая фраза — что хочу узнать", "calls": [{{"tool": "имя инструмента", "args": {{...}}}}]}}

Когда узнал достаточно (класс найден, связанные товары в каталоге поискал), дай итог вместо шага. В результатах поиска уже могут быть базовый продукт, другие редакции и версии — свяжи их, если это они:
{{"thought": "...", "final": {{
  "class_id": "код класса из find_classes",
  "does": "что делает товар — одна фраза простыми словами",
  "needs": ["что нужно для работы: платформа, базовый продукт и т. п.; пусто, если из карточки не видно"],
  "audience": "b2b, b2c или any",
  "links": [{{"dst": "id из каталога", "type": "{types}", "reason": "одна фраза для покупателя", "quote": "слова из карточки, на которых основана связь, или пусто"}}]
}}}}
Виды связей: {type_help}.
Осталось шагов: {left}. Связей — не больше шести, только уверенные."""
CRITIC_SYSTEM = (
    "Ты — придирчивый руководитель закупок ИТ. Тебе показывают связи, которые агент предложил для нового "
    "товара. Оставляй только те, что дал бы честный менеджер: связь должна следовать из карточки или из "
    "общеизвестного назначения товаров. Отвечай строго JSON."
)
CRITIC = """Новый товар:
{card}

Агент определил: {does}

Предложенные связи:
{links}

Для каждого номера: keep (true или false) и why — одна короткая фраза.
Убирай, если: товары заменяют друг друга, а связь названа дополнением; связь верна только в редком случае; довод не следует из карточки и не общеизвестен.
Ответ: {{"verdicts": [{{"i": 0, "keep": true, "why": "..."}}]}}"""


def to_edge(name: str, cat: Catalog, link: dict) -> dict:
    """Связь агента -> ребро графа. «new» — сам новый товар.

    Кто из двух товаров старше по редакции или версии, решает код по названиям: модель в этом
    направлении путается, а правило простое.
    """
    dst, typ = link["dst"], link["type"]
    dst_name = cat.nodes[dst]["name"]
    if typ in ("upgrade_of", "new_version_of"):
        _, r_new, _ = split_edition(name)
        _, r_dst, _ = split_edition(dst_name)
        v_new, v_dst = version_of(name), version_of(dst_name)
        if newer(v_dst, v_new):
            return {"src": dst, "dst": "new", "type": "new_version", "why": link["reason"]}
        if newer(v_new, v_dst):
            return {"src": "new", "dst": dst, "type": "new_version", "why": link["reason"]}
        if r_new is not None and r_dst is not None and r_new != r_dst:
            lo, hi = ("new", dst) if r_new < r_dst else (dst, "new")
            return {"src": lo, "dst": hi, "type": "upgrade", "why": link["reason"]}
        return {"src": dst, "dst": "new", "type": "same_line", "why": link["reason"]}
    if typ == "addon_for":
        return {"src": dst, "dst": "new", "type": "complements", "why": link["reason"]}
    if typ == "analog_of":
        return {"src": dst, "dst": "new", "type": "analog_ru", "why": link["reason"]}
    return {"src": "new", "dst": dst, "type": typ, "why": link["reason"]}      # requires, complements


def place(llm, cat: Catalog, card: dict, verbose: bool = False) -> dict:
    """Ставит товар в граф. Возвращает паспорт, связи после проверки и журнал шагов."""
    t0 = time.time()
    steps: list[dict] = []
    seen_ids: set[str] = set()
    seen_classes: set[str] = set()
    final = None
    card_text = json.dumps(card, ensure_ascii=False, indent=1)
    # ступень без модели: класс по ближайшим товарам каталога
    hint = cat.neighbor_class(card.get("name", ""), card.get("vendor", ""))
    if hint["class_id"]:
        seen_classes.add(hint["class_id"])
        for n in hint["near"]:
            seen_ids.add(n["id"])
        card_text += (f"\n\nПодсказка без модели — класс ближайших по названию товаров каталога: {hint['class_id']} "
                      f"«{hint['name']}», уверенность {hint['confidence']}. Ближайшие товары: "
                      + "; ".join(f"{n['id']} — {n['name']}" for n in hint["near"])
                      + ". Если уверенность высокая, класс можно не искать — трать шаги на связи.")
    # ещё одна ступень без модели: поиск по самому названию (соседи по линейке) и по продукту,
    # названному после «for / для» (базовый продукт дополнения). Агент начинает уже с кандидатами.
    queries = [card.get("name", "")]
    m = re.split(r"\s+(?:for|для)\s+", card.get("name", ""), maxsplit=1, flags=re.I)
    if len(m) == 2 and m[1].strip():
        queries.append(m[1].strip())
    for q in queries:
        result = cat.search_catalog(q)
        for r in result:
            seen_ids.add(r["id"])
        steps.append({"tool": "search_catalog", "args": {"query": q}, "thought": "без модели: поиск по названию",
                      "result": result, "ask": 0})
    asks = 0                                   # сколько раз обращались к модели за шагом
    for i in range(MAX_STEPS + 1):
        history = "\n".join(
            f"{n + 1}. {s['tool']} {json.dumps(s['args'], ensure_ascii=False)} -> {json.dumps(s['result'], ensure_ascii=False)[:1400]}"
            for n, s in enumerate(steps)) or "пока ничего"
        left = MAX_STEPS - asks
        prompt = STEP.format(card=card_text, tools=TOOLS_HELP, history=history, types="|".join(LINK_TYPES),
                             type_help="; ".join(f"{k} — {v}" for k, v in LINK_TYPES.items()), left=left)
        if left <= 0:
            prompt += "\nШаги закончились: дай итог сейчас."
        ans = llm.json(SYSTEM, prompt, purpose="агент: шаг")
        asks += 1
        if not isinstance(ans, dict):
            continue
        if "final" not in ans and "calls" not in ans and any(k in ans for k in ("class_id", "does", "links")):
            ans = {"thought": ans.get("thought", ""), "final": ans}       # итог без обёртки «final» — тоже итог
        if "final" in ans or left <= 0:
            final = ans.get("final") or {}
            steps.append({"tool": "итог", "args": {}, "thought": ans.get("thought", ""), "result": "паспорт и связи", "ask": asks})
            break
        calls = ans.get("calls")
        if not isinstance(calls, list):                       # модель ответила одним вызовом в старом виде
            calls = [{"tool": ans.get("tool", ""), "args": ans.get("args") or {}}]
        for call in calls[:MAX_CALLS]:
            if not isinstance(call, dict):
                continue
            tool, args = str(call.get("tool", "")), call.get("args") or {}
            if not tool:
                continue                                      # пустой вызов не выполняем и в журнал не пишем
            result = cat.call(tool, args if isinstance(args, dict) else {})
            for r in result if isinstance(result, list) else [result]:
                if isinstance(r, dict):
                    if r.get("id"):
                        seen_ids.add(r["id"])
                    if r.get("class_id"):
                        seen_classes.add(r["class_id"])
                    for t in r.get("top", []) if isinstance(r.get("top"), list) else []:
                        seen_ids.add(t.get("id", ""))
            steps.append({"tool": tool, "args": args, "thought": ans.get("thought", ""), "result": result, "ask": asks})
            if verbose:
                print(f"  запрос {asks}: {tool} {json.dumps(args, ensure_ascii=False)[:90]} — {str(ans.get('thought', ''))[:70]}", flush=True)
    final = final or {}

    # ---- проверка: сначала правила в коде, потом критик
    proposed, dropped = [], []
    for link in final.get("links") or []:
        if not isinstance(link, dict):
            continue
        dst, typ = str(link.get("dst", "")), str(link.get("type", ""))
        if dst not in cat.nodes or dst in cat.hidden:
            dropped.append({**link, "why": "такого товара нет в каталоге"})
        elif dst not in seen_ids:
            dropped.append({**link, "why": "агент не получал этот товар от инструментов"})
        elif typ not in LINK_TYPES:
            dropped.append({**link, "why": "неизвестный вид связи"})
        else:
            proposed.append({"dst": dst, "type": typ, "reason": str(link.get("reason", ""))[:200],
                             "quote": str(link.get("quote", ""))[:200], "dst_name": cat.nodes[dst]["name"]})
    kept = []
    if proposed:
        listing = "\n".join(f"{j}. [{LINK_TYPES[l['type']]}] dst = «{l['dst_name']}» ({cat.nodes[l['dst']].get('class_name', '')}). "
                            f"Довод: {l['reason']}" + (f" Цитата: «{l['quote']}»" if l["quote"] else "")
                            for j, l in enumerate(proposed))
        verdict = llm.json(CRITIC_SYSTEM, CRITIC.format(card=card_text, does=final.get("does", ""), links=listing),
                           purpose="агент: критик")
        vs = {int(v["i"]): v for v in (verdict.get("verdicts", []) if isinstance(verdict, dict) else [])
              if str(v.get("i", "")).isdigit()}
        for j, link in enumerate(proposed):
            v = vs.get(j)
            if v and v.get("keep"):
                kept.append({**link, "critic": str(v.get("why", ""))[:160]})
            else:
                dropped.append({**link, "why": "критик: " + str((v or {}).get("why", "нет вердикта"))[:160]})
    class_id = str(final.get("class_id", "")).strip()
    class_note = ""
    agent_class = class_id
    if hint["class_id"] and hint["confidence"] >= SURE:
        class_id = hint["class_id"]                           # соседи единодушны: берём их класс
    if class_id not in cat.class_names:
        by_name = {v.strip().lower(): k for k, v in cat.class_names.items()}
        if class_id.lower() in by_name:                        # агент назвал класс словами, а не кодом
            class_id = by_name[class_id.lower()]
        else:                                                  # не назвал: берём первый класс, который он нашёл сам
            first = next((r.get("class_id") for st_ in steps if st_["tool"] == "find_classes" and isinstance(st_["result"], list)
                          for r in st_["result"] if isinstance(r, dict) and r.get("class_id")), "")
            class_id = hint["class_id"] or first or ""
            class_note = "агент класс не назвал: взят у ближайших товаров каталога"
    rule_edges = cat.line_edges(card.get("name", ""), card.get("vendor", ""))     # линейка — правилом, без модели
    edges = [to_edge(card.get("name", ""), cat, link) for link in kept]
    have = {(e["src"], e["dst"]) for e in edges} | {(e["dst"], e["src"]) for e in edges}
    edges += [e for e in rule_edges if (e["src"], e["dst"]) not in have]
    return {
        "card": card,
        "passport": {
            "class_id": class_id if class_id in cat.class_names else "",
            "class_name": cat.class_names.get(class_id, ""),
            "class_seen": class_id in seen_classes, "class_note": class_note,
            "class_source": ("соседи по каталогу" if hint["class_id"] and hint["confidence"] >= SURE else "агент"),
            "class_agent": cat.class_names.get(agent_class, ""), "neighbors": hint,
            "does": str(final.get("does", ""))[:300],
            "needs": [str(x)[:120] for x in (final.get("needs") or [])][:6],
            "audience": str(final.get("audience", "any")),
        },
        "links": kept, "edges": edges, "rule_edges": rule_edges, "dropped": dropped, "steps": steps,
        "seconds": round(time.time() - t0, 1),
        "model_requests": asks + (1 if proposed else 0),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="", help="товар каталога: спрятать его и поставить заново")
    ap.add_argument("--name", default="")
    ap.add_argument("--vendor", default="")
    ap.add_argument("--text", default="")
    args = ap.parse_args()
    from recsys.llm.client import LLM
    cat = Catalog()
    if args.id:
        card = cat.card(args.id)
        cat.hidden = {args.id}
        truth = cat.nodes[args.id]
        print(f"Спрятан товар: {truth['name']} — класс в магазине: {truth.get('class_name', '')}")
    else:
        card = {"name": args.name, "vendor": args.vendor, "description": args.text}
    res = place(LLM(), cat, card, verbose=True)
    if args.id:
        res["truth"] = {"id": args.id, "class": truth.get("class_name", ""), "kind": "", "related": []}
    p = res["passport"]
    print(f"\nКласс: {p['class_name']} ({p['class_id']})\nЧто делает: {p['does']}\nЧто нужно: {'; '.join(p['needs']) or '—'}\nДля кого: {p['audience']}")
    print("Связи после проверки:")
    for link in res["links"]:
        print(f"  [{link['type']}] {link['dst_name']} — {link['reason']}  (критик: {link['critic']})")
    for link in res["dropped"]:
        print(f"  отброшено: {link.get('dst', '')} — {link['why']}")
    print(f"Время: {res['seconds']} с, запросов к модели: {res['model_requests']}")
    out = INTERIM / "agent_runs"
    out.mkdir(exist_ok=True)
    name = (args.id or args.name or "run").replace("/", "__")[:80]
    (out / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
