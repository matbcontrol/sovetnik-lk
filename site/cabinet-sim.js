/* Симуляция в макете кабинета: «три месяца за минуту».
 *
 * Клиенты группы A из данных организаторов идут по одному. У каждого — два блока на 31.08.2026: тройка нашего
 * советника (та же, что в замере по протоколу) и тройка базового способа организаторов «совместные покупки»
 * (baseline.py). Затем видно, что клиент на самом деле купил с 07.09 по 23.11, и счётчики попаданий растут.
 *
 * Клиенты есть только в локальном запуске (site/data/private.json, не публикуется). Без него — только итоговые
 * числа из открытого отчёта (site/data/report.json). Языковая модель на странице не вызывается.
 */
(async function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x, d = 1) => (x == null ? "—" : `${(100 * x).toFixed(d).replace(".", ",")} %`);
  const nf = new Intl.NumberFormat("ru-RU");
  const getJson = async (url) => { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (e) { return null; } };
  const fmtDate = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join(".") : "—");

  // ---- вид «Симуляция» по адресу #sim; остальной кабинет при этом скрыт
  const cabinet = Array.from(document.querySelectorAll("main > section:not(#sim)"));
  function route() {
    const sim = location.hash === "#sim";
    $("#sim").hidden = !sim;
    cabinet.forEach((s) => { s.hidden = sim; });
    document.querySelectorAll("nav.side a").forEach((a) => a.classList.toggle("on", sim ? a.getAttribute("href") === "#sim" : a.dataset.home === "1"));
  }
  window.addEventListener("hashchange", route);
  route();

  const report = (await getJson("data/report.json")) || {};
  const priv = await getJson("data/private.json");
  const tests = priv && Array.isArray(priv.clients) ? priv.clients.filter((c) => c.mode === "test" && c.protocol && c.baseline) : [];
  const items = (priv && priv.items) || {};
  // полное название: у вариантов одного продукта (срок, число мест) короткие названия совпадают
  const name = (code) => { const it = items[code] || {}; const n = it.name || it.title || it.short || code; return n.length > 120 ? n.slice(0, 118) + "…" : n; };
  const vendor = (code) => (items[code] || {}).vendor || "";
  const isOrg = (c) => c.group === "B2B";
  const kind = (c) => (c.type === "ФЛ" ? "Частное лицо" : c.type === "ИП" ? "Индивидуальный предприниматель" : "Организация");
  const hits = (list) => (list || []).reduce((n, h) => n + (h.hit ? 1 : 0), 0);

  const from = (priv && priv.test_from) || "2026-09-07";
  const until = (priv && priv.today) || "2026-11-23";
  const trainUntil = (priv && priv.train_until) || "2026-08-31";
  $("#sim-window").textContent = `советы на ${fmtDate(trainUntil)}, покупки с ${fmtDate(from)} по ${fmtDate(until)}`;

  // ---- итог: по клиентам из данных, а без них — по открытому отчёту
  function summary() {
    if (tests.length) {
      const part = (sel) => {
        const s = tests.filter(sel), slots = 3 * s.length || 1;
        const sum = (f) => s.reduce((n, c) => n + f(c), 0);
        const ours = sum((c) => hits(c.protocol)), co = sum((c) => hits(c.baseline.copurchase)), pop = sum((c) => hits(c.baseline.popularity));
        return { n: s.length, ours, co, pop, p: [ours / slots, co / slots, pop / slots] };
      };
      return { all: part(() => true), org: part(isOrg), b2c: part((c) => !isOrg(c)) };
    }
    const r = (report.regimes || {}).full || {}, o = report.organisers || {};
    const g = (k) => ({ p: [(r.p3 || {})[k], (o["совместные покупки"] || {})[k], (o["популярность"] || {})[k]] });
    return { all: g("A все"), org: g("A B2B"), b2c: g("A B2C") };
  }

  function renderFinal() {
    const s = summary();
    const nr = (report.shown || {}).no_renewals || {};
    // базовые способы — с тем же фильтром «без продлений купленного» (report.why), иначе — популярность из отчёта
    const seg = ((report.why || {}).segments || {})["A B2B"] || {};
    const sameFilter = seg.copurchase ? Math.max(seg.copurchase.no_renewals || 0, (seg.popularity || {}).no_renewals || 0) : nr.baseline;
    const counts = s.all.ours != null
      ? `<p class="sim-big"><b>${s.all.ours}</b> попаданий у нашего советника против <b>${s.all.co}</b> у совместных покупок и <b>${s.all.pop}</b> у популярности — на ${s.all.n} клиентах группы A.</p>`
      : `<p class="hint">Клиенты из данных организаторов есть только в локальном запуске: данные нельзя публиковать. Ниже — итог по открытому отчёту.</p>`;
    const row = (title, x) => `<tr><td>${title}${x.n ? ` <small>(${x.n})</small>` : ""}</td><td class="sim-ours">${pct(x.p[0])}</td><td>${pct(x.p[1])}</td><td>${pct(x.p[2])}</td></tr>`;
    $("#sim-final").innerHTML = `<h3>Итог за три месяца</h3>${counts}
      <table class="sim-table"><tr><th>Precision@3 по протоколу организаторов</th><th>Наш советник</th><th>Совместные покупки</th><th>Популярность</th></tr>
        ${row("Организации", s.org)}${row("Частные лица", s.b2c)}</table>
      <p class="sim-note"><b>Честная оговорка.</b> Почти половина попаданий у организаций — продления купленного; для них в кабинете свой сценарий «Пора продлить».
        Если продления из тройки убрать, у организаций остаётся ${pct(nr.p3)} против ${pct(sameFilter)} у лучшего базового способа с тем же фильтром — всё ещё почти вдвое выше.
        У частных лиц прибавка в пределах погрешности.</p>`;
    $("#sim-final").hidden = false;
    renderExtras();
  }

  // ---- «почему это работает» (report.why) и слепая оценка ИИ-клиентами (report.ai_judge) — если поля есть в отчёте
  const times = (a, b) => (b ? `${(a / b).toFixed(1).replace(".", ",")}×` : "—");
  function whyHtml(w) {
    // деление попаданий — по правилу организаторов (product_group датасета v5), как на «Цифрах» стенда
    const off = w && w.official, seg = off && off["A B2B"], segs = w && w.segments && w.segments["A B2B"];
    if (!seg || !seg.ours || !seg.copurchase || !seg.popularity) return "";
    const n = (v) => nf.format(v || 0);
    const share = (v) => `${String(v != null ? v : 0).replace(".", ",")} %`;
    const o = seg.ours, c = seg.copurchase, pp = seg.popularity, best = Math.max(c.p3_new || 0, pp.p3_new || 0);
    const bb = off["B B2B"] || {}, bc = off["A B2C"] || {};
    const row = (title, v, mine) => `<tr><td>${mine ? `<b>${title}</b>` : title}</td><td class="${mine ? "sim-ours" : ""}">${n(v.hits)}</td>
      <td>${n(v.variant)} (${share(v.variant_share)})</td><td class="${mine ? "sim-ours" : ""}">${n(v.new)}</td><td>${v.p3_new != null ? pct(v.p3_new) : "—"}</td></tr>`;
    const noRenew = segs && segs.ours && segs.copurchase
      ? ` Если убрать из тройки продления купленного — их в кабинете ведёт свой сценарий, — остаётся ${pct(segs.ours.no_renewals)} против ${pct(Math.max(segs.copurchase.no_renewals || 0, (segs.popularity || {}).no_renewals || 0))} у лучшего базового способа.` : "";
    return `<h3>Почему это работает</h3>
      <p class="hint" style="max-width:none">Организации группы A. Деление — по правилу организаторов, по колонке product_group датасета v5: <b>вариант</b> — группа угаданного товара уже есть в истории клиента (базовая лицензия и продление, другой срок, количество, месяц подписки), <b>новый продукт</b> — такой группы в истории нет.</p>
      <table class="sim-table"><tr><th></th><th>Попаданий</th><th>Варианты купленного</th><th>Новые продукты</th><th>Precision@3 только по новым</th></tr>
        ${row("Наш советник", o, true)}${row("Совместные покупки", c)}${row("Популярность", pp)}</table>
      <ul class="sim-list">
        <li><b>Советник знает жизненный цикл лицензии.</b> ${share(o.variant_share)} наших попаданий — варианты купленного: продление вовремя, другой срок, больше мест. У совместных покупок таких ${share(c.variant_share)}, у популярности ни одного: они не видят ни времени, ни того, что у клиента уже есть.${noRenew}</li>
        <li><b>И новых продуктов советник угадывает вдвое больше:</b> ${n(o.new)} против ${n(c.new)} и ${n(pp.new)}, ${pct(o.p3_new)} против ${pct(best)} у лучшего базового способа (${times(o.p3_new, best)}). Здесь работают знания каталога — линейка и редакции, проверенные пары «купил А — советовать ли Б».</li>
        ${bb.ours ? `<li><b>Холодный старт.</b> У организаций группы B одна покупка: ${n(bb.ours.hits)} попаданий против ${n((bb.copurchase || {}).hits)} и ${n((bb.popularity || {}).hits)}, из них новых продуктов ${n(bb.ours.new)} против ${n((bb.copurchase || {}).new)} и ${n((bb.popularity || {}).new)}.</li>` : ""}
        ${bc.ours ? `<li><b>Где перевеса нет.</b> У частных лиц новых продуктов ${n(bc.ours.new)} против ${n((bc.popularity || {}).new)} у популярности: покупки у них похожи друг на друга, и популярное почти так же хорошо. Здесь блок берёт качеством: каждый совет проверен на паре с товаром клиента и объяснён.</li>` : ""}
      </ul>
      <p class="hint" style="max-width:none">Оговорка: product_group организаторов мельче их же словесного правила — Kaspersky Standard на 5 и на 10 устройств, разные лицензии КриптоПро CSP попадают в разные группы. По нашей более строгой мерке, где смена редакции и продление — тот же продукт, из ${n(o.new)} «новых» по-настоящему новых продуктов 23, как у популярности.</p>`;
  }
  const shares = (m) => {                     // {ours, base, tie}: числа оценок или уже доли — привести к долям
    if (!m) return null;
    const sum = (m.ours || 0) + (m.base || 0) + (m.tie || 0);
    return sum > 1.001 ? { ours: (m.ours || 0) / sum, base: (m.base || 0) / sum, tie: (m.tie || 0) / sum, n: sum } : { ...m, n: null };
  };
  const score = (v) => (v == null ? "—" : Number(v).toFixed(2).replace(".", ","));
  function judgeHtml(j) {
    if (!j || !j.by_group) return "";
    const n0 = (v) => (v == null ? "—" : nf.format(v));
    const row = (title, v) => {
      if (!v) return "";
      const p = v.prefer || {}, s = v.scores || {}, u = v.useless || {}, b = v.bought || {};
      const n = v.judgments || (p.ours || 0) + (p.base || 0) + (p.tie || 0);
      return `<tr><td>${title} <small>(${n} оценок)</small></td><td class="sim-ours">${n0(p.ours)}</td><td>${n0(p.base)}</td><td>${n0(p.tie)}</td>
        <td>${score(s.ours)} / ${score(s.base)}</td><td>${pct(u.ours, 0)} / ${pct(u.base, 0)}</td><td>${n0(b.ours)} / ${n0(b.base)}</td></tr>`;
    };
    const intro = j.how || `${j.clients || "—"} клиентов группы A, ${j.judges || 2} оценщика в роли клиента: видят историю покупок и два набора — наш и «совместные покупки», — не зная, где чей.`;
    return `<h3>Слепая оценка ИИ-клиентами</h3>
      <p class="hint" style="max-width:none">${esc(intro)}</p>
      <table class="sim-table"><tr><th></th><th>Выбрали наш набор</th><th>Выбрали совместные покупки</th><th>Поровну</th><th>Польза 0–2: наш / базовый</th><th>Ненужных советов: наш / базовый</th><th>Купили на деле: из нашего / из базового</th></tr>
        ${row("Организации", j.by_group.B2B)}${row("Частные лица", j.by_group.B2C)}</table>
      ${j.note ? `<p class="sim-note">${esc(j.note)}</p>` : ""}
      ${j.agreement != null ? `<p class="hint">Оба оценщика выбрали одно и то же у ${pct(j.agreement, 0)} клиентов.</p>` : ""}`;
  }
  function renderExtras() {
    const w = whyHtml(report.why), j = judgeHtml(report.ai_judge);
    $("#sim-why").innerHTML = w;
    $("#sim-why").hidden = !w;
    $("#sim-judge").innerHTML = j;
    $("#sim-judge").hidden = !j;
  }
  const WINNER = { ours: "выбрал наш набор", base: "выбрал набор совместных покупок", tie: "не выбрал: наборы равны" };
  const judgeLine = (c) => (Array.isArray(c.ai_judge) && c.ai_judge.length
    ? `<div class="sim-judge">${c.ai_judge.map((v) => `<div>ИИ-клиент ${esc(v.judge)} ${WINNER[v.winner] || ""}${v.why ? `: «${esc(v.why)}»` : ""}</div>`).join("")}</div>` : "");

  if (!tests.length) {
    $("#sim-controls").hidden = true;
    $("#sim-stage").innerHTML = "";
    renderFinal();
    return;
  }

  // ---- прогон
  const total = tests.length;
  let i = 0, ours = 0, co = 0, pop = 0, timer = null, speed = 1, running = false;

  function item(h, mark) {
    return `<div class="sim-item ${h.hit ? "hit" : ""}"><span>${esc(name(h.item))}</span>${h.hit ? `<em>${mark}</em>` : ""}
      <small>${esc(vendor(h.item))}</small></div>`;
  }

  function renderClient(c, k) {
    const hist = (c.history || []).slice().sort((a, b) => String(b.last || "").localeCompare(String(a.last || "")));
    const shown = hist.slice(0, 4).map((h) => `<li>${esc(name(h.item))} <small>${fmtDate(h.last)}</small></li>`).join("");
    const oursSet = new Set(c.protocol.filter((h) => h.hit).map((h) => h.item));
    const coSet = new Set(c.baseline.copurchase.filter((h) => h.hit).map((h) => h.item));
    const bought = (c.target || []).map((t) => `<span class="${oursSet.has(t) ? "ours" : coSet.has(t) ? "co" : ""}">${esc(name(t))}</span>`).join("");
    $("#sim-stage").innerHTML = `
      <div class="sim-head"><b>Клиент ${k + 1} из ${total}</b> · ${kind(c)}${c.cold ? " · почти без истории" : ""}</div>
      <div class="sim-hist">Купил до ${fmtDate(trainUntil)}: <ul>${shown}</ul>${hist.length > 4 ? `<small>и ещё ${hist.length - 4}</small>` : ""}</div>
      <div class="sim-cols">
        <div class="sim-col sim-col--ours"><h3>Наш советник на ${fmtDate(trainUntil)}</h3>${c.protocol.map((h) => item(h, "купил")).join("") || '<div class="empty">совета нет</div>'}</div>
        <div class="sim-col"><h3>Совместные покупки на ${fmtDate(trainUntil)} <small>базовый способ организаторов</small></h3>${c.baseline.copurchase.map((h) => item(h, "купил")).join("") || '<div class="empty">по истории этого клиента совместных покупок нет — совета нет</div>'}</div>
      </div>
      <div class="sim-target">Купил с ${fmtDate(from)} по ${fmtDate(until)}: ${bought || "<i>ничего нового</i>"}</div>${judgeLine(c)}`;
  }

  function counters() {
    $("#sim-ours").textContent = nf.format(ours);
    $("#sim-co").textContent = nf.format(co);
    $("#sim-pop").textContent = nf.format(pop);
    $("#sim-progress").textContent = `клиентов просмотрено: ${i} из ${total}`;
    $("#sim-bar").style.width = `${(100 * i) / total}%`;
  }

  function step() {
    if (i >= total) { stop(); renderFinal(); return; }
    const c = tests[i];
    renderClient(c, i);
    const a = hits(c.protocol), b = hits(c.baseline.copurchase);
    ours += a; co += b; pop += hits(c.baseline.popularity);
    i += 1;
    counters();
    const wait = (a ? 520 : b ? 320 : 40) / speed;           // на попаданиях — пауза, чтобы успеть увидеть
    timer = setTimeout(step, wait);
  }
  function stop() { running = false; clearTimeout(timer); $("#sim-play").textContent = i >= total ? "Перемотать ещё раз" : "Продолжить"; }
  function reset() { i = 0; ours = co = pop = 0; $("#sim-final").hidden = true; counters(); }

  $("#sim-play").addEventListener("click", () => {
    if (running) { stop(); return; }
    if (i >= total) reset();
    running = true;
    $("#sim-play").textContent = "Пауза";
    step();
  });
  $("#sim-fast").addEventListener("click", () => {
    speed = speed === 1 ? 4 : 1;
    $("#sim-fast").textContent = speed === 1 ? "Быстрее ×4" : "Обычная скорость";
  });
  $("#sim-end").addEventListener("click", () => {
    stop();
    tests.slice(i).forEach((c) => { ours += hits(c.protocol); co += hits(c.baseline.copurchase); pop += hits(c.baseline.popularity); });
    i = total;
    counters();
    renderClient(tests[total - 1], total - 1);
    renderFinal();
  });
  counters();
  $("#sim-stage").innerHTML = `<div class="empty">Нажмите «Перемотать три месяца»: клиенты пойдут по одному, на попаданиях — короткая пауза.</div>`;
})();
