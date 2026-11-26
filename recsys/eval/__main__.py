"""Проверка на истории продаж из командной строки.

  python -m recsys.eval --events data/interim/events.csv --cutoff 2026-04-30
  python -m recsys.eval --synthetic            # проверка самого счёта на искусственных данных

Файл событий — приведённые продажи: client_id, date, family, klass, client_type, industry,
order_id, qty, license_type, term_months, sku (лишние столбцы не мешают, недостающие считаются пустыми).
Отчёт: reports/backtest.json и таблица на экране.
"""
import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from recsys.config import ROOT
from recsys.eval import backtest
from recsys.eval.backtest import Event
from recsys.eval.baselines import ALL
from recsys.eval.graph_model import GraphModel
from recsys.recommend.engine import Graph


def load_events(path: Path) -> list[Event]:
    out = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            term = r.get("term_months") or ""
            out.append(Event(
                client_id=r["client_id"], date=date.fromisoformat(r["date"][:10]), family=r["family"],
                klass=r.get("klass", ""), client_type=r.get("client_type", ""), industry=r.get("industry", ""),
                order_id=r.get("order_id", ""), qty=float(r.get("qty") or 1), license_type=r.get("license_type", ""),
                term_months=int(float(term)) if term else None, sku=r.get("sku", ""),
            ))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="")
    ap.add_argument("--synthetic", action="store_true", help="искусственные данные: только проверка счёта")
    ap.add_argument("--cutoff", default="", help="дата разреза; по умолчанию — конец данных минус окно")
    ap.add_argument("--horizon", type=int, default=180)
    ap.add_argument("--level", default="family", choices=["family", "class"])
    args = ap.parse_args()

    graph = Graph.load()
    if args.synthetic:
        from recsys.eval.synthetic import generate
        events = generate(graph)
        print("ВНИМАНИЕ: данные искусственные, числа ниже проверяют только сам счёт и результатом не являются.\n")
    else:
        events = load_events(Path(args.events))
    last = max(e.date for e in events)
    cutoff = date.fromisoformat(args.cutoff) if args.cutoff else last - timedelta(days=args.horizon)
    print(f"событий {len(events)}, клиентов {len({e.client_id for e in events})}, "
          f"семейств {len({e.family for e in events})}, даты {min(e.date for e in events)} — {last}\n")

    reports = []
    for only_new in (False, True):
        models = [m() for m in ALL] + [GraphModel(graph, use_sales=False, horizon_days=args.horizon),
                                       GraphModel(graph, use_sales=True, horizon_days=args.horizon)]
        models[-2].name = "граф без продаж"
        rep = backtest.compare(models, events, cutoff, args.horizon, 3, args.level, only_new)
        rep["calibration"] = models[-1].calibration
        reports.append(rep)
        print(backtest.table(rep), "\n")
    if reports[0].get("calibration"):
        print("сверка связей с покупками (внутри «прошлого»):")
        for t, row in reports[0]["calibration"].items():
            print(f"  {t:14} могла сработать {row['tried']:>6}, подтверждена {row['confirmed']:>5} ({row['rate']:.1%})")
    out = ROOT / "reports" / ("backtest_synthetic.json" if args.synthetic else "backtest.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nотчёт: {out}")


if __name__ == "__main__":
    main()
