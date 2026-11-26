"""Проба рекомендателя из командной строки.

Пример:
  python -m recsys.recommend --type b2b --today 2026-11-26 \
      contentai/contentreader-pdf-15-standard:2025-12-10:12  adobe/adobe-acrobat-standard

Покупка задаётся как «семейство[:дата покупки[:срок в месяцах]]».
"""
import argparse
from datetime import date

from recsys.recommend.engine import Client, Graph, Purchase, Recommender


def parse_purchase(s: str) -> Purchase:
    parts = s.split(":")
    return Purchase(
        family=parts[0],
        date=date.fromisoformat(parts[1]) if len(parts) > 1 and parts[1] else None,
        term_months=int(parts[2]) if len(parts) > 2 and parts[2] else None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("purchases", nargs="*", help="семейство[:ГГГГ-ММ-ДД[:срок]]")
    ap.add_argument("--type", default="b2b", choices=["b2b", "b2c"])
    ap.add_argument("--today", default="")
    ap.add_argument("--min-confidence", type=float, default=0.0)
    args = ap.parse_args()

    g = Graph.load()
    client = Client(
        client_type=args.type,
        purchases=[parse_purchase(p) for p in args.purchases],
        today=date.fromisoformat(args.today) if args.today else date.today(),
    )
    unknown = [p.family for p in client.purchases if p.family not in g.nodes]
    if unknown:
        print("Нет в графе:", ", ".join(unknown))
    recs, bans = Recommender(g, min_confidence=args.min_confidence).recommend(client, explain_bans=True)
    if not recs:
        print("Советов нет: у купленного пока нет связей в графе.")
    for i, r in enumerate(recs, 1):
        print(f"{i}. [{r.slot_title}] {r.name} — {r.vendor}  (оценка {r.score:.2f}; {r.label})")
        print(f"   {r.why}")
        if r.also:
            print(f"   Заодно: {r.also}")
        for p in r.paths:
            print(f"     путь: {p['src']} --{p['type']}--> {p['dst']}  ({p['source']}, уверенность {p['confidence']})")
    if bans:
        print("Отсеяно запретами:")
        for b in bans[:10]:
            print(f"   {b['family']} ({b['type']} от {b['from']}): {b['reason']}")


if __name__ == "__main__":
    main()
