/* Движок советов для страницы стенда — точная копия recsys/recommend/engine.py.
 *
 * Совет считается в браузере по файлу графа: ни сервера, ни модели на запрос нет.
 * Совпадение с движком на Python проверяется на контрольных клиентах: node tests/parity.mjs
 */
(function (root) {
  "use strict";

  const SLOT = {
    renewal: "renew", upgrade: "next", new_version: "next",
    requires: "missing", complements: "missing", compatible: "missing",
    copurchase: "missing", next_purchase: "missing", mentions: "missing",
    analog_ru: "replace",
  };
  const SLOT_TITLE = {
    renew: "Что истекает", next: "Следующая ступень", missing: "Чего не хватает",
    replace: "Замена", popular: "Часто берут",
  };
  const WEIGHT = {
    renewal: 1.0, requires: 0.85, upgrade: 0.7, new_version: 0.6,
    copurchase: 0.65, next_purchase: 0.65, complements: 0.55, compatible: 0.5,
    analog_ru: 0.45, mentions: 0.25,
  };
  const LABEL = {
    "таблица сравнения магазина": "подтверждено магазином",
    "типы лицензий на странице": "подтверждено магазином",
    "срок лицензии": "срок вашей покупки",
    "ссылка на странице магазина": "упоминание на странице магазина",
    "названия редакций": "выведено из названий",
    "номера версий в названиях": "выведено из названий",
    "названия": "выведено из названий",
    "название товара": "сказано в названии товара",
    "таблица замещения АРПП": "реестр российского ПО",
    "продажи": "подтверждено покупками",
    "модель": "вывод модели, проверен",
    "витрина": "порядок витрины магазина",
  };
  const TYPE_TITLE = {
    renewal: "продление", upgrade: "старшая редакция", new_version: "новая версия",
    requires: "требуется", complements: "дополняет", compatible: "совместимо",
    copurchase: "покупают вместе", next_purchase: "покупают следом", mentions: "упоминание",
    analog_ru: "российский аналог", same_line: "та же линейка", popular: "часто берут",
  };
  const RENEW_WINDOW = [-60, 120];
  const DAY = 86400000;

  function parseDate(s) {
    if (!s) return null;
    const p = String(s).slice(0, 10).split("-").map(Number);
    const ms = Date.UTC(p[0], p[1] - 1, p[2]);
    return Number.isFinite(ms) ? ms : null;          // нечитаемая дата — то же, что дата неизвестна
  }
  function fmt(ms) {
    const d = new Date(ms);
    const two = (x) => String(x).padStart(2, "0");
    return `${two(d.getUTCDate())}.${two(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
  }
  function addMonths(ms, months) {
    const d = new Date(ms);
    const total = d.getUTCMonth() + months;
    const y = d.getUTCFullYear() + Math.floor(total / 12);
    const m = ((total % 12) + 12) % 12;
    const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
    const dim = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m];
    return Date.UTC(y, m, Math.min(d.getUTCDate(), dim));
  }
  const split = (s) => new Set(String(s || "").split("|").filter(Boolean));
  const round = (x, n) => Math.round(x * 10 ** n) / 10 ** n;

  class Graph {
    constructor(data) {
      this.meta = data.meta || {};
      this.nodes = new Map();
      for (const n of data.nodes) this.nodes.set(n.id, n);
      this.classes = data.classes || {};
      this.out = new Map();
      this.inc = new Map();
      this.line = new Map();
      this.analogs = new Map();
      const add = (map, k, v) => { if (!map.has(k)) map.set(k, new Set()); map.get(k).add(v); };
      for (const row of data.edges) {
        const e = { src: row[0], dst: row[1], type: row[2], source: row[3], confidence: row[4], evidence: row[5] || "" };
        if (e.type === "same_line") { add(this.line, e.src, e.dst); add(this.line, e.dst, e.src); continue; }
        if (e.type === "analog_ru") { add(this.analogs, e.src, e.dst); add(this.analogs, e.dst, e.src); }
        if (!this.out.has(e.src)) this.out.set(e.src, []);
        this.out.get(e.src).push(e);
        if (!this.inc.has(e.dst)) this.inc.set(e.dst, []);
        this.inc.get(e.dst).push(e);
      }
      this.classLinks = new Map();
      for (const link of data.class_links || []) {
        if (!this.classLinks.has(link.src_class)) this.classLinks.set(link.src_class, []);
        this.classLinks.get(link.src_class).push(link);
      }
      this.classRank = data.class_rank || {};
      this.popular = data.popular || {};
    }
    onSale(f) { const n = this.nodes.get(f); return !!(n && n.sale === 1); }
    byRequest(f) { const n = this.nodes.get(f); return !!(n && n.sale !== 1 && n.req === 1 && n.ru === 1); }
    available(f, clientType) { return this.onSale(f) || (clientType === "b2b" && this.byRequest(f)); }
    klass(f) { const n = this.nodes.get(f); return (n && n.cls) || "/"; }
    lineOf(f) { return this.line.get(f) || new Set(); }
  }

  class Recommender {
    constructor(graph, opts) {
      opts = opts || {};
      this.g = graph;
      this.w = Object.assign({}, WEIGHT, opts.weights || {});
      this.minConfidence = opts.minConfidence || 0;
      this.diversify = opts.diversify !== false;
      this.renewWindow = opts.renewWindow || RENEW_WINDOW;
      this.fill = !!opts.fill;
    }

    _term(p) {
      if (p.term !== null && p.term !== undefined) return [p.term, false];
      const n = this.g.nodes.get(p.family);
      return [n && n.term !== undefined && n.term !== null ? n.term : null, true];
    }

    _renewal(client, p) {
      const [term, assumed] = this._term(p);
      if (term === 0) return null;
      const n = this.g.nodes.get(p.family) || {};
      const hasRenewal = split(n.lic).has("renewal");
      let strength, note;
      if (p.dateMs !== null && term) {
        const end = addMonths(p.dateMs, term);
        const left = Math.round((end - client.todayMs) / DAY);
        if (!(this.renewWindow[0] <= left && left <= this.renewWindow[1])) return null;
        strength = (hasRenewal ? 0.95 : 0.85) * (assumed ? 0.8 : 1.0);
        const when = left >= 0 ? `истекает ${fmt(end)}` : `истекла ${fmt(end)}`;
        note = `куплена ${fmt(p.dateMs)}, ${assumed ? "обычно на" : "на"} ${term} мес., ${when}`;
      } else if (hasRenewal) {
        strength = 0.35;
        note = "срок покупки неизвестен; в магазине есть лицензия продления";
      } else {
        return null;
      }
      let target = p.family;
      if (!this.g.available(target, client.type)) {
        const newer = (this.g.out.get(p.family) || []).filter((e) => e.type === "new_version" && this.g.available(e.dst, client.type));
        if (!newer.length) return null;
        let best = newer[0];
        for (const e of newer) if (e.confidence > best.confidence) best = e;
        target = best.dst;
        note += "; эта версия снята с продажи, продаётся следующая";
      }
      const edge = {
        src: p.family, dst: target, type: "renewal",
        source: hasRenewal ? "типы лицензий на странице" : "срок лицензии",
        confidence: round(strength, 2), evidence: note,
      };
      return [edge, this.w.renewal * strength];
    }

    _banned(client, owned, platforms, dst, edgeType, src) {
      const g = this.g;
      const n = g.nodes.get(dst);
      if (!n || !g.available(dst, client.type)) return "нет в продаже";
      if (edgeType !== "renewal" && owned.has(dst)) return "уже куплено";
      const b2bOnly = (x) => x.b2b === 1;
      const homeOnly = (x) => { const c = split(x.ct); return c.size === 1 && c.has("b2c") && !b2bOnly(x); };
      const s = g.nodes.get(src) || {};
      const inLine = edgeType === "renewal" || edgeType === "upgrade" || edgeType === "new_version";
      if (client.type === "b2c" && b2bOnly(n) && !(inLine && b2bOnly(s))) return "продаётся только юридическим лицам";
      if (client.type === "b2b" && homeOnly(n) && !(inLine && homeOnly(s))) return "лицензия только для дома";
      const np = new Set();
      for (const x of split(n.pf)) for (const p of x.split(",")) if (p.trim()) np.add(p.trim().toLowerCase());
      if (edgeType !== "renewal" && platforms.size && np.size && ![...platforms].some((p) => np.has(p))) return "не та платформа";
      if (!inLine) {
        for (const o of owned.keys()) {
          if (g.lineOf(o).has(dst)) return "та же линейка, что уже куплена";
          if (edgeType !== "analog_ru" && (g.analogs.get(o) || new Set()).has(dst) && g.onSale(o)) return "аналог уже купленного";
        }
      }
      if (edgeType === "upgrade" || edgeType === "new_version") {
        for (const o of owned.keys()) {
          if (o !== src && g.lineOf(o).has(dst)) {
            const ro = (g.nodes.get(o) || {}).rank, rd = n.rank;
            if (ro !== undefined && ro !== null && rd !== undefined && rd !== null && ro >= rd) {
              return "в линейке уже куплена такая же или старшая редакция";
            }
          }
        }
      }
      return "";
    }

    _why(edge) {
      const g = this.g;
      const name = (f) => (g.nodes.get(f) || {}).name || f;
      const q = (x) => { x = String(x || "").trim(); return x.startsWith("«") && x.endsWith("»") ? x : `«${x}»`; };
      const src = q(name(edge.src)), dst = q(name(edge.dst)), t = edge.type, ev = edge.evidence || "";
      if (edge.class) {
        const cname = g.classes[edge.class] || edge.class;
        return `${ev.replace(/[. ]+$/, "")}. Подойдёт ${dst} — ${edge.rank}-е место на витрине магазина в классе «${cname}».`;
      }
      if (t === "renewal") {
        return edge.src !== edge.dst
          ? `Лицензия ${src}: ${ev}. Актуальная версия — ${dst}.`
          : `Лицензия ${src}: ${ev}. Продление сохраняет работу без перерыва.`;
      }
      if (t === "upgrade") {
        const gained = ev.includes("добавляет:") ? ev.split("добавляет:")[1].split("; http")[0].trim() : "";
        return gained
          ? `У вас ${src}. Редакция ${dst} добавляет то, чего нет в вашей: ${gained}.`
          : `У вас ${src}. Следующая редакция в этой линейке — ${dst}.`;
      }
      if (t === "new_version") return `У вас ${src}. В продаже есть более новая версия — ${dst}.`;
      if (t === "analog_ru") {
        const sn = g.nodes.get(edge.src) || {};
        const gone = g.onSale(edge.src) ? "" : (sn.req === 1 ? ", который теперь продаётся только по запросу" : ", которого больше нет в продаже");
        const reg = ev.includes("(реестр № ") ? ev.split("(реестр № ")[1].split(")")[0] : "";
        const tail = reg ? ` (реестр российского ПО, запись № ${reg})` : "";
        return `${src} — зарубежный продукт${gone}. Российский аналог: ${dst}${tail}.`;
      }
      if (t === "requires") return `Для работы ${src} нужен ${dst}. ${ev}`.trim();
      if (t === "copurchase" || t === "next_purchase") return `Клиенты, купившие ${src}, берут и ${dst}. ${ev}`.trim();
      if (t === "mentions") return `На странице ${src} магазин упоминает ${dst}.`;
      return `К ${src} подходит ${dst}. ${ev}`.trim();
    }

    _rec(dst, slot, score, edge, paths, fallback) {
      const n = this.g.nodes.get(dst);
      return {
        family: dst, name: n.name, vendor: n.vendor || "", url: n.url || "", slot, slotTitle: SLOT_TITLE[slot],
        score, why: "", label: LABEL[edge.source] || edge.source || "", paths, fallback: !!fallback, also: "", alsoFamily: "",
        viaClass: edge.class || "",
      };
    }

    _popular(client, owned, platforms, taken, k) {
      const g = this.g;
      const segKey = `${client.type}|${client.industry || ""}`;
      const pool = (client.industry && g.popular[segKey]) || g.popular[client.type] || [];
      const block = new Set();
      for (const r of taken) { block.add(r.family); for (const x of g.lineOf(r.family)) block.add(x); }
      const out = [];
      for (const it of pool) {
        if (taken.length + out.length >= k) break;
        const fam = it.family;
        if (block.has(fam) || !g.nodes.has(fam) || this._banned(client, owned, platforms, fam, "popular", "")) continue;
        const who = client.industry && g.popular[segKey] ? "клиенты вашей отрасли" : (client.type === "b2b" ? "организации" : "частные покупатели");
        const source = it.source || "продажи";
        const ev = it.evidence || "";
        const why = source === "продажи"
          ? `Чаще всего берут ${who}: ${ev}`.replace(/[: ]+$/, "") + "."
          : `Один из самых востребованных товаров магазина: ${ev}.`;
        const edge = { src: "", dst: fam, type: "popular", source, confidence: 0, evidence: ev, score: 0 };
        const rec = this._rec(fam, "popular", 0, edge, [edge], true);
        rec.why = why;
        out.push(rec);
        block.add(fam);
        for (const x of g.lineOf(fam)) block.add(x);
      }
      return out;
    }

    recommend(client, k) {
      k = k || 3;
      const g = this.g;
      const todayMs = parseDate(client.today);
      client = Object.assign({}, client, { todayMs: todayMs === null ? parseDate(new Date().toISOString()) : todayMs });
      const purchases = (client.purchases || []).map((p) => Object.assign({}, p, { dateMs: parseDate(p.date) }));
      const ordered = purchases.slice().sort((a, b) => (a.dateMs === null ? -Infinity : a.dateMs) - (b.dateMs === null ? -Infinity : b.dateMs));
      const owned = new Map();
      for (const p of ordered) owned.set(p.family, p);
      const platforms = new Set();
      for (const p of purchases) for (const x of String(p.platform || "").split(",")) if (x.trim()) platforms.add(x.trim().toLowerCase());

      const cands = new Map();
      const bans = [];
      const push = (dst, slot, s, edge) => {
        const key = dst + "\u0000" + slot;
        if (!cands.has(key)) cands.set(key, { dst, slot, miss: 1.0, paths: [], best: 0, bestEdge: null });
        const c = cands.get(key);
        c.miss *= 1.0 - Math.min(Math.max(s, 0), 0.99);
        c.paths.push(Object.assign({}, edge, { score: round(s, 3) }));
        if (s > c.best) { c.best = s; c.bestEdge = edge; }
      };

      for (const [fam, p] of owned) {
        const age = p.dateMs === null ? null : Math.round((client.todayMs - p.dateMs) / DAY);
        const recency = age === null ? 0.7 : Math.max(0.3, Math.exp(-Math.max(age, 0) / 730));
        const renewal = this._renewal(client, p);
        if (renewal) {
          const [edge, s] = renewal;
          const reason = this._banned(client, owned, platforms, edge.dst, "renewal", fam);
          if (reason) bans.push({ family: edge.dst, type: "renewal", from: fam, reason });
          else push(edge.dst, "renew", s, edge);
        }
        for (const e of g.out.get(fam) || []) {
          const t = e.type;
          if (t === "renewal" || e.confidence < this.minConfidence || !(t in this.w)) continue;
          const reason = this._banned(client, owned, platforms, e.dst, t, fam);
          if (reason) { bans.push({ family: e.dst, type: t, from: fam, reason }); continue; }
          let s = this.w[t] * e.confidence * recency;
          if (t === "analog_ru" && !g.onSale(fam)) s = Math.min(0.95, s * 2.0);
          if (!g.onSale(e.dst)) s *= 0.8;
          push(e.dst, SLOT[t], s, e);
        }
        for (const link of g.classLinks.get(g.klass(fam)) || []) {
          if (link.confidence < this.minConfidence) continue;
          const aud = link.audience || "any";
          if (aud !== "any" && aud !== client.type) continue;
          const t = link.type in this.w ? link.type : "complements";
          const list = (g.classRank[link.dst_class] || []).slice(0, 10);
          list.forEach((dst, rank) => {
            if (this._banned(client, owned, platforms, dst, t, fam)) return;
            const edge = {
              src: fam, dst, type: t, source: link.source || "модель", confidence: link.confidence,
              evidence: link.reason || "", class: link.dst_class, rank: rank + 1,
            };
            push(dst, "missing", (this.w[t] * link.confidence * recency) / (1 + 0.35 * rank), edge);
          });
        }
      }

      const recs = [];
      for (const c of cands.values()) {
        if (!c.bestEdge) continue;                     // у кандидата нет ни одного основания с весом
        const paths = c.paths.slice().sort((a, b) => b.score - a.score).slice(0, 4);
        const rec = this._rec(c.dst, c.slot, round(1.0 - c.miss, 4), c.bestEdge, paths, false);
        rec.why = this._why(c.bestEdge);
        recs.push(rec);
      }
      let top = this._pick(recs, k);
      if (this.fill && top.length < k) top = top.concat(this._popular(client, owned, platforms, top, k));
      return { recs: top, bans, candidates: recs.length };
    }

    _pick(recs, k) {
      const g = this.g;
      let pool = recs.slice().sort((a, b) => b.score - a.score);
      const chosen = [];
      const used = {};
      while (pool.length && chosen.length < k) {
        const val = (r) => r.score * (this.diversify ? Math.pow(0.6, used[r.slot] || 0) : 1.0);
        let best = pool[0];
        for (const r of pool) if (val(r) > val(best)) best = r;
        chosen.push(best);
        used[best.slot] = (used[best.slot] || 0) + 1;
        const block = new Set([best.family, ...g.lineOf(best.family)]);
        if (best.slot === "renew") {
          const steps = pool.filter((r) => r.slot === "next" && block.has(r.family) && r.family !== best.family);
          if (steps.length) {
            let step = steps[0];
            for (const r of steps) if (r.score > step.score) step = r;
            best.also = step.why;
            best.alsoFamily = step.family;
          }
        }
        pool = pool.filter((r) => !block.has(r.family) && !(best.viaClass && r.viaClass === best.viaClass));
      }
      return chosen;
    }
  }

  const api = { Graph, Recommender, SLOT, SLOT_TITLE, WEIGHT, LABEL, TYPE_TITLE, parseDate, fmt, addMonths };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Engine = api;
})(typeof window !== "undefined" ? window : globalThis);
