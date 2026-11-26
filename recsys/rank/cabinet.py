"""Блок личного кабинета с вердиктами пар: что клиент увидит на самом деле.

Порядок кандидатов даёт единая оценка (recsys.rank.unified). Поверх неё — заранее вынесенные
вердикты «купил А — советовать ли Б» (recsys.llm.pair_verdicts):

  «Пора продлить»      до двух продлений, срок которых подходит (вердикты их не касаются);
  «К вашим продуктам»  до трёх советов: другой вариант своей линейки, товар каталога по проверенной
                       связи графа от покупки клиента и чужие линейки с вердиктом «нужно»;
  «Комплект»           вердикт «комплект» (прямой связи нет, но товар из того же типового набора)
                       клиенту не показывается: на истории такие советы не сбылись ни разу
                       (recsys.eval.verdict_effect). KIT_MAX оставлен настройкой для замера.

  «Часто докупают»     одна строка: продукт, который впервые берут не меньше 5 % вернувшихся клиентов
                       группы. Это не личный совет, и подписан он так же честно; «уже есть» и «то же
                       самое» по покупкам клиента в эту строку не попадают.

Кандидаты с вердиктом «уже есть», «то же самое», «не связано» и пары без вердикта в личные советы
не идут. Популярное в свободные места не подмешивается: личных советов может быть меньше трёх.
"""
from __future__ import annotations

from dataclasses import dataclass

from recsys.llm.pair_verdicts import SHOWN, Verdicts
from recsys.rank.unified import NEW_MAX, Scored, Unified

KIT_MAX = 0                  # сколько советов «комплект» идёт в личные советы; замер показал, что они не сбываются
HORIZON = 15                 # сколько чужих линеек по порядку оценки рассматриваем для одного клиента
POPULAR_N = None             # товаров в строке «Часто докупают»: один, а если личных советов меньше трёх — по числу свободных мест
MARK = "вердикт|"            # так помечаются причины отсева, которые дал вердикт пары


@dataclass
class Card:
    s: Scored
    tier: str            # renew / line / graph / need / kit / popular / plain (блок без проверки пар)
    reason: str = ""     # фраза «зачем» из вердикта
    by: str = ""         # покупка клиента (товар), к которой относится совет


def classify(at: dict, verdicts: Verdicts, history: list[str], s: Scored) -> tuple[str, str, str]:
    """Вид кандидата, фраза «зачем» и покупка клиента, от которой он идёт.

    line    — другой вариант линейки, которая у клиента уже есть;
    graph   — товар каталога по проверенной связи графа от покупки клиента (новая версия, старшая
              редакция, аналог из реестра) либо преемник снятого с продажи товара своей линейки;
    need, kit, has, same, unrelated — вердикт пары «линейка клиента — линейка кандидата»;
    unknown — пара ещё не размечена.
    Если товар занял место снятого с продажи (s.replaces), судится тот, чьё место он занял: иначе
    популярный товар вернулся бы в блок под видом своей замены.
    """
    if s.src:
        return "graph", "", s.src
    ab = at.get(s.replaces or s.item)
    if ab is None:
        return "unknown", "", ""
    last: dict[str, str] = {}
    for a in history:                                  # последняя покупка каждой линейки
        last[at[a].line] = a
    if ab.line in last:
        return ("graph" if s.replaces else "line"), "", last[ab.line]
    if ab.nline:
        for a in reversed(history):
            if at[a].nline == ab.nline:
                return ("graph" if s.replaces else "line"), "", a
    j = verdicts.judge(dict.fromkeys(at[a].line for a in reversed(history)), ab.line)
    return j.verdict or "unknown", j.reason, last.get(j.by, "")


def keeper(at: dict, verdicts: Verdicts, allow: tuple[str, ...] = SHOWN, popular: frozenset[str] = frozenset()):
    """Фильтр новых советов для Unified.cabinet: (история клиента, кандидат) -> (оставить, причина).

    popular — продукты, популярные в группе клиента: «комплект» из их числа в личные советы не идёт,
    его место — строка «Часто докупают» с честной подписью.
    """
    steps: set[tuple[str, str]] = set()                # по одному совету «следующая ступень» и «замена» на покупку
    foreign: list[str] = []                            # чужие линейки в порядке оценки: дальше горизонта не смотрим
    shown: list[str] = []                              # чужие линейки, уже принятые в блок

    def keep(history: list[str], s: Scored) -> tuple[bool, str]:
        ab = at.get(s.replaces or s.item)
        if not s.src and ab is not None:
            own = {at[a].line for a in history}
            own_n = {at[a].nline for a in history if at[a].nline}
            if ab.line not in own and not (ab.nline and ab.nline in own_n):
                if ab.line not in foreign:
                    foreign.append(ab.line)
                if foreign.index(ab.line) >= HORIZON:
                    # пары размечаются только для первых HORIZON кандидатов клиента: ниже оценка модели слишком мала
                    return False, "за горизонтом кандидатов: оценка модели слишком низкая"
        tier, _, by = classify(at, verdicts, history, s)
        if tier == "graph":
            group = ("замена" if s.basis == "замена" else "ступень", by)
            if group in steps:
                return False, "по этой покупке уже есть совет о следующей ступени или замене"
            steps.add(group)
            return True, ""
        if tier == "line" and ab is not None and ab.version is not None:
            mine = [at[a].version for a in history if at[a].version is not None
                    and (at[a].line == ab.line or (ab.nline and at[a].nline == ab.nline))]
            if mine and ab.version < min(mine):          # версии старше всех купленных у клиента не было
                return False, "предыдущая версия уже купленного продукта"
        if tier == "kit" and ab is not None and (ab.nline or ab.line) in popular:
            return False, "комплект из числа популярных: идёт строкой «Часто докупают»"
        if tier == "line":
            return True, ""
        if tier in allow and ab is not None:
            # два совета, которые по вердикту пары — одно и то же (две программы подписи файлов), не нужны
            for k in shown:
                for pair in ((k, ab.line), (ab.line, k)):
                    v = verdicts.get(*pair)
                    if v is not None and v.verdict == "same":
                        return False, "то же самое, что уже показанный совет"
            shown.append(ab.line)
            return True, ""
        return False, f"{MARK}{tier}|{by}"
    return keep


def cabinet(uni: Unified, c: str, verdicts: Verdicts | None, prices: dict[str, float] | None = None,
            kit_max: int = KIT_MAX, kit_first: bool = False, popular_row: bool = True) -> dict:
    """{"renew": [Card], "new": [Card], "popular": [Card], "rejected": [...], "dropped": [...]}.

    verdicts=None — блок без проверки пар, как было: популярное идёт вперемешку с остальным.

    rejected — кандидаты, которых убрал вердикт пары: {"item", "verdict", "by"} в порядке оценки модели.
    kit_first=True — «нужно» и «комплект» идут вперемешку по оценке модели (для сравнения в замерах).
    """
    if verdicts is None:                                 # как было: первые три по оценке, популярное вперемешку
        cab = uni.cabinet(c, prices, lambda h, s: (True, ""))
        return {"renew": [Card(s, "renew") for s in cab["renew"]], "new": [Card(s, "plain") for s in cab["new"]],
                "popular": [], "rejected": [], "dropped": cab["dropped"]}
    at, h = uni.at, uni.fold.history[c]
    common = frozenset(uni.popular.get(uni.task.clients.get(c, {}).get("client_group", ""), {})) if popular_row         else frozenset()
    if kit_first:
        cab = uni.cabinet(c, prices, keeper(at, verdicts, ("need", "kit")))
        new = list(cab["new"])
    else:
        cab = uni.cabinet(c, prices, keeper(at, verdicts, ("need",)))
        new = list(cab["new"])
        if len(new) < NEW_MAX and kit_max:
            wide = uni.cabinet(c, prices, keeper(at, verdicts, ("need", "kit"), common))
            have = {s.item for s in new}
            kits = [s for s in wide["new"] if s.item not in have][:kit_max]
            new += kits[: NEW_MAX - len(new)]
    shown = {s.item for s in new}
    rejected = []
    for d in cab["dropped"]:
        if d["reason"].startswith(MARK) and d["item"] not in shown:
            _, tier, by = d["reason"].split("|", 2)
            rejected.append({"item": d["item"], "verdict": tier, "by": by})

    def card(s: Scored) -> Card:
        tier, reason, by = classify(at, verdicts, h, s)
        return Card(s, tier, reason, by)

    def not_owned(history: list[str], s: Scored) -> tuple[bool, str]:
        return classify(at, verdicts, history, s)[0] != "has", ""    # «то же самое» здесь допустимо: вторую лицензию докупают

    popular = uni.popular_row(c, [*cab["renew"], *new], prices, not_owned, n=POPULAR_N) if popular_row else []
    return {"renew": [Card(s, "renew") for s in cab["renew"]], "new": [card(s) for s in new],
            "popular": [Card(s, "popular") for s in popular], "rejected": rejected, "dropped": cab["dropped"]}
