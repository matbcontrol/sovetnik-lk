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
    analog_ru: "replace", analog_class: "replace",
  };
  const SLOT_TITLE = {
    renew: "Что истекает", next: "Следующая ступень", missing: "К вашему продукту", kit: "Типовой набор",
    replace: "Замена", popular: "Часто берут",
  };
  const WEIGHT = {
    renewal: 1.0, requires: 0.85, upgrade: 0.7, new_version: 0.6,
    copurchase: 0.65, next_purchase: 0.65, complements: 0.55, compatible: 0.5,
    analog_ru: 0.45, analog_class: 0.45, mentions: 0.25,
  };
  // Замена по классу — вывод слабее прямого аналога: тот же класс ещё не значит «то же самое».
  const CLASS_ANALOG_CONFIDENCE = 0.5;
  const CLASS_ANALOG_TOP = 5;
  // Срок лицензии подошёл, а продлить её нельзя: замена так же срочна, как продление.
  const LAPSE_ANALOG = 0.9;
  const LAPSE_CLASS = 0.75;
  // Таблица замещения называет аналогами и продукты другого назначения (к AutoCAD — геоинформационную
  // систему). Аналог из другого класса каталога идёт после аналога того же класса.
  const OTHER_CLASS_ANALOG = 0.8;
  // Совет не должен быть в разы дороже того, что клиент покупал: по данным продаж следующая покупка дороже
  // самой дорогой прошлой больше чем в пять раз лишь в 5,5 % случаев. Замены и продления под правило не попадают.
  const PRICE_CAP = 5.0;
  const PRICE_FLOOR = 5000.0;  // у дешёвой покупки потолок считается от этой суммы
  const LABEL = {
    "таблица сравнения магазина": "подтверждено магазином",
    "типы лицензий на странице": "подтверждено магазином",
    "срок лицензии": "срок вашей покупки",
    "ссылка на странице магазина": "упоминание на странице магазина",
    "названия редакций": "выведено из названий",
    "номера версий в названиях": "выведено из названий",
    "названия": "выведено из названий",
    "название товара": "сказано в названии товара",
    "серия в названии": "та же серия по названию: комплект",
    "характеристики каталога": "совместимо по характеристикам каталога",
    "характеристики каталога: комплект": "совместимо по характеристикам каталога: комплект",
    "таблица замещения АРПП": "реестр российского ПО",
    "реестр и класс": "тот же класс из реестра, ждёт проверки менеджером",
    "продажи": "подтверждено покупками",
    "модель": "предложено моделью, ждёт проверки менеджером",
    "разметка: нужно": "пара проверена заранее: нужно",
    "разметка: комплект": "пара проверена заранее: комплект",
    "разметка по API": "размечено моделью по API, ждёт проверки менеджером",
    "разметка класса: нужно": "товар проверен для этого класса: нужно",
    "разметка класса: комплект": "товар проверен для этого класса: комплект",
    "менеджер": "принято менеджером",
    "агент каталога": "предложено агентом каталога, принято менеджером",
    "витрина": "порядок витрины магазина",
  };
  const TYPE_TITLE = {
    renewal: "продление", upgrade: "старшая редакция", new_version: "новая версия",
    requires: "требуется", complements: "дополняет", compatible: "совместимо",
    copurchase: "покупают вместе", next_purchase: "покупают следом", mentions: "упоминание",
    analog_ru: "российский аналог", analog_class: "российский продукт того же класса", same_line: "та же линейка", popular: "часто берут",
  };
  // Связь между товарами разных линеек — вывод, а не факт каталога. Клиенту она показывается, если саму пару
  // товаров заранее проверили по правилам: «нужно» — личный совет, «комплект» — не больше одного и после
  // остальных. Подсказка менеджеру, которая ждёт его решения: товар, подобранный по связи классов (проверена
  // связь классов, но не пара с товаром клиента), вердикт модели, не прошедшей сверку, и замена по классу.
  const PENDING = "ждёт проверки менеджером";
  const KIT_MAX = 1;           // сколько советов «типовой набор» может быть в блоке
  const KIT_TO_CLIENT = true;  // показывать «комплект» клиенту сразу; false — только после решения менеджера
  const VERDICT_SOURCE = { need: "разметка: нужно", kit: "разметка: комплект" };
  // Серия, прочитанная в названиях: курс той же серии, картридж той же серии другого цвета. Тоже «комплект».
  const SERIES_SOURCE = "серия в названии";
  // Совместимость, проверенная по характеристикам каталога: сокет, тип памяти, форм-фактор.
  const SPEC_SOURCE = "характеристики каталога", SPEC_KIT = "характеристики каталога: комплект";
  const isKit = (source) => source === VERDICT_SOURCE.kit || source === SERIES_SOURCE || source === SPEC_KIT;
  // Товар, проверенный для класса: разметчик судил не пару с конкретным товаром клиента, а «подходит ли товар Б
  // любому товару класса А» (или только товарам того же производителя). Совет слабее проверенной пары товаров.
  const PICK_SOURCE = { need: "разметка класса: нужно", kit: "разметка класса: комплект" };
  const PICK_CONFIDENCE = { need: 0.7, kit: 0.4 };
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
      // пары товаров, по которым вердикт уже вынесен: связь классов такой товар не подбирает, за него говорит пара
      this.judged = new Set((data.veto || []).map((x) => x[0] + "|" + x[1]));
      for (const row of data.edges) {
        const e = { src: row[0], dst: row[1], type: row[2], source: row[3], confidence: row[4], evidence: row[5] || "" };
        if (String(e.source || "").startsWith("разметка")) this.judged.add(e.src + "|" + e.dst);
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
      // класс А -> товары, проверенные на пригодность для любого товара этого класса (или своего производителя)
      this.classProducts = data.class_products || {};
      // «класс А — товар Б», по которым вердикт уже вынесен (любой): связь классов такой товар не подбирает
      this.pickJudged = new Set();
      for (const [a, bs] of Object.entries(data.pick_judged || {})) for (const b of bs) this.pickJudged.add(a + "|" + b);
      // пары товаров с вердиктом «уже есть», «то же самое», «не связано»: такой товар не советуем и по связи классов
      this.veto = new Set((data.veto || []).map((x) => x[0] + "|" + x[1]));
      this.vetoHard = new Set((data.veto || []).filter((x) => x.length < 3 || x[2] === "has" || x[2] === "same").map((x) => x[0] + "|" + x[1]));
      this.vetoSame = new Set((data.veto || []).filter((x) => x[2] === "same").map((x) => x[0] + "|" + x[1]));
      // пары классов с вердиктом «уже есть» или «то же самое»: такое не идёт и в строку «часто берут»
      this.classVeto = new Set((data.class_veto || []).map((x) => x[0] + "|" + x[1]));
      // «товар — класс»: первые товары класса этому товару не подошли, связь классов для него не действует
      this.classBlock = new Set((data.class_block || []).map((x) => x[0] + "|" + x[1]));
      // «класс А|класс Б» -> товары класса Б, прошедшие проверку пары с ходовым товаром класса А
      this.classPicks = {};
      for (const [k, v] of Object.entries(data.class_picks || {})) this.classPicks[k] = new Set(v);
      this.popular = data.popular || {};
      // российские продукты из реестра по классам — чем заменить снятый с продажи зарубежный продукт.
      // Порядок: сначала те, что в продаже, затем по месту на витрине класса (первые 12 мест), затем по ключу.
      this.ruByClass = new Map();
      for (const n of data.nodes) {
        if (!n.cls || n.ru !== 1 || n.kind === "hardware") continue;
        if (!this.ruByClass.has(n.cls)) this.ruByClass.set(n.cls, []);
        this.ruByClass.get(n.cls).push(n.id);
      }
      for (const [cid, fams] of this.ruByClass) {
        const pos = new Map((this.classRank[cid] || []).slice(0, 12).map((f, i) => [f, i]));
        const key = (f) => [this.onSale(f) ? 0 : 1, pos.has(f) ? pos.get(f) : 1e6];
        fams.sort((a, b) => {
          const ka = key(a), kb = key(b);
          return ka[0] - kb[0] || ka[1] - kb[1] || (a < b ? -1 : a > b ? 1 : 0);
        });
      }
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
      this.unverified = !!opts.unverified;             // показывать и связи по классу, которые никто не проверил
      this.approved = opts.approved || new Set();      // ключи связей по классу, принятых менеджером
      this.rejected = opts.rejected || new Set();      // ключи связей по классу, отклонённых менеджером
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

    /** Срок лицензии подошёл, а продлить её нельзя: продукт клиенту не продаётся и новой версии нет.
     *  Возвращает пояснение для клиента или пустую строку. */
    _lapsing(client, p) {
      const [term, assumed] = this._term(p);
      if (!term || p.dateMs === null) return "";
      const end = addMonths(p.dateMs, term);
      const left = Math.round((end - client.todayMs) / DAY);
      if (!(this.renewWindow[0] <= left && left <= this.renewWindow[1])) return "";
      if (this.g.available(p.family, client.type)) return "";
      if ((this.g.out.get(p.family) || []).some((e) => e.type === "new_version" && this.g.available(e.dst, client.type))) return "";
      const when = left >= 0 ? `истекает ${fmt(end)}` : `истекла ${fmt(end)}`;
      return `куплена ${fmt(p.dateMs)}, ${assumed ? "обычно на" : "на"} ${term} мес., ${when}`;
    }

    _banned(client, owned, platforms, dst, edgeType, src) {
      const g = this.g;
      const n = g.nodes.get(dst);
      if (!n || !g.available(dst, client.type)) return "нет в продаже";
      if (edgeType !== "renewal" && owned.has(dst)) return "уже куплено";
      if (edgeType === "popular") {
        // популярное — не личный совет, но то, что у клиента заведомо есть или решает ту же задачу, не показываем
        const kd = g.klass(dst);
        for (const o of owned.keys()) {
          const ko = g.klass(o);
          if (g.vetoHard.has(o + "|" + dst) || g.classVeto.has(ko + "|" + kd) || ko === kd) return "у клиента это уже есть или решает ту же задачу";
        }
      } else if (!["renewal", "upgrade", "new_version", "analog_ru", "analog_class"].includes(edgeType)) {
        for (const o of owned.keys()) if (g.veto.has(o + "|" + dst)) return "пара проверена: уже есть, то же самое или не связано";
      }
      const b2bOnly = (x) => x.b2b === 1;
      // в каталоге признак «для дома» заполнен не у всех: смотрим и на класс («Антивирусы для дома»)
      const homeOnly = (x) => { const c = split(x.ct); const homeClass = String(g.classes[x.cls || ""] || "").toLowerCase().includes("для дома"); return ((c.size === 1 && c.has("b2c")) || homeClass) && !b2bOnly(x); };
      const s = g.nodes.get(src) || {};
      const inLine = edgeType === "renewal" || edgeType === "upgrade" || edgeType === "new_version";
      if (client.type === "b2c" && b2bOnly(n) && !(inLine && b2bOnly(s))) return "продаётся только юридическим лицам";
      if (client.type === "b2b" && homeOnly(n) && !homeOnly(s)) return "лицензия только для дома";   // кто сам берёт домашние лицензии, тому можно
      const np = new Set();
      for (const x of split(n.pf)) for (const p of x.split(",")) if (p.trim()) np.add(p.trim().toLowerCase());
      if (edgeType !== "renewal" && platforms.size && np.size && ![...platforms].some((p) => np.has(p))) return "не та платформа";
      if (!["renewal", "analog_ru", "analog_class", "popular"].includes(edgeType)) {
        let top = 0;
        for (const o of owned.keys()) { const x = Number((g.nodes.get(o) || {}).price) || 0; if (x > top) top = x; }
        const cost = Number(n.price) || 0;
        if (top && cost && cost > PRICE_CAP * Math.max(top, PRICE_FLOOR)) return "намного дороже прошлых покупок клиента";
      }
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
      if (edge.source === PICK_SOURCE.need || edge.source === PICK_SOURCE.kit) {
        return `${ev.replace(/[. ]+$/, "")}.`;       // товар проверен для класса: причина написана для клиента
      }
      if ((edge.source === VERDICT_SOURCE.need || isKit(edge.source)) && !edge.class) {
        return `${ev.replace(/[. ]+$/, "")}.`;       // пара товаров проверена заранее: причина уже написана для клиента
      }
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
      if (t === "analog_ru" || t === "analog_class") {
        const onRequest = (g.nodes.get(edge.src) || {}).req === 1;
        let target;
        if (t === "analog_ru") {
          const reg = ev.includes("(реестр № ") ? ev.split("(реестр № ")[1].split(")")[0] : "";
          const tail = reg ? ` (реестр российского ПО, запись № ${reg})` : "";
          target = `Российский аналог: ${dst}${tail}.`;
        } else {
          const cname = g.classes[edge.of_class] || "";
          const via = edge.sibling ? ` В таблице замещения он назван аналогом ${q(name(edge.sibling))}.` : "";
          target = `Полной замены в таблице замещения нет. Ближайший российский продукт того же класса${cname ? ` («${cname}»)` : ""} из реестра — ${dst}.${via}`;
        }
        if (edge.lapse) {                              // срок подошёл, а продлить лицензию нельзя
          const how = onRequest ? "В открытой продаже продукта нет: продлить её можно только по запросу цены у менеджера."
            : "Продлить её нельзя: продукт снят с продажи.";
          return `Лицензия ${src}: ${edge.lapse}. ${how} ${target}`;
        }
        const gone = g.onSale(edge.src) ? "" : (onRequest ? ", который теперь продаётся только по запросу" : ", которого больше нет в продаже");
        return `${src} — зарубежный продукт${gone}. ${target}`;
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
        viaClass: edge.class || edge.group || "",
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
        const lapse = this._lapsing(client, p);        // срок подошёл, а продлить нельзя: замена срочная
        const renewal = this._renewal(client, p);
        if (renewal) {
          const [edge, s] = renewal;
          const reason = this._banned(client, owned, platforms, edge.dst, "renewal", fam);
          if (reason) bans.push({ family: edge.dst, type: "renewal", from: fam, reason });
          else push(edge.dst, "renew", s, edge);
        }
        let direct = false;                            // у продукта нашёлся прямой российский аналог
        for (const e0 of g.out.get(fam) || []) {
          let e = e0;
          const t = e.type;
          if (t === "renewal" || e.confidence < this.minConfidence || !(t in this.w)) continue;
          const reason = this._banned(client, owned, platforms, e.dst, t, fam);
          if (reason) { bans.push({ family: e.dst, type: t, from: fam, reason }); continue; }
          let s = this.w[t] * e.confidence * recency;
          if (t === "analog_ru") {
            e = Object.assign({}, e, { group: "replace:" + fam });        // на один продукт — одна замена в блоке
            if (!g.onSale(fam)) {
              s = Math.min(0.95, s * 2.0);
              if (lapse) { s = Math.max(s, LAPSE_ANALOG * e.confidence); e.lapse = lapse; }
            }
            const ca = (g.nodes.get(fam) || {}).cls || "", cb = (g.nodes.get(e.dst) || {}).cls || "";
            if (ca && cb && ca !== cb) s *= OTHER_CLASS_ANALOG;
          }
          if (!g.onSale(e.dst)) s *= 0.8;
          direct = direct || t === "analog_ru";
          if (t === "upgrade" || t === "new_version") e = Object.assign({}, e, { group: "next:" + fam });   // на одну покупку — одна «следующая ступень»
          if (t === "complements" && (e.source === VERDICT_SOURCE.need || e.source === VERDICT_SOURCE.kit || e.source === SPEC_SOURCE)) {
            // проверенная пара товаров: из чужого класса в блоке один совет (два антивируса к одной системе
            // не нужны). В своём классе у продукта бывает несколько разных дополнений: их не склеиваем.
            const dstClass = (g.nodes.get(e.dst) || {}).cls || "";
            if (dstClass !== ((g.nodes.get(fam) || {}).cls || "")) e = Object.assign({}, e, { group: dstClass });
          }
          if (isKit(e.source)) {
            const key = `pk|${fam}|${e.dst}`;
            if (this.rejected.has(key)) continue;
            if (!(KIT_TO_CLIENT || this.unverified || this.approved.has(key))) {     // комплект — после менеджера
              bans.push({ family: e.dst, type: t, from: fam, reason: PENDING, key });
              continue;
            }
            e = Object.assign({}, e, { key });
          }
          push(e.dst, isKit(e.source) ? "kit" : SLOT[t], s, e);
        }
        // замена по классу: купленный зарубежный продукт сняли с продажи, а прямого аналога в таблице
        // замещения нет — советуем российские продукты того же класса из реестра
        const node = g.nodes.get(fam) || {};
        const cls = node.cls || "";
        if (cls && !direct && !g.onSale(fam) && node.ru !== 1 && node.kind !== "hardware"
            && CLASS_ANALOG_CONFIDENCE >= this.minConfidence) {
          const vendor = fam.split("/")[0];
          const mine = g.analogs.get(fam) || new Set();
          // продукт того же вендора и класса, аналогом которого d назван в таблице замещения
          const sibling = (d) => {
            for (const x of Array.from(g.analogs.get(d) || []).sort()) {
              if (x !== fam && x.split("/")[0] === vendor && ((g.nodes.get(x) || {}).cls || "") === cls) return x;
            }
            return "";
          };
          let pool = (g.ruByClass.get(cls) || []).filter((d) => d !== fam && !mine.has(d)).map((d) => [d, sibling(d)]);
          // сначала те, кого таблица замещения называет аналогом другого продукта того же вендора
          pool = pool.filter((x) => x[1]).concat(pool.filter((x) => !x[1])).slice(0, CLASS_ANALOG_TOP);
          for (let rank = 0; rank < pool.length; rank++) {
            const [dst, sib] = pool[rank];
            if (this._banned(client, owned, platforms, dst, "analog_class", fam)) continue;
            const key = `cr|${cls}|${dst}`;
            if (this.rejected.has(key)) continue;
            if (!(this.unverified || this.approved.has(key))) {
              bans.push({ family: dst, type: "analog_class", from: fam, reason: PENDING, key });
              break;
            }
            let s = Math.min(0.95, this.w.analog_class * CLASS_ANALOG_CONFIDENCE * recency * 2.0);
            if (lapse) s = Math.max(s, LAPSE_CLASS);
            if (!g.onSale(dst)) s *= 0.8;
            const edge = {
              src: fam, dst, type: "analog_class", key, source: this.approved.has(key) ? "менеджер" : "реестр и класс",
              confidence: CLASS_ANALOG_CONFIDENCE,
              evidence: "", lapse, of_class: cls, group: "replace:" + fam, sibling: sib,
            };
            push(dst, "replace", s / (1 + 0.35 * rank), edge);
          }
        }
        for (const link of g.classLinks.get(g.klass(fam)) || []) {
          if (link.confidence < this.minConfidence) continue;
          const aud = link.audience || "any";
          if (aud !== "any" && aud !== client.type) continue;
          const t = link.type in this.w ? link.type : "complements";
          const key = `cl|${link.src_class}|${link.dst_class}`;
          if (this.rejected.has(key)) continue;        // менеджер отклонил связь
          if (g.classBlock.has(fam + "|" + link.dst_class)) continue;   // первые товары этого класса владельцу fam не подошли
          const verdict = link.verdict || "";
          const labelled = link.source === "разметка" && verdict in VERDICT_SOURCE;
          // проверена связь классов, а не пара с товаром клиента: это подсказка менеджеру
          const shown = this.unverified || this.approved.has(key);
          const source = this.approved.has(key) ? "менеджер" : labelled ? VERDICT_SOURCE[verdict] : (link.source || "модель");
          const list = (g.classRank[link.dst_class] || []).slice(0, 10);
          const allowed = g.classPicks[link.src_class + "|" + link.dst_class];
          const hard = node.kind === "hardware";
          for (let rank = 0; rank < list.length; rank++) {
            const dst = list[rank];
            if (g.judged.has(fam + "|" + dst) || g.pickJudged.has(link.src_class + "|" + dst)) continue;    // свой вердикт уже есть
            if (allowed && !allowed.has(dst)) continue;     // товары этого класса проверяли: берём только подошедшие
            if (hard && (g.nodes.get(dst) || {}).kind === "hardware" && dst.split("/")[0] !== fam.split("/")[0]) continue;   // расходники — только того же вендора
            if (this._banned(client, owned, platforms, dst, t, fam)) continue;
            if (!shown) {                              // клиент не увидит совет, пока связь не принята
              bans.push({ family: dst, type: t, from: fam, reason: PENDING, key });
              break;
            }
            const edge = {
              src: fam, dst, type: t, source, confidence: link.confidence,
              evidence: link.reason || "", class: link.dst_class, rank: rank + 1, key,
            };
            push(dst, verdict === "kit" ? "kit" : "missing", (this.w[t] * link.confidence * recency) / (1 + 0.35 * rank), edge);
          }
        }
        // товары, проверенные для класса купленного товара: совет есть и там, где своих пар у товара нет
        const klass = g.klass(fam), order = {};
        for (const pick of g.classProducts[klass] || []) {
          const dst = pick[0], verdict = pick[1], scope = pick[2], reason = pick[3], aud = pick[4] || "any";
          if (dst === fam || g.judged.has(fam + "|" + dst)) continue;       // по этой паре есть свой вердикт: он главнее
          if (scope === "vendor" && dst.split("/")[0] !== fam.split("/")[0]) continue;   // только своему производителю
          if (aud !== "any" && aud !== client.type) continue;
          const key = `cp|${klass}|${dst}`;
          if (this.rejected.has(key) || PICK_CONFIDENCE[verdict] < this.minConfidence) continue;
          if (this._banned(client, owned, platforms, dst, "complements", fam)) continue;
          if (verdict === "kit" && !(KIT_TO_CLIENT || this.unverified || this.approved.has(key))) {
            bans.push({ family: dst, type: "complements", from: fam, reason: PENDING, key });
            continue;
          }
          const dstClass = (g.nodes.get(dst) || {}).cls || "";
          const edge = {
            src: fam, dst, type: "complements", source: PICK_SOURCE[verdict], confidence: PICK_CONFIDENCE[verdict],
            evidence: reason, key, group: dstClass !== klass ? dstClass : "",
          };
          // чем дальше товар в списке проверенных для своего класса, тем слабее совет
          const s = (this.w.complements * PICK_CONFIDENCE[verdict] * recency) / (1 + 0.35 * (order[dstClass] || 0));
          order[dstClass] = (order[dstClass] || 0) + 1;
          push(dst, verdict === "kit" ? "kit" : "missing", s, edge);
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
      const usedSrc = {};                              // сколько советов уже идёт от каждой покупки клиента
      const srcOf = (r) => (r.paths && r.paths[0] && r.paths[0].src) || "";
      while (pool.length && chosen.length < k) {
        // разнообразие: каждый следующий совет того же вида и от той же покупки весит меньше
        const val = (r) => r.score * (this.diversify ? Math.pow(0.6, used[r.slot] || 0) * Math.pow(0.75, usedSrc[srcOf(r)] || 0) : 1.0);
        // «типовой набор» идёт после всех остальных советов и не больше KIT_MAX раз
        let rest = pool.filter((r) => r.slot !== "kit");
        if (!rest.length) rest = pool.filter(() => (used.kit || 0) < KIT_MAX);
        if (!rest.length) break;
        let best = rest[0];
        for (const r of rest) if (val(r) > val(best)) best = r;
        chosen.push(best);
        used[best.slot] = (used[best.slot] || 0) + 1;
        if (srcOf(best)) usedSrc[srcOf(best)] = (usedSrc[srcOf(best)] || 0) + 1;
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
        // в блоке нет двух товаров, которые по вердикту пары — одно и то же (две редакции одного продукта)
        pool = pool.filter((r) => !block.has(r.family) && !(best.viaClass && r.viaClass === best.viaClass)
          && !g.vetoSame.has(best.family + "|" + r.family) && !g.vetoSame.has(r.family + "|" + best.family));
      }
      return chosen;
    }
  }

  const api = { Graph, Recommender, SLOT, SLOT_TITLE, WEIGHT, LABEL, TYPE_TITLE, PENDING, KIT_MAX, KIT_TO_CLIENT, parseDate, fmt, addMonths };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Engine = api;
})(typeof window !== "undefined" ? window : globalThis);
