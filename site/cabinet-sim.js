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

  // ---- «почему это работает» (report.why) и слепая оценка ИИ-клиентами (report.ai_judge) — если поля есть в отчёте
  const times = (a, b) => (b ? `${(a / b).toFixed(1).replace(".", ",")}×` : "—");
  function whyHtml(w) {
    const seg = w && w.segments && w.segments["A B2B"];
    if (!seg || !seg.ours || !seg.copurchase || !seg.popularity) return "";
    const kinds = [["продление купленного", "Продление того, что у клиента уже есть, — вовремя по сроку лицензии"],
                   ["другая редакция или версия", "Старшая редакция или новая версия купленного продукта"],
                   ["вариант той же группы", "Другой вариант той же лицензии: больше мест, другой срок"],
                   ["новый продукт", "Новый для клиента продукт"]];
    const cell = (m, k) => nf.format((m.hits || {})[k] || 0);
    const o = seg.ours, c = seg.copurchase, pp = seg.popularity, b = w.segments["B B2B"] || {};
    const best = (f) => Math.max(c[f] || 0, pp[f] || 0);
    const bBest = Math.max((b.copurchase || {}).protocol || 0, (b.popularity || {}).protocol || 0);
    return `<h3>Почему это работает</h3>
      <p class="hint" style="max-width:none">Организации группы A: чем оказался совет, который сбылся.</p>
      <table class="sim-table"><tr><th>Вид попадания</th><th>Наш советник</th><th>Совместные покупки</th><th>Популярность</th></tr>
        ${kinds.map(([k, label]) => `<tr><td>${esc(label)}</td><td class="sim-ours">${cell(o, k)}</td><td>${cell(c, k)}</td><td>${cell(pp, k)}</td></tr>`).join("")}
        <tr><td><b>Всего попаданий</b></td><td class="sim-ours">${nf.format(o.hits_total || 0)}</td><td>${nf.format(c.hits_total || 0)}</td><td>${nf.format(pp.hits_total || 0)}</td></tr></table>
      <ul class="sim-list">
        <li><b>Советник знает жизненный цикл лицензии:</b> когда истекает срок и что продаётся следом — продление, старшая редакция, новая версия. Совместные покупки смотрят на корзину и не видят времени, популярность не видит клиента: ${pct(o.protocol)} против ${pct(best("protocol"))} у лучшего базового способа — в ${times(o.protocol, best("protocol"))} больше.</li>
        <li><b>Перевес держится и без продлений:</b> ${pct(o.no_renewals)} против ${pct(best("no_renewals"))}; без продлений и вариантов той же группы — ${pct(o.no_renew_variants)} против ${pct(best("no_renew_variants"))}.</li>
        <li><b>Холодный старт:</b> у организаций группы B с одной покупкой — ${b.ours ? `${pct(b.ours.protocol)} против ${pct(bBest)}` : "—"}: знания каталога о линейке, редакциях и классе товара работают и без истории.</li>
        <li><b>Где перевеса нет:</b> совсем новых для клиента продуктов советник угадывает столько же, сколько популярность (${cell(o, "новый продукт")} и ${cell(pp, "новый продукт")}). Здесь блок берёт качеством: каждый совет проверен на паре с товаром клиента и объяснён.</li>
      </ul>`;
  }
  const shares = (m) => {                     // {ours, base, tie}: числа оценок или уже доли — привести к долям
    if (!m) return null;
    const sum = (m.ours || 0) + (m.base || 0) + (m.tie || 0);
    return sum > 1.001 ? { ours: (m.ours || 0) / sum, base: (m.base || 0) / sum, tie: (m.tie || 0) / sum, n: sum } : { ...m, n: null };
  };
  const score = (v) => (v == null ? "—" : Number(v).toFixed(2).replace(".", ","));
  function judgeHtml(j) {
    const pr = shares(j && j.prefer);
    if (!pr) return "";
    const grp = Object.entries(j.by_group || {}).map(([g, v]) => {
      const s = shares(v.prefer) || {};
      return `<tr><td>${g === "B2B" ? "Организации" : g === "B2C" ? "Частные лица" : esc(g)}</td><td class="sim-ours">${pct(s.ours, 0)}</td><td>${pct(s.base, 0)}</td><td>${pct(s.tie, 0)}</td>
        <td>${score((v.scores || {}).ours)} / ${score((v.scores || {}).base)}</td></tr>`;
    }).join("");
    return `<h3>Слепая оценка ИИ-клиентами</h3>
      <p class="hint" style="max-width:none">${j.clients || "—"} клиентов группы A, ${j.judges || 2} оценщика в роли клиента: видят историю покупок и два набора — наш и «совместные покупки», — не зная, где чей.${j.how ? " " + esc(j.how) : ""}</p>
      <p class="sim-big">Наш набор выбран в <b>${pct(pr.ours, 0)}</b> оценок, набор совместных покупок — в ${pct(pr.base, 0)}, поровну — ${pct(pr.tie, 0)}.</p>
      <table class="sim-table"><tr><th></th><th>Выбрали наш</th><th>Выбрали базовый</th><th>Поровну</th><th>Польза 0–2: наш / базовый</th></tr>
        <tr><td><b>Все</b></td><td class="sim-ours">${pct(pr.ours, 0)}</td><td>${pct(pr.base, 0)}</td><td>${pct(pr.tie, 0)}</td><td>${score((j.scores || {}).ours)} / ${score((j.scores || {}).base)}</td></tr>
        ${grp}</table>
      <ul class="sim-list">
        ${j.useless ? `<li>Совет, бесполезный для клиента (оценка 0): у нас ${pct(j.useless.ours, 0)}, у совместных покупок ${pct(j.useless.base, 0)}.</li>` : ""}
        ${j.agreement != null ? `<li>Оба оценщика выбрали одно и то же у ${pct(j.agreement, 0)} клиентов.</li>` : ""}
        ${j.note ? `<li>${esc(j.note)}</li>` : ""}
      </ul>`;
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
