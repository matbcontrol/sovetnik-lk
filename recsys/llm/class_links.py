"""Связи между классами товаров: «к этому классу обычно берут…». Разовая разметка моделью с проверкой.

Зачем: у большинства товаров нет своих связей, и блок советов для их покупателей пуст.
Класс есть у каждого товара, поэтому связь «класс -> класс» закрывает пробел сразу для всего каталога.

Шаг 1, предложение. Модель видит весь список классов магазина и для каждого класса из порции называет
        до пяти классов, которые стоит предложить после покупки, с видом связи и фразой для клиента.
Шаг 2, проверка. Отдельный запрос с ролью придирчивого закупщика оценивает каждую пару и отсеивает слабые.
Шаг 3 (не здесь): связи сверяются с продажами — recsys.eval.graph_model.calibrate.

Запуск:  python -m recsys.llm.class_links            разметить все классы
         python -m recsys.llm.class_links --dry-run  показать запросы и оценку объёма, модель не вызывать
Результат: data/interim/class_links.csv
"""
from __future__ import annotations

import argparse
import csv
import json

from recsys.config import INTERIM

PROPOSE_SYSTEM = (
    "Ты — опытный менеджер по продажам ИТ-дистрибьютора. Ты подсказываешь клиенту, что ему пригодится "
    "после покупки, как это делает хороший менеджер: только то, что действительно нужно и совместимо, "
    "без навязывания. Отвечай строго в формате JSON."
)
PROPOSE_USER = """Ниже полный список классов товаров магазина: код | название | примеры товаров.

{catalog}

Разбери классы с этими кодами: {batch}

Для каждого из них назови от 0 до 5 классов из общего списка, товары которых стоит предложить клиенту ПОСЛЕ покупки товара этого класса.

Правила:
- связь практическая: второе нужно, чтобы первое работало, было защищено, сохранено, учтено, или обычно закупается в том же проекте;
- не предлагай тот же класс и его заменители: аналог — это не дополнение;
- если уверенной связи нет, не придумывай: пустой список — нормальный ответ;
- type: "requires" (без второго первое не работает или теряет смысл) или "complements" (второе усиливает первое);
- audience: "b2b" (только организациям), "b2c" (только частным лицам) или "any";
- reason: одна фраза для клиента на русском, до 140 знаков, без рекламных слов — зачем это нужно именно после такой покупки;
- confidence: от 0 до 1 — насколько часто хороший менеджер дал бы такой совет.

Ответ: {{"links": [{{"src": "код", "dst": "код", "type": "...", "audience": "...", "reason": "...", "confidence": 0.0}}]}}"""

CRITIC_SYSTEM = (
    "Ты — придирчивый руководитель закупок ИТ. Тебе показывают советы вида «купил А — предложи Б». "
    "Ты отсеиваешь слабые: навязчивые, несовместимые, слишком общие, дублирующие уже купленное. "
    "Отвечай строго в формате JSON."
)
CRITIC_USER = """Оцени каждый совет. Оставляй только те, что дал бы клиенту честный менеджер, которому важно, чтобы клиент вернулся.

{pairs}

Для каждого номера верни: keep (true или false), score от 0 до 1 и why — одна короткая фраза, почему оставить или убрать.
Убирай, если: Б заменяет А, а не дополняет; связь верна только для редкого случая; фраза обещает то, чего товар не делает.

Ответ: {{"verdicts": [{{"i": 0, "keep": true, "score": 0.0, "why": "..."}}]}}"""


def load_classes() -> dict[str, dict]:
    """Классы магазина: название и примеры товаров (первые на витрине)."""
    names = json.loads((INTERIM / "class_names.json").read_text(encoding="utf-8"))
    rank = json.loads((INTERIM / "class_rank.json").read_text(encoding="utf-8"))
    nodes = {}
    with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            nodes[r["family"]] = r
    out = {}
    for key, name in names.items():
        fams = [f for f in rank.get(key, []) if nodes.get(f, {}).get("on_sale") == "1"]
        if not fams:
            continue                                    # в классе нечего предложить
        out[key] = {"name": name, "examples": [nodes[f]["name"][:60] for f in fams[:4]], "size": len(fams)}
    return out


def catalog_text(classes: dict[str, dict]) -> str:
    return "\n".join(f"{k} | {c['name']} | {'; '.join(c['examples'])}" for k, c in classes.items())


def batches(keys: list[str], small: int = 12, small_part: int = 60, big: int = 33) -> list[list[str]]:
    """Порции классов для запросов. Первые порции маленькие (они были размечены до того, как выяснился
    суточный лимит запросов, и лежат в кеше), остальные крупные — чтобы уложиться в лимит."""
    out = [keys[i:i + small] for i in range(0, min(small_part, len(keys)), small)]
    rest = keys[small_part:]
    return out + [rest[i:i + big] for i in range(0, len(rest), big)]


def propose(llm, classes: dict[str, dict]) -> list[dict]:
    keys, links = list(classes), []
    catalog = catalog_text(classes)
    done = 0
    for batch in batches(keys):
        done += len(batch)
        i, batch_size = done - len(batch), len(batch)
        data = llm.json(PROPOSE_SYSTEM, PROPOSE_USER.format(catalog=catalog, batch=", ".join(batch)),
                        purpose="связи классов: предложение")
        for link in data.get("links", []) if isinstance(data, dict) else []:
            src, dst = link.get("src"), link.get("dst")
            if src in batch and dst in classes and src != dst:     # модель могла назвать код, которого нет
                links.append({
                    "src_class": src, "dst_class": dst,
                    "type": link.get("type") if link.get("type") in ("requires", "complements") else "complements",
                    "audience": link.get("audience") if link.get("audience") in ("b2b", "b2c", "any") else "any",
                    "reason": str(link.get("reason", "")).strip()[:200],
                    "confidence": max(0.0, min(1.0, float(link.get("confidence") or 0))),
                })
        print(f"  предложение: классов {min(i + batch_size, len(keys))}/{len(keys)}, связей {len(links)}", flush=True)
    return links


def criticize(llm, classes: dict[str, dict], links: list[dict], batch_size: int = 90) -> list[dict]:
    kept = []
    for i in range(0, len(links), batch_size):
        part = links[i:i + batch_size]
        pairs = "\n".join(
            f"{j}. Купил: {classes[l['src_class']]['name']} (например, {classes[l['src_class']]['examples'][0]}). "
            f"Предложить: {classes[l['dst_class']]['name']} (например, {classes[l['dst_class']]['examples'][0]}). "
            f"Довод: {l['reason']}" for j, l in enumerate(part))
        data = llm.json(CRITIC_SYSTEM, CRITIC_USER.format(pairs=pairs), purpose="связи классов: проверка")
        verdicts = {int(v["i"]): v for v in (data.get("verdicts", []) if isinstance(data, dict) else [])
                    if str(v.get("i", "")).lstrip("-").isdigit()}
        for j, link in enumerate(part):
            v = verdicts.get(j)
            if not v or not v.get("keep"):
                continue                                # без явного «оставить» связь не проходит
            score = max(0.0, min(1.0, float(v.get("score") or 0)))
            if score < 0.5:
                continue
            kept.append({**link, "confidence": round(min(link["confidence"], score), 2),
                         "critic": str(v.get("why", ""))[:200], "source": "модель"})
        print(f"  проверка: пар {min(i + batch_size, len(links))}/{len(links)}, оставлено {len(kept)}", flush=True)
    return kept


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="показать запрос и объём, модель не вызывать")
    args = ap.parse_args()
    classes = load_classes()
    catalog = catalog_text(classes)
    print(f"классов с товарами в продаже: {len(classes)}; список классов ~{len(catalog) // 3} токенов")
    if args.dry_run:
        calls = len(batches(list(classes)))
        print(f"запросов на предложение: {calls}, на проверку: примерно {-(-len(classes) * 2 // 90)}")
        print("---- запрос (начало) ----")
        print(PROPOSE_USER.format(catalog=catalog[:1500] + "\n…", batch=", ".join(list(classes)[:12])))
        return
    from recsys.llm.client import LLM, usage_summary
    llm = LLM()
    links = propose(llm, classes)
    kept = criticize(llm, classes, links)
    path = INTERIM / "class_links.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["src_class", "dst_class", "type", "audience", "confidence", "reason", "critic", "source"])
        w.writeheader()
        w.writerows(kept)
    covered = len({l["src_class"] for l in kept})
    print(f"предложено {len(links)}, после проверки {len(kept)}; классов со связями {covered} из {len(classes)} -> {path}")
    print("расход:", json.dumps(usage_summary(), ensure_ascii=False))


if __name__ == "__main__":
    main()
