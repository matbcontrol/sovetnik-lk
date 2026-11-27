"""Правки первой разметки линеек после просмотра: пишет data/private/task/pairs/fixes.tsv.

Часть правок опирается на продажи: «клиенты заметно часто берут Б следом за А». Эта сверка считается
ТОЛЬКО по обучающему периоду официальной проверки (покупки до 31.08.2026 включительно). Первая версия
скрипта считала её по всем продажам, включая проверочные месяцы, — это завышало числа блока кабинета
(recsys.eval.verdict_effect); на протокольную цифру вердикты не влияют.

Правило «то же самое, но берут следом → нужно» для повторной покупки того же продукта убрано: это
продление под другим артикулом, а заказчик просил продления в блок рекомендаций не ставить.

Запуск: python -m recsys.llm.line_fixes, затем python -m recsys.llm.pair_verdicts merge
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date

from recsys.llm.pair_verdicts import CODES, WORK, _numbered
from recsys.rank.data import OFFICIAL, load_task
from recsys.rank.features import build_attrs

OS = ("операционн", "astra linux", "альт сп", "альт рабочая", "ред ос", "windows")
CBI = ("terrier", "фикс", "ревизор", "фиксации и контроля исходного", "модели системы разграничения",
       "контроля полномочий доступа", "поиска и гарантированного уничтожения")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    info = json.loads((WORK / "line_info.json").read_text(encoding="utf-8"))
    table, _, _ = _numbered(WORK, "ids.json", "out_*.tsv")       # разметка как она пришла, без прежних правок
    task = load_task()
    at = build_attrs(task)
    until = date.fromisoformat(OFFICIAL[1])
    first: dict[str, dict[str, date]] = defaultdict(dict)
    for r in task.rows:
        if r.week > until:
            continue                                            # проверочный период в сверку не входит
        line = at[r.item].line
        if line not in first[r.client] or r.week < first[r.client][line]:
            first[r.client][line] = r.week
    owners, after = Counter(), Counter()
    for bought in first.values():
        for a, wa in bought.items():
            owners[a] += 1
            for b, wb in bought.items():
                if a != b and wb > wa:
                    after[(a, b)] += 1
    rows, why = [], Counter()
    for (a, b), v in sorted(table.items()):
        an, bn = info[a]["name"].lower(), info[b]["name"].lower()
        sub = (info[a].get("sub") or "").lower()
        often = after[(a, b)] >= 3 and after[(a, b)] / max(owners[a], 1) >= 0.10
        # 1. операционная система сама по себе не создаёт потребность в криптопровайдере: это комплект
        if v.verdict == "need" and "csp" in bn and ("операционн" in sub or any(x in an for x in OS)):
            rows.append((a, b, "K", "Если нужна электронная подпись: криптопровайдер с версией для этой системы — "
                                    "для госпорталов и ЭДО"))
            why["ОС → криптопровайдер: комплект"] += 1
        # 2. зарубежный продукт сняли с продажи, клиенты переходят на российский аналог — это замена, а не «то же самое»
        elif v.verdict == "same" and often and "adobe" in an and "contentreader" in bn:
            rows.append((a, b, "N", "Российская замена: прежний продукт в магазине больше не продаётся"))
            why["замена снятого с продажи продукта"] += 1
        # 3. инструменты ЦБИ для аттестации (TERRIER, ФИКС, Ревизор) берут набором; разметчик не узнал их по описаниям
        elif v.verdict == "unrelated" and often and any(x in an for x in CBI) and any(x in bn for x in CBI):
            rows.append((a, b, "N", "Инструменты одной аттестационной проверки: контроль доступа, целостности "
                                    "и остаточной информации"))
            why["набор ЦБИ для аттестации"] += 1
        # 4. программа подписи работает через криптопровайдер, но лицензию на него покупают отдельно
        elif v.verdict == "has" and "криптоарм" in an and "криптопро csp" in bn:
            rows.append((a, b, "N", "Подпись по ГОСТ создаёт криптопровайдер: на этом компьютере ему нужна своя лицензия"))
            why["КриптоАРМ → CSP: нужно"] += 1
    assert all(code in CODES for _, _, code, _ in rows)
    (WORK / "fixes.tsv").write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    print(f"правок: {len(rows)}; сверка с покупками — по {OFFICIAL[1]} включительно")
    for k, n in why.most_common():
        print(f"  {n:3}  {k}")


if __name__ == "__main__":
    main()
