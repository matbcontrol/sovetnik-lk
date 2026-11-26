"""Проверка устойчивости главной цифры — скрипт независимой проверки проекта.

Что считается (все на протоколе организаторов, только сводные числа):
  - разброс по зёрнам обучения: модель с пятью разными random_state и без ранней остановки;
  - парные разницы с базовыми способами организаторов на тех же клиентах;
  - модель без признаков, которые могут знать будущее: без знаний каталога и без профиля клиента
    (сегмент, отрасль, тип) — снимков каталога и профиля на дату разреза нет;
  - два более ранних разреза (обучение до 01.06.2026 и до 02.03.2026) — проверка, что модель не подогнана
    под проверочные недели официального разреза: модель, базовые способы и правило «та же линейка».

Запуск:  python -m recsys.eval.robust      (около 5 минут)  ->  reports/robust.json
"""

import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from recsys.eval.value import CATALOG, Prep, by_segment, ci, paired, same_line_rule
from recsys.rank.breakdown import breakdown, kind_of
from recsys.rank.data import OFFICIAL, load_task, make_fold
from recsys.rank.features import FEATURES, FoldStats, build_attrs
from recsys.rank.ltr import copies

from recsys.stable import rerun_with_fixed_order

rerun_with_fixed_order("recsys.eval.robust")
sys.stdout.reconfigure(encoding="utf-8")
T0 = time.time()
OUT = {}


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


task = load_task()
attrs = build_attrs(task)
COL = {n: i for i, n in enumerate(FEATURES)}


def fitm(parts, seed=0, es="auto", drop=()):
    X = np.vstack([p.X for p in parts])
    y = np.concatenate([p.y for p in parts])
    if drop:
        X = X.copy()
        X[:, list(drop)] = 0
    m = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=300, max_leaf_nodes=12, min_samples_leaf=60,
                                       l2_regularization=2.0, max_bins=64, random_state=seed, early_stopping=es)
    m.fit(X, y)
    return m


def evaluate(test, model, drop=()):
    X = test.X
    if drop:
        X = X.copy()
        X[:, list(drop)] = 0
    recs = test.recommend(model.predict_proba(X)[:, 1])
    fold = test.fold
    per = {c: len(set(recs[c]) & fold.target[c]) / 3 for c in fold.group_a}
    return recs, by_segment(task, fold, per), model


def org_baselines(fold):
    pop = Counter()
    for c, d in fold.first.items():
        pop.update(d.keys())
    co = defaultdict(Counter)
    for items in fold.orders.values():
        for a in items:
            for b in items:
                if a != b:
                    co[a][b] += 1

    def top3(scores, seen):
        cand = [i for i in scores if i not in seen]
        cand.sort(key=lambda i: (-scores[i], -pop[i], i))
        return cand[:3]

    rp = {c: top3(pop, set(fold.history[c])) for c in fold.group_a}
    rc = {}
    for c in fold.group_a:
        s = Counter()
        for a in fold.history[c]:
            s.update(co[a])
        rc[c] = top3(s, set(fold.history[c]))
    return rp, rc


def seg_of(fold, recs):
    per = {c: len(set(recs[c]) & fold.target[c]) / 3 for c in fold.group_a}
    return by_segment(task, fold, per)


def show(name, seg):
    s = " ".join(f"{k} {seg[k]['value']*100:5.2f}" for k in ("A B2B", "B B2B", "A все") if k in seg)
    log(f"{name:45} {s}")
    OUT[name] = {k: seg[k]["value"] for k in seg}


# ---------- folds
specs = copies(date.fromisoformat(OFFICIAL[1]))
log("copies:", [s[1] for s in specs])
stats = {s[1]: FoldStats(task, make_fold(task, *s), attrs) for s in specs}
off = FoldStats(task, make_fold(task, *OFFICIAL), attrs)
fold = off.fold
log("stats built; group A", len(fold.group_a), "B", len(fold.group_b))

# sanity: targets equal to official protocol
from recsys.eval import official as org
ot = org.load()
same_targets = all((ot.test[c] - ot.hist[c]) == fold.target[c] for c in ot.group_a) and ot.group_a == fold.group_a
log("targets identical to official protocol:", same_targets)

# baselines on official fold via my reimplementation
rp, rc = org_baselines(fold)
show("official: popularity (my impl)", seg_of(fold, rp))
show("official: co-purchase (my impl)", seg_of(fold, rc))

# ---------- main model, seeds and early stopping
train_full = [Prep(st, "full") for st in stats.values()]
test_full = Prep(off, "full")
log("rows train", sum(len(p.y) for p in train_full), "positives", int(sum(p.y.sum() for p in train_full)),
    "test rows", len(test_full.y))
cand_cov = len({c for c, yv in zip(test_full.clients, test_full.y) if yv}) / len(fold.group_a)
log(f"official: clients with a target among candidates: {cand_cov:.1%}")
seeds = {}
recs0 = None
for seed in range(5):
    recs, seg, m = evaluate(test_full, fitm(train_full, seed=seed))
    seeds[seed] = seg
    log(f"seed {seed}: n_iter {m.n_iter_}")
    show(f"full model seed {seed}", seg)
    if seed == 0:
        recs0, model0 = recs, m
vals = [seeds[s]["A B2B"]["value"] for s in seeds]
valsb = [seeds[s]["B B2B"]["value"] for s in seeds]
log(f"A B2B over seeds: min {min(vals)*100:.2f} max {max(vals)*100:.2f} mean {np.mean(vals)*100:.2f}")
log(f"B B2B over seeds: min {min(valsb)*100:.2f} max {max(valsb)*100:.2f} mean {np.mean(valsb)*100:.2f}")
OUT["seeds_A_B2B"] = vals
OUT["seeds_B_B2B"] = valsb
recs_nes, seg_nes, m = evaluate(test_full, fitm(train_full, seed=0, es=False))
show("full model, early_stopping off", seg_nes)

# paired CI vs organizers baseline (co-purchase) on same clients
segp = seg_of(fold, rp)
segc = seg_of(fold, rc)
for k in ("A B2B", "B B2B"):
    d = paired(seeds[0][k]["values"], segc[k]["values"])
    log(f"model - copurchase {k}: {d[0]*100:+.2f} [{d[1]*100:+.2f}; {d[2]*100:+.2f}]")
    d = paired(seeds[0][k]["values"], segp[k]["values"])
    log(f"model - popularity {k}: {d[0]*100:+.2f} [{d[1]*100:+.2f}; {d[2]*100:+.2f}]")
log("bootstrap CI model A B2B", ci(seeds[0]["A B2B"]["values"]), "B B2B", ci(seeds[0]["B B2B"]["values"]))

# ---------- anonymisation-threshold channel: items with 1-2 distinct buyers in training
own_tr = Counter()
for c, d in fold.first.items():
    own_tr.update(d.keys())
b2b = [c for c in fold.group_a if task.clients[c]["client_group"] == "B2B"]
low = {i for i, n in own_tr.items() if n <= 2}
tg_low = sum(1 for c in b2b for t in fold.target[c] if t in low)
tg_all = sum(len(fold.target[c]) for c in b2b)
for name, recs in (("model", recs0), ("popularity", rp), ("copurchase", rc)):
    rec_low = sum(1 for c in b2b for b in recs[c] if b in low)
    hit_low = sum(1 for c in b2b for b in recs[c] if b in low and b in fold.target[c])
    hits = sum(len(set(recs[c]) & fold.target[c]) for c in b2b)
    log(f"{name}: recs on items with <=2 train buyers {rec_low}/{3*len(b2b)}, hits on them {hit_low}/{hits}")
log(f"targets (A B2B) on items with <=2 train buyers: {tg_low}/{tg_all}; such items {len(low)} of {len(own_tr)}")
# model hit rate by training-popularity bucket
buck = defaultdict(lambda: [0, 0])
for c in b2b:
    for b in recs0[c]:
        n = own_tr.get(b, 0)
        k = "1-2" if n <= 2 else ("3-9" if n < 10 else ("10-49" if n < 50 else "50+"))
        buck[k][0] += 1
        buck[k][1] += b in fold.target[c]
log("model recs/hits by train-buyers bucket:", dict(buck))


# ---------- robustness: without features that may know the future (catalog + client profile)
PROFILE = ["segment", "has_industry", "seg_ind", "seg_size", "ctype", "seg_gt"]
drop_prof = [COL[n] for n in PROFILE]
train_nc = [Prep(st, "no_catalog") for st in stats.values()]
test_nc = Prep(off, "no_catalog")
recs_nc, seg_nc, _ = evaluate(test_nc, fitm(train_nc))
show("no_catalog (as value.py)", seg_nc)
recs_ncp, seg_ncp, _ = evaluate(test_nc, fitm(train_nc, drop=drop_prof), drop=drop_prof)
show("no_catalog + no client profile", seg_ncp)
recs_p, seg_p, _ = evaluate(test_full, fitm(train_full, drop=drop_prof), drop=drop_prof)
show("full minus client profile", seg_p)
for k in ("A B2B", "B B2B"):
    d = paired(seg_ncp[k]["values"], segc[k]["values"])
    log(f"no_catalog+no_profile - copurchase {k}: {d[0]*100:+.2f} [{d[1]*100:+.2f}; {d[2]*100:+.2f}]")
bd = breakdown(task, fold, attrs, recs_ncp)["A B2B"]["hits"]
log("no_catalog+no_profile hits A B2B by kind:", bd)

# ---------- other time split: inner window (train <= 2026-06-01, test 2026-06-08..2026-08-31)
for (tu, tf, tt) in (("2026-06-01", "2026-06-08", "2026-08-31"), ("2026-03-02", "2026-03-09", "2026-06-01")):
    lim = date.fromisoformat(tu)
    sp = copies(lim)
    tr = [Prep(stats[s[1]] if s[1] in stats else FoldStats(task, make_fold(task, *s), attrs), "full") for s in sp]
    vst = FoldStats(task, make_fold(task, "v", tu, tf, tt), attrs)
    vt = Prep(vst, "full")
    vf = vst.fold
    nb2b = sum(task.clients[c]["client_group"] == "B2B" for c in vf.group_a)
    nbb = sum(task.clients[c]["client_group"] == "B2B" for c in vf.group_b)
    log(f"split {tu}: copies {len(sp)} (last {sp[-1][1]}..{sp[-1][3]}), group A {len(vf.group_a)} (B2B {nb2b}), B B2B {nbb}")
    r_m, s_m, _ = evaluate(vt, fitm(tr))
    vp, vc = org_baselines(vf)
    s_p, s_c = seg_of(vf, vp), seg_of(vf, vc)
    s_r = seg_of(vf, {c: same_line_rule(vst, c) for c in vf.group_a})
    show(f"split {tu}: model", s_m)
    show(f"split {tu}: popularity", s_p)
    show(f"split {tu}: co-purchase", s_c)
    show(f"split {tu}: same-line rule", s_r)
    for k in ("A B2B",):
        d = paired(s_m[k]["values"], s_r[k]["values"])
        log(f"split {tu}: model - rule {k}: {d[0]*100:+.2f} [{d[1]*100:+.2f}; {d[2]*100:+.2f}]")
        log(f"split {tu}: CI model {k}", ci(s_m[k]["values"]))
    log(f"split {tu}: model hits by kind A B2B", breakdown(task, vf, attrs, r_m)["A B2B"]["hits"],
        "targets", breakdown(task, vf, attrs, r_m)["A B2B"]["targets"])

from recsys.config import ROOT
(ROOT / "reports" / "robust.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
log("done")
