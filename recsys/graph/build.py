"""Сборка графа связей между семействами товаров. Модель здесь не используется.

Узел — семейство товаров магазина. Связь имеет тип, источник, уверенность и пояснение.

Типы связей:
  upgrade     — старшая редакция той же линейки (из таблицы сравнения или из названий)
  renewal     — продление того же семейства (у семейства есть лицензии типа «продление»)
  same_line   — другая редакция или вариант той же линейки (это замена, а не дополнение)
  mentions    — на странице семейства есть ссылка на другое семейство
  analog_ru   — российский аналог зарубежного продукта (таблица замещения АРПП)

Запуск:  python -m recsys.graph.build
Результат: data/interim/graph_nodes.csv, data/interim/graph_edges.csv
"""
import csv
import json
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from recsys.config import INTERIM
from recsys.graph.textnorm import PLATFORM_WORDS, jaccard, newer, split_edition, tokens, version_of
from recsys.normalize.config import features

MAX_LINE = 40  # в линейке больше семейств — основа названия слишком общая, пары не строим
YES = {"✔", "✓", "+", "да", "есть", "yes"}
NO = {"✖", "✗", "×", "-", "—", "–", "нет", "no", ""}


def load_families() -> dict[str, dict]:
    fams = {}
    with (INTERIM / "families.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # последняя строка может дописываться обходом
            key = f"{r['vendor_slug']}/{r['family_slug']}"
            fams[key] = r
    return fams


HOME_NAME = re.compile(r"для семьи|для дома|для уч[её]бы|домашн|персональн|\b(home|family|personal|student)\b", re.I)


def audience(name: str, client_types: str) -> str:
    """Типы клиентов семейства. Если страница их не называет, «домашнее» узнаём по названию семейства."""
    if client_types:
        return client_types
    return "b2c" if HOME_NAME.search(name or "") else ""


def node_row(key: str, r: dict) -> dict:
    lic, cli, plat, terms = Counter(), Counter(), Counter(), Counter()
    for p in r["products"]:
        f = features(p["name"], p["config"], p["min_qty"], p["max_qty"])
        lic[f["license_type"]] += 1
        if f["term_months"] is not None:
            terms[f["term_months"]] += 1
        if f["client_type"]:
            cli[f["client_type"]] += 1
        for pl in (f["platform"] or "").split(","):
            if pl.strip():
                plat[pl.strip()] += 1
    prices = [p["price"] for p in r["products"] if p.get("price")]
    for opt in r["options"].get("Тип лицензии", []):
        lt = features(opt, f"Тип лицензии: {opt}")["license_type"]
        lic[lt] += 0  # вариант есть в выборе, даже если товары этого типа не попали в выдачу страницы
    base, rank, ed = split_edition(r["name"])
    return {
        "family": key,
        "family_id": r.get("family_id") or "",
        "name": r["name"],
        "vendor": r["vendor"],
        "vendor_slug": r["vendor_slug"],
        "category": r["category"],
        "subcategory": r["subcategory"],
        "on_sale": int(bool(r["products"])),
        "on_request": 0,                               # на витрине только «Запросить цену»
        "b2b_only": int(bool(r["b2b_only"])),
        "license_types": "|".join(sorted(lic)),
        "client_types": audience(r["name"], "|".join(sorted(cli))),
        "platforms": "|".join(sorted(plat)),
        # срок, на который семейство продаётся чаще всего (0 — бессрочно, пусто — неизвестно)
        "typical_term": terms.most_common(1)[0][0] if terms else "",
        "price_from": min(prices) if prices else "",   # публичная цена «от», рублей
        "base": " ".join(base),
        "edition": ed,
        "edition_rank": "" if rank is None else rank,
        "version": version_of(r["name"]),
        "url": r["url"],
    }


def edges_from_names(nodes: dict[str, dict]) -> list[dict]:
    """Связи внутри линейки по названиям: один вендор и одна основа названия.

    upgrade     — та же версия, редакция старше
    new_version — та же редакция, версия новее
    same_line   — любые два семейства линейки (это замена друг другу, а не дополнение)
    """
    groups = defaultdict(list)
    for k, n in nodes.items():
        if n["base"]:
            groups[(n["vendor_slug"], n["base"])].append(k)
    edges = []
    for (_, base), keys in groups.items():
        if len(keys) < 2 or len(keys) > MAX_LINE:
            continue
        for a in keys:
            for b in keys:
                if a == b:
                    continue
                na, nb = nodes[a], nodes[b]
                edges.append({
                    "src": a, "dst": b, "type": "same_line", "source": "названия",
                    "confidence": 0.7, "evidence": f"одна линейка «{base}»",
                })
                if na.get("kind") == "hardware" or nb.get("kind") == "hardware":
                    continue  # у техники числа в названии — номера моделей, а не версии и редакции
                ra, rb = na["edition_rank"], nb["edition_rank"]
                if ra != "" and rb != "" and rb > ra and na["version"] == nb["version"]:
                    edges.append({
                        "src": a, "dst": b, "type": "upgrade", "source": "названия редакций",
                        "confidence": 0.6,
                        "evidence": f"редакция «{na['edition']}» -> «{nb['edition']}» в линейке «{base}»",
                    })
                if ra == rb and newer(na["version"], nb["version"]):
                    edges.append({
                        "src": a, "dst": b, "type": "new_version", "source": "номера версий в названиях",
                        "confidence": 0.7,
                        "evidence": f"версия {na['version']} -> {nb['version']} в линейке «{base}»",
                    })
    return edges


def _gained(rows: list[list[str]], i: int, j: int, limit: int = 3) -> list[str]:
    """Функции, которых нет в колонке i и которые есть в колонке j (колонки считаются без первой)."""
    out = []
    for row in rows:
        a, b = row[i + 1].strip().lower(), row[j + 1].strip().lower()
        if a in NO and b in YES:
            name = row[0]
            for _ in range(4):                          # пояснения в скобках (в том числе вложенные) в совет не берём
                name = re.sub(r"\s*\([^()]*\)", "", name)
            out.append(name.strip() or row[0])
    return out[:limit]


def edges_from_compare(fams: dict[str, dict], nodes: dict[str, dict]) -> list[dict]:
    """Лестница редакций из таблицы сравнения на странице: порядок колонок = порядок редакций.

    Колонку сопоставляем с семейством того же вендора: редакция должна совпасть точно,
    основа названия — почти точно, версия — та же, что у семейства страницы (или без версии).
    """
    by_vendor = defaultdict(list)
    for k, n in nodes.items():
        by_vendor[n["vendor_slug"]].append(k)
    edges = []
    for key, r in fams.items():
        cmp_ = r.get("compare")
        if not cmp_ or len(cmp_["columns"]) < 2:
            continue
        me = nodes[key]
        my_base = [t for t in me["base"].split() if t not in PLATFORM_WORDS]
        matched: list[str | None] = []
        for col in cmp_["columns"]:
            c_base, c_rank, _ = split_edition(col)
            c_base = [t for t in c_base if t not in PLATFORM_WORDS]
            best, best_score = None, 0.0
            for cand in by_vendor[me["vendor_slug"]]:
                n = nodes[cand]
                if c_rank is not None:
                    if n["edition_rank"] == "" or float(n["edition_rank"]) != c_rank:
                        continue
                    n_base = [t for t in n["base"].split() if t not in PLATFORM_WORDS]
                    score = jaccard(c_base or my_base, n_base)
                    need = 0.6
                else:
                    if len(c_base) < 2:
                        continue
                    score = jaccard(c_base, [t for t in tokens(n["name"]) if t not in PLATFORM_WORDS])
                    need = 0.75
                if score < need:
                    continue
                if n["version"] == me["version"]:
                    score += 0.2          # та же версия, что у страницы
                elif n["version"]:
                    continue              # другая версия — не смешиваем
                if score > best_score:
                    best, best_score = cand, score
            matched.append(best)
        if key not in matched:
            continue
        i = matched.index(key)
        for j in range(i + 1, len(matched)):
            dst = matched[j]
            if not dst or dst == key:
                continue
            ri, rj = nodes[key]["edition_rank"], nodes[dst]["edition_rank"]
            if ri != "" and rj != "" and float(rj) < float(ri):
                continue  # порядок колонок спорит с названиями редакций — не доверяем
            gained = _gained(cmp_["rows"], i, j)
            extra = f"; добавляет: {'; '.join(gained)}" if gained else ""
            edges.append({
                "src": key, "dst": dst, "type": "upgrade", "source": "таблица сравнения магазина",
                "confidence": 0.9,
                "evidence": f"колонка «{cmp_['columns'][i]}» -> «{cmp_['columns'][j]}»{extra}; {r['url']}",
            })
    return edges


def edges_from_links(fams: dict[str, dict]) -> list[dict]:
    edges = []
    for key, r in fams.items():
        seen = set()
        for link in r.get("store_links", []):
            parts = [p for p in urlparse(link["href"]).path.split("/") if p]
            if len(parts) == 2:
                dst = f"{parts[0]}/{parts[1]}"
                if dst in fams and dst != key and dst not in seen:
                    seen.add(dst)
                    edges.append({
                        "src": key, "dst": dst, "type": "mentions", "source": "ссылка на странице магазина",
                        "confidence": 0.4, "evidence": f"«{link['text']}»; {r['url']}",
                    })
    return edges


def renewal_edges(nodes: dict[str, dict]) -> list[dict]:
    return [
        {"src": k, "dst": k, "type": "renewal", "source": "типы лицензий на странице",
         "confidence": 0.95, "evidence": "у семейства есть лицензии типа «продление»"}
        for k, n in nodes.items()
        if "renewal" in n["license_types"].split("|") and n["on_sale"]
    ]


COURSE_NAME = re.compile(r"^\s*(курс|обучение|тренинг|экзамен|сертификация)\b", re.I)
GENERIC = PLATFORM_WORDS | {"office", "security", "pro", "professional", "enterprise", "standard", "business",
                            "cloud", "online", "suite", "studio", "manager", "system", "data", "web", "pdf",
                            "mobile", "тариф", "бизнес", "бизнеса", "курс", "программы", "программа", "по", "ос",
                            "education", "teams", "team", "html", "file", "files", "wpf", "asp", "net", "java",
                            "php", "school", "home", "family", "outlook", "excel", "word"}


def edges_from_addons(nodes: dict[str, dict]) -> list[dict]:
    """Дополнения и учебные курсы к продукту — из самих названий товаров.

    «V-Ray for SketchUp», «TITAN для Advance Steel», «Курс Администрирование ОС Astra Linux» —
    в названии прямо сказано, к какому продукту это относится. Клиенту, у которого этот продукт есть,
    такое дополнение уместно предложить. Связь идёт от продукта к дополнению.

    Название продукта должно стоять в названии дополнения целиком и подряд; продукт из одного слова
    засчитывается, только если после «for / для» нет других значимых слов («for Maya», но не «for SQL Anywhere»).
    """
    own: dict[str, list[str]] = {}
    index = defaultdict(set)
    for k, n in nodes.items():
        if n.get("kind") == "hardware" or COURSE_NAME.search(n["name"]):
            continue
        vendor = set(tokens(n["vendor"])) | set(tokens(n["vendor_slug"].replace("-", " ")))
        head = re.split(r"\s+(?:for|для)\s+", n["name"], maxsplit=1, flags=re.I)[0]
        seq = [t for t in split_edition(head)[0] if t not in vendor]
        if not seq or set(seq) <= GENERIC:
            continue                                    # у продукта нет собственного отличимого имени
        own[k] = seq
        index[seq[0]].add(k)

    def contains(seq: list[str], inside: list[str]) -> bool:
        return any(inside[i:i + len(seq)] == seq for i in range(len(inside) - len(seq) + 1))

    found: dict[tuple[str, str], dict] = {}
    for k, n in nodes.items():
        if n.get("kind") == "hardware" or not (n["on_sale"] or n["on_request"]):
            continue
        name = n["name"]
        course = bool(COURSE_NAME.search(name))
        parts = re.split(r"\s+(?:for|для)\s+", name, maxsplit=1, flags=re.I)
        if course:
            tail = tokens(name)
        elif len(parts) == 2:
            tail = tokens(parts[1])
        else:
            continue
        meaningful = set(tail) - GENERIC
        best, best_size = [], 0
        for tok in set(tail):
            for y in index.get(tok, ()):
                seq = own[y]
                if y == k or not contains(seq, tail):
                    continue
                if nodes[y]["vendor_slug"] == n["vendor_slug"] and not course:
                    continue                            # «для» внутри линейки одного вендора — это редакция
                if len(seq) == 1 and (course or meaningful != set(seq)):
                    continue                            # одно слово — только если больше ничего не названо
                if len(seq) > best_size:
                    best, best_size = [y], len(seq)
                elif len(seq) == best_size and y not in best:
                    best.append(y)
        for y in best[:4]:
            kind = "учебный курс по продукту" if course else "дополнение к продукту"
            found[(y, k)] = {
                "src": y, "dst": k, "type": "complements", "source": "название товара",
                "confidence": 0.6 if course else 0.75,
                "evidence": f"{kind}: в названии «{name}» прямо назван «{nodes[y]['name']}»",
            }
    by_src = defaultdict(list)
    for e in found.values():
        by_src[e["src"]].append(e)
    edges = []
    for es in by_src.values():                          # у продукта оставляем не больше восьми дополнений
        es.sort(key=lambda e: (0 if nodes[e["dst"]]["on_sale"] else 1, nodes[e["dst"]].get("store_rank") or 9999))
        edges += es[:8]
    return edges


def match_arpp(nodes: dict[str, dict]) -> list[dict]:
    """Российские аналоги: сопоставляем названия из таблицы АРПП с семействами магазина."""
    path = INTERIM / "arpp_replacements.csv"
    if not path.exists():
        return []
    index = defaultdict(set)
    full: dict[str, set[str]] = {}      # все слова названия
    own: dict[str, set[str]] = {}       # слова самого продукта: без имени вендора и без «для чего-то»
    for k, n in nodes.items():
        if n.get("kind") == "hardware":
            continue
        vendor = set(tokens(n["vendor"])) | set(tokens(n["vendor_slug"].replace("-", " ")))
        head = re.split(r"\s+(?:for|для)\s+", n["name"], maxsplit=1, flags=re.I)[0]
        full[k] = set(tokens(n["name"]))
        head_base = set(split_edition(head)[0])         # без слов редакции: «Acrobat Standard» — это «Acrobat»
        own[k] = (head_base - vendor) or head_base or full[k]
        for tok in full[k]:
            index[tok].add(k)

    def best_matches(name: str, limit: int = 3) -> list[tuple[str, float]]:
        """Семейства, которым соответствует название из таблицы, и доля покрытия названия семейства.

        Все слова названия из таблицы должны быть в названии семейства. Оценка — какая часть
        собственных слов продукта названа в таблице: запись «VMware» (имя вендора) не подходит
        ни к одному продукту VMware, а «Jira» не подходит к дополнению «Capture for Jira».
        """
        t = set(tokens(name))
        if not t:
            return []
        cands = Counter()
        for tok in t:
            for k in index.get(tok, ()):
                cands[k] += 1
        scored = []
        for k, hit in cands.items():
            if hit < len(t):
                continue
            score = len(t & own[k]) / len(own[k])
            # название из одного слова должно совпасть с продуктом целиком («Esprit» — не «Vue Esprit»)
            if score >= (1.0 if len(t - (full[k] - own[k])) <= 1 else 0.5):
                scored.append((k, score))
        scored.sort(key=lambda x: (-x[1], nodes[x[0]].get("store_rank") or 9999))
        return scored[:limit]

    cache: dict[str, list[tuple[str, float]]] = {}
    found: dict[tuple[str, str], dict] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fo, do = row["foreign_product"], row["domestic_product"]
            for name in (fo, do):
                if name not in cache:
                    cache[name] = best_matches(name)
            for dst, _ in cache[do]:
                nodes[dst]["ru"] = 1                    # продукт есть в реестре российского ПО
            for src, s1 in cache[fo]:
                for dst, s2 in cache[do]:
                    # российский продукт без цены на витрине организация может запросить — он тоже годится
                    if src == dst or not (nodes[dst]["on_sale"] or nodes[dst]["on_request"]):
                        continue
                    conf = round(0.5 + 0.4 * min(s1, s2), 2)
                    if (src, dst) in found and found[(src, dst)]["confidence"] >= conf:
                        continue
                    found[(src, dst)] = {
                        "src": src, "dst": dst, "type": "analog_ru", "source": "таблица замещения АРПП",
                        "confidence": conf,
                        "evidence": f"«{fo}» -> «{do}» (реестр № {row['registry_no']}); классы: {row['classes']}",
                    }
    # у одного продукта оставляем не больше шести аналогов: самые уверенные, при равенстве — выше на витрине
    by_src = defaultdict(list)
    for e in found.values():
        by_src[e["src"]].append(e)
    edges = []
    for es in by_src.values():
        es.sort(key=lambda e: (-e["confidence"], 0 if nodes[e["dst"]]["on_sale"] else 1,
                               nodes[e["dst"]].get("store_rank") or 9999))
        edges += es[:6]
    return edges


SKIP_TAGS = ("Возврат НДС", "Несколько видов лицензий", "Тех. консультация", "Попробовать ДЕМО",
             "Альтернатива есть", "Есть решение", "Сравнить легко", "Бухгалтерские документы")
NOT_A_CLASS = {"productCollections"}   # подборки магазина — не класс продукта


def apply_listing(fams: dict[str, dict], nodes: dict[str, dict]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Витрина категорий: класс, место на витрине, рейтинг и условия магазина для каждого семейства.

    Товар витрины, у которого нет своей страницы семейства, становится отдельным узлом:
    всё, что продаётся, должно быть в графе. Возвращает порядок семейств в классах и названия классов.
    """
    path = INTERIM / "category_listing.jsonl"
    rank: dict[str, list[str]] = defaultdict(list)
    class_names: dict[str, str] = {}
    for n in nodes.values():
        n.update({"class": "", "class_name": "", "store_rank": "", "rating": "", "reviews": "", "promo": "",
                  "kind": "software", "ru": 0})
    if not path.exists():
        return rank, class_names
    hardware = set()                                    # адреса товаров-техники из карты сайта
    with (INTERIM / "sitemap_urls.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["kind"] == "hardwareProducts":
                hardware.add(f"{r['vendor_slug']}/{r['slug']}")
    by_id = {}
    for key, r in fams.items():
        for p in r["products"]:
            m = re.search(r"-(\d+)/?$", p.get("link") or "")
            pid = str(p.get("id") or (m.group(1) if m else ""))
            if pid:
                by_id.setdefault(pid, key)
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row["href"].strip("/")
            if key.count("/") != 1:
                continue
            if key not in nodes:
                m = re.search(r"-(\d+)$", key)
                if m and m.group(1) in by_id:
                    key = by_id[m.group(1)]             # товар семейства, которое уже есть в графе
                else:
                    vendor_slug = key.split("/")[0]
                    base, ed_rank, ed = split_edition(row["title"])
                    nodes[key] = {
                        "family": key, "family_id": row.get("item_id") or "", "name": row["title"],
                        "vendor": row["vendor"], "vendor_slug": vendor_slug,
                        "category": "", "subcategory": row["subcategory"],
                        "on_sale": 0, "on_request": 0, "b2b_only": 0,
                        "license_types": "", "client_types": audience(row["title"], ""), "platforms": "",
                        "typical_term": "",
                        "price_from": "", "base": " ".join(base), "edition": ed,
                        "edition_rank": "" if ed_rank is None else ed_rank, "version": version_of(row["title"]),
                        "url": f"https://store.softline.ru/{key}/",
                        "class": "", "class_name": "", "store_rank": "", "rating": "", "reviews": "", "promo": "",
                        "kind": "hardware" if key in hardware else "software", "ru": 0,
                    }
            n = nodes[key]
            if row.get("sale", "buy") == "buy":         # на карточке есть цена — товар можно купить
                n["on_sale"] = 1
                if row.get("price") and (n["price_from"] == "" or row["price"] < float(n["price_from"])):
                    n["price_from"] = row["price"]
            elif not n["on_sale"]:
                n["on_request"] = 1                     # цена только по запросу: в блок «купить» не ставим
            klass = f"{row['category_slug']}/{row['subcategory_slug']}".rstrip("/")
            if row["category_slug"] in NOT_A_CLASS:
                continue
            class_names[klass] = row["subcategory"]
            if key not in rank[klass]:
                rank[klass].append(key)
            if n["store_rank"] == "" or row["position"] < n["store_rank"]:
                promo = [t for t in row["tags"] if not t.startswith(SKIP_TAGS)]
                n.update({
                    "class": klass, "class_name": row["subcategory"], "store_rank": row["position"],
                    "rating": row["rating"] if row["rating"] is not None else "",
                    "reviews": row["reviews"] or "", "promo": " | ".join(promo)[:400],
                })
    # семейства, которых нет на витрине (сняты с продажи): класс берём из «хлебных крошек» страницы
    for key, r in fams.items():
        n = nodes[key]
        if not n["class"] and r.get("breadcrumbs"):
            last = r["breadcrumbs"][-1]
            n["class"] = last["href"].strip("/")
            n["class_name"] = last["name"]
            class_names.setdefault(n["class"], last["name"])
    return rank, class_names


def main() -> None:
    fams = load_families()
    nodes = {k: node_row(k, r) for k, r in fams.items()}
    class_rank, class_names = apply_listing(fams, nodes)
    edges = []
    edges += edges_from_compare(fams, nodes)
    edges += edges_from_names(nodes)
    edges += edges_from_links(fams)
    edges += renewal_edges(nodes)
    edges += edges_from_addons(nodes)
    edges += match_arpp(nodes)

    # одна связь на пару и тип: оставляем самую уверенную
    best: dict[tuple, dict] = {}
    for e in edges:
        k = (e["src"], e["dst"], e["type"])
        if k not in best or e["confidence"] > best[k]["confidence"]:
            best[k] = e
    edges = list(best.values())

    with (INTERIM / "graph_nodes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(next(iter(nodes.values())).keys()))
        w.writeheader()
        w.writerows(nodes.values())
    with (INTERIM / "graph_edges.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["src", "dst", "type", "source", "confidence", "evidence"])
        w.writeheader()
        w.writerows(edges)
    (INTERIM / "class_rank.json").write_text(json.dumps(class_rank, ensure_ascii=False), encoding="utf-8")
    (INTERIM / "class_names.json").write_text(json.dumps(class_names, ensure_ascii=False), encoding="utf-8")

    on_sale = sum(n["on_sale"] for n in nodes.values())
    by_type = Counter(e["type"] for e in edges)
    linked = {e["src"] for e in edges if e["type"] not in ("same_line",)}
    print(f"узлов: {len(nodes)} (страниц семейств: {len(fams)}), в продаже: {on_sale}, классов: {len(class_names)}")
    print("связей по типам:", dict(by_type))
    print(f"узлов хотя бы с одной полезной связью: {len(linked)} ({len(linked) / max(len(nodes), 1):.0%})")


if __name__ == "__main__":
    main()
