/* Страница стенда.
 *
 * Открытая часть: граф связей из открытых источников (data/graph.json); совет считается здесь же,
 * движком из engine.js — без сервера и без модели.
 * Закрытая часть (только если рядом лежит data/private.json): клиенты из обезличенного датасета
 * организаторов с советами обученного ранжирования и объяснениями. Этот файл не публикуется.
 */
(async function () {
  "use strict";
  const E = window.Engine;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nf = new Intl.NumberFormat("ru-RU");
  const pct = (x, d) => (x * 100).toFixed(d == null ? 1 : d).replace(".", ",") + " %";
  const getJson = async (url) => { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (e) { return null; } };

  const data = await getJson("data/graph.json");
  if (!data) { $("main").innerHTML = '<div class="panel">Не удалось загрузить data/graph.json. Соберите данные: <code>python -m recsys.export</code></div>'; return; }
  const examples = (await getJson("data/examples.json")) || [];
  const report = await getJson("data/report.json");
  // открытый стенд на статическом хостинге: закрытого файла и сервера агента там нет — не просим их зря
  const STATIC = /\.github\.io$/.test(location.hostname);
  const priv = STATIC ? null : await getJson("data/private.json");
  const agent = await getJson("data/agent_runs.json");
  const holesData = await getJson("data/agent_holes.json");

  // ---- решения менеджера по связям: хранятся в браузере, отклонённая связь сразу выпадает из советов
  const VOTES_KEY = "sovetnik.votes.v1";
  let votes = {};
  try { votes = JSON.parse(localStorage.getItem(VOTES_KEY) || "{}"); } catch (e) { votes = {}; }
  const ekey = (e) => `${e[0]}|${e[1]}|${e[2]}`;
  let graph;
  // решения менеджера по связям классов и заменам по классу: ключи «cl|класс|класс» и «cr|класс|товар»
  let approved = new Set(), rejected = new Set();
  // Предложения агента каталога, принятые менеджером: новый товар и его связи. Хранятся в браузере рядом
  // с решениями менеджера, чтобы принятое пережило перезагрузку страницы.
  const AGENT_KEY = "sovetnik.agent.v1";
  let agentStore = {};
  try { agentStore = JSON.parse(localStorage.getItem(AGENT_KEY) || "{}"); } catch (e) { agentStore = {}; }
  const nodeIds = new Set(data.nodes.map((n) => n.id));
  // связь агента, закрывшего дыру: «нужно», «комплект» или редакция — от вида зависят источник и уверенность ребра
  const holeEdge = (src, l) => [src, l.dst, l.type || "complements", l.kind === "kit" ? "агент каталога: комплект" : "агент каталога",
                                l.confidence || (l.kind === "kit" ? 0.5 : l.kind === "need" ? 0.75 : 0.7), l.evidence || l.reason || ""];
  const holesOf = (d) => { const h = Array.isArray(d) ? d : ((d && d.holes) || []); return Array.isArray(h) ? h : Object.values(h); };
  // Связи агента, которых ещё нет в собранном графе: живой запуск на этой странице или запуск после последней
  // сборки данных. Клиент видит их сразу; без цитаты связь идёт только в очередь менеджера.
  const liveEdges = [], liveQuotes = {}, liveQueue = [];
  // пары, по которым разметка сказала «уже есть», «то же самое» или «не связано»: связь агента её не перебивает
  const vetoPairs = new Set((data.veto || []).map((v) => `${v[0]}|${v[1]}`));
  {
    const known = new Set(data.edges.map((e) => `${e[0]}|${e[1]}`));
    for (const h of holesOf(holesData)) for (const l of h.links || []) {
      if (!nodeIds.has(h.family) || !nodeIds.has(l.dst) || known.has(`${h.family}|${l.dst}`) || vetoPairs.has(`${h.family}|${l.dst}`)) continue;
      const e = holeEdge(h.family, l);
      if (l.status === "graph") { liveEdges.push(e); if (l.quote) liveQuotes[ekey(e)] = l.quote; }
      else liveQueue.push({ src: e[0], dst: e[1], type: e[2], confidence: e[4], evidence: e[5], quote: l.quote || "" });
    }
  }
  function rebuild() {
    const edges = data.edges.filter((e) => votes[ekey(e)] !== "no");
    const newNodes = new Map(), newEdges = [];
    for (const [k, p] of Object.entries(agentStore)) {
      if (votes[k] !== "ok" || !p || !p.edge) continue;
      if (p.node && !nodeIds.has(p.node.id)) newNodes.set(p.node.id, p.node);
      newEdges.push(p.edge);
    }
    graph = new E.Graph(Object.assign({}, data, { nodes: data.nodes.concat(Array.from(newNodes.values())), edges: edges.concat(newEdges, liveEdges.filter((e) => votes[ekey(e)] !== "no")) }));
    const byClass = (v) => new Set(Object.keys(votes).filter((k) => votes[k] === v && /^(c[lrp]|pk)\|/.test(k)));
    approved = byClass("ok");
    rejected = byClass("no");
  }
  rebuild();
  /** Настройки движка: что клиент видит. Непроверенные связи по классу — только если включён просмотр менеджера. */
  const recOpts = (extra) => Object.assign({ unverified: state.unverified, approved, rejected }, extra);
  /** Ссылка в магазин с метками перехода — такими же, какие ставит блок кабинета. */
  function shopUrl(url, slot, pos) {
    if (!url) return "";
    return url + (url.includes("?") ? "&" : "?") + `utm_source=lk.softline.ru&utm_medium=internal&utm_campaign=reco&utm_content=sovetnik_${slot}&utm_term=${pos}`;
  }

  const slotOf = (type) => E.SLOT[type] || "popular";
  const slotVar = (slot) => `var(--${slot})`;
  const nodeName = (id) => (graph.nodes.get(id) || {}).name || id;
  const className = (n) => (n && n.cls && data.classes[n.cls]) || "";
  function saleState(n) {
    if (!n) return ["нет в магазине", "no"];
    if (n.sale === 1) return ["в продаже", "ok"];
    if (n.req === 1) return ["только по запросу цены", ""];
    return ["снят с продажи", "no"];
  }

  // ---- всплывающая подсказка
  const tip = $("#tip");
  function showTip(ev, html) {
    tip.innerHTML = html; tip.hidden = false;
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
    if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
    tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
  }
  const hideTip = () => { tip.hidden = true; };
  function edgeTip(p) {
    if (p.text) return `<b>${esc(p.type)}</b><span>${esc(p.text)}</span>`;
    const title = `${E.TYPE_TITLE[p.type] || p.type} — ${E.LABEL[p.source] || p.source}`;
    const ev = (p.evidence || "").replace(/; https?:\S+$/, "");
    const conf = p.confidence ? `уверенность связи ${String(p.confidence).replace(".", ",")}` : "";
    return `<b>${esc(title)}</b>${ev ? `<span>${esc(ev)}</span>` : ""}${conf ? `<span>${conf}</span>` : ""}`;
  }

  // ---- вкладки
  $$(".tab").forEach((b) => b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("is-active", x === b));
    $$(".pane").forEach((p) => p.classList.toggle("is-active", p.id === "pane-" + b.dataset.tab));
    if (b.dataset.tab === "client") drawWhy();
    if (b.dataset.tab === "links") renderLinks();
    if (b.dataset.tab === "product" && !$("#prod-view").innerHTML.trim()) {
      // вкладка не открывается пустой: готовый пример — плата, к которой процессор подобран по сокету и чипсету
      const sample = ["asus/90mb19x0-m0eay0-406930", "kaspersky/kaspersky-standard"].map((f) => graph.nodes.get(f)).find(Boolean);
      if (sample) showProduct(sample);
    }
    if (b.dataset.tab === "agent") renderAgent();
    if (b.dataset.tab === "numbers") renderNumbers();
  }));

  // ---- шапка
  const onSale = data.nodes.filter((n) => n.sale === 1).length;
  const adviceEdges = data.edges.filter((e) => e[2] !== "same_line").length;
  $("#facts").innerHTML = [
    ["товаров в графе", nf.format(data.nodes.length)],
    ["из них в продаже", nf.format(onSale)],
    ["связей-советов", nf.format(adviceEdges)],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");

  // ---- поиск товара
  const index = data.nodes.map((n) => ({ n, text: `${n.name} ${n.vendor || ""}`.toLowerCase().replace(/ё/g, "е") }));
  function search(q, limit) {
    const words = q.toLowerCase().replace(/ё/g, "е").split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const hits = [];
    for (const it of index) {
      if (!words.every((w) => it.text.includes(w))) continue;
      const linked = graph.out.has(it.n.id) ? 2 : 0;
      const starts = it.text.startsWith(words[0]) ? 1 : 0;
      hits.push([linked + starts + (it.n.sale === 1 ? 0.5 : 0), it.n]);
    }
    hits.sort((a, b) => b[0] - a[0] || a[1].name.length - b[1].name.length);
    return hits.slice(0, limit || 12).map((h) => h[1]);
  }
  function bindSearch(input, list, onPick) {
    let cur = [];
    const close = () => list.classList.remove("is-open");
    const open = () => {
      cur = search(input.value, 12);
      list.innerHTML = cur.map((n, i) => `<li data-i="${i}"><span>${esc(n.name)}</span><small>${esc(n.vendor || "")}${graph.out.has(n.id) ? " · есть связи" : ""}</small></li>`).join("")
        || (input.value.trim() ? '<li><span>Ничего не найдено</span></li>' : "");
      list.classList.toggle("is-open", !!input.value.trim());
    };
    input.addEventListener("input", open);
    input.addEventListener("focus", open);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && cur[0]) { onPick(cur[0]); input.value = ""; close(); } if (ev.key === "Escape") close(); });
    list.addEventListener("mousedown", (ev) => {
      const li = ev.target.closest("li[data-i]");
      if (li) { ev.preventDefault(); onPick(cur[+li.dataset.i]); input.value = ""; close(); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
  }

  // =========================== вкладка «Клиент» ===========================
  const state = { type: "b2b", today: new Date().toISOString().slice(0, 10), purchases: [], minConf: 0, unverified: false };
  let last = { recs: [], bans: [] };       // что сейчас показано: советы и их пути
  let ownedView = [];                      // левая колонка схемы: [{id, name, note}]

  const exSel = $("#example");
  exSel.innerHTML = '<option value="">— собрать клиента самому —</option>' + examples.map((x, i) => `<option value="${i}">${esc(x.title)}</option>`).join("");
  function loadExample(i) {
    const x = examples[i];
    if (!x) return;
    state.type = x.client.type;
    state.today = x.client.today || state.today;
    state.purchases = x.client.purchases.map((p) => Object.assign({}, p));
    syncControls(); render();
  }
  exSel.addEventListener("change", () => { if (exSel.value !== "") loadExample(+exSel.value); });
  $$("#ctype button").forEach((b) => b.addEventListener("click", () => { state.type = b.dataset.v; syncControls(); render(); }));
  $("#today").addEventListener("change", (ev) => { if (ev.target.value) { state.today = ev.target.value; render(); } });
  $("#unverified").addEventListener("change", (ev) => { state.unverified = ev.target.checked; render(); });
  $("#minconf").addEventListener("input", (ev) => { state.minConf = +ev.target.value; $("#minconf-v").textContent = state.minConf.toFixed(2).replace(".", ","); render(); });
  bindSearch($("#add-q"), $("#add-list"), (n) => {
    state.purchases.push({ family: n.id, date: isoShift(state.today, -11), term: null });
    exSel.value = ""; render();
  });
  function isoShift(iso, months) { return new Date(E.addMonths(E.parseDate(iso), months)).toISOString().slice(0, 10); }
  function syncControls() {
    $$("#ctype button").forEach((b) => { b.classList.toggle("is-on", b.dataset.v === state.type); b.setAttribute("aria-checked", b.dataset.v === state.type); });
    $("#today").value = state.today;
  }

  function renderOwned() {
    const ul = $("#owned");
    if (!state.purchases.length) {
      ul.innerHTML = '<li class="owned__empty">Покупок нет — клиент без истории. Личный совет дать не на чем: блок покажет только то, что чаще берут.</li>';
      return;
    }
    ul.innerHTML = state.purchases.map((p, i) => {
      const n = graph.nodes.get(p.family);
      const [st] = saleState(n);
      const typical = n && n.term !== undefined ? (n.term === 0 ? "бессрочно" : `обычно ${n.term}`) : "не задан";
      return `<li>
        <div class="owned__name"><span>${esc(n ? n.name : p.family)}</span><button title="Убрать" data-del="${i}" aria-label="Убрать покупку">×</button></div>
        <div class="owned__meta">${esc([n && n.vendor, className(n), st].filter(Boolean).join(" · "))}</div>
        <div class="owned__edit">
          <label>Дата покупки<input type="date" data-date="${i}" value="${esc(p.date || "")}"></label>
          <label>Срок, мес.<input type="number" min="0" max="120" data-term="${i}" value="${p.term == null ? "" : p.term}" placeholder="${typical}"></label>
        </div></li>`;
    }).join("");
    $$("[data-del]", ul).forEach((b) => b.addEventListener("click", () => { state.purchases.splice(+b.dataset.del, 1); exSel.value = ""; render(); }));
    $$("[data-date]", ul).forEach((inp) => inp.addEventListener("change", () => { state.purchases[+inp.dataset.date].date = inp.value || null; render(true); }));
    $$("[data-term]", ul).forEach((inp) => inp.addEventListener("change", () => { state.purchases[+inp.dataset.term].term = inp.value === "" ? null : +inp.value; render(true); }));
  }

  function cardHtml(r, i) {
    const n = graph.nodes.get(r.family) || {};
    const price = n.price ? `<span class="card__price">от ${nf.format(Math.round(n.price))} ₽</span>`
      : (n.sale !== 1 && n.req === 1 ? '<span class="chip">цена по запросу</span>' : "");
    return `<article class="card" data-rec="${i}" style="--slot:${slotVar(r.slot)}">
      <div class="card__slot"><i class="dot"></i>${esc(r.slotTitle)}</div>
      <div class="card__name">${esc(r.name)}</div>
      <div class="card__vendor">${esc([r.vendor, className(n)].filter(Boolean).join(" · "))}</div>
      <p class="card__why">${esc(r.why)}</p>
      ${r.also ? `<p class="card__also"><b>Заодно.</b> ${esc(r.also)}</p>` : ""}
      ${n.promo ? `<p class="card__promo">Условие магазина: ${esc(n.promo)}</p>` : ""}
      <div class="card__foot"><span class="chip">${esc(r.label)}</span>${price}${r.url ? `<a href="${esc(shopUrl(r.url, r.slot, i + 1))}" target="_blank" rel="noopener">Открыть в магазине</a>` : ""}</div>
    </article>`;
  }

  /** Строка «Пора продлить»: продление — не совет, а напоминание, поэтому оно стоит отдельно от трёх советов. */
  function renderRenew(rows) {
    const el = $("#renew");
    el.hidden = !rows.length;
    el.innerHTML = !rows.length ? "" : "<h3>Пора продлить</h3>" + rows.map((r, i) => `<div class="renew__row">
      <div><b>${esc(r.name)}</b><p>${esc(r.text)}</p></div>
      ${r.url ? `<a href="${esc(shopUrl(r.url, "renew", i + 1))}" target="_blank" rel="noopener">Продлить в магазине</a>` : ""}</div>`).join("");
  }
  /** Строка «Часто докупают»: не личный совет, а то, что часто берут клиенты того же типа. Стоит отдельно от советов. */
  function renderPopular(rows) {
    const el = $("#popular");
    el.hidden = !rows.length;
    el.innerHTML = !rows.length ? "" : "<h3>Часто докупают</h3>" + rows.map((r, i) => `<div class="renew__row">
      <div><b>${esc(r.name)}</b><p>${esc(r.text)}</p></div>
      ${r.url ? `<a href="${esc(shopUrl(r.url, "popular", i + 1))}" target="_blank" rel="noopener">Открыть в магазине</a>` : ""}</div>`).join("");
  }
  /** Неподтверждённое — отдельной строкой: проверена связь классов, а не пара с товаром клиента. */
  function renderUnconfirmed(rows) {
    const el = $("#unconfirmed");
    el.hidden = !rows.length;
    el.innerHTML = !rows.length ? "" : "<h3>Совместимость требует проверки</h3>" + rows.map((r, i) => `<div class="renew__row">
      <div><b>${esc(r.name)}</b><p>${esc(r.text)}</p></div>
      ${r.url ? `<a href="${esc(shopUrl(r.url, "unconfirmed", i + 1))}" target="_blank" rel="noopener">Открыть в магазине</a>` : ""}</div>`).join("");
  }
  /** Тот же ответ в виде, который блок кабинета получает сейчас, плюс объяснение и вид совета. */
  function renderContract(rows) {
    const el = $("#contract");
    el.hidden = false;
    el.innerHTML = `<summary>Ответ в формате кабинета (JSON)</summary>
      <p class="hint small" style="margin-top:6px">Поля link, name, vendorName и price блок кабинета получает и сейчас; reason и slot — новые: объяснение совета и его вид.</p>
      <pre>${esc(JSON.stringify({ status: rows.length ? "shown" : "hidden", data: rows }, null, 1))}</pre>`;
  }
  document.addEventListener("click", (ev) => {
    if (!ev.target.matches || !ev.target.matches("[data-agent-queue]")) return;
    $("#f-type").value = "agent"; $("#f-state").value = "";
    $$(".tab").find((b) => b.dataset.tab === "links").click();
  });
  function openQueue() {
    $("#f-type").value = "pending"; $("#f-state").value = "";
    $$(".tab").find((b) => b.dataset.tab === "links").click();
  }

  function bindCards() {
    $$("#cards .card").forEach((c) => {
      c.addEventListener("mouseenter", () => highlight(+c.dataset.rec));
      c.addEventListener("mouseleave", () => highlight(null));
    });
  }

  /** Режим «собрать клиента самому»: совет считает движок страницы по графу открытых источников. */
  function render(keepOwned) {
    if (dstate.on) return renderData();
    const t0 = performance.now();
    // в блоке — только подтверждённое; то, что ждёт менеджера, идёт отдельной строкой ниже
    const rec = new E.Recommender(graph, recOpts({ fill: false, minConfidence: state.minConf, unverified: false }));
    const res = rec.recommend({ type: state.type, today: state.today, purchases: state.purchases }, 5);
    // продление выносим в отдельную строку, три места в блоке — для новых советов
    const renew = res.recs.filter((r) => r.slot === "renew").slice(0, 2);
    last = { recs: res.recs.filter((r) => r.slot !== "renew").slice(0, 3), bans: res.bans };
    renderRenew(renew.map((r) => ({ name: r.name, text: r.why + (r.also ? " Заодно: " + r.also : ""), url: r.url })));
    // «часто докупают» — отдельной строкой: первый популярный товар, который не повторяет советы и не лежит у клиента
    const filled = new E.Recommender(graph, recOpts({ fill: true, minConfidence: state.minConf, unverified: false }))
      .recommend({ type: state.type, today: state.today, purchases: state.purchases }, 6);
    const pops = filled.recs.filter((r) => r.slot === "popular").slice(0, state.purchases.length ? 1 : 3);
    renderPopular(pops.map((r) => ({ name: r.name, text: "Не личный совет. " + r.why, url: r.url })));
    let loose = [];
    if (state.unverified) {
      const have = new Set(res.recs.map((r) => r.family));
      const pendingOnly = (r) => r.paths.length && r.paths.every((p) => (p.class || p.type === "analog_class") && !approved.has(p.key));
      loose = new E.Recommender(graph, recOpts({ fill: false, minConfidence: state.minConf, unverified: true }))
        .recommend({ type: state.type, today: state.today, purchases: state.purchases }, 10)
        .recs.filter((r) => !have.has(r.family) && pendingOnly(r)).slice(0, 2);
    }
    // замена по классу — это «тот же класс из реестра», а не связь классов: подпись у неё своя
    const unproven = (r) => (r.paths.every((p) => p.type === "analog_class")
      ? " Тот же класс из реестра; эта пара товаров не проверена." : " Проверена связь классов, а не эта пара товаров.");
    renderUnconfirmed(loose.map((r) => ({ name: r.name, text: r.why + unproven(r), url: r.url })));
    const waiting = new Set(res.bans.filter((b) => b.reason === E.PENDING).map((b) => b.key)).size;
    const pend = $("#pending");
    pend.hidden = !waiting;
    const w10 = waiting % 10, w100 = waiting % 100;
    const waits = w10 === 1 && w100 !== 11 ? "совет ждёт" : w10 >= 2 && w10 <= 4 && (w100 < 12 || w100 > 14) ? "совета ждут" : "советов ждут";
    pend.innerHTML = `Ещё ${waiting} ${waits} решения менеджера — подбор по связи классов и замены по классу: у них проверена не сама пара с товаром клиента. <button class="linkbtn" id="to-queue">Открыть очередь</button>`;
    if (waiting) $("#to-queue").addEventListener("click", openQueue);
    renderContract(last.recs.map((r) => { const n = graph.nodes.get(r.family) || {}; return { link: r.url, name: r.name, vendorName: r.vendor, price: n.price || null, slot: r.slot, reason: r.why }; }));
    const ms = performance.now() - t0;
    if (!keepOwned) renderOwned();
    $("#block-title").textContent = "Рекомендуем вам";
    $("#cost").innerHTML = `Обращений к модели: <b>0</b> · посчитано за ${ms < 1 ? "меньше 1" : Math.round(ms)} мс`;
    $("#cards").innerHTML = (last.recs.length ? last.recs.map(cardHtml).join("") : "")
      + (last.recs.length < 3 ? `<div class="empty">${!state.purchases.length ? "У клиента нет покупок, поэтому личных советов нет" : last.recs.length ? "Больше советов по делу нет" : "Для этого набора покупок советов по делу нет"}: показываем только то, что связано с покупками, популярное идёт отдельной строкой ниже.</div>` : "");
    $("#fact").hidden = true;
    bindCards();
    const srcs = new Set();
    for (const r of last.recs) for (const p of r.paths) if (p.src) srcs.add(p.src);
    const seen = new Set();
    ownedView = [];
    for (const p of state.purchases.slice().sort((a, b) => String(b.date).localeCompare(String(a.date)))) {
      if (seen.has(p.family)) continue;
      seen.add(p.family);
      const when = p.date ? `куплено ${E.fmt(E.parseDate(p.date))}` : "дата покупки неизвестна";
      ownedView.push({ id: p.family, name: nodeName(p.family), note: srcs.has(p.family) ? when : `${when} · связей пока нет`, idle: !srcs.has(p.family) });
    }
    renderWhy();
    renderBans();
  }

  // ---- «почему эти товары»: слева купленное, справа советы, между ними связи
  function renderWhy() {
    const left = $("#why-left"), right = $("#why-right");
    const rows = ownedView.filter((o) => !o.idle).concat(ownedView.filter((o) => o.idle));
    left.innerHTML = rows.map((o) => `<div class="why__node${o.idle ? " is-dim" : ""}" data-src="${esc(o.id)}">${esc(o.name)}<small>${esc(o.note)}</small></div>`).join("")
      || '<div class="why__node is-dim">У клиента нет покупок<small>советы — из того, что берут чаще всего</small></div>';
    right.innerHTML = last.recs.map((r, i) => `<div class="why__node why__node--rec" data-rec="${i}" style="--slot:${slotVar(r.slot)}">${esc(r.name)}<small>${esc(r.slotTitle)}</small></div>`).join("");
    const used = Array.from(new Set(last.recs.map((r) => r.slot)));
    $("#legend").innerHTML = used.map((s) => `<li style="--slot:${slotVar(s)}"><i></i>${esc(E.SLOT_TITLE[s])}</li>`).join("");
    requestAnimationFrame(drawWhy);
  }
  function drawWhy() {
    const box = $("#why"), svg = $("#why-svg"), labels = $("#why-labels");
    if (!box.offsetParent) return;
    const br = box.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${br.width} ${br.height}`);
    let paths = "", chips = "";
    const perSrc = {};
    last.recs.forEach((r, i) => {
      const to = $(`.why__node[data-rec="${i}"]`, box);
      if (!to) return;
      const tr = to.getBoundingClientRect();
      r.paths.forEach((p, j) => {
        if (!p.src) return;
        const from = $(`.why__node[data-src="${CSS.escape(p.src)}"]`, box);
        if (!from) return;
        const fr = from.getBoundingClientRect();
        const k = (perSrc[p.src] = (perSrc[p.src] || 0) + 1);
        const x1 = fr.right - br.left, y1 = fr.top - br.top + Math.min(fr.height - 8, 8 + k * 9);
        const x2 = tr.left - br.left, y2 = tr.top - br.top + tr.height / 2 + (j - (r.paths.length - 1) / 2) * 7;
        const dx = (x2 - x1) * 0.5;
        const d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
        // пунктир — основание слабее: выведено из названий или из общей статистики класса и линейки
        const weak = p.source === "названия редакций" || p.source === "номера версий в названиях" || p.kind === "семейство" || p.kind === "класс";
        paths += `<path class="line" data-rec="${i}" d="${d}" style="--slot:${slotVar(r.slot)}"${weak ? ' stroke-dasharray="6 5"' : ""}></path><path class="hit" data-rec="${i}" data-p="${j}" d="${d}"></path>`;
        const t = 0.5 + ((j % 3) - 1) * 0.12;
        const lx = bez(x1, x1 + dx, x2 - dx, x2, t), ly = bez(y1, y1, y2, y2, t);
        const text = E.TYPE_TITLE[p.type] || p.type;
        chips += `<span class="why__label" data-rec="${i}" data-p="${j}" style="left:${lx}px;top:${ly}px;--slot:${slotVar(r.slot)}"><i class="dot"></i>${esc(text.length > 34 ? text.slice(0, 32) + "…" : text)}</span>`;
      });
    });
    svg.innerHTML = paths;
    labels.innerHTML = chips;
    const hover = (el) => {
      el.addEventListener("mousemove", (ev) => { const p = last.recs[+el.dataset.rec].paths[+el.dataset.p]; showTip(ev, edgeTip(p)); highlight(+el.dataset.rec); });
      el.addEventListener("mouseleave", () => { hideTip(); highlight(null); });
    };
    $$("path.hit", svg).forEach(hover);
    $$(".why__label", labels).forEach(hover);
  }
  function bez(a, b, c, d, t) { const u = 1 - t; return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d; }
  function highlight(i) {
    const box = $("#why");
    $$("[data-rec]", box).forEach((el) => el.classList.toggle("is-dim", i !== null && +el.dataset.rec !== i));
    $$("#cards .card").forEach((c) => c.classList.toggle("is-hot", i !== null && +c.dataset.rec === i));
  }
  addEventListener("resize", drawWhy);

  function renderBans() {
    const el = $("#bans");
    const seen = new Set(), rows = [];
    for (const b of last.bans) {
      const k = b.family + "|" + b.reason;
      if (seen.has(k)) continue;
      seen.add(k);
      rows.push(`<li><b>${esc(nodeName(b.family))}</b> — ${esc(b.reason)} <span class="chip">${esc(E.TYPE_TITLE[b.type] || b.type)} от «${esc(nodeName(b.from))}»</span></li>`);
    }
    el.innerHTML = rows.length
      ? `<summary>Отсеяно запретами: ${rows.length}. Блок советует только то, что можно купить и что подходит клиенту.</summary><ul>${rows.join("")}</ul>`
      : "<summary>Запреты ничего не отсеяли</summary>";
  }

  // ---- клиенты из данных задачи (закрытая часть) --------------------------------------------
  const dstate = { on: false, set: "", idx: 0, hide: false };
  const SETS = [
    ["test-b2b", "Проверка на истории: организации", (c) => c.mode === "test" && c.group === "B2B"],
    ["test-cold", "Проверка на истории: холодный старт, организации", (c) => c.mode === "test" && c.group === "B2B" && c.cold],
    ["test-b2c", "Проверка на истории: частные лица", (c) => c.mode === "test" && c.group === "B2C"],
    ["today-b2b", "Сегодня: организации с доступом в кабинет", (c) => c.mode === "today" && c.group === "B2B"],
    ["today-b2c", "Сегодня: частные лица с доступом в кабинет", (c) => c.mode === "today" && c.group === "B2C"],
  ];
  const item = (id) => (priv && priv.items[id]) || { name: id, short: id, desc: "" };
  const ruDate = (iso) => E.fmt(E.parseDate(iso));
  const shownOf = (c) => c.recs.full.concat((c.renew || {}).full || [], (c.popular || {}).full || []);
  const hitsOf = (c) => shownOf(c).filter((x) => x.hit).length;
  function setClients() { const f = SETS.find((s) => s[0] === dstate.set); return f ? priv.clients.filter(f[2]) : []; }

  function setupData() {
    if (!priv) return;
    $("#data-box").hidden = false;
    $("#d-set").innerHTML = SETS.map(([k, title, f]) => `<option value="${k}">${esc(title)} (${priv.clients.filter(f).length})</option>`).join("");
    $("#d-set").addEventListener("change", () => { dstate.set = $("#d-set").value; dstate.idx = 0; fillClients(); renderData(); });
    $("#d-client").addEventListener("change", () => { dstate.idx = +$("#d-client").value; renderData(); });
    $("#d-hide").addEventListener("change", () => { dstate.hide = $("#d-hide").checked; renderData(); });
    $("#d-prev").addEventListener("click", () => step(-1));
    $("#d-next").addEventListener("click", () => step(1));
    $("#d-off").addEventListener("click", () => switchMode(false));
    $("#d-on").addEventListener("click", () => switchMode(true));
    dstate.set = SETS[0][0];
    fillClients();
    // открываем на первом клиенте, у которого сбылся хотя бы один совет: так сразу видно, как выглядит попадание
    const list = setClients();
    const lines = (c) => new Set(c.recs.full.map((x) => x.slot + "|" + (x.paths[0] ? x.paths[0].src : ""))).size;
    let first = list.findIndex((c) => hitsOf(c) > 0 && lines(c) >= 2 && c.history.length <= 4);
    if (first < 0) first = list.findIndex((c) => hitsOf(c) > 0);
    dstate.idx = Math.max(first, 0);
    $("#d-client").value = String(dstate.idx);
    switchMode(true);
  }
  function step(d) {
    const n = setClients().length;
    if (!n) return;
    dstate.idx = (dstate.idx + d + n) % n;
    $("#d-client").value = String(dstate.idx);
    renderData();
  }
  function fillClients() {
    $("#d-client").innerHTML = setClients().map((c, i) => {
      const kind = [c.type, c.segment].filter(Boolean).join(", ");
      const fact = c.mode === "test" ? ` · в блоке сбылось ${hitsOf(c)} из ${shownOf(c).length}` : "";
      return `<option value="${i}">${esc(c.id)} · ${esc(kind)} · покупок: ${c.history.length}${fact}</option>`;
    }).join("");
  }
  function switchMode(on) {
    dstate.on = on && !!priv;
    $("#pane-client").classList.toggle("is-data", dstate.on);
    if (dstate.on) renderData(); else { syncControls(); render(); }
  }

  function dataCard(x, i, c) {
    const it = item(x.item);
    const price = it.price ? `<span class="card__price">${nf.format(Math.round(it.price))} ₽</span>` : "";
    const fact = c.mode !== "test" ? "" : (x.hit
      ? '<p class="card__fact card__fact--yes">Сбылось: клиент купил этот товар после даты совета.</p>'
      : '<p class="card__fact">Клиент этот товар за три месяца не купил.</p>');
    return `<article class="card" data-rec="${i}" style="--slot:${slotVar(x.slot)}">
      <div class="card__slot"><i class="dot"></i>${esc(x.title)}</div>
      <div class="card__name" title="${esc(it.name)}">${esc(it.title || (it.name.length > 130 ? it.name.slice(0, 128) + "…" : it.name))}</div>
      <div class="card__vendor">${esc([it.vendor, it.cls, x.desc || it.desc].filter(Boolean).join(" · "))}</div>
      <p class="card__why">${esc(x.why)}</p>
      ${x.also ? `<details class="card__more"><summary>Ещё основание</summary><p class="card__also">${esc(x.also)}</p></details>` : ""}
      ${fact}
      <div class="card__foot"><span class="chip">${esc(x.label)}</span>${price}${it.url ? `<a href="${esc(shopUrl(it.url, x.slot, i + 1))}" target="_blank" rel="noopener">Открыть в магазине</a>` : ""}</div>
    </article>`;
  }

  /** Режим «клиент из данных задачи»: советы выбраны обученным ранжированием заранее, здесь только показ. */
  function renderData() {
    const list = setClients();
    const c = list[dstate.idx];
    if (!c) { $("#cards").innerHTML = '<div class="empty">В этом наборе нет клиентов.</div>'; return; }
    const recs = c.recs[dstate.hide ? "new_item" : "full"];
    const who = [c.group === "B2B" ? "организация" : "частное лицо", c.type, c.segment, c.industry].filter(Boolean).join(" · ");
    const asOf = c.mode === "test" ? priv.train_until : priv.today;
    $("#d-note").textContent = `${c.id}: ${who}. Советы даны по покупкам до ${ruDate(asOf)} включительно` + (c.cold ? "; в истории один товар — холодный старт." : ".");
    $("#owned").innerHTML = c.history.map((h) => {
      const it = item(h.item);
      const when = h.first === h.last ? `куплено ${ruDate(h.last)}` : `покупки с ${ruDate(h.first)} по ${ruDate(h.last)}`;
      return `<li><div class="owned__name"><span title="${esc(it.name)}">${esc(it.title || (it.name.length > 110 ? it.name.slice(0, 108) + "…" : it.name))}</span></div>
        <div class="owned__meta">${esc([it.vendor, it.cls, it.desc, when].filter(Boolean).join(" · "))}</div></li>`;
    }).join("");
    $("#block-title").textContent = c.mode === "test" ? `Что мы посоветовали бы ${ruDate(asOf)}` : "Рекомендуем вам";
    $("#cost").innerHTML = dstate.hide
      ? "История купленных товаров <b>спрятана</b>: совет выведен только из каталога · обращений к языковой модели: <b>0</b>"
      : "Порядок — ранжирование по истории и каталогу, чужие продукты — только с проверенной парой · обращений к языковой модели: <b>0</b>";
    $("#cards").innerHTML = recs.map((x, i) => dataCard(x, i, c)).join("")
      + (recs.length < 3 ? `<div class="empty">${recs.length ? "Больше советов по делу нет" : "Новых советов по делу нет"}: показываем только то, что связано с покупками клиента, популярное идёт отдельной строкой ниже.</div>` : "");
    const mode = dstate.hide ? "new_item" : "full";
    const renew = (c.renew || {})[mode] || [];
    renderRenew(renew.map((x) => ({ name: item(x.item).title || item(x.item).name, text: x.why + (x.hit ? " Сбылось: клиент купил это продление после даты совета." : ""), url: item(x.item).url })));
    const popular = (c.popular || {})[mode] || [];
    renderPopular(popular.map((x) => ({ name: item(x.item).title || item(x.item).name, text: x.why + (x.hit ? " Сбылось: клиент купил этот товар после даты совета." : ""), url: item(x.item).url })));
    $("#pending").hidden = true;
    renderUnconfirmed([]);
    renderContract(recs.map((x) => ({ link: item(x.item).url || null, name: item(x.item).name, vendorName: item(x.item).vendor || null, price: item(x.item).price || null, slot: x.slot, reason: x.why })));
    const fact = $("#fact");
    if (c.mode === "test") {
      const named = new Set(recs.concat(renew, popular).map((x) => x.item));
      fact.hidden = false;
      const proto = c.protocol || [];
      fact.innerHTML = `<b>Что клиент купил на самом деле с ${ruDate(priv.test_from)}:</b> ` + c.target.map((t) =>
        `<span class="bought${named.has(t) ? " bought--yes" : ""}">${named.has(t) ? "✓ " : ""}${esc(item(t).short)}${item(t).desc ? ` (${esc(item(t).desc)})` : ""}</span>`).join(" ")
        + (proto.length ? `<p class="hint small" style="margin:8px 0 0"><b>Тройка по протоколу организаторов</b> — три лучших товара по оценке модели, из неё считается Precision@3 (сбылось ${proto.filter((x) => x.hit).length} из ${proto.length}): `
          + proto.map((x) => `<span class="bought${x.hit ? " bought--yes" : ""}">${x.hit ? "✓ " : ""}${esc(item(x.item).short)}${item(x.item).desc ? ` (${esc(item(x.item).desc)})` : ""}</span>`).join(" ")
          + ` Блок выше — вид кабинета: одинаковые варианты одного продукта в нём не повторяются, а чужие продукты проходят проверку пар.</p>` : "");
    } else {
      fact.hidden = true;
    }
    bindCards();
    last = {
      bans: [],
      recs: recs.map((x) => ({ family: x.item, name: item(x.item).short + (x.desc ? ` — ${x.desc}` : ""), slot: x.slot, slotTitle: x.title, paths: x.paths })),
    };
    const srcs = new Set();
    for (const r of last.recs) for (const p of r.paths) if (p.src) srcs.add(p.src);
    ownedView = c.history.slice().reverse().map((h) => {
      const it = item(h.item);
      return { id: h.item, name: it.short + (it.desc ? ` — ${it.desc}` : ""), note: `куплено ${ruDate(h.last)}`, idle: !srcs.has(h.item) };
    });
    renderWhy();
    const rej = (c.rejected || {})[mode] || [], left = (c.unlabelled || {})[mode] || 0;
    $("#bans").innerHTML = rej.length
      ? `<summary>Что предлагала статистика, а проверка пар убрала: ${rej.length}${left ? ` · ещё ${left} без вердикта ждут разметки` : ""}</summary><ul>`
        + rej.map((x) => `<li><b>${esc(item(x.item).title || item(x.item).short)}</b> — ${esc(x.why)}</li>`).join("") + "</ul>"
      : "<summary>Проверка пар у этого клиента ничего не убрала. Товары из истории клиента не предлагаются.</summary>";
  }

  // =========================== вкладка «Товар» ===========================
  bindSearch($("#prod-q"), $("#prod-list"), showProduct);
  function linkRows(edges, other) {
    return edges.map((e) => {
      const n = graph.nodes.get(e[other]);
      const [st, cls] = saleState(n);
      return `<tr><td><span class="typ" style="--slot:${slotVar(slotOf(e.type))}"><i class="dot"></i>${esc(E.TYPE_TITLE[e.type] || e.type)}</span></td>
        <td><a href="#" data-open="${esc(e[other])}">${esc(n ? n.name : e[other])}</a> <span class="state state--${cls}">${st}</span></td>
        <td>${esc(E.LABEL[e.source] || e.source)}</td><td class="num">${String(e.confidence).replace(".", ",")}</td>
        <td class="ev">${esc((e.evidence || "").replace(/; https?:\S+$/, ""))}</td></tr>`;
    }).join("");
  }
  // Кто купил товар: по умолчанию как в отчёте об охвате (recsys.eval.coverage) — домашний продукт берёт
  // частное лицо, остальное — организация. Раньше вкладка считала частным лицом всех, кроме товаров
  // «только юрлицам», и советы для организаций здесь не показывались.
  const vetoSrc = new Set((data.veto || []).map((x) => x[0]));
  function homeProduct(n) {
    const ct = new Set(String(n.ct || "").split("|").filter(Boolean));
    const homeClass = String(data.classes[n.cls || ""] || "").toLowerCase().includes("для дома");
    return ((ct.size === 1 && ct.has("b2c")) || homeClass) && n.b2b !== 1;
  }
  /** Почему у товара нет личного совета: ждёт менеджера, есть у другого типа клиента, отсеян запретом или кандидатов нет вовсе. */
  function whyEmpty(n, who, client, res) {
    const personal = (r) => r.slot !== "renew";
    const parts = [];
    const loose = new E.Recommender(graph, recOpts({ fill: false, unverified: true })).recommend(client, 5).recs.filter(personal);
    if (loose.length) {
      parts.push(`Подсказки ждут решения менеджера (${loose.length}): ${loose.slice(0, 3).map((r) => `«${esc(r.name)}»`).join(", ")}. `
        + "У них проверена связь классов, а не эта пара товаров, поэтому клиент их пока не видит.");
    }
    const otherType = who === "b2b" ? "b2c" : "b2b";
    const other = new E.Recommender(graph, recOpts({ fill: false, unverified: false })).recommend(Object.assign({}, client, { type: otherType }), 3).recs.filter(personal);
    if (other.length) parts.push(`${otherType === "b2b" ? "Организации" : "Частному лицу"} совет есть — переключите покупателя выше.`);
    const counts = new Map();
    for (const b of res.bans) if (b.reason !== E.PENDING) counts.set(b.reason, (counts.get(b.reason) || 0) + 1);
    if (counts.size) {
      parts.push("Отсеяно запретами: " + Array.from(counts).sort((a, b) => b[1] - a[1]).slice(0, 4)
        .map(([why, k]) => `${esc(why)} — ${k}`).join("; ") + ".");
    }
    if (!parts.length) {
      const links = graph.classLinks.get(n.cls) || [];
      const linked = Array.from(new Set(links.map((l) => data.classes[l.dst_class]).filter(Boolean)));
      if (vetoSrc.has(n.id)) parts.push("Пары с этим товаром проверены, и ни одна не подошла: «уже есть», «то же самое» или «не связано».");
      else if (linked.length) {
        parts.push(`Класс «${esc(className(n))}» связан с классами ${linked.slice(0, 4).map((c) => `«${esc(c)}»`).join(", ")}, `
          + "но товара, который подходит любому товару этого класса, нет: совместимость зависит от модели.");
      } else parts.push(`У класса «${esc(className(n) || "без класса")}» нет связей с другими классами: разметка не нашла, что обычно нужно владельцу такого товара.`);
    }
    return `<div class="empty"><b>Личных советов нет.</b> ${parts.join(" ")}</div>`;
  }
  function showProduct(n, chosen) {
    const who = chosen === "b2b" || chosen === "b2c" ? chosen : (homeProduct(n) ? "b2c" : "b2b");
    const out = graph.out.get(n.id) || [], inc = graph.inc.get(n.id) || [];
    const line = Array.from(graph.lineOf(n.id));
    const [st, cls] = saleState(n);
    const client = { type: who, today: state.today, purchases: [{ family: n.id, date: isoShift(state.today, -11), term: null }] };
    // в блоке — только подтверждённое, как на вкладке «Клиент»; продление — не совет, его здесь не показываем
    const res = new E.Recommender(graph, recOpts({ fill: false, unverified: false })).recommend(client, 4);
    const recs = res.recs.filter((r) => r.slot !== "renew").slice(0, 3);
    const head = '<tr><th>Вид связи</th><th>Товар</th><th>Источник</th><th class="num">Уверенность</th><th>Основание</th></tr>';
    $("#prod-view").innerHTML = `
      <div class="prod__head"><strong>${esc(n.name)}</strong><span class="state state--${cls}">${st}</span>
        ${n.b2b === 1 ? '<span class="state">только юридическим лицам</span>' : ""}
        ${n.price ? `<span class="state">от ${nf.format(Math.round(n.price))} ₽</span>` : ""}
        ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">страница в магазине</a>` : ""}</div>
      <p class="hint" style="margin-top:6px">${esc([n.vendor, className(n)].filter(Boolean).join(" · "))}</p>
      <h3>Что посоветуем клиенту, у которого есть только этот товар</h3>
      <div style="display:flex;gap:10px;align-items:center;margin:0 0 12px"><span class="hint">Покупатель:</span>
        <div class="seg" id="prod-who" role="radiogroup" aria-label="Кто купил товар" style="max-width:320px">
          <button type="button" data-who="b2b" class="${who === "b2b" ? "is-on" : ""}" aria-checked="${who === "b2b"}">Организация</button>
          <button type="button" data-who="b2c" class="${who === "b2c" ? "is-on" : ""}" aria-checked="${who === "b2c"}">Частное лицо</button>
        </div></div>
      <div class="cards">${recs.length ? recs.map(cardHtml).join("") : whyEmpty(n, who, client, res)}</div>
      <h3>Связи от этого товара (${out.length})</h3>
      ${out.length ? `<table>${head}${linkRows(out, "dst")}</table>` : '<p class="hint">Нет.</p>'}
      <h3>Связи к этому товару (${inc.length})</h3>
      ${inc.length ? `<table>${head}${linkRows(inc, "src")}</table>` : '<p class="hint">Нет.</p>'}
      <h3>Та же линейка — не предлагаем вместе с этим товаром (${line.length})</h3>
      <p class="hint">${line.length ? line.slice(0, 30).map((f) => `<a href="#" data-open="${esc(f)}">${esc(nodeName(f))}</a>`).join(" · ") : "Нет."}</p>`;
    $$("[data-open]", $("#prod-view")).forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); const m = graph.nodes.get(a.dataset.open); if (m) showProduct(m); }));
    $$("#prod-who button", $("#prod-view")).forEach((b) => b.addEventListener("click", () => showProduct(n, b.dataset.who)));
  }

  // =========================== вкладка «Связи на проверку» ===========================
  // В очереди четыре вида строк. Связи «товар — товар» из каталога, таблиц магазина и таблицы замещения,
  // а также проверенные пары с вердиктом «нужно» клиент видит сразу, менеджер может отклонить. Связи между
  // классами, «комплекты» к товарам и замены по классу — подсказки: клиент не увидит их, пока менеджер не примет.
  const PAGE = 50;
  let page = 0;
  const allNodes = new Map(data.nodes.map((n) => [n.id, n]));
  const nodeNameAll = (id) => (allNodes.get(id) || {}).name || id;
  const clsName = (c) => data.classes[c] || c;
  const goneByClass = {};
  for (const n of data.nodes) if (n.cls && n.sale !== 1 && n.ru !== 1 && n.kind !== "hardware") goneByClass[n.cls] = (goneByClass[n.cls] || 0) + 1;
  const queue = [];
  /** Строка очереди менеджера для связи «товар — товар». */
  function edgeRow(e) {
    const kit = e[3] === "разметка: комплект" || e[3] === "серия в названии" || e[3] === "характеристики каталога: комплект" || e[3] === "агент каталога: комплект";   // комплект виден клиенту сразу; менеджер может его отклонить
    const byAgent = String(e[3]).startsWith("агент каталога");   // связь нашёл агент и подтвердил цитатой
    const cited = byAgent && ((data.agent_quotes || {})[ekey(e)] || liveQuotes[ekey(e)]);
    const evidence = cited ? `${e[5] || ""} Цитата: «${cited}»` : (e[5] || "");
    return { key: kit ? `pk|${e[0]}|${e[1]}` : ekey(e), group: byAgent ? "agent" : kit ? "kit" : "edge", type: e[2], from: nodeNameAll(e[0]), to: nodeNameAll(e[1]), source: E.LABEL[e[3]] || e[3],
                 conf: e[4], ev: evidence.replace(/; https?:\S+$/, ""), auto: kit ? E.KIT_TO_CLIENT : true, urlFrom: (allNodes.get(e[0]) || {}).url, urlTo: (allNodes.get(e[1]) || {}).url };
  }
  for (const e of data.edges.concat(liveEdges)) queue.push(edgeRow(e));
  for (const l of data.class_links || []) {
    const labelled = l.source === "разметка";
    const top = ((data.class_rank || {})[l.dst_class] || [])[0];
    queue.push({ key: `cl|${l.src_class}|${l.dst_class}`, group: "class", type: l.type || "complements", from: `Класс «${clsName(l.src_class)}»`,
                 to: `Класс «${clsName(l.dst_class)}»` + (top ? ` — например, ${nodeNameAll(top)}` : ""),
                 source: labelled ? (l.verdict === "kit" ? "разметка: комплект" : "разметка: нужно") : "модель по API", conf: l.confidence, ev: l.reason || "", auto: false });
  }
  // товары, проверенные для класса: клиент видит совет сразу, менеджер может отклонить
  for (const [cid, picks] of Object.entries(data.class_products || {})) {
    for (const x of picks) {
      queue.push({ key: `cp|${cid}|${x[0]}`, group: "pick", type: "complements", from: `Класс «${clsName(cid)}»` + (x[2] === "vendor" ? " — товары того же производителя" : ""),
                   to: nodeNameAll(x[0]), source: E.LABEL[x[1] === "kit" ? "разметка класса: комплект" : "разметка класса: нужно"], conf: x[1] === "kit" ? 0.4 : 0.7,
                   ev: x[3] || "", auto: true, urlTo: (allNodes.get(x[0]) || {}).url });
    }
  }
  for (const [cid, fams] of new E.Graph(data).ruByClass) {
    if (!goneByClass[cid]) continue;
    for (const dst of fams) {
      queue.push({ key: `cr|${cid}|${dst}`, group: "replace", type: "analog_class", from: `Класс «${clsName(cid)}»: зарубежные программы, снятые с продажи (${goneByClass[cid]})`,
                   to: nodeNameAll(dst), source: "реестр российского ПО", conf: 0.5, ev: "", auto: false, urlTo: (allNodes.get(dst) || {}).url });
    }
  }
  // Предложения агента каталога: одна строка на связь. Ждут менеджера; после «принять» товар встаёт в граф
  // страницы, и владелец исходного товара видит его в личных советах.
  const slug = (s) => String(s || "").toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  const knownEdges = new Set(data.edges.map(ekey));
  function agentRowsOf(r) {
    if (!r || !r.card || !r.card.name) return [];
    const hidden = (r.truth && r.truth.id) || "";            // проверочный прогон: товар спрятан из каталога, он уже существует
    const id = hidden || "new/" + slug(r.card.name);
    const p = r.passport || {};
    const node = hidden ? null : { id, name: r.card.name, vendor: r.card.vendor || "", cls: p.class_id || "", sale: 1,
                                   b2b: p.audience === "b2b" ? 1 : 0, kind: "software" };
    const rows = [];
    for (const e of r.edges || []) {
      if (e.type === "same_line") continue;
      const src = e.src === "new" ? id : e.src, dst = e.dst === "new" ? id : e.dst;
      if (knownEdges.has(`${src}|${dst}|${e.type}`)) continue;   // каталог эту связь уже знает
      const target = allNodes.get(dst);
      if (target && target.sale !== 1) continue;                  // товара нет в продаже: клиенту он не покажется, решать нечего
      rows.push({ key: `ag|${id}|${src}|${dst}|${e.type}`, group: "agent", type: e.type,
                  from: src === id ? r.card.name : nodeNameAll(src), to: dst === id ? r.card.name : nodeNameAll(dst),
                  source: "агент каталога", conf: 0.75, ev: e.why || "", auto: false,
                  urlFrom: (allNodes.get(src) || {}).url, urlTo: (allNodes.get(dst) || {}).url,
                  payload: { node, edge: [src, dst, e.type, "агент каталога, принято менеджером", 0.75, e.why || ""] } });
    }
    return rows;
  }
  function addAgentRows(run) {
    let added = 0;
    for (const row of agentRowsOf(run)) if (!queue.some((q) => q.key === row.key)) { queue.push(row); added++; }
    return added;
  }
  ((agent && agent.runs) || []).forEach(addAgentRows);
  // предложения агента, закрывавшего дыру, для которых критик не нашёл подтверждающей цитаты: ждут менеджера
  // почему предложение агента ждёт менеджера, а не пошло клиенту: причина записана в отчёте агента
  const queueWhy = {};
  for (const h of holesOf(holesData)) for (const l of h.links || []) if (l.status !== "graph" && l.quote_note) queueWhy[`${h.family}|${l.dst}`] = l.quote_note;
  function addQueueRow(x) {
    const key = `ag|${x.src}|${x.dst}|${x.type}`;
    if (queue.some((q) => q.key === key)) return;
    const why = x.note || queueWhy[`${x.src}|${x.dst}`] || "";
    const tail = (x.quote ? ` Цитата: «${x.quote}».` : "") + (why ? ` Ждёт менеджера: ${why}.` : (x.quote ? "" : " Подтверждающей цитаты нет."));
    queue.push({ key, group: "agent", type: x.type, from: nodeNameAll(x.src), to: nodeNameAll(x.dst), source: "агент каталога",
                 conf: x.confidence || 0.5, ev: (x.evidence || "").replace(/[.\s]+$/, "") + "." + tail, auto: false,
                 urlFrom: (allNodes.get(x.src) || {}).url, urlTo: (allNodes.get(x.dst) || {}).url,
                 payload: { node: null, edge: [x.src, x.dst, x.type, "агент каталога, принято менеджером", x.confidence || 0.5, x.evidence || ""] } });
  }
  (data.agent_queue || []).forEach(addQueueRow);
  liveQueue.forEach(addQueueRow);
  // принятое раньше (например, в живом прогоне до перезагрузки) остаётся в очереди, чтобы его можно было отменить
  for (const [k, p] of Object.entries(agentStore)) {
    if (p && p.edge && !queue.some((q) => q.key === k)) {
      queue.push({ key: k, group: "agent", type: p.edge[2], from: p.from || p.edge[0], to: p.to || p.edge[1], source: "агент каталога",
                   conf: 0.75, ev: p.edge[5] || "", auto: false, payload: { node: p.node || null, edge: p.edge } });
    }
  }
  const waitingRows = () => queue.filter((r) => !r.auto && !votes[r.key]);
  const statusOf = (r) => (votes[r.key] === "ok" ? "принято" : votes[r.key] === "no" ? "отклонено" : r.auto ? "показывается клиенту" : "ждёт проверки");
  const types = Array.from(new Set(data.edges.map((e) => e[2])));
  const sources = Array.from(new Set(queue.map((r) => r.source)));
  $("#f-type").innerHTML = '<option value="pending">ждут проверки менеджера</option><option value="class">связи между классами</option><option value="kit">комплекты к товарам</option><option value="pick">товары, проверенные для класса</option><option value="replace">замены по классу</option><option value="agent">предложения агента каталога</option>'
    + '<option value="advice">связи товаров (без «той же линейки»)</option><option value="">все</option>'
    + types.map((t) => `<option value="${esc(t)}">товары: ${esc(E.TYPE_TITLE[t] || t)}</option>`).join("");
  $("#f-source").innerHTML = '<option value="">любой</option>' + sources.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  ["f-type", "f-source", "f-state", "f-q"].forEach((id) => $("#" + id).addEventListener("input", () => { page = 0; renderLinks(); }));
  function filteredLinks() {
    const t = $("#f-type").value, src = $("#f-source").value, st = $("#f-state").value, q = $("#f-q").value.trim().toLowerCase();
    return queue.filter((r) => {
      const v = votes[r.key];
      if (t === "pending") { if (r.auto || v) return false; }
      else if (t === "class" || t === "replace" || t === "kit" || t === "pick" || t === "agent") { if (r.group !== t) return false; }
      else if (t === "advice") { if (r.group !== "edge" || r.type === "same_line") return false; }
      else if (t && (r.group !== "edge" || r.type !== t)) return false;
      if (src && r.source !== src) return false;
      if (st && (v || "new") !== st) return false;
      if (q && !(`${r.from} ${r.to}`.toLowerCase().includes(q))) return false;
      return true;
    });
  }
  function saveVotes() {
    for (const r of queue) {
      if (r.group !== "agent") continue;
      if (votes[r.key] === "ok") agentStore[r.key] = Object.assign({ from: r.from, to: r.to }, r.payload);
      else delete agentStore[r.key];
    }
    localStorage.setItem(AGENT_KEY, JSON.stringify(agentStore));
    localStorage.setItem(VOTES_KEY, JSON.stringify(votes));
    rebuild(); renderLinks(); render();
  }
  function renderLinks() {
    const rows = filteredLinks();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    page = Math.min(page, pages - 1);
    const part = rows.slice(page * PAGE, page * PAGE + PAGE);
    const kind = (r) => (r.group === "class" ? "классы: " : r.group === "pick" ? "класс — товар: " : r.group === "agent" ? "агент: " : "") + (E.TYPE_TITLE[r.type] || r.type);
    $("#links").innerHTML = '<tr><th>Откуда</th><th>Вид связи</th><th>Куда</th><th>Источник</th><th class="num">Уверенность</th><th>Основание</th><th>Решение</th></tr>' +
      part.map((r) => {
        const v = votes[r.key] || "";
        return `<tr class="${v === "no" ? "is-no" : ""}"><td>${esc(r.from)}</td>
          <td><span class="typ" style="--slot:${slotVar(slotOf(r.type))}"><i class="dot"></i>${esc(kind(r))}</span></td>
          <td>${esc(r.to)}</td><td>${esc(r.source)}</td><td class="num">${String(r.conf).replace(".", ",")}</td>
          <td class="ev">${esc(r.ev)}</td>
          <td><span class="vote"><button data-k="${esc(r.key)}" data-v="ok" class="${v === "ok" ? "is-ok" : ""}" title="Принять связь">принять</button><button data-k="${esc(r.key)}" data-v="no" class="${v === "no" ? "is-no" : ""}" title="Отклонить связь">отклонить</button></span>
            <div class="status${statusOf(r) === "ждёт проверки" ? " status--wait" : ""}">${statusOf(r)}</div></td></tr>`;
      }).join("");
    const done = Object.values(votes);
    $("#pager").innerHTML = `<span>По отбору: ${nf.format(rows.length)} · ждут проверки всего: ${nf.format(waitingRows().length)} · принято ${done.filter((x) => x === "ok").length} · отклонено ${done.filter((x) => x === "no").length}</span>
      <span><button class="btn" id="pg-prev"${page ? "" : " disabled"}>Назад</button> стр. ${page + 1} из ${pages} <button class="btn" id="pg-next"${page < pages - 1 ? "" : " disabled"}>Вперёд</button></span>`;
    $("#pg-prev").onclick = () => { page--; renderLinks(); };
    $("#pg-next").onclick = () => { page++; renderLinks(); };
    $$("#links .vote button").forEach((b) => b.addEventListener("click", () => {
      if (votes[b.dataset.k] === b.dataset.v) delete votes[b.dataset.k]; else votes[b.dataset.k] = b.dataset.v;
      saveVotes();
    }));
  }
  $("#accept-all").addEventListener("click", () => {
    for (const r of filteredLinks()) if (!votes[r.key]) votes[r.key] = "ok";
    saveVotes();
  });
  $("#export-links").addEventListener("click", () => {
    const q = (x) => `"${String(x == null ? "" : x).replace(/"/g, '""')}"`;
    const lines = [["откуда", "адрес", "вид связи", "куда", "адрес", "источник", "уверенность", "основание", "решение"].join(";")];
    for (const r of queue) {
      if (r.type === "same_line" || votes[r.key] === "no") continue;
      lines.push([q(r.from), q(r.urlFrom), q(E.TYPE_TITLE[r.type] || r.type), q(r.to), q(r.urlTo), q(r.source), String(r.conf).replace(".", ","), q(r.ev), q(statusOf(r))].join(";"));
    }
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "svyazi-tovarov.csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // =========================== вкладка «Цифры» ===========================
  /** Полоса с подписью значения: длина — доля от наибольшего значения в группе; усы — 95% интервал. */
  function bars(rows, max) {
    return `<div class="bars">${rows.map((r) => {
      const w = Math.max(0.5, (r.value / max) * 100);
      const ci = r.ci ? `<i class="bars__ci" style="left:${(r.ci[0] / max) * 100}%;width:${((r.ci[1] - r.ci[0]) / max) * 100}%"></i>` : "";
      const tipText = `${r.name}: ${pct(r.value)}` + (r.ci ? `; 95% интервал ${pct(r.ci[0])} – ${pct(r.ci[1])}` : "") + (r.note ? `. ${r.note}` : "");
      return `<div class="bars__row" data-tip="${esc(tipText)}"><span class="bars__name">${esc(r.name)}</span>
        <span class="bars__track"><i class="bars__bar${r.ours ? " bars__bar--ours" : ""}" style="width:${w}%"></i>${ci}</span>
        <span class="bars__val">${pct(r.value)}</span></div>`;
    }).join("")}</div>`;
  }
  function officialHtml() {
    const r = report, reg = r.regimes, org = r.organisers;
    const seg = ["A все", "A B2B", "A B2C", "B все", "B B2B", "B B2C"];
    const head = ["Группа A, все", "A, организации", "A, частные лица", "Группа B, все", "B, организации", "B, частные лица"];
    const row = (name, v, ours) => `<tr><td>${ours ? "<b>" + esc(name) + "</b>" : esc(name)}</td>${seg.map((s) => `<td class="num">${v[s] == null ? "—" : pct(v[s])}</td>`).join("")}</tr>`;
    const full = reg.full.p3, cat = r.catalog, m = r.money;
    const fillAll = (r.fill_all["все"] || {})["история или линейка"];
    const chartA = bars([
      { name: "Популярность (организаторы)", value: org["популярность"]["A B2B"] },
      { name: "Совместные покупки (организаторы)", value: org["совместные покупки"]["A B2B"] },
      ...(r.same_line && r.same_line.p3 ? [{ name: "Правило «та же линейка», без обучения", value: r.same_line.p3["A B2B"], ci: (r.same_line.ci || {})["A B2B"], note: "сильный базовый способ: продление, другой размер лицензии, следующая версия" }] : []),
      { name: "Наша модель без знаний каталога", value: reg.no_catalog.p3["A B2B"], ci: reg.no_catalog.ci["A B2B"], ours: true },
      { name: "Наша модель", value: full["A B2B"], ci: reg.full.ci["A B2B"], ours: true },
    ], 0.2);
    const chartNew = bars([
      { name: "Без знаний каталога", value: reg.new_item_no_catalog.p3["A B2B"], ci: reg.new_item_no_catalog.ci["A B2B"], note: "остаётся только общая популярность" },
      { name: "Со знаниями каталога", value: reg.new_item.p3["A B2B"], ci: reg.new_item.ci["A B2B"], ours: true },
      { name: "Для сравнения: с полной историей", value: full["A B2B"], ci: reg.full.ci["A B2B"], ours: true },
    ], 0.2);
    const rub = (x) => nf.format(Math.round(x / 1000)) + " тыс. ₽";
    return `${proofHtml(r.proof)}
      ${fillHtml(r.block_fill)}
      ${restoreHtml(r.restore)}
      <h2 style="margin-top:26px">Проверка на истории по протоколу организаторов</h2>
      <p class="hint" style="margin-top:6px">Обезличенный датасет организаторов${r.dataset ? `, версия ${esc(String(r.dataset).replace(/^dataset_v?|\.xlsx$/g, ""))}` : ""}. Советы даны по покупкам до ${esc(r.protocol.train_until)}, засчитываются новые для клиента товары, купленные с ${esc(r.protocol.test_from)}. Модель и её режимы обучались только на периоде до даты разреза.</p>
      <dl class="tiles">
        <div class="tile"><dt>Угадано из трёх советов, организации</dt><dd>${pct(full["A B2B"])}</dd><small>базовые способы организаторов: ${pct(org["популярность"]["A B2B"])} и ${pct(org["совместные покупки"]["A B2B"])}</small></div>
        <div class="tile"><dt>То же, холодный старт</dt><dd>${pct(full["B B2B"])}</dd><small>базовые способы: ${pct(org["популярность"]["B B2B"])} и ${pct(org["совместные покупки"]["B B2B"])}</small></div>
        <div class="tile"><dt>Если спрятать историю пар купленного товара</dt><dd>${pct(reg.new_item.p3["A B2B"])}</dd><small>соседние артикулы линейки и каталог остаются; без знаний каталога ${pct(reg.new_item_no_catalog.p3["A B2B"])}</small></div>
        <div class="tile"><dt>Fill Rate по протоколу, группы A и B</dt><dd>100 %</dd><small>у совместных покупок ${pct(0.923, 1)} и ${pct(0.872, 1)}; совет по собственному основанию — у ${fillAll == null ? "—" : pct(fillAll, 0)} клиентов с покупками. Показ блока в кабинете (${pct(r.current_fill)}) считается по визитам и с этим не сравним</small></div>
      </dl>
      <div class="charts">
        <figure><figcaption>Precision@3, организации (группа A)</figcaption>${chartA}</figure>
        <figure><figcaption>Если спрятать историю самого купленного товара (соседние артикулы линейки и каталог остаются)</figcaption>${chartNew}</figure>
      </div>
      <p class="hint">Серые полосы — базовые способы, синие — наша модель; отрезок — 95-процентный интервал. Шкала у обоих графиков одна, от 0 до 20 %.</p>
      <h3>Все группы</h3>
      <div class="tablewrap"><table class="links"><tr><th>Способ</th>${head.map((h) => `<th class="num">${h}</th>`).join("")}</tr>
        ${row("Популярность (организаторы)", org["популярность"])}${row("Совместные покупки (организаторы)", org["совместные покупки"])}
        ${r.same_line && r.same_line.p3 ? row("Правило «та же линейка», без обучения", r.same_line.p3) : ""}
        ${Object.keys(reg).map((k) => row(reg[k].title[0].toUpperCase() + reg[k].title.slice(1), reg[k].p3, k === "full")).join("")}
      </table></div>
      <p class="hint" style="margin-top:8px">Частных лиц в проверке всего несколько десятков — их цифры шумные, главных выводов по ним не делаем.</p>
      ${shownHtml(r.shown)}
      ${whyHtml(r.why)}
      ${clientsHtml(r.ai_clients)}
      ${judgeHtml(r.ai_judge)}
      ${breakdownHtml(r)}
      <h3>Потолок: у скольких артикулов фида есть хоть какая-то опора для совета</h3>
      <table class="links"><tr><th>На что можно опереться</th><th class="num">Артикулов в наличии</th><th class="num">Доля от ${nf.format(cat.available)}</th></tr>
        <tr><td>История продаж: после товара хотя бы три клиента купили один и тот же другой товар</td><td class="num">${nf.format(cat.history_3)}</td><td class="num">${pct(cat.history_3 / cat.available)}</td></tr>
        <tr><td>Каталог: есть другой товар той же линейки</td><td class="num">${nf.format(cat.line_available)}</td><td class="num">${pct(cat.line_available / cat.available, 0)}</td></tr>
        <tr><td>Каталог: линейка или класс</td><td class="num">${nf.format(cat.line_or_class)}</td><td class="num">${pct(cat.line_or_class / cat.available, 0)}</td></tr></table>
      <p class="hint" style="margin-top:8px">Это верхняя граница по артикулам фида: сосед по линейке или класс — ещё не совет. Сколько товаров получают личный совет на деле, показано ниже, в таблице «У скольких товаров есть личный совет»: там счёт идёт по товарам (семействам), а не по артикулам.</p>
      <h3>Деньги за угаданными покупками, на 100 организаций</h3>
      <table class="links"><tr><th>Способ</th><th class="num">Угадано покупок</th><th class="num">Сумма по ценам витрины</th></tr>
        ${Object.entries(m).filter(([k]) => k !== "all_targets").map(([k, v]) => `<tr><td>${esc(k[0].toUpperCase() + k.slice(1))}</td><td class="num">${v.hits}</td><td class="num">${rub(v.rub_per_100_clients)}</td></tr>`).join("")}
        <tr><td>Все новые покупки этих клиентов</td><td class="num">${m.all_targets.targets}</td><td class="num">${rub(m.all_targets.rub_per_100_clients)}</td></tr></table>
      <p class="hint" style="margin-top:8px">Цены нынешние, количество лицензий в данных не указано, у части товаров цены нет. Это покупки, которые клиент сделал и без нашего блока: число показывает уместность совета, а не прирост продаж.</p>`;
  }
  /** Из чего состоят попадания: вариант уже купленного продукта, другая версия или новый для клиента продукт. */
  function breakdownHtml(r) {
    const b = r.breakdown || {};
    const order = ["популярность", "совместные покупки", "та же линейка", "наша модель"].filter((k) => b[k] && b[k]["A B2B"]);
    if (!order.length) return "";
    const KINDS = [["вариант", "Другой вариант уже купленного"], ["версия", "Другая версия или редакция"], ["новый продукт", "Новый для клиента продукт"]];
    const title = { "популярность": "Популярность (организаторы)", "совместные покупки": "Совместные покупки (организаторы)", "та же линейка": "Правило «та же линейка», без обучения", "наша модель": "<b>Наша модель</b>" };
    const sum = (o) => KINDS.reduce((a, [k]) => a + o[k], 0);
    const targets = b[order[0]]["A B2B"].targets;
    const row = (k) => {
      const x = b[k]["A B2B"];
      return `<tr><td>${title[k]}</td>${KINDS.map(([kk]) => `<td class="num">${x.hits[kk]} <small>· ${pct(x.parts[kk])}</small></td>`).join("")}
        <td class="num">${sum(x.hits)} <small>· ${pct(x.p3)}</small></td></tr>`;
    };
    const ours = b["наша модель"] && b["наша модель"]["A B2B"], pop = b["популярность"] && b["популярность"]["A B2B"];
    const note = ours && pop
      ? `На новых для клиента продуктах наша модель угадала ${ours.hits["новый продукт"]} покупок из ${targets["новый продукт"]}, популярность — ${pop.hits["новый продукт"]}`
        + (b["та же линейка"] ? `, правило «та же линейка» — ${b["та же линейка"]["A B2B"].hits["новый продукт"]}. Простое правило почти не уступает модели на продлениях и других вариантах купленного. ${ours.hits["новый продукт"] > pop.hits["новый продукт"] ? "Прибавка модели — новые продукты." : "На новых продуктах модель популярность не обгоняет: её прибавка — варианты и версии уже купленного."}` : ".") : "";
    const future = r.future_information || [];
    return `<h3>Из чего состоят попадания, организации (группа A)</h3>
      <p class="hint">Разные артикулы одного продукта в проверке считаются разными товарами, поэтому попадание бывает трёх видов. В ячейке — число угаданных покупок и их вклад в Precision@3; вклады в сумме дают общую цифру.</p>
      <table class="links"><tr><th>Способ</th>${KINDS.map(([, name]) => `<th class="num">${name}</th>`).join("")}<th class="num">Всего</th></tr>
        <tr><td>Таких покупок у клиентов в проверочный период</td>${KINDS.map(([kk]) => `<td class="num">${targets[kk]}</td>`).join("")}<td class="num">${sum(targets)}</td></tr>
        ${order.map(row).join("")}</table>
      ${note ? `<p class="hint" style="margin-top:8px">${note}</p>` : ""}
      ${future.length ? `<h3>Что в расчёте может знать о будущем</h3>
        <p class="hint">Снимка каталога на дату разреза нет, поэтому часть сведений о товаре взята из сегодняшнего каталога:</p>
        <ul class="hint">${future.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}`;
  }

  /** Показ блока в единицах заказчика: какой доле клиентов кабинета есть что посоветовать по делу. */
  /** Тест «спрятали — вернул»: у товара прячут разметку, агент выводит связи заново; счёт — в клиентах кабинета. */
  function restoreHtml(r, pending) {
    if (!r || !r.clients_with_access) return "";
    const st = r.clients_by_state || {}, q = r.queue || {};
    const held = st["держится на вердиктах одного товара"] || 0;
    const intro = `У ${nf.format(held)} клиентов кабинета из ${nf.format(r.clients_with_access)} совет по делу держится на связках одного товара. Тест: у товара прячут всю разметку, как будто его завели сегодня; агент выводит связи заново по открытому каталогу, и мы считаем, скольким клиентам совет вернулся и совпал ли агент со спрятанной разметкой.`;
    const all = (x) => nf.format((x || {}).all || 0);
    const kilo = (x) => String((x / 1000).toFixed(1)).replace(".", ",") + " тыс.";
    const ok = (m) => !!(m && m.products && m.with_own_ground_advice && m.with_own_ground_advice.before);
    /** Сверка решений агента с разметкой: сколько пар разметка знает и в скольких решение то же. */
    const tally = (p) => {
      const same = (p || {})["то же решение"] || 0, other = (p || {})["решение другое"] || 0;
      return { same, known: same + other, fresh: (p || {})["пары нет в разметке"] || 0 };
    };
    const judged = (m) => tally(m.pairs_vs_labelling);                // все предложения агента, включая очередь менеджера
    const judgedAuto = (m) => (m.pairs_vs_labelling_confirmed ? tally(m.pairs_vs_labelling_confirmed) : null);   // только то, что идёт в граф само
    const hiddenNeed = (m) => (m.need_pairs_hidden_in_catalog != null ? m.need_pairs_hidden_in_catalog : (m.need_pairs_hidden || 0));
    /** Пояснение из отчёта замера: без названий товаров; точку в конце ставим сами. */
    const noteOf = (m) => (m.note ? esc(String(m.note).trim().replace(/[.\s]+$/, "")) + ". " : "");
    const took = (sec) => (sec >= 180 ? `${nf.format(Math.round(sec / 60))} мин` : `${nf.format(Math.round(sec))} с`);
    const runLine = (m) => {
      const run = m.agent_run || {};
      return run.tokens_in ? ` Прогон: обращений модели к инструментам — ${nf.format(run.tool_calls_by_model || 0)}, ${kilo(run.tokens_in)} токенов на вход и ${kilo(run.tokens_out || 0)} на выход, ${took(run.seconds || 0)}.` : "";
    };
    /** Замер в клиентах: было → спрятали → вернул агент сам → вместе с очередью менеджера. */
    const byClients = (m, title) => {
      const w = m.with_own_ground_advice, j = judged(m);
      const withManager = w.proposed && m.returned_with_manager != null;
      return `<h3>${esc(title)}</h3>
        <dl class="tiles" style="margin:8px 0">
          <div class="tile"><dt>Было: клиентов с советом по делу</dt><dd>${all(w.before)}</dd><small>владельцы ${nf.format(m.products)} товаров из теста${m.owners_with_access ? ` (клиентов кабинета: ${nf.format(m.owners_with_access)})` : ""}, вся разметка на месте</small></div>
          <div class="tile"><dt>Спрятали разметку этих товаров</dt><dd>${all(w.hidden)}</dd><small>совет потеряли ${nf.format(m.lost_when_hidden || 0)} — ${pct(m.lost_share_of_all_clients || 0)} клиентов кабинета</small></div>
          <div class="tile"><dt>Вернул агент сам</dt><dd>${all(w.restored)}</dd><small>совет вернулся ${nf.format(m.returned_by_agent || 0)} клиентам${m.share_returned != null ? ` — ${pct(m.share_returned, 0)} потерянного` : ""}: связь подтверждена дословной цитатой и идёт в граф сразу</small></div>
          ${withManager ? `<div class="tile"><dt>Вместе с очередью менеджера</dt><dd>${all(w.proposed)}</dd><small>${nf.format(m.returned_with_manager)} клиентов${m.share_returned_with_manager != null ? ` — ${pct(m.share_returned_with_manager, 0)} потерянного` : ""}: агент назвал связь, но дословной цитаты нет, нужно одно согласие менеджера</small></div>` : ""}
        </dl>
        <p class="hint">${noteOf(m) || `Агент дал связь к ${nf.format(m.products_with_agent_link || 0)} товарам из ${nf.format(m.products)}; к остальным ответил «связи нет». `}Связей ${nf.format(m.agent_links || 0)}, с дословной цитатой ${nf.format(m.agent_links_confirmed || 0)}. Из ${nf.format(hiddenNeed(m))} спрятанных пар «нужно» агент назвал ${nf.format(m.need_pairs_proposed_by_agent != null ? m.need_pairs_proposed_by_agent : (m.need_pairs_found_by_agent || 0))}, с дословной цитатой — ${nf.format(m.need_pairs_found_by_agent || 0)}.${j.known ? ` С разметкой решение совпало в ${j.same} случаях из ${j.known}.` : ""}${runLine(m)}</p>`;
    };
    /** Замер в парах — когда владельцы товаров совета не теряют (их держат варианты своей линейки) и клиентский счёт пуст. */
    const byPairs = (m, title) => {
      const j = judged(m), ja = judgedAuto(m), need = hiddenNeed(m), found = m.need_pairs_found_by_agent || 0;
      return `<h3>${esc(title)}</h3>
        <dl class="tiles" style="margin:8px 0">
          <div class="tile"><dt>Товаров в выборке</dt><dd>${nf.format(m.products)}</dd><small>связь агент дал к ${nf.format(m.products_with_agent_link || 0)}, к остальным ответил «связи нет»</small></div>
          <div class="tile"><dt>Связей агента</dt><dd>${nf.format(m.agent_links || 0)}</dd><small>в граф без менеджера идут ${nf.format(m.agent_links_confirmed || 0)} — те, что подтверждены цитатой из описания; остальные ждут менеджера</small></div>
          <div class="tile"><dt>Предложения совпали с разметкой</dt><dd>${j.same} из ${j.known}</dd><small>это все предложения агента менеджеру${ja ? `; из связей, которые идут в граф сами, сверить удалось ${ja.known}, совпало ${ja.same}` : ""}. Ещё ${nf.format(j.fresh)} пар в разметке нет</small></div>
          <div class="tile"><dt>Найдено спрятанных «нужно»</dt><dd>${nf.format(found)} из ${nf.format(need)}</dd><small>${need ? pct(found / need) : "—"} — с подтверждением; всего агент назвал ${nf.format(m.need_pairs_proposed_by_agent != null ? m.need_pairs_proposed_by_agent : found)}, остальное ждёт менеджера</small></div>
        </dl>
        <p class="hint">${noteOf(m)}Клиентский счёт у этой выборки пуст: владельцев этих товаров держат советы по вариантам своей линейки, от спрятанной разметки они не зависят (с советом по делу ${all(m.with_own_ground_advice.before)} из ${nf.format(m.owners_with_access || 0)} и до, и после). Поэтому здесь считаются пары.${runLine(m)}</p>`;
    };
    const block = (m, title) => (!ok(m) ? "" : (m.lost_when_hidden ? byClients(m, title) : byPairs(m, title)));
    const top = r.measured, wide = r.measured_wide, think = r.measured_wide_thinking;
    const parts = [
      block(top, `Товары, на которых держится больше всего клиентов: ${nf.format((top || {}).products || 0)}. На них агент настраивался`),
      block(wide, `Широкая выборка: ${nf.format((wide || {}).products || 0)} товаров, которые до прогона никто не разглядывал`),
      block(think, `Та же широкая выборка с рассуждением модели: ${nf.format((think || {}).products || 0)} товаров`),
    ].filter(Boolean);
    if (!parts.length) {
      if (!pending) return "";                              // замера нет: обещание без числа открытому стенду ни к чему
      return `<h2>Проверка агента: спрятали — вернул</h2>
        <p class="hint" style="margin-top:6px">${intro} В очереди теста ${nf.format(q.products || 0)} товаров, за ними ${nf.format(q.clients || 0)} клиентов. Замера ещё нет: агент на сильной модели не запускался.</p>`;
    }
    // общий вывод по обеим выборкам — одной фразой и без округления в свою пользу
    let verdict = "";
    if (ok(top) && ok(wide)) {
      const j = judged(wide), need = hiddenNeed(wide);
      const ja = judgedAuto(wide);
      verdict = `<p style="margin:8px 0 2px"><b>Итог.</b> На ${nf.format(top.products)} товарах агент настраивался: сам вернул совет ${top.share_returned != null ? pct(top.share_returned, 0) : "—"} клиентов, с согласием менеджера — ${top.share_returned_with_manager != null ? pct(top.share_returned_with_manager, 0) : "—"}. На ${nf.format(wide.products)} остальных он проверялся. Агент предлагает связи менеджеру: его предложения совпали с разметкой в ${j.same} случаях из ${j.known}. Без согласия менеджера в граф идёт только то, что подтверждено цитатой из описания, — таких связей ${nf.format(wide.agent_links_confirmed || 0)}${ja ? `, сверить с разметкой удалось ${ja.known}, совпала ${ja.same}` : ""}. Из ${nf.format(need)} спрятанных «нужно» он назвал ${nf.format(wide.need_pairs_proposed_by_agent != null ? wide.need_pairs_proposed_by_agent : (wide.need_pairs_found_by_agent || 0))}, с подтверждением — ${nf.format(wide.need_pairs_found_by_agent || 0)}. ${(() => {
        // тот же прогон с рассуждением модели: точнее и находит больше, но в разы дольше и дороже
        if (!ok(think)) return "";
        const jt = judgedAuto(think), a = (wide.agent_run || {}).seconds || 0, b = (think.agent_run || {}).seconds || 0;
        return ` С рассуждением модели на тех же товарах в граф сами идут ${nf.format(think.agent_links_confirmed || 0)} связей${jt ? `, с разметкой совпало ${jt.same} из ${jt.known} сверяемых` : ""}, спрятанных «нужно» возвращено ${nf.format(think.need_pairs_found_by_agent || 0)} из ${nf.format(hiddenNeed(think))}${a && b ? ` — но прогон шёл ${took(b)} вместо ${took(a)}` : ""}.`;
      })()} Агент — помощник менеджера с предложениями, а не автомат: разметку он не заменяет, а дополняет.</p>`;
    }
    return `<h2>Проверка агента: спрятали — вернул</h2>
      <p class="hint" style="margin-top:6px">${intro}</p>
      ${verdict}
      ${parts.join("")}
      <p class="hint">${(() => { const rule = (wide && wide.check_rules) || (top && top.check_rules) || ""; return rule ? "Правила проверки: " + esc(String(rule).trim().replace(/[.\s]+$/, "")) + ". " : ""; })()}Комплект агента советом по делу не считается — как и в блоке. Пробные связи теста лежат отдельно и в граф стенда не попадают.</p>`;
  }

  function fillHtml(f) {
    if (!f) return "";
    const b = f.b2b, c = f.b2c;
    return `<h2>Кому блок кабинета даст совет</h2>
      <dl class="tiles" style="margin-top:10px">
        <div class="tile"><dt>Блок с обоснованным советом увидят</dt><dd>${pct(b.own, 0)} организаций</dd><small>сейчас блок рекомендаций заполнен в ${pct(f.now)} визитов</small></div>
        <div class="tile"><dt>То же, частные лица</dt><dd>${pct(c.own, 0)}</dd><small>ещё ${pct(c.kit_only, 0)} получат только «комплект» — совет «если ещё нет…»</small></div>
      </dl>
      <p class="hint" style="margin:6px 0 14px">Клиенты с доступом в кабинет на ${esc(f.today)}: ${nf.format(b.clients)} организаций и ${nf.format(c.clients)} частных лиц. «Обоснованный совет» — карточка с собственным основанием: вердикт «нужно», связь графа (версия, редакция, замена) или другой вариант своей линейки. У организаций ещё ${pct(b.kit_only)} получат только «комплект». Оговорки: у нас доля клиентов, у заказчика — доля визитов; у частных лиц прибавка над нынешним показом держится на «комплекте», который на истории пока не сбывался.</p>`;
  }

  /** Доказательства, которые можно проверить: счёт организаторов на наших ответах, значимость, устойчивость. */
  function proofHtml(p) {
    if (!p || !p.segments) return "";
    const S = p.segments, num = (x) => String(x).replace(".", ",");
    const pv = (x) => (x < 1e-3 ? `меньше 10<sup>${Math.ceil(Math.log10(x))}</sup>` : num(x.toFixed(2)));
    const line = (k, title) => {
      const r = S[k];
      if (!r) return "";
      const o = r["наша система"], pp = r["популярность"], cc = r["совместные покупки"];
      return `<tr><td>${esc(title)} <small style="color:var(--muted)">(${nf.format(r.clients)})</small></td><td class="num"><b>${num(o.p3)} %</b></td><td class="num">${num(pp.p3)} %</td><td class="num">${num(cc.p3)} %</td><td class="num">${o.variant_share_ != null ? "" : ""}${num(o["variant_share_%"])} % / ${num(cc["variant_share_%"])} %</td></tr>`;
    };
    const sig = (k, title) => {
      const r = S[k];
      if (!r) return "";
      const a = r["против: популярность"], b = r["против: совместные покупки"];
      const worst = a.diff_pp <= b.diff_pp ? a : b, pmax = Math.max(a.p_sign, b.p_sign);
      return `<li><b>${esc(title)}:</b> разница с лучшим базовым способом +${num(worst.diff_pp)} п.п., 95 % интервал от +${num(worst.ci95_pp[0])} до +${num(worst.ci95_pp[1])}; клиентов, которым наш набор угадал больше, — ${nf.format(worst.clients_better)}, меньше — ${nf.format(worst.clients_worse)}; вероятность получить такой перевес случайно — ${pv(pmax)}.</li>`;
    };
    const rb = p.robust || {}, sp = rb.splits || {}, pc = (x) => (x == null ? "—" : pct(x));
    const seeds = (a) => (a && a.length ? `${pc(Math.min(...a))} – ${pc(Math.max(...a))}` : "—");
    const splitRow = (d) => {
      const x = sp[d];
      if (!x || !x.model) return "";
      const lbl = d.split("-").reverse().join(".");
      return `<tr><td>разрез ${lbl}</td><td class="num"><b>${pc(x.model["A B2B"])}</b></td><td class="num">${pc((x.popularity || {})["A B2B"])}</td><td class="num">${pc((x["co-purchase"] || {})["A B2B"])}</td><td class="num">${pc((x["same-line rule"] || {})["A B2B"])}</td></tr>`;
    };
    const c = S["A B2C"] && S["A B2C"]["против: популярность"];
    return `<h2>Доказательства, которые можно проверить</h2>
      <p class="hint" style="margin-top:6px">${esc(p.how || "")} Датасет ${esc(p.dataset || "")} (SHA-256 ${esc(String(p.dataset_sha256 || "").slice(0, 16))}…), файл наших ответов — ${nf.format(p.answers_clients || 0)} троек (SHA-256 ${esc(String(p.answers_sha256 || "").slice(0, 16))}…): организаторы могут прогнать его своим скриптом и получить те же числа.</p>
      <h3>1. Их скрипт, наши ответы: Precision@3</h3>
      <table class="links"><tr><th>Клиенты</th><th class="num">Наша система</th><th class="num">Популярность</th><th class="num">Совместные покупки</th><th class="num">Доля вариантов: наша / совместные</th></tr>
        ${line("A B2B", "Организации, группа A")}${line("B B2B", "Организации с одной покупкой, группа B")}${line("A B2C", "Частные лица, группа A")}${line("A all", "Все клиенты группы A")}</table>
      <h3>2. Перевес не случаен</h3>
      <ul class="hint" style="margin:6px 0 0 18px;padding:0">${sig("A B2B", "Организации, группа A")}${sig("B B2B", "Холодный старт, группа B")}
        ${c ? `<li><b>Частные лица:</b> перевес над популярностью не доказан — разница +${num(c.diff_pp)} п.п., интервал от ${num(c.ci95_pp[0])} до +${num(c.ci95_pp[1])}. Это пишем прямо.</li>` : ""}</ul>
      ${sp["2026-06-01"] || sp["2026-03-02"] ? `<h3>3. Перевес держится на других разрезах и при другом обучении</h3>
      <table class="links"><tr><th>Организации, группа A</th><th class="num">Наша система</th><th class="num">Популярность</th><th class="num">Совместные покупки</th><th class="num">Правило «та же линейка»</th></tr>
        ${splitRow("2026-03-02")}${splitRow("2026-06-01")}</table>
      <p class="hint">Тот же расчёт, но обучение до марта или до июня и проверка на следующих покупках — перевес тот же. Правило «советуй другую редакцию той же линейки» — сильный простой способ, сильнее базовых; система обгоняет и его. Пять обучений с разными случайными семенами дают ${seeds(rb.seeds_A_B2B)} у организаций группы A и ${seeds(rb.seeds_B_B2B)} на холодном старте; без знаний каталога остаётся ${pc((rb.no_catalog || {})["A B2B"])} — всё ещё вдвое выше базовых.</p>` : ""}`;
  }

  /** Почему система обгоняет базовые способы организаторов: откуда берутся попадания. Деление — по правилу
      организаторов (product_group из датасета v5): вариант того, что у клиента уже есть, или новый для него продукт. */
  function whyHtml(w) {
    const off = w && w.official, seg = off && off["A B2B"], segs = w && w.segments && w.segments["A B2B"];
    if (!seg || !seg.ours || !seg.copurchase || !seg.popularity) return "";
    const n = (x) => nf.format(x || 0);
    const hitsW = (k) => { const d1 = k % 10, h1 = k % 100; return n(k) + (d1 === 1 && h1 !== 11 ? " попадание" : d1 >= 2 && d1 <= 4 && (h1 < 12 || h1 > 14) ? " попадания" : " попаданий"); };
    const clients = (w.clients || {})["A B2B"] || 302, d = clients % 10, h = clients % 100;
    const word = d === 1 && h !== 11 ? "клиент" : d >= 2 && d <= 4 && (h < 12 || h > 14) ? "клиента" : "клиентов";
    const row = (title, x, bold) => `<tr><td>${bold ? `<b>${title}</b>` : title}</td><td class="num">${bold ? `<b>${n(x.hits)}</b>` : n(x.hits)}</td>
      <td class="num">${n(x.variant)} (${String(x.variant_share != null ? x.variant_share : 0).replace(".", ",")} %)</td>
      <td class="num">${bold ? `<b>${n(x.new)}</b>` : n(x.new)}</td><td class="num">${x.p3_new != null ? pct(x.p3_new) : "—"}</td></tr>`;
    const o = seg.ours, c = seg.copurchase, pp = seg.popularity, best = Math.max(c.p3_new || 0, pp.p3_new || 0);
    const x = (a1, b1) => (b1 ? (a1 / b1).toFixed(1).replace(".", ",") + "×" : "—");
    const bb = off["B B2B"] || {}, bc = off["A B2C"] || {};
    const noRenew = segs && segs.ours && segs.copurchase ? ` Если убрать из тройки продления купленного — их в кабинете ведёт свой сценарий, — остаётся ${pct(segs.ours.no_renewals)} против ${pct(Math.max(segs.copurchase.no_renewals || 0, (segs.popularity || {}).no_renewals || 0))} у лучшего базового способа.` : "";
    return `<h2 style="margin-top:26px">Почему система обгоняет базовые способы</h2>
      <p class="hint" style="margin-top:6px">Организации, группа A: ${nf.format(clients)} ${word}, протокол организаторов; их baseline.py на этом датасете даёт ровно наши базовые числа. Попадание — товар, который клиент купил с сентября по ноябрь. Деление — по их правилу (колонка product_group датасета v5): <b>вариант</b> — группа угаданного товара уже есть в истории клиента (базовая лицензия и продление, другой срок, количество, месяц подписки), <b>новый продукт</b> — такой группы в истории нет.</p>
      <table class="links"><tr><th></th><th class="num">Попаданий</th><th class="num">Варианты купленного</th><th class="num">Новые продукты</th><th class="num">Precision@3 только по новым</th></tr>
        ${row("Наша система", o, true)}${row("Совместные покупки", c)}${row("Популярность", pp)}</table>
      <ol class="hint" style="margin:10px 0 0 18px;padding:0">
        <li><b>Система знает жизненный цикл лицензии.</b> ${String(o.variant_share).replace(".", ",")} % наших попаданий — варианты купленного: продление вовремя, другой срок, больше мест. У совместных покупок таких ${String(c.variant_share).replace(".", ",")} %, у популярности ни одного: они не видят ни времени, ни того, что у клиента уже есть.${noRenew}</li>
        <li><b>По делению организаторов и «новых продуктов» у нас вдвое больше:</b> ${hitsW(o.new)} против ${n(c.new)} и ${n(pp.new)}, ${pct(o.p3_new)} против ${pct(best)} у лучшего базового способа (${x(o.p3_new, best)}).${(() => {
          // колонка product_group мельче правила организаторов: продление и вариант семейства иногда в другой группе
          const k = (m, key) => ((m || {}).hits || {})[key] || 0, so = (segs || {}).ours, sc = (segs || {}).copurchase, sp = (segs || {}).popularity;
          if (!so) return "";
          return ` Но колонка product_group мельче их же правила: среди наших «новых» есть продления купленного и варианты того же семейства, которые попали в другую группу. По строгой мерке совсем новых продуктов у нас ${n(k(so, "новый продукт"))}, у совместных покупок ${n(k(sc, "новый продукт"))}, у популярности ${n(k(sp, "новый продукт"))}; старших редакций и новых версий — ${n(k(so, "другая редакция или версия"))} против ${n(k(sc, "другая редакция или версия"))} и ${n(k(sp, "другая редакция или версия"))}.`;
        })()}</li>
        ${bb.ours ? `<li><b>Холодный старт.</b> У организаций группы B одна покупка: ${hitsW(bb.ours.hits)} против ${n((bb.copurchase || {}).hits)} и ${n((bb.popularity || {}).hits)}, из них новых продуктов ${n(bb.ours.new)} против ${n((bb.copurchase || {}).new)} и ${n((bb.popularity || {}).new)}.</li>` : ""}
        ${bc.ours ? `<li><b>Где перевеса нет.</b> Совсем новых для клиента продуктов система угадывает столько же, сколько популярность, а у частных лиц новых продуктов ${n(bc.ours.new)} против ${n((bc.popularity || {}).new)}: их покупки похожи друг на друга, и популярное почти так же хорошо. Здесь блок берёт не числом, а качеством: каждый совет проверен на паре с товаром клиента и объяснён.</li>` : ""}
      </ol>`;
  }

  /** Симуляция ИИ-клиентов на всех клиентах группы A: нажал бы, купил бы, сбылось ли — и есть ли у ИИ-клиента сигнал. */
  function clientsHtml(a) {
    if (!a || !a.by_group) return "";
    const g = a.by_group, b = g.B2B, c = g.B2C;
    const plural = (k, one, few, many) => { const d = k % 10, h = k % 100; return `${nf.format(k)} ${d === 1 && h !== 11 ? one : d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many}`; };
    const two = (x, y) => `<b>${x != null ? pct(x, 0) : "—"}</b> / ${y != null ? pct(y, 0) : "—"}`;
    const row = (title, x) => (!x || !x.ours ? "" : `<tr><td>${esc(title)} <small style="color:var(--muted)">(${plural(x.judgments, "оценка", "оценки", "оценок")})</small></td>
      <td class="num">${two(x.ours.click, x.base.click)}</td><td class="num">${two(x.ours.buy, x.base.buy)}</td>
      <td class="num">${two(x.ours.buy_came_true, x.base.buy_came_true)}</td>
      <td class="num"><b>${nf.format(x.prefer.ours)}</b> / ${nf.format(x.prefer.base)} / ${nf.format(x.prefer.tie)}</td></tr>`);
    const sb = b && b.signal, sc = c && c.signal;
    const sig = sb && sb.would_buy && sb.would_not_buy ? `<p class="hint" style="margin-top:8px"><b>У ИИ-клиента есть сигнал, а не только мнение.</b> Товары, о которых организация сказала «купил бы», на деле куплены в ${pct(sb.would_buy.share, 0)} случаев (${nf.format(sb.would_buy.bought)} из ${nf.format(sb.would_buy.items)}), остальные показанные — в ${pct(sb.would_not_buy.share, 1)}.${sb.by_score && sb.by_score["2"] ? ` По оценке пользы: «нужно» (2) куплено в ${pct(sb.by_score["2"].share, 0)} случаев, «может пригодиться» (1) — в ${pct((sb.by_score["1"] || {}).share || 0, 0)}, «не нужно» (0) — в ${pct((sb.by_score["0"] || {}).share || 0, 0)}.` : ""} Поэтому то, что ИИ-клиенты-организации чаще выбирают наш набор и чаще готовы его купить, — это не вкус модели: её «купил бы» совпадает с настоящими покупками.${sc && sc.would_buy ? ` У частных лиц сигнал слабее (${pct(sc.would_buy.share, 0)} против ${pct(sc.would_not_buy.share, 0)}, всего ${nf.format(sc.would_buy.items)} «купил бы»), и перевеса нашего набора у них нет.` : ""}</p>` : "";
    const empty = b && b.base && b.base.empty_sets ? ` У части организаций совместные покупки не дали ни одного совета (${plural(b.base.empty_sets, "пустой набор", "пустых набора", "пустых наборов")} из ${nf.format(b.judgments)}) — так работает и нынешний блок: нет связок — нет показа.` : "";
    return `<h2 style="margin-top:26px">Симуляция: ${nf.format(a.clients || 0)} ИИ-клиентов в кабинете</h2>
      <p class="hint" style="margin-top:6px">${esc(a.how || "")}</p>
      <table class="links"><tr><th>Клиенты</th><th class="num">Нажал бы: наш / базовый</th><th class="num">Купил бы</th><th class="num">Из «купил бы» куплено на деле</th><th class="num">Выбрал: наш / базовый / поровну</th></tr>
        ${row("Организации", b)}${row("Частные лица", c)}</table>
      ${sig}
      <p class="hint">«Нажал бы» и «купил бы» — доля оценок, где ИИ-клиент нажал бы хотя бы на один совет набора или купил бы его; «куплено на деле» — сколько из этих «купил бы» клиент действительно купил с 07.09 по 23.11.2026. Это намерения языковой модели в роли клиента, а не поведение живых клиентов.${empty}${a.agreement != null ? ` Оценщики выбрали один и тот же набор в ${pct(a.agreement, 0)} случаев.` : ""}</p>`;
  }

  /** ИИ-тест: виртуальные клиенты вслепую сравнивают два набора советов. Итог — по типам клиентов: в среднем он прячет главное. */
  function judgeHtml(j) {
    if (!j || !j.cases) return "";
    const g = j.by_group || {}, b = g.B2B, c = g.B2C;
    const tile = (title, x, big, small) => `<div class="tile"><dt>${esc(title)}</dt><dd>${big}</dd><small>${small}</small></div>`;
    const tiles = [];
    if (b && b.judgments) {
      tiles.push(tile("Организации: выбрали наш набор", b.judgments, `${nf.format(b.prefer.ours)} из ${nf.format(b.judgments)}`,
        `совместные покупки — ${nf.format(b.prefer.base)}, равноценны — ${nf.format(b.prefer.tie)}`));
      tiles.push(tile("Организации: ненужных советов", b.judgments, b.useless.ours != null ? pct(b.useless.ours, 0) : "—",
        `у совместных покупок ${b.useless.base != null ? pct(b.useless.base, 0) : "—"}; на деле из нашего набора купили ${nf.format((b.bought || {}).ours || 0)}, из базового — ${nf.format((b.bought || {}).base || 0)}`));
    }
    if (c && c.judgments) {
      tiles.push(tile("Частные лица: выбрали наш набор", c.judgments, `${nf.format(c.prefer.ours)} из ${nf.format(c.judgments)}`,
        `совместные покупки — ${nf.format(c.prefer.base)}; на деле из нашего набора купили ${nf.format((c.bought || {}).ours || 0)}, из базового — ${nf.format((c.bought || {}).base || 0)}`));
    }
    if (j.agreement != null) tiles.push(tile("Оценщики согласны", 0, pct(j.agreement, 0), "два независимых оценщика выбрали один и тот же набор"));
    return `<h2 style="margin-top:26px">ИИ-тест: виртуальные клиенты сравнивают вслепую</h2>
      <p class="hint" style="margin-top:6px">${esc(j.how || "")}</p>
      <dl class="tiles" style="margin:10px 0 8px">${tiles.join("")}</dl>
      ${j.note ? `<p class="hint">${esc(j.note)}</p>` : ""}`;
  }

  /** Тройка протокола и блок, который видит клиент, — рядом: по первой сравниваемся с baseline, вторая — продукт. */
  function shownHtml(s) {
    if (!s) return "";
    const share = (a, b) => (b ? pct(a / b) : "—");
    const places = s.clients * 3;
    return `<h3>Что считает протокол и что видит клиент, организации (группа A)</h3>
      <table class="links"><tr><th></th><th class="num">Мест или карточек</th><th class="num">Сбылось</th><th class="num">Доля</th></tr>
        <tr><td>Тройка протокола — по ней сравнение с baseline (у популярности ${pct(s.protocol.baseline)})</td><td class="num">${nf.format(places)}</td><td class="num">${s.protocol.hits}</td><td class="num">${pct(s.protocol.p3)}</td></tr>
        <tr><td>Та же тройка без продлений уже купленного (у популярности ${pct(s.no_renewals.baseline)})</td><td class="num">${nf.format(places)}</td><td class="num">${s.no_renewals.hits}</td><td class="num">${pct(s.no_renewals.p3)}</td></tr>
        <tr><td><b>Блок кабинета: личные советы</b></td><td class="num"><b>${nf.format(s.block.cards)}</b></td><td class="num"><b>${s.block.hits}</b></td><td class="num"><b>${share(s.block.hits, s.block.cards)}</b></td></tr>
        <tr><td>&nbsp;&nbsp;из них «комплект» — совет на вырост</td><td class="num">${nf.format(s.block.kit_cards)}</td><td class="num">${s.block.kit_hits}</td><td class="num">${share(s.block.kit_hits, s.block.kit_cards)}</td></tr>
        <tr><td>Полоса «Пора продлить» — отдельно от советов</td><td class="num">${nf.format(s.renew.cards)}</td><td class="num">${s.renew.hits}</td><td class="num">${share(s.renew.hits, s.renew.cards)}</td></tr>
        <tr><td>Строка «Часто докупают» — не личный совет</td><td class="num">${nf.format(s.popular.cards)}</td><td class="num">${s.popular.hits}</td><td class="num">${share(s.popular.hits, s.popular.cards)}</td></tr></table>
      <p class="hint" style="margin-top:8px">Продукт — это блок кабинета, а не тройка протокола. Тройка нужна, чтобы сравниться с baseline по правилам организаторов; около половины её попаданий — продления уже купленного, а их заказчик просит в блок рекомендаций не ставить. В блоке продления вынесены в отдельную полосу, каждый личный совет проходит проверку пары, а «комплект» на истории пока не сбывался. Хоть одна карточка блока сбылась у ${s.any_hit} клиентов из ${s.clients}.</p>`;
  }

  /** Проверка пар «купил А — советовать ли Б»: сколько чего размечено и что это меняет в блоке. */
  function verdictsHtml(vs) {
    const KINDS = [["need", "нужно"], ["kit", "комплект"], ["has", "уже есть"], ["same", "то же самое"], ["unrelated", "не связано"]];
    const ve = report && report.verdict_effect;
    const lines = ve ? ve.verdicts : null;
    const sum = (o) => KINDS.reduce((a, [k]) => a + ((o || {})[k] || 0), 0);
    const cell = (o, k) => (o ? nf.format(o[k] || 0) : "—");
    let h = `<h3>Проверка пар «купил А — советовать ли Б»</h3>
      <p class="hint">Статистика продаж не отличает «нужно» от «и так есть»: после антивируса чаще всего покупают Windows, потому что её покупают все. Поэтому каждую пару заранее судит модель по правилам хорошего менеджера. В личные советы идёт «нужно» — с фразой «зачем», а за ним не больше одного «комплекта» с условием «если ещё нет…». На истории комплекты пока не сбывались: это совет на вырост, поэтому он стоит последним. На обращение клиента модель не вызывается: вердикт — строка таблицы.</p>
      <table class="links"><tr><th>Вердикт</th><th class="num">Пары классов каталога</th><th class="num">Товар для класса</th><th class="num">Пары товаров каталога</th><th class="num">Пары линеек из продаж</th></tr>
      ${KINDS.map(([k, t]) => `<tr><td>${t}${k === "need" ? " — идёт в личные советы" : k === "kit" ? " — последней карточкой, не больше одной" : ""}</td><td class="num">${cell(vs.classes, k)}</td><td class="num">${cell(vs.picks, k)}</td><td class="num">${cell(vs.families, k)}</td><td class="num">${cell(lines, k)}</td></tr>`).join("")}
      <tr><td><b>Всего</b></td><td class="num"><b>${nf.format(sum(vs.classes))}</b></td><td class="num"><b>${nf.format(sum(vs.picks))}</b></td><td class="num"><b>${nf.format(sum(vs.families))}</b></td><td class="num"><b>${lines ? nf.format(sum(lines)) : "—"}</b></td></tr></table>
      <p class="hint">«Товар для класса» — проверка для оборудования и малоизвестных программ, у которых своих проверенных пар нет: подходит ли товар любому товару класса или только товарам своего производителя (таких ${nf.format(vs.pick_vendor_only || 0)}). Если совместимость зависит от модели, товар отвергается.</p>`;
    const old = vs.old_links;
    if (old && old.total) {
      h += `<p class="hint">Из ${nf.format(old.total)} связей классов, которые раньше предложила облегчённая модель, проверку прошли ${nf.format((old.need || 0) + (old.kit || 0))}. Остальные сняты: «уже есть» — ${old.has || 0}, «то же самое» — ${old.same || 0}, «не связано» — ${old.unrelated || 0}. Прежняя проверка продажами такие связи подтверждала: самые продаваемые товары встречаются рядом с чем угодно, поэтому мы от неё отказались.</p>`;
    }
    if (ve && ve.rows && ve.rows.length) {
      h += `<h3>Что проверка пар меняет в блоке кабинета</h3>
        <p class="hint">Организации из проверки на истории (${ve.clients} клиентов): советы даны по покупкам до 31 августа, «сбылось» — клиент купил этот товар осенью. Строки «Пора продлить» и «Часто докупают» в таблицу не входят.</p>
        <table class="links"><tr><th>Блок новых советов</th><th class="num">Карточек</th><th class="num">Сбылось</th><th class="num">Доля</th><th class="num">Самый частый товар видят</th><th class="num">Клиентов без новых советов</th></tr>
        ${ve.rows.map((r) => `<tr><td>${esc(r.title)}${r.stand ? " — так на стенде" : ""}</td><td class="num">${nf.format(r.cards)}</td><td class="num">${r.hits}</td><td class="num">${pct(r.share)}</td><td class="num">${pct(r.top_share)} клиентов</td><td class="num">${r.without}</td></tr>`).join("")}</table>
        <p class="hint">Без проверки один и тот же товар («${esc(ve.top_before)}») стоял в личных советах почти у всех; с проверкой он остаётся там, где у него есть основание, а остальным показывается отдельной строкой «Часто докупают». ${esc(ve.cost)}</p>`;
    }
    if (vs.check && vs.check.length) {
      h += `<h3>Разметка через API</h3>
        <p class="hint">Первую разметку сделал ИИ-ассистент во время разработки. Новые пары в эксплуатации размечает модель по API тем же запросом (<code>python -m recsys.llm.pair_verdicts label</code>). Какой модели можно верить без менеджера, показывает сверка с первой разметкой: порог — ${pct(vs.trust)} одинаковых решений «советовать или нет».</p>
        <table class="links"><tr><th>Модель</th><th class="num">Пар в сверке</th><th class="num">То же решение</th><th class="num">Верных среди показанного</th><th>Вывод</th></tr>
        ${vs.check.map((c) => `<tr><td>${esc(c.model)}</td><td class="num">${c.pairs}</td><td class="num">${pct(c.same_decision)}</td><td class="num">${c.precision == null ? "—" : pct(c.precision)}</td><td>${c.trusted ? "вердикты идут клиенту сразу" : "вердикты ждут менеджера"}</td></tr>`).join("")}</table>`;
      const bl = vs.blind || [];
      if (bl.length) {
        const [all, unseen] = bl;
        h += `<p class="hint">Для сравнения — вторая разметка вслепую, без API: другая сессия ассистента разметила те же ${all.pairs} пар, не видя вердиктов первой. То же решение — ${pct(all.same_decision)}, верных среди показанного — ${pct(all.precision)}`
          + (unseen ? `; без пар, которые она раньше видела на проверке (осталось ${unseen.pairs}), — ${pct(unseen.same_decision)} и ${pct(unseen.precision)}` : "")
          + `. Оба разметчика — одной семьи моделей, так что это нижняя оценка расхождения, а не независимая проверка. Порог ${pct(vs.trust, 0)} — уровень согласия двух таких разметчиков.</p>`;
      }
    }
    return h;
  }

  /** Охват: у скольких товаров в продаже есть личный совет и на чём он держится (python -m recsys.eval.coverage). */
  function coverageHtml(cov) {
    const row = (title, s, bold) => {
      const b = (x) => (bold ? `<b>${x}</b>` : x);
      return `<tr><td>${b(title)}</td><td class="num">${b(nf.format(s.on_sale))}</td><td class="num">${b(nf.format(s.with_advice) + " · " + pct(s.with_advice / s.on_sale, 0))}</td>
        <td class="num">${nf.format(s.own_need + s.own_kit)}</td><td class="num">${nf.format(s.class_need + s.class_kit)}</td><td class="num">${b(nf.format(s.solo))}</td></tr>`;
    };
    const t = cov.total;
    const top = (cov.solo_classes || []).slice(0, 6).map((x) => `«${esc(x.class)}» — ${nf.format(x.solo)}`).join(", ");
    return `<h3>У скольких товаров есть личный совет</h3>
      <p class="hint">Клиент с одной покупкой: товар куплен в марте, сегодня ноябрь. Продление своей же покупки и строка «Часто докупают» личным советом не считаются.</p>
      <table class="links"><tr><th>Товары в продаже</th><th class="num">Всего</th><th class="num">С личным советом</th><th class="num">по паре с самим товаром</th><th class="num">по проверке товара для класса</th><th class="num">Без совета</th></tr>
      ${row("Программы", cov.kinds.software)}${row("Оборудование", cov.kinds.hardware)}${row("Всего", t, true)}</table>
      <p class="hint">Часть связей прочитана прямо в названиях товаров, без разметки: ${nf.format(data.edges.filter((e) => e[3] === "серия в названии").length)} — курсы одной серии и расходные материалы одной серии разных цветов (показываются как «комплект»), и принадлежности, в названии которых названа модель устройства того же производителя. Ещё ${nf.format(data.edges.filter((e) => String(e[3]).startsWith("характеристики каталога")).length)} связей — совместимость по характеристикам из фида магазина: сокет и чипсет платы и процессора, тип памяти, отвод тепла кулера, корпус без блока питания, отсеки хранилища.</p>
      <p class="hint">Без совета остаются товары, которым ни один проверенный кандидат не подошёл: к оборудованию без знания модели часто добавить нечего, а несовместимое мы не советуем. Больше всего таких в классах: ${top}. Ещё у ${nf.format(t.manager)} товаров подсказка есть, но проверена только связь классов — она ждёт менеджера.</p>`;
  }

  function renderNumbers() {
    const sale = data.nodes.filter((n) => n.sale === 1);
    const linkedIds = new Set(data.edges.filter((e) => e[2] !== "same_line").map((e) => e[0]));
    const bySource = {};
    for (const e of data.edges) { if (e[2] === "same_line") continue; const k = E.LABEL[e[3]] || e[3]; bySource[k] = (bySource[k] || 0) + 1; }
    const byType = {};
    for (const e of data.edges) { if (e[2] === "same_line") continue; byType[e[2]] = (byType[e[2]] || 0) + 1; }
    const built = data.meta.built ? new Date(data.meta.built).toLocaleString("ru-RU") : "";
    let html = report && report.regimes ? officialHtml()
      : '<p class="note">Проверка на истории продаж появится здесь после расчёта: <code>python -m recsys.eval.value</code> и <code>python -m recsys.eval.site_report</code>.</p>';
    html += `<h2 style="margin-top:28px">Граф связей из открытых источников</h2>
      <dl class="tiles">
        <div class="tile"><dt>Товаров и семейств</dt><dd>${nf.format(data.nodes.length)}</dd><small>из них в продаже ${nf.format(sale.length)}</small></div>
        <div class="tile"><dt>Связей-советов</dt><dd>${nf.format(adviceEdges)}</dd><small>собраны ${esc(built)}</small></div>
        <div class="tile"><dt>Товаров со своей связью</dt><dd>${nf.format(linkedIds.size)}</dd><small>остальным совет даёт линейка и класс</small></div>
        <div class="tile"><dt>Модель на обращение клиента</dt><dd>0 вызовов</dd><small>${(() => {
          // деньги — из расчёта стоимости (submission/cost.md через отчёт стенда), чтобы на стенде стояло то же число
          const eco = (report && report.economics) || {}, r2 = (x) => x.toFixed(2).replace(".", ",");
          return eco.per_request
            ? `с агентом каталога и разметкой пар для новых товаров — около ${r2(eco.per_request)} ₽ на обращение${eco.with_server ? `, вместе с сервером около ${r2(eco.with_server)} ₽` : ""}${eco.model ? ` (${esc(eco.model)})` : ""}`
            : "модель нужна только агенту каталога и разметке пар для новых товаров";
        })()}</small></div>
      </dl>
      <h3>Связи по источникам</h3>
      <table class="links"><tr><th>Источник</th><th class="num">Связей</th></tr>${Object.entries(bySource).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${nf.format(v)}</td></tr>`).join("")}</table>
      <h3>Связи по видам</h3>
      <table class="links"><tr><th>Вид связи</th><th class="num">Связей</th></tr>${Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td><span class="typ" style="--slot:${slotVar(slotOf(k))}"><i class="dot"></i>${esc(E.TYPE_TITLE[k] || k)}</span></td><td class="num">${nf.format(v)}</td></tr>`).join("")}</table>`;
    const rp = data.meta.replace;
    if (rp && rp.gone) {
      const found = rp.direct + rp.sibling + rp.klass;
      html += `<h3>Замена зарубежных программ, снятых с продажи</h3>
        <p class="hint">В кабинете магазина кнопка «Подобрать замену» отправляет заявку менеджеру с ответом через 1–2 рабочих дня. Здесь замена готова для ${nf.format(found)} программ из ${nf.format(rp.gone)} (${pct(found / rp.gone, 0)}). Клиент сразу видит только прямые аналоги; замены по классу показываются после того, как их примет менеджер.</p>
        <table class="links"><tr><th>Откуда замена</th><th class="num">Программ</th></tr>
          <tr><td>Прямой российский аналог из таблицы замещения</td><td class="num">${nf.format(rp.direct)}</td></tr>
          <tr><td>Российский продукт того же класса, названный в таблице аналогом другого продукта того же вендора</td><td class="num">${nf.format(rp.sibling)}</td></tr>
          <tr><td>Российский продукт того же класса из реестра — самый слабый вид совета</td><td class="num">${nf.format(rp.klass)}</td></tr></table>`;
    }
    const vs = data.meta.verdicts;
    if (vs) html += verdictsHtml(vs);
    if (report && report.coverage) html += coverageHtml(report.coverage);
    html += `<h3>Очередь менеджера</h3>
      <p class="hint">Сейчас блок построен примерно на 6 000 ручных связок «товар — товар». Здесь клиент сразу видит пары товаров, проверенные заранее: «нужно» — личным советом, «комплект» — последней карточкой; любую из них менеджер отклоняет одним нажатием на вкладке «Связи». Решения менеджера ждёт то, где проверена не сама пара с товаром клиента: связи между классами и замены по классу — сейчас ${nf.format(waitingRows().length)} строк.</p>`;
    $("#numbers").innerHTML = html;
    $$("#numbers [data-tip]").forEach((el) => {
      el.addEventListener("mousemove", (ev) => showTip(ev, `<span>${esc(el.dataset.tip)}</span>`));
      el.addEventListener("mouseleave", hideTip);
    });
  }

  // =========================== вкладка «Агент» ===========================
  const TOOL_RU = { find_classes: "поиск класса", search_catalog: "поиск по каталогу", get_product: "карточка товара", class_companions: "спутники класса",
                    judged: "что уже рассужено", line: "другие редакции линейки", addons: "дополнения «для товара»", spec: "характеристики из фида", "итог": "итог" };
  /** Аргументы шага по-человечески: вместо ключа товара или класса — его название. */
  const argText = (a) => Object.entries(a || {}).filter(([, v]) => v).map(([k, v]) => (k === "id" ? nodeNameAll(v) : k === "class_id" ? clsName(v) : v)).join(", ");
  const LINK_RU = { addon_for: "дополнение к", requires: "нужен для работы", complements: "стоит предложить вместе", upgrade_of: "редакция в линейке с", new_version_of: "версия в линейке с", analog_of: "российский аналог для" };
  let agentIdx = 0;
  function foundText(st) {
    let r = st.result;
    if (st.tool === "итог") return "паспорт товара и связи";
    if (r && Array.isArray(r.pairs)) r = r.pairs;              // «что уже рассужено»: пары с вердиктом
    if (Array.isArray(r)) {
      const named = r.filter((x) => x && x.name);
      if (!named.length) return (r[0] && (r[0].note || r[0].error)) || "ничего не найдено";
      const names = named.slice(0, 4).map((x) => (x.name.length > 46 ? x.name.slice(0, 44) + "…" : x.name) + (x.verdict ? ` (${x.verdict})` : ""));
      return `найдено ${named.length}: ${names.join("; ")}`;
    }
    if (r && r.error) return r.error;
    if (r && r.spec) return String(r.spec).slice(0, 160);
    if (r && r.name) return `${r.name}${r.class ? " · " + r.class : ""}${r.sale ? " · " + r.sale : ""}`;
    return "";
  }

  // ---- агент закрывает «дыры»: товары, владельцу которых блоку нечего посоветовать по делу
  const OUTCOME = { closed: "закрыта", kit: "найден только комплект", queue: "ждёт менеджера", known: "нового не найдено",
                    no_grounded_link: "обоснованной связи нет" };
  // пары, которые граф знал и без агента: повтор такой пары дыру не закрывает
  // «та же линейка» знанием о паре не считается: каталог знает, что товары — соседи, но не знает, какая редакция старше
  const knownPairs = new Set(data.edges.filter((e) => e[2] !== "same_line" && !String(e[3]).startsWith("агент каталога")).map((e) => `${e[0]}|${e[1]}`));
  const holeNew = (r, l) => !knownPairs.has(`${r.family}|${l.dst}`);
  /** Итог по дыре в единицах замера показа: дыра закрыта, только если агент дал новый совет сильнее «комплекта».
      Комплект — совет «если ещё нет», он идёт последней карточкой и обоснованным советом в замере не считается. */
  function holeResult(r) {
    const links = r.links || [], fresh = links.filter((l) => l.status === "graph" && holeNew(r, l));
    if (fresh.some((l) => l.kind !== "kit")) return "closed";
    if (fresh.length) return "kit";
    if (links.some((l) => l.status === "queue")) return "queue";
    return links.length ? "known" : "no_grounded_link";
  }
  const holeRuns = holesOf(holesData);
  const holeBy = new Map(holeRuns.map((h) => [h.family, h]));
  const holeQueue = (priv && priv.holes) || [];             // очередь с числом клиентов — только в закрытом стенде
  // счётчик «товаров без совета: было → стало» и очередь товаров каталога, которые агент ещё не разбирал
  let holeExtra = holesData && !Array.isArray(holesData) ? holesData : {};
  let holeCur = null, holesLive = false;
  const GROUP = { clients: "Очередь по клиентам кабинета", done: "Разобрано агентом", catalog: "Товары каталога без совета — на очереди" };
  function holeList() {
    const seen = new Set(), out = [];
    for (const q of holeQueue) {
      if (q.family) seen.add(q.family);
      out.push({ group: "clients", family: q.family || "", name: q.name || q.family || "", cls: q.cls || "", clients: q.clients || 0, b2b: q.b2b || 0, b2c: q.b2c || 0,
                 renew: !!q.renew, sale: q.sale !== false, rebuy: q.rebuy, other: q.other, run: (q.family && holeBy.get(q.family)) || null });
    }
    // разобранное вне очереди клиентов: сначала то, где агент что-то нашёл, длинный хвост «связи нет» — в конце
    const rank = { closed: 0, kit: 1, queue: 2, known: 3, no_grounded_link: 4 };
    const done = holeRuns.filter((h) => !seen.has(h.family)).map((h) => ({ h, r: rank[holeResult(h)] }));
    done.sort((a, b) => a.r - b.r || String(a.h.name || "").localeCompare(String(b.h.name || ""), "ru"));
    for (const { h } of done) {
      if (seen.has(h.family)) continue;
      seen.add(h.family);
      out.push({ group: "done", family: h.family, name: h.name || nodeNameAll(h.family), cls: h.class || "", clients: null, renew: false, sale: true, run: h });
    }
    // на очереди — только когда агента можно запустить: без сервера и ключа список без кнопки ни к чему
    if (holesLive) for (const q of holeExtra.queue || []) {
      if (seen.has(q.family) || !nodeIds.has(q.family)) continue;
      out.push({ group: "catalog", family: q.family, name: q.name || nodeNameAll(q.family), cls: q.cls || "", clients: null, renew: false, sale: true, run: null });
    }
    return out;
  }
  /** Агент берёт дыру, если это товар открытого каталога в продаже и ему нужен совет, а не продление. */
  const holeOpen = (x) => !!x.family && !x.renew && x.sale;
  const holeState = (x) => (x.run ? OUTCOME[holeResult(x.run)] : x.renew ? "нужно продление, а не совет"
    : !x.family ? "нет в открытом каталоге" : !x.sale ? "нет в продаже" : "не разобрана");
  // вкладка открывается на прогоне агента: главный сценарий — новый товар; дыру выбирают в списке ниже
  function renderHoles() {
    const list = holeList();
    $("#holes").hidden = !list.length;
    if (!list.length) return;
    const done = list.filter((x) => x.run);
    const n = (o) => done.filter((x) => holeResult(x.run) === o).length;
    const tally = `закрыто ${n("closed")}, найден только комплект ${n("kit")}, ждёт менеджера ${n("queue")}, нового не найдено ${n("known") + n("no_grounded_link")}`;
    const sum = (xs) => xs.reduce((t, x) => t + (x.clients || 0), 0);
    const gain = holeExtra.gain || {};
    const parts = [gain.before
      ? `Дыра — товар в продаже, владельцу которого блоку нечего посоветовать лично. Таких товаров было <b>${nf.format(gain.before)}</b> из ${nf.format(gain.on_sale)}, стало <b>${nf.format(gain.now)}</b>: агент каталога нашёл совет к ${nf.format(gain.gained)}.`
      : "Дыра — товар в продаже, владельцу которого блоку нечего посоветовать лично."];
    parts.push(`Агент разобрал: ${done.length} — ${tally}.`);
    if (holeQueue.length) {
      const open = list.filter((x) => x.group === "clients" && holeOpen(x)), renew = list.filter((x) => x.renew);
      const rest = holeQueue.length - open.length - renew.length;
      const closed = done.filter((x) => holeResult(x.run) === "closed");
      parts.push(`Клиенты кабинета без совета по делу стоят за ${nf.format(holeQueue.length)} товарами. Агент берёт товары открытого каталога в продаже: таких ${nf.format(open.length)}, клиентов за ними ${nf.format(sum(open))}.`);
      if (renew.length) parts.push(`Нужно продление, а не совет: ${nf.format(renew.length)} (клиентов ${nf.format(sum(renew))}).`);
      if (rest > 0) parts.push(`Без карточки в открытом каталоге или не в продаже: ${nf.format(rest)} — наружу их не отправляем.`);
      if (sum(closed)) parts.push(`Клиентов за закрытыми дырами: ${nf.format(sum(closed))}.`);
    } else {
      const hs = report && report.holes, wo = hs && hs.without_advice;
      if (hs && hs.holes && wo) {
        const rn = hs.renew || {}, renewClients = (rn.clients_b2b || 0) + (rn.clients_b2c || 0);
        parts.push(`По данным кабинета на ${esc(String(hs.today || "").split("-").reverse().join("."))} без совета по делу ${nf.format((wo.B2B || 0) + (wo.B2C || 0))} клиентов, за ними ${nf.format(hs.holes)} товара`
          + (rn.holes ? `; ${nf.format(renewClients)} из этих клиентов стоят за ${nf.format(rn.holes)} товарами, которым нужно продление, а не совет.` : "."));
      }
    }
    const bf = report && report.block_fill, was = bf && bf.before_holes;
    if (was && (was.b2b.own !== bf.b2b.own || was.b2c.own !== bf.b2c.own)) {
      parts.push(`Обоснованный совет в блоке: у организаций было ${pct(was.b2b.own)}, стало ${pct(bf.b2b.own)}; у частных лиц было ${pct(was.b2c.own)}, стало ${pct(bf.b2c.own)}.`);
    }
    $("#holes-sum").innerHTML = parts.join(" ");
    const groups = new Set(list.map((x) => x.group));
    let shown = "";
    $("#holes-list").innerHTML = list.slice(0, 400).map((x, i) => {
      // подзаголовок группы — когда групп больше одной: в открытом стенде список один, и заголовок ему не нужен
      const title = GROUP[x.group] + (x.group === "catalog" && holeExtra.queue_total ? ` (первые ${(holeExtra.queue || []).length} из ${nf.format(holeExtra.queue_total)})` : "");
      const head = groups.size > 1 && x.group !== shown ? `<li class="sep" style="border:0;background:none;cursor:default;padding:8px 2px 0;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em">${esc(title)}</li>` : "";
      shown = x.group;
      return `${head}<li data-h="${i}" class="${holeCur === i ? "is-cur" : ""}">${esc(x.name)}
      <small>${x.group === "clients" ? `клиентов: ${nf.format(x.clients)} · ` : ""}<span class="mark mark--${x.run && ["closed", "kit"].includes(holeResult(x.run)) ? "yes" : "no"}">${esc(holeState(x))}</span></small></li>`;
    }).join("");
    $$("#holes-list li[data-h]").forEach((li) => li.addEventListener("click", () => { holeCur = +li.dataset.h; renderHoles(); showHole(list[holeCur]); }));
  }
  /** Что блок показывает владельцу товара: расчёт на странице по тому же графу, что и вкладка «Клиент». */
  function holeCards(x) {
    const node = graph.nodes.get(x.family);
    if (!node) return "";
    // тип клиента: в очереди кабинета — тот, каких клиентов за дырой больше; иначе как в отчёте об охвате каталога
    const type = x.clients != null ? (x.b2b >= x.b2c ? "b2b" : "b2c") : (/для дома/i.test(className(node)) ? "b2c" : "b2b");
    const res = new E.Recommender(graph, recOpts({ fill: false, unverified: false }))
      .recommend({ type, today: state.today, purchases: [{ family: x.family, date: isoShift(state.today, -2), term: null }] }, 5);
    const recs = res.recs.filter((r) => r.slot !== "renew").slice(0, 3);
    const who = type === "b2b" ? "организации" : "частному лицу";
    if (!recs.length) return `<p class="hint">Блок ничего не советует ${who} с одним этим товаром: дыра остаётся.</p>`;
    const notes = [`Клиент — ${type === "b2b" ? "организация" : "частное лицо"} с одним этим товаром. Совет считается на странице, модель не вызывается.`];
    // совет по делу у владельца одного товара есть, а дыра всё равно числится: она про клиентов, у которых советуемое уже куплено
    const fresh = ((x.run && x.run.links) || []).filter((l) => l.status === "graph" && holeNew(x.run, l));
    // совет по делу был и без агента, а дыра всё равно числится: она про клиентов, у которых советуемое уже куплено
    if (x.clients && recs.some((r) => r.slot !== "kit" && !fresh.some((l) => l.dst === r.family))) notes.push("Владельцу одного этого товара совет по делу есть и без агента. Дыра — про клиентов кабинета, у которых советуемое уже куплено или не подходит им по типу: им нужна ещё одна связь.");
    const lost = fresh.filter((l) => !recs.some((r) => r.family === l.dst));
    if (lost.length) notes.push(`Совет агента «${lost.map((l) => l.dst_name || nodeNameAll(l.dst)).join("», «")}» в эти три карточки не попал: комплект в блоке один, и место занято парой, проверенной заранее.`);
    return `<p class="hint small">${notes.map(esc).join(" ")}</p>
      <div class="cards">${recs.map((r) => {
        const m = graph.nodes.get(r.family) || {};
        return `<article class="card" style="--slot:${slotVar(r.slot)}"><div class="card__slot"><i class="dot"></i>${esc(r.slotTitle)}</div>
          <div class="card__name">${esc(r.name)}</div><div class="card__vendor">${esc([r.vendor, className(m)].filter(Boolean).join(" · "))}</div>
          <p class="card__why">${esc(r.why)}</p><div class="card__foot"><span class="chip">${esc(r.label)}</span></div></article>`;
      }).join("")}</div>`;
  }
  function showHole(x) {
    const view = $("#agent-view"), r = x.run;
    view.className = "panel agent";
    const links = ((r && r.links) || []).map((l) => `<li><b>${esc(l.kind ? ({ kit: "комплект", need: "нужно", upgrade: "старшая редакция", new_version: "новая версия" }[l.kind] || l.kind) : (LINK_RU[l.type] || l.type || ""))}</b> «${esc(l.dst_name || nodeNameAll(l.dst))}» — ${esc(l.evidence || l.reason || "")}
      ${l.quote ? `<span class="chip">цитата: «${esc(String(l.quote).slice(0, 110))}»</span>` : '<span class="chip">цитаты нет</span>'}
      <br><small style="color:var(--muted)">${l.status !== "graph" ? "в очереди менеджера: " + esc(l.quote_note || "без цитаты клиент совет не увидит") : holeNew(r, l) ? "в графе: клиент видит совет сразу, менеджер может отклонить" : "эта пара уже была в разметке: агент её повторил, нового совета нет"}${l.critic ? " · критик: " + esc(l.critic) : ""}</small></li>`).join("");
    let ask = -1;
    const steps = ((r && r.steps) || []).map((st) => {
      const k = st.ask || 0;
      const head = k === ask ? "" : `<div class="ask">${k === 0 ? "без модели: что о товаре уже известно" : "запрос к модели № " + k + (st.thought ? " — " + esc(st.thought) : "")}</div>`;
      ask = k;
      if (st.tool === "итог") return `<li>${head}<code>итог</code><div class="found">${(r.links || []).length + (r.dropped || []).length ? "агент назвал советы — дальше их проверяют правила в коде и критик" : "агент решил, что обоснованного совета нет"}</div></li>`;
      const args = argText(st.args);
      return `<li>${head}<code>${esc(TOOL_RU[st.tool] || st.tool || "")}</code>${args ? " «" + esc(args) + "»" : ""}<div class="found">${esc(foundText(st))}</div></li>`;
    }).join("");
    const cost = r ? [r.model, r.model_requests ? `запросов к модели: ${r.model_requests}` : "", r.tokens ? `токенов ${nf.format((r.tokens.in || 0) + (r.tokens.out || 0))}` : "", r.seconds != null ? `${Math.round(r.seconds)} с` : ""].filter(Boolean).join(" · ") : "";
    const cut = ((r && r.dropped) || []).map((l) => `<li>«${esc(l.dst_name || nodeNameAll(l.dst || ""))}» — ${esc(l.why || l.critic || l.reason || "")}</li>`).join("");
    const why = x.renew ? `Агент такую дыру не берёт: ту же линейку снова купили ${pct(x.rebuy || 0, 0)} её владельцев, что-то другое — ${pct(x.other || 0, 0)}. Им нужно продление, а для него в кабинете есть свой сценарий.`
      : !x.family ? "У товара нет карточки в открытом каталоге: агент по API его не берёт — данные задачи наружу не уходят."
      : !x.sale ? "Товара нет в продаже: агент его не берёт." : "";
    const result = r ? holeResult(r) : "";
    const canRun = holesLive && holeOpen(x) && result !== "closed";
    const verdict = { closed: "Агент нашёл новый совет и подтвердил его цитатой: владелец товара теперь получает совет по делу.",
                      kit: "Комплект — совет «если ещё нет», он идёт последней карточкой. По строгому счёту дыра остаётся: совета «нужно» к этому товару агент не нашёл.",
                      queue: "Строгую проверку связь не прошла: совет ждёт решения менеджера, клиент его не видит. Причина указана у каждой связи.",
                      known: "Агент пришёл к паре, которая уже была в разметке: нового совета не прибавилось." }[result] || "";
    view.innerHTML = `
      <div class="panel__head"><h2>${esc(x.name)}</h2><span class="cost">${esc(cost)}</span></div>
      <div class="agent__card"><b>Дыра.</b> ${x.cls ? esc(x.cls) + ". " : ""}${x.clients != null ? `Клиентов кабинета, которым из-за неё нечего посоветовать по делу: ${nf.format(x.clients)} (организаций ${nf.format(x.b2b)}, частных лиц ${nf.format(x.b2c)}). ` : ""}${r ? `Итог: <b>${esc(OUTCOME[result])}</b>. ${esc(verdict)}` : why ? "" : "Агент её ещё не разбирал."}</div>
      ${why ? `<p class="hint" style="margin-top:8px">${esc(why)}</p>` : ""}
      ${r && r.explanation ? `<p class="hint" style="margin-top:8px">Почему связи нет: ${esc(r.explanation)}</p>` : ""}
      ${canRun ? `<button class="btn" id="hole-run" style="margin-top:12px">${r ? "Разобрать заново" : "Закрыть дыру агентом"}</button><p class="hint small" id="hole-status"></p><ul class="steps" id="hole-live"></ul>` : ""}
      ${links ? `<h3>Что нашёл агент</h3><ul class="kept">${links}</ul>` : ""}
      ${cut ? `<h3>Что снято правилами и критиком</h3><ul class="cut">${cut}</ul>` : ""}
      ${x.family && nodeIds.has(x.family) && !x.renew ? `<h3>Что блок показывает владельцу одного этого товара</h3>${holeCards(x)}` : ""}
      ${steps ? `<h3>Шаги агента</h3><ul class="steps">${steps}</ul>` : ""}`;
    const btn = $("#hole-run");
    if (btn) btn.addEventListener("click", () => runHole(x));
  }
  async function runHole(x) {
    const btn = $("#hole-run"), status = $("#hole-status"), live = $("#hole-live");
    btn.disabled = true;
    const t0 = performance.now();
    // шаги показываем по мере работы: сервер отдаёт то, что агент уже сделал
    const stepLine = (st) => (st.ask ? `<li><div class="ask">${esc(st.ask)}</div></li>`
      : `<li><code>${esc(TOOL_RU[st.tool] || st.tool || "")}</code>${argText(st.args) ? " «" + esc(argText(st.args)) + "»" : ""}<div class="found">${esc(st.found || "")}</div></li>`);
    const timer = setInterval(async () => {
      status.textContent = `Агент работает: ${Math.round((performance.now() - t0) / 1000)} с. Одна дыра — от половины минуты до трёх минут.`;
      const p = await getJson("api/hole/progress");
      if (p && p.family === x.family && !p.done) live.innerHTML = p.steps.map(stepLine).join("");
    }, 1000);
    const ctl = new AbortController();
    const stop = setTimeout(() => ctl.abort(), 300000);
    try {
      const resp = await fetch("api/hole", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ family_id: x.family }), signal: ctl.signal });
      const res = await resp.json();
      if (!resp.ok) throw new Error(res.error || "ошибка агента");
      res.family = res.family || x.family;
      const at = holeRuns.findIndex((h) => h.family === res.family);
      if (at >= 0) holeRuns.splice(at, 1);
      holeRuns.unshift(res);
      holeBy.set(res.family, res);
      for (const l of res.links || []) {
        if (!nodeIds.has(l.dst) || vetoPairs.has(`${res.family}|${l.dst}`)) continue;
        const edge = holeEdge(res.family, l);
        if (l.status !== "graph") { addQueueRow({ src: edge[0], dst: edge[1], type: edge[2], confidence: edge[4], evidence: edge[5], quote: l.quote || "", note: l.quote_note || "" }); continue; }
        if (data.edges.concat(liveEdges).some((e) => e[0] === edge[0] && e[1] === edge[1])) continue;
        liveEdges.push(edge);                               // клиент видит совет сразу, менеджер может отклонить
        if (l.quote) liveQuotes[ekey(edge)] = l.quote;
        queue.push(edgeRow(edge));
      }
      rebuild(); render(true);
      const fresh = await getJson("data/agent_holes.json");  // сервер уже пересчитал счётчик «было → стало» и очередь
      if (fresh && !Array.isArray(fresh)) holeExtra = fresh;
      const list = holeList();
      holeCur = list.findIndex((h) => h.family === res.family);
      renderHoles(); showHole(list[holeCur]);
    } catch (err) {
      status.textContent = "Не получилось: " + (err.name === "AbortError" ? "агент не уложился в пять минут" : err.message);
      btn.disabled = false;
    } finally {
      clearInterval(timer); clearTimeout(stop);
    }
  }
  function renderAgent() {
    renderHoles();
    const test = restoreHtml(report && report.restore, holesLive);   // тест «спрятали — вернул» — над разбором дыры
    $("#restore").hidden = !test;
    $("#restore").innerHTML = test;
    const view = $("#agent-view");
    const hole = holeCur != null ? holeList()[holeCur] : null;   // выбрана дыра: справа она, а не прогон экзамена
    if (!agent || !agent.runs.length) {
      if (hole) return showHole(hole);
      view.innerHTML = '<p class="note">Журналов агента пока нет: <code>python -m recsys.agent.exam</code>, затем <code>python -m recsys.agent.site</code>.</p>';
      return;
    }
    const e = agent.exam || {}, nh = agent.exam_nohints || {}, pr = agent.per_run;
    const rows = [];
    const of = (x, n) => `${Math.round(x * n)} из ${n}`;
    if (e.runs) {
      if (e.model) rows.push(["Модель экзамена", e.model]);   // прогоны ниже сделаны этой моделью; стоимость товара — по замеру ниже
      rows.push(["Прогонов в экзамене", e.runs]);
      rows.push(["Класс совпал с рубрикой магазина", of(e.class_exact, e.runs)]);
      if (e.class_code_only != null) rows.push(["…один код, без модели", of(e.class_code_only, e.runs)]);
      if (nh.runs) rows.push(["…модель без подсказок кода", of(nh.class_exact, nh.runs)]);
      const k = e.by_kind || {};
      if (k["дополнение"]) rows.push(["Дополнения: агент нашёл базовый продукт", `${k["дополнение"].agent} из ${k["дополнение"].runs}`]);
      if (k["линейка"]) rows.push(["Линейки: соседа нашёл агент", `${k["линейка"].agent} из ${k["линейка"].runs}`]);
      if (e.runs_with_model_tools != null) rows.push(["Модель сама вызывала инструменты", `в ${of(e.runs_with_model_tools, e.runs)} прогонов`]);
      rows.push(["Запросов к модели на товар", String(e.requests_per_run.toFixed(1)).replace(".", ",")]);
    }
    if (pr) rows.push([`Стоимость товара (${pr.price})`, String(pr.rub).replace(".", ",") + " ₽"]);
    $("#agent-exam").innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
    $("#agent-runs").innerHTML = agent.runs.map((r, i) => {
      const t = r.truth || {};
      const okClass = t.class && r.passport.class_name === t.class;
      return `<li data-i="${i}" class="${i === agentIdx && !hole ? "is-cur" : ""}">${esc(r.card.name)}
        <small>${t.kind ? esc(t.kind) + " · " : ""}${r.truth ? `<span class="mark mark--${okClass ? "yes" : "no"}">${okClass ? "класс совпал" : "класс другой"}</span>` : '<span class="mark mark--yes">живой запуск</span>'} · связей ${r.links.length} · ${r.requests || "?"} запр., ${r.seconds >= 0.5 ? Math.round(r.seconds) + " с" : "ответы из кеша"}</small></li>`;
    }).join("");
    $$("#agent-runs li").forEach((li) => li.addEventListener("click", () => { agentIdx = +li.dataset.i; holeCur = null; renderAgent(); }));
    if (hole) return showHole(hole);
    const r = agent.runs[agentIdx], t = r.truth || {}, p = r.passport;
    let ask = -1;
    const steps = r.steps.map((st) => {
      const n = st.ask || 0;
      const head = n === ask ? "" : (n === 0
        ? '<div class="ask">без модели — поиск по названию товара</div>'
        : `<div class="ask">запрос к модели № ${n}${st.thought ? " — " + esc(st.thought) : ""}</div>`);
      ask = n;
      const args = Object.values(st.args || {}).filter(Boolean).join(", ");
      return `<li>${head}<code>${esc(TOOL_RU[st.tool] || st.tool)}</code>${args ? " «" + esc(args) + "»" : ""}<div class="found">${esc(foundText(st))}</div></li>`;
    }).join("");
    const kept = r.links.map((l) => `<li><b>${esc(LINK_RU[l.type] || l.type)}</b> «${esc(l.dst_name)}» — ${esc(l.reason)}${l.quote ? ` <span class="chip">из карточки: «${esc(l.quote.slice(0, 80))}»</span>` : ""}<br><small style="color:var(--muted)">критик: ${esc(l.critic || "")}</small></li>`).join("");
    const cut = r.dropped.map((l) => `<li>«${esc(l.dst_name || l.dst || "")}» — ${esc(l.why)}</li>`).join("");
    view.className = "panel agent";
    view.innerHTML = `
      <div class="panel__head"><h2>${esc(r.card.name)}</h2><span class="cost">запросов к модели: <b>${r.requests || "?"}</b> · ${r.seconds >= 0.5 ? Math.round(r.seconds) + " с" : "ответы из кеша"}${pr ? ` · оценка ${String(pr.rub).replace(".", ",")} ₽ один раз` : ""}</span></div>
      <div class="agent__card"><b>Что получил агент.</b> Название: ${esc(r.card.name)}. Вендор: ${esc(r.card.vendor || "—")}. Описание: ${esc((r.card.description || "—").slice(0, 380))}${(r.card.description || "").length > 380 ? "…" : ""}</div>
      <h3>Шаги агента</h3>
      <ul class="steps">${steps}</ul>
      <h3>Что получилось</h3>
      <div class="pair">
        <div><h4>Агент</h4>
          <p><b>Класс:</b> ${esc(p.class_name || "в каталоге нет подходящего класса — товар уходит на проверку менеджеру")} ${p.class_name ? `<span class="chip">${esc(p.class_source === "соседи по каталогу"
            ? `по соседям в каталоге, уверенность ${String((p.neighbors || {}).confidence || "").replace(".", ",")}` : "определил агент")}</span>` : ""}</p>
          <p><b>Что делает:</b> ${esc(p.does || "—")}</p>
          <p><b>Что нужно для работы:</b> ${esc((p.needs || []).join("; ") || "из карточки не видно")}</p>
          <p><b>Для кого:</b> ${esc({ b2b: "организации", b2c: "частные лица", any: "все" }[p.audience] || p.audience)}</p></div>
        ${r.truth ? `<div><h4>Было спрятано</h4>
          <p><b>Класс в магазине:</b> ${esc(t.class || "—")}</p>
          <p><b>Связанные товары в каталоге:</b> ${esc((t.related || []).join("; ") || "не отмечены")}</p></div>`
          : `<div><h4>Проверка</h4>
          <p>Товар заведён вручную, готового ответа у стенда нет. Каждую связь проверил второй запрос к модели; связи с товарами, которых нет в каталоге, отбрасывает код.</p>
          <p>Последнее слово остаётся за менеджером: связи попадают в очередь на проверку.</p></div>`}
      </div>
      <h3>Связи, прошедшие проверку (${r.links.length})</h3>
      ${kept ? `<ul class="kept">${kept}</ul>` : '<p class="hint">Нет.</p>'}
      <h3>Отброшено (${r.dropped.length})</h3>
      ${cut ? `<ul class="cut">${cut}</ul>` : '<p class="hint">Ничего.</p>'}
      <h3>Кому теперь посоветуем этот товар</h3>
      ${whoGets(r)}
      ${(() => { const n = agentRowsOf(r).length; return n ? `<p class="hint" style="margin-top:6px">Результат агента не остаётся на этой вкладке: ${n} ${n % 10 === 1 && n % 100 !== 11 ? "связь стоит" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "связи стоят" : "связей стоят"} в очереди менеджера. После «принять» владелец исходного товара увидит этот товар в личных советах. <button class="linkbtn" data-agent-queue>Открыть очередь</button></p>` : ""; })()}
      <h3>Что теперь увидит покупатель этого товара</h3>
      <div class="cards">${afterPlacement(r)}</div>
      <p class="hint" style="margin-top:8px">Так блок будет выглядеть после проверки менеджером: совет посчитан на этой странице по графу, в который товар только что поставлен. Связи по классу, которые менеджер ещё не принял, клиент не видит. Модель при расчёте не вызывается.</p>`;
  }
  /** Входящие связи нового товара: чьим владельцам он теперь будет предложен. */
  function whoGets(r) {
    const hidden = (r.truth && r.truth.id) || "";
    const kind = { complements: "как дополнение", upgrade: "как старшую редакцию", new_version: "как новую версию", analog_ru: "как российский аналог", requires: "как необходимое для работы" };
    const rows = (r.edges || []).filter((e) => e.dst === "new" && kind[e.type] && e.src !== hidden)
      .map((e) => `<li>владельцам «${esc(nodeNameAll(e.src))}» — ${kind[e.type]}</li>`);
    return rows.length ? `<ul class="kept">${rows.join("")}</ul>` : '<p class="hint">Таких связей агент не нашёл: товар будет предлагаться по связям своего класса.</p>';
  }
  /** Товар, поставленный агентом, как временный узел графа: какие советы получит его покупатель. */
  function afterPlacement(r) {
    const id = "agent/new";
    const p = r.passport;
    const node = { id, name: r.card.name, vendor: r.card.vendor || "", cls: p.class_id || "", sale: 1,
                   b2b: p.audience === "b2b" ? 1 : 0, kind: "software" };
    const extra = (r.edges || []).map((e) => [e.src === "new" ? id : e.src, e.dst === "new" ? id : e.dst, e.type, "агент", 0.75, e.why || ""])
      .filter((e) => e[2] !== "same_line");
    const line = (r.edges || []).filter((e) => e.type === "same_line").map((e) => [e.src === "new" ? id : e.src, e.dst === "new" ? id : e.dst, "same_line", "агент", 0.7, ""]);
    const hidden = (r.truth && r.truth.id) || "";
    const g2 = new E.Graph(Object.assign({}, data, {
      nodes: data.nodes.filter((n) => n.id !== hidden).concat([node]),
      edges: data.edges.filter((e) => e[0] !== hidden && e[1] !== hidden && votes[ekey(e)] !== "no").concat(extra, line),
    }));
    const res = new E.Recommender(g2, { fill: false, unverified: true, rejected }).recommend(
      { type: p.audience === "b2c" ? "b2c" : "b2b", today: state.today, purchases: [{ family: id, date: isoShift(state.today, -2), term: null }] }, 3);
    if (!res.recs.length) return '<div class="empty">Для этого класса связей пока нет — совет появится, когда класс будет размечен.</div>';
    return res.recs.map((x, i) => {
      const n = g2.nodes.get(x.family) || {};
      return `<article class="card" style="--slot:${slotVar(x.slot)}"><div class="card__slot"><i class="dot"></i>${esc(x.slotTitle)}</div>
        <div class="card__name">${esc(x.name)}</div><div class="card__vendor">${esc([x.vendor, className(n)].filter(Boolean).join(" · "))}</div>
        <p class="card__why">${esc(x.why)}</p><div class="card__foot"><span class="chip">${esc(x.label)}</span></div></article>`;
    }).join("");
  }
  // живой запуск агента: доступен, когда стенд открыт через локальный сервер (python -m recsys.server)
  (async function setupLive() {
    const ping = STATIC ? null : await getJson("api/ping");
    if (!ping) return;                                    // стенд открыт без сервера: живого запуска нет
    holesLive = !!(ping.agent && ping.holes);
    if (holesLive) renderAgent();                          // появились кнопка запуска и очередь товаров каталога
    if (!ping.agent) {
      // сервер есть, но модель недоступна: говорим прямо, а не показываем форму, которая упадёт
      $("#agent-live").hidden = false;
      $("#agent-live").innerHTML = `<h3>Завести новый товар</h3><p class="hint small">Живой запуск сейчас недоступен — ${esc(ping.why || "модель не отвечает")}. Ниже записанные прогоны: каждый шаг агента в них настоящий.</p>`;
      return;
    }
    $("#agent-live").hidden = false;
    $("#a-run").addEventListener("click", async () => {
      const body = { name: $("#a-name").value.trim(), vendor: $("#a-vendor").value.trim(), description: $("#a-text").value.trim() };
      if (body.name.length < 3) { $("#a-status").textContent = "Нужно название товара."; return; }
      $("#a-run").disabled = true;
      const t0 = performance.now();
      const timer = setInterval(() => { $("#a-status").textContent = `Агент работает: ${Math.round((performance.now() - t0) / 1000)} с…`; }, 500);
      try {
        // агент делает до десятка запросов к модели; дольше полутора минут не ждём — на демо это пауза, а не ответ
        const ctl = new AbortController();
        const stop = setTimeout(() => ctl.abort(), 90000);
        let resp;
        try {
          resp = await fetch("api/place", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctl.signal });
        } catch (e) {
          throw new Error(e.name === "AbortError" ? "модель не ответила за 90 секунд. Ниже — записанные прогоны агента" : e.message);
        } finally {
          clearTimeout(stop);
        }
        const res = await resp.json();
        if (!resp.ok) throw new Error(res.error || "ошибка агента");
        const run = { card: res.card, truth: null, passport: res.passport, steps: res.steps, links: res.links, edges: res.edges || [],
                      dropped: res.dropped, seconds: res.seconds, requests: res.model_requests };
        if (!agent) return;
        agent.runs.unshift(run);
        addAgentRows(run);
        agentIdx = 0;
        holeCur = null;                                    // справа — только что поставленный товар, а не выбранная дыра
        $("#a-status").textContent = `Готово: ${Math.round(res.seconds)} с, запросов к модели: ${res.model_requests}.`;
        renderAgent();
        $("#agent-view").scrollIntoView({ behavior: "smooth", block: "start" });   // результат ниже формы: показываем его сразу
      } catch (err) {
        $("#a-status").textContent = "Не получилось: " + err.message;
      } finally {
        clearInterval(timer);
        $("#a-run").disabled = false;
      }
    });
  })();

  // ---- запуск
  syncControls();
  if (!priv) $("#d-on").parentElement.hidden = true;     // открытый стенд: клиентов из данных задачи в нём нет
  if (priv) setupData();
  else if (examples.length) { exSel.value = "0"; loadExample(0); }
  else render();
})();
