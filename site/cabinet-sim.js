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
    const counts = s.all.ours != null
      ? `<p class="sim-big"><b>${s.all.ours}</b> попаданий у нашего советника против <b>${s.all.co}</b> у совместных покупок и <b>${s.all.pop}</b> у популярности — на ${s.all.n} клиентах группы A.</p>`
      : `<p class="hint">Клиенты из данных организаторов есть только в локальном запуске: данные нельзя публиковать. Ниже — итог по открытому отчёту.</p>`;
    const row = (title, x) => `<tr><td>${title}${x.n ? ` <small>(${x.n})</small>` : ""}</td><td class="sim-ours">${pct(x.p[0])}</td><td>${pct(x.p[1])}</td><td>${pct(x.p[2])}</td></tr>`;
    $("#sim-final").innerHTML = `<h3>Итог за три месяца</h3>${counts}
      <table class="sim-table"><tr><th>Precision@3 по протоколу организаторов</th><th>Наш советник</th><th>Совместные покупки</th><th>Популярность</th></tr>
        ${row("Организации", s.org)}${row("Частные лица", s.b2c)}</table>
      <p class="sim-note"><b>Честная оговорка.</b> Почти половина попаданий у организаций — продления купленного; для них в кабинете свой сценарий «Пора продлить».
        Если продления из тройки убрать, у организаций остаётся ${pct(nr.p3)} против ${pct(nr.baseline, 2)} у популярности — всё ещё почти вдвое выше.
        У частных лиц прибавка в пределах погрешности.</p>`;
    $("#sim-final").hidden = false;
    renderExtras();
  }

  // ---- разбор «почему это работает» и слепая оценка — если основная сессия положила их в отчёт
  function renderExtras() {
    const why = report.why, judge = report.ai_judge;
    $("#sim-why").hidden = !why;
    $("#sim-judge").hidden = !judge;
    if (why && window.SimExtras && window.SimExtras.why) $("#sim-why").innerHTML = window.SimExtras.why(why, priv);
    if (judge && window.SimExtras && window.SimExtras.judge) $("#sim-judge").innerHTML = window.SimExtras.judge(judge, priv);
  }

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
      <div class="sim-target">Купил с ${fmtDate(from)} по ${fmtDate(until)}: ${bought || "<i>ничего нового</i>"}</div>`;
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
