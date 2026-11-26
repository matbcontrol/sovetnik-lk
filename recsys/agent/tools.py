"""Инструменты агента каталога: всё, что агент знает о магазине, он узнаёт через них.

Инструменты работают только с открытыми данными магазина (граф, страницы семейств, витрина).
Данных клиентов и продаж здесь нет.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict

from recsys.config import INTERIM
from recsys.graph.textnorm import newer, split_edition, tokens, version_of

TOOLS_HELP = """Инструменты (вызывай по одному за шаг):
- find_classes {"query": "слова о назначении товара"} — найти подходящие классы каталога; вернёт код, название и примеры товаров.
- search_catalog {"query": "название или вендор", "class_id": "код класса (необязательно)"} — найти товары в каталоге; вернёт id, название, вендора, класс и продаётся ли.
- get_product {"id": "id товара из каталога"} — карточка товара из каталога: описание и его известные связи.
- class_companions {"class_id": "код класса"} — какие классы обычно берут вместе с этим классом и какие товары в них первые на витрине."""


class Catalog:
    def __init__(self) -> None:
        with (INTERIM / "graph_nodes.csv").open(encoding="utf-8") as f:
            self.nodes = {r["family"]: r for r in csv.DictReader(f)}
        self.class_names: dict[str, str] = json.loads((INTERIM / "class_names.json").read_text(encoding="utf-8"))
        self.class_rank: dict[str, list[str]] = json.loads((INTERIM / "class_rank.json").read_text(encoding="utf-8"))
        self.desc: dict[str, dict] = {}
        with (INTERIM / "families.jsonl").open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.desc[f"{r['vendor_slug']}/{r['family_slug']}"] = r
        self.edges: dict[str, list[dict]] = defaultdict(list)
        with (INTERIM / "graph_edges.csv").open(encoding="utf-8") as f:
            for e in csv.DictReader(f):
                if e["type"] != "same_line":
                    self.edges[e["src"]].append(e)
        # спутники класса — из таблицы вердиктов пар; прежняя разметка облегчённой моделью — запасной источник
        from recsys.llm.pair_verdicts import CLASS_FILE, as_class_links, read

        self.class_links: dict[str, list[dict]] = defaultdict(list)
        p = INTERIM / "class_links.csv"
        if CLASS_FILE.exists():
            for link in as_class_links(read(CLASS_FILE)):
                self.class_links[link["src_class"]].append(link)
        elif p.exists():
            with p.open(encoding="utf-8") as f:
                for link in csv.DictReader(f):
                    self.class_links[link["src_class"]].append(link)
        # поиск по словам названия и вендора
        self.tok: dict[str, list[str]] = {}
        self.index: dict[str, set[str]] = defaultdict(set)
        df = Counter()
        for k, n in self.nodes.items():
            t = tokens(f"{n['name']} {n['vendor']}")
            self.tok[k] = t
            for w in set(t):
                self.index[w].add(k)
                df[w] += 1
        total = max(len(self.nodes), 1)
        self.idf = {w: math.log(1 + total / c) for w, c in df.items()}
        # поиск классов: слова названия класса и названий первых товаров на витрине
        self.class_tok: dict[str, Counter] = {}
        for cid, name in self.class_names.items():
            c = Counter()
            for w in tokens(name):
                c[w] += 3
            for fam in self.class_rank.get(cid, [])[:25]:
                for w in set(tokens(self.nodes.get(fam, {}).get("name", ""))):
                    c[w] += 1
            self.class_tok[cid] = c
        self.hidden: set[str] = set()          # товары, которых агент не должен видеть («вынь и вставь»)

    # ---- инструменты -------------------------------------------------------------------------

    def _class_tokens(self, cid: str) -> Counter:
        """Слова класса для поиска. Индекс строится по первым товарам класса на витрине; если среди них
        есть спрятанный товар («вынь и вставь»), слова класса пересчитываются без него — иначе поиск
        класса подсказывал бы агенту ответ названием спрятанного товара."""
        top = self.class_rank.get(cid, [])[:25]
        if not self.hidden or not self.hidden.intersection(top):
            return self.class_tok[cid]
        c = Counter()
        for w in tokens(self.class_names[cid]):
            c[w] += 3
        for fam in [f for f in self.class_rank.get(cid, []) if f not in self.hidden][:25]:
            for w in set(tokens(self.nodes.get(fam, {}).get("name", ""))):
                c[w] += 1
        return c

    def find_classes(self, query: str, limit: int = 8) -> list[dict]:
        q = tokens(query)
        scored = []
        for cid in self.class_tok:
            c = self._class_tokens(cid)
            s = sum(c.get(w, 0) * self.idf.get(w, 3.0) for w in q)
            if s > 0:
                scored.append((s / (5 + math.sqrt(sum(c.values()))), cid))
        scored.sort(reverse=True)
        out = []
        for _, cid in scored[:limit]:
            fams = [f for f in self.class_rank.get(cid, []) if f not in self.hidden][:3]
            out.append({"class_id": cid, "name": self.class_names[cid],
                        "examples": [self.nodes[f]["name"][:50] for f in fams if f in self.nodes]})
        return out

    def search_catalog(self, query: str, class_id: str = "", limit: int = 8) -> list[dict]:
        q = set(tokens(query))
        cand = Counter()
        for w in q:
            for k in self.index.get(w, ()):
                cand[k] += self.idf.get(w, 1.0)
        scored = []
        for k, s in cand.items():
            n = self.nodes[k]
            if k in self.hidden or (class_id and n.get("class") != class_id):
                continue
            cover = s / (sum(self.idf.get(w, 1.0) for w in set(self.tok[k])) or 1.0)
            scored.append((s + 2.0 * cover + (0.5 if n["on_sale"] == "1" else 0.0), k))
        scored.sort(reverse=True)
        return [self.brief(k) for _, k in scored[:limit]]

    def neighbor_class(self, name: str, vendor: str = "", top: int = 5) -> dict:
        """Класс по ближайшим товарам каталога — без модели.

        Ищем товары с похожим названием; товары того же вендора весят больше. Уверенность — доля
        веса у победившего класса. При уверенности от 0,7 класс совпадает с рубрикой магазина
        примерно в 9 случаях из 10 (проверено на 600 товарах, спрятанных по одному).
        """
        v = set(tokens(vendor))
        q = set(tokens(f"{name} {vendor}"))
        rare = max(self.idf.values(), default=1.0)             # слово, которого нет в каталоге, весит как самое редкое
        q_weight = sum(self.idf.get(w, rare) for w in q) or 1.0
        votes: Counter = Counter()
        alike: dict[str, float] = {}
        near = []
        for rank, h in enumerate(self.search_catalog(f"{name} {vendor}", limit=top)):
            n = self.nodes[h["id"]]
            if not n.get("class"):
                continue
            same_vendor = bool(v and v & set(tokens(n.get("vendor", "")))) or bool(v & set(tokens(n["vendor_slug"].replace("-", " "))))
            votes[n["class"]] += (1.5 if same_vendor else 1.0) / (1 + 0.3 * rank)
            share = sum(self.idf.get(w, rare) for w in q & set(self.tok[h["id"]])) / q_weight
            alike[n["class"]] = max(alike.get(n["class"], 0.0), share)
            near.append({"id": h["id"], "name": h["name"], "class": n.get("class_name", "")})
        if not votes:
            return {"class_id": "", "confidence": 0.0, "similarity": 0.0, "near": []}
        (cid, w), total = votes.most_common(1)[0], sum(votes.values())
        # similarity — какая доля слов нового товара (по весу) нашлась у самого похожего соседа этого класса:
        # единодушие соседей ничего не значит, если сами соседи на товар не похожи
        return {"class_id": cid, "name": self.class_names.get(cid, ""), "confidence": round(w / total, 2),
                "similarity": round(alike[cid], 2), "near": near[:3]}

    def line_edges(self, name: str, vendor: str = "") -> list[dict]:
        """Соседи по линейке для нового товара — правило по названию, без модели.

        Тот же вендор и та же основа названия; кто старше по редакции или новее по версии, видно из названий.
        «new» в ребре — сам новый товар.
        """
        base, rank, _ = split_edition(name)
        base_key, ver = " ".join(base), version_of(name)
        v = set(tokens(vendor))
        out = []
        for h in self.search_catalog(f"{name} {vendor}", limit=15):
            n = self.nodes[h["id"]]
            if not base_key or n.get("base") != base_key or n.get("kind") == "hardware":
                continue
            nv = set(tokens(n.get("vendor", ""))) | set(tokens(n["vendor_slug"].replace("-", " ")))
            if v and not (v & nv) and len(base) < 2:
                continue                                     # вендор не совпал, а основа названия слишком короткая
            r2 = float(n["edition_rank"]) if n.get("edition_rank") not in ("", None) else None
            v2 = n.get("version", "")
            if newer(v2, ver):
                e = {"src": h["id"], "dst": "new", "type": "new_version"}
            elif newer(ver, v2):
                e = {"src": "new", "dst": h["id"], "type": "new_version"}
            elif rank is not None and r2 is not None and rank != r2 and ver == v2:
                e = {"src": "new", "dst": h["id"], "type": "upgrade"} if rank < r2 else {"src": h["id"], "dst": "new", "type": "upgrade"}
            else:
                e = {"src": h["id"], "dst": "new", "type": "same_line"}
            e["why"] = "та же линейка: совпали вендор и основа названия"
            e["name"] = n["name"]
            out.append(e)
        return out

    def brief(self, k: str) -> dict:
        n = self.nodes[k]
        sale = "в продаже" if n["on_sale"] == "1" else ("по запросу" if n.get("on_request") == "1" else "не продаётся")
        return {"id": k, "name": n["name"][:90], "vendor": n.get("vendor", "")[:40],
                "class": n.get("class_name", "")[:50], "sale": sale}

    def get_product(self, id: str) -> dict:
        if id not in self.nodes or id in self.hidden:
            return {"error": "такого id нет в каталоге"}
        out = self.brief(id)
        d = self.desc.get(id) or {}
        out["class_id"] = self.nodes[id].get("class", "")
        out["description"] = (d.get("description") or "")[:600]
        out["links"] = [{"type": e["type"], "to": self.nodes.get(e["dst"], {}).get("name", e["dst"])[:60]}
                        for e in self.edges.get(id, [])[:8] if e["dst"] not in self.hidden]
        return out

    def class_companions(self, class_id: str) -> list[dict]:
        out = []
        for link in sorted(self.class_links.get(class_id, []), key=lambda x: -float(x["confidence"]))[:6]:
            fams = [f for f in self.class_rank.get(link["dst_class"], []) if f not in self.hidden
                    and self.nodes.get(f, {}).get("on_sale") == "1"][:3]
            out.append({"class_id": link["dst_class"], "name": self.class_names.get(link["dst_class"], ""),
                        "type": link["type"], "why": link["reason"][:120],
                        "top": [{"id": f, "name": self.nodes[f]["name"][:60]} for f in fams]})
        return out or [{"note": "для этого класса связей с другими классами пока нет"}]

    def call(self, tool: str, args: dict) -> object:
        try:
            if tool == "find_classes":
                return self.find_classes(str(args.get("query", "")))
            if tool == "search_catalog":
                return self.search_catalog(str(args.get("query", "")), str(args.get("class_id") or ""))
            if tool == "get_product":
                return self.get_product(str(args.get("id", "")))
            if tool == "class_companions":
                return self.class_companions(str(args.get("class_id", "")))
        except Exception as e:                                   # ошибка инструмента — тоже ответ агенту
            return {"error": f"{type(e).__name__}: {e}"}
        return {"error": f"нет инструмента «{tool}»"}

    def card(self, k: str) -> dict:
        """Карточка товара в том виде, в каком её вводит контент-менеджер: название, вендор, описание."""
        n = self.nodes[k]
        d = self.desc.get(k) or {}
        return {"name": n["name"], "vendor": n.get("vendor", ""), "description": (d.get("description") or "")[:1500]}
