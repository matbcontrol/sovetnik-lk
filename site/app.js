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
  const priv = await getJson("data/private.json");
  const agent = await getJson("data/agent_runs.json");

  // ---- решения менеджера по связям: хранятся в браузере, отклонённая связь сразу выпадает из советов
  const VOTES_KEY = "sovetnik.votes.v1";
  let votes = {};
  try { votes = JSON.parse(localStorage.getItem(VOTES_KEY) || "{}"); } catch (e) { votes = {}; }
  const ekey = (e) => `${e[0]}|${e[1]}|${e[2]}`;
  let graph;
  function rebuild() {
    const edges = data.edges.filter((e) => votes[ekey(e)] !== "no");
    graph = new E.Graph(Object.assign({}, data, { edges }));
  }
  rebuild();

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
  const state = { type: "b2b", today: new Date().toISOString().slice(0, 10), purchases: [], minConf: 0 };
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
      ul.innerHTML = '<li class="owned__empty">Покупок нет — клиент без истории. Блок всё равно не останется пустым.</li>';
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
      <div class="card__foot"><span class="chip">${esc(r.label)}</span>${price}${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Открыть в магазине</a>` : ""}</div>
    </article>`;
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
    const rec = new E.Recommender(graph, { fill: true, minConfidence: state.minConf });
    last = rec.recommend({ type: state.type, today: state.today, purchases: state.purchases }, 3);
    const ms = performance.now() - t0;
    if (!keepOwned) renderOwned();
    $("#block-title").textContent = "Рекомендуем вам";
    $("#cost").innerHTML = `Обращений к модели: <b>0</b> · стоимость запроса: <b>0 ₽</b> · посчитано за ${ms < 1 ? "меньше 1" : Math.round(ms)} мс`;
    $("#cards").innerHTML = last.recs.length ? last.recs.map(cardHtml).join("") : '<div class="empty">Для этого набора покупок советов нет.</div>';
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
  const hitsOf = (c) => c.recs.full.filter((x) => x.hit).length;
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
      const fact = c.mode === "test" ? ` · сбылось ${hitsOf(c)} из 3` : "";
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
      <div class="card__foot"><span class="chip">${esc(x.label)}</span>${price}${it.url ? `<a href="${esc(it.url)}" target="_blank" rel="noopener">Открыть в магазине</a>` : ""}</div>
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
      : "Совет выбран ранжированием по истории и каталогу · обращений к языковой модели: <b>0</b> · стоимость запроса: <b>0 ₽</b>";
    $("#cards").innerHTML = recs.map((x, i) => dataCard(x, i, c)).join("");
    const fact = $("#fact");
    if (c.mode === "test") {
      const named = new Set(recs.map((x) => x.item));
      fact.hidden = false;
      fact.innerHTML = `<b>Что клиент купил на самом деле с ${ruDate(priv.test_from)}:</b> ` + c.target.map((t) =>
        `<span class="bought${named.has(t) ? " bought--yes" : ""}">${named.has(t) ? "✓ " : ""}${esc(item(t).short)}${item(t).desc ? ` (${esc(item(t).desc)})` : ""}</span>`).join(" ");
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
    $("#bans").innerHTML = "<summary>Товары из истории клиента не предлагаются — так требует и протокол проверки.</summary>";
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
  function showProduct(n) {
    const out = graph.out.get(n.id) || [], inc = graph.inc.get(n.id) || [];
    const line = Array.from(graph.lineOf(n.id));
    const [st, cls] = saleState(n);
    const rec = new E.Recommender(graph, { fill: false });
    const res = rec.recommend({ type: n.b2b === 1 ? "b2b" : "b2c", today: state.today, purchases: [{ family: n.id, date: isoShift(state.today, -11), term: null }] }, 3);
    const head = '<tr><th>Вид связи</th><th>Товар</th><th>Источник</th><th class="num">Уверенность</th><th>Основание</th></tr>';
    $("#prod-view").innerHTML = `
      <div class="prod__head"><strong>${esc(n.name)}</strong><span class="state state--${cls}">${st}</span>
        ${n.b2b === 1 ? '<span class="state">только юридическим лицам</span>' : ""}
        ${n.price ? `<span class="state">от ${nf.format(Math.round(n.price))} ₽</span>` : ""}
        ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">страница в магазине</a>` : ""}</div>
      <p class="hint" style="margin-top:6px">${esc([n.vendor, className(n)].filter(Boolean).join(" · "))}</p>
      <h3>Что посоветуем клиенту, у которого есть только этот товар</h3>
      <div class="cards">${res.recs.length ? res.recs.map(cardHtml).join("") : '<div class="empty">У товара пока нет связей — это пробел, который закрывает разметка каталога.</div>'}</div>
      <h3>Связи от этого товара (${out.length})</h3>
      ${out.length ? `<table>${head}${linkRows(out, "dst")}</table>` : '<p class="hint">Нет.</p>'}
      <h3>Связи к этому товару (${inc.length})</h3>
      ${inc.length ? `<table>${head}${linkRows(inc, "src")}</table>` : '<p class="hint">Нет.</p>'}
      <h3>Та же линейка — не предлагаем вместе с этим товаром (${line.length})</h3>
      <p class="hint">${line.length ? line.slice(0, 30).map((f) => `<a href="#" data-open="${esc(f)}">${esc(nodeName(f))}</a>`).join(" · ") : "Нет."}</p>`;
    $$("[data-open]", $("#prod-view")).forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); const m = graph.nodes.get(a.dataset.open); if (m) showProduct(m); }));
  }

  // =========================== вкладка «Связи на проверку» ===========================
  const PAGE = 50;
  let page = 0;
  const types = Array.from(new Set(data.edges.map((e) => e[2])));
  const sources = Array.from(new Set(data.edges.map((e) => e[3])));
  $("#f-type").innerHTML = '<option value="advice">все советы (без «той же линейки»)</option><option value="">все</option>' + types.map((t) => `<option value="${esc(t)}">${esc(E.TYPE_TITLE[t] || t)}</option>`).join("");
  $("#f-source").innerHTML = '<option value="">любой</option>' + sources.map((s) => `<option value="${esc(s)}">${esc(E.LABEL[s] || s)}</option>`).join("");
  ["f-type", "f-source", "f-state", "f-q"].forEach((id) => $("#" + id).addEventListener("input", () => { page = 0; renderLinks(); }));
  const allNodes = new Map(data.nodes.map((n) => [n.id, n]));
  const nodeNameAll = (id) => (allNodes.get(id) || {}).name || id;
  function filteredLinks() {
    const t = $("#f-type").value, s = $("#f-source").value, st = $("#f-state").value, q = $("#f-q").value.trim().toLowerCase();
    return data.edges.filter((e) => {
      if (t === "advice" ? e[2] === "same_line" : t && e[2] !== t) return false;
      if (s && e[3] !== s) return false;
      const v = votes[ekey(e)] || "new";
      if (st && v !== st) return false;
      if (q && !(`${nodeNameAll(e[0])} ${nodeNameAll(e[1])}`.toLowerCase().includes(q))) return false;
      return true;
    });
  }
  function renderLinks() {
    const rows = filteredLinks();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    page = Math.min(page, pages - 1);
    const part = rows.slice(page * PAGE, page * PAGE + PAGE);
    $("#links").innerHTML = '<tr><th>Откуда</th><th>Вид связи</th><th>Куда</th><th>Источник</th><th class="num">Уверенность</th><th>Основание</th><th>Решение</th></tr>' +
      part.map((e) => {
        const v = votes[ekey(e)] || "";
        return `<tr class="${v === "no" ? "is-no" : ""}"><td>${esc(nodeNameAll(e[0]))}</td>
          <td><span class="typ" style="--slot:${slotVar(slotOf(e[2]))}"><i class="dot"></i>${esc(E.TYPE_TITLE[e[2]] || e[2])}</span></td>
          <td>${esc(nodeNameAll(e[1]))}</td><td>${esc(E.LABEL[e[3]] || e[3])}</td><td class="num">${String(e[4]).replace(".", ",")}</td>
          <td class="ev">${esc((e[5] || "").replace(/; https?:\S+$/, ""))}</td>
          <td><span class="vote"><button data-k="${esc(ekey(e))}" data-v="ok" class="${v === "ok" ? "is-ok" : ""}" title="Принять связь">принять</button><button data-k="${esc(ekey(e))}" data-v="no" class="${v === "no" ? "is-no" : ""}" title="Отклонить связь">отклонить</button></span></td></tr>`;
      }).join("");
    const done = Object.values(votes);
    $("#pager").innerHTML = `<span>Связей по отбору: ${nf.format(rows.length)} · принято ${done.filter((x) => x === "ok").length} · отклонено ${done.filter((x) => x === "no").length}</span>
      <span><button class="btn" id="pg-prev"${page ? "" : " disabled"}>Назад</button> стр. ${page + 1} из ${pages} <button class="btn" id="pg-next"${page < pages - 1 ? "" : " disabled"}>Вперёд</button></span>`;
    $("#pg-prev").onclick = () => { page--; renderLinks(); };
    $("#pg-next").onclick = () => { page++; renderLinks(); };
    $$("#links .vote button").forEach((b) => b.addEventListener("click", () => {
      votes[b.dataset.k] = votes[b.dataset.k] === b.dataset.v ? undefined : b.dataset.v;
      if (!votes[b.dataset.k]) delete votes[b.dataset.k];
      localStorage.setItem(VOTES_KEY, JSON.stringify(votes));
      rebuild(); renderLinks(); render();
    }));
  }
  $("#export-links").addEventListener("click", () => {
    const q = (s) => `"${String(s == null ? "" : s).replace(/"/g, '""')}"`;
    const lines = [["откуда", "адрес", "вид связи", "куда", "адрес", "источник", "уверенность", "основание", "решение"].join(";")];
    for (const e of data.edges) {
      if (e[2] === "same_line" || votes[ekey(e)] === "no") continue;
      const a = allNodes.get(e[0]) || {}, b = allNodes.get(e[1]) || {};
      lines.push([q(a.name || e[0]), q(a.url), q(E.TYPE_TITLE[e[2]] || e[2]), q(b.name || e[1]), q(b.url), q(E.LABEL[e[3]] || e[3]), String(e[4]).replace(".", ","), q(e[5]), q(votes[ekey(e)] === "ok" ? "принято" : "не рассмотрено")].join(";"));
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
      { name: "Наша модель без знаний каталога", value: reg.no_catalog.p3["A B2B"], ci: reg.no_catalog.ci["A B2B"], ours: true },
      { name: "Наша модель", value: full["A B2B"], ci: reg.full.ci["A B2B"], ours: true },
    ], 0.2);
    const chartNew = bars([
      { name: "Без знаний каталога", value: reg.new_item_no_catalog.p3["A B2B"], ci: reg.new_item_no_catalog.ci["A B2B"], note: "остаётся только общая популярность" },
      { name: "Со знаниями каталога", value: reg.new_item.p3["A B2B"], ci: reg.new_item.ci["A B2B"], ours: true },
      { name: "Для сравнения: с полной историей", value: full["A B2B"], ci: reg.full.ci["A B2B"], ours: true },
    ], 0.2);
    const rub = (x) => nf.format(Math.round(x / 1000)) + " тыс. ₽";
    return `
      <h2>Проверка на истории по протоколу организаторов</h2>
      <p class="hint" style="margin-top:6px">Обезличенный датасет организаторов${r.dataset ? `, версия ${esc(String(r.dataset).replace(/^dataset_v?|\.xlsx$/g, ""))}` : ""}. Советы даны по покупкам до ${esc(r.protocol.train_until)}, засчитываются новые для клиента товары, купленные с ${esc(r.protocol.test_from)}. Модель и её режимы обучались только на периоде до даты разреза.</p>
      <dl class="tiles">
        <div class="tile"><dt>Угадано из трёх советов, организации</dt><dd>${pct(full["A B2B"])}</dd><small>базовые способы организаторов: ${pct(org["популярность"]["A B2B"])} и ${pct(org["совместные покупки"]["A B2B"])}</small></div>
        <div class="tile"><dt>То же, холодный старт</dt><dd>${pct(full["B B2B"])}</dd><small>базовые способы: ${pct(org["популярность"]["B B2B"])} и ${pct(org["совместные покупки"]["B B2B"])}</small></div>
        <div class="tile"><dt>Товар без единой продажи</dt><dd>${pct(reg.new_item.p3["A B2B"])}</dd><small>без знаний каталога было бы ${pct(reg.new_item_no_catalog.p3["A B2B"])}</small></div>
        <div class="tile"><dt>Клиентов, которым есть что показать</dt><dd>${fillAll == null ? "—" : pct(fillAll, 0)}</dd><small>сейчас блок виден в ${pct(r.current_fill)} визитов</small></div>
      </dl>
      <div class="charts">
        <figure><figcaption>Precision@3, организации (группа A)</figcaption>${chartA}</figure>
        <figure><figcaption>Если у купленного товара нет истории продаж — «соло» или новинка</figcaption>${chartNew}</figure>
      </div>
      <p class="hint">Серые полосы — базовые способы, синие — наша модель; отрезок — 95-процентный интервал. Шкала у обоих графиков одна, от 0 до 20 %.</p>
      <h3>Все группы</h3>
      <div class="tablewrap"><table class="links"><tr><th>Способ</th>${head.map((h) => `<th class="num">${h}</th>`).join("")}</tr>
        ${row("Популярность (организаторы)", org["популярность"])}${row("Совместные покупки (организаторы)", org["совместные покупки"])}
        ${Object.keys(reg).map((k) => row(reg[k].title[0].toUpperCase() + reg[k].title.slice(1), reg[k].p3, k === "full")).join("")}
      </table></div>
      <p class="hint" style="margin-top:8px">Частных лиц в проверке всего несколько десятков — их цифры шумные, главных выводов по ним не делаем.</p>
      ${breakdownHtml(r)}
      <h3>Охват каталога</h3>
      <table class="links"><tr><th>Откуда берётся совет</th><th class="num">Товаров в наличии с советом</th><th class="num">Доля от ${nf.format(cat.available)}</th></tr>
        <tr><td>История продаж: после товара хотя бы три клиента купили один и тот же другой товар</td><td class="num">${nf.format(cat.history_3)}</td><td class="num">${pct(cat.history_3 / cat.available)}</td></tr>
        <tr><td>Каталог: есть другой товар той же линейки</td><td class="num">${nf.format(cat.line_available)}</td><td class="num">${pct(cat.line_available / cat.available, 0)}</td></tr>
        <tr><td>Каталог: линейка или класс</td><td class="num">${nf.format(cat.line_or_class)}</td><td class="num">${pct(cat.line_or_class / cat.available, 0)}</td></tr></table>
      <h3>Деньги за угаданными покупками, на 100 организаций</h3>
      <table class="links"><tr><th>Способ</th><th class="num">Угадано покупок</th><th class="num">Сумма по ценам витрины</th></tr>
        ${Object.entries(m).filter(([k]) => k !== "all_targets").map(([k, v]) => `<tr><td>${esc(k[0].toUpperCase() + k.slice(1))}</td><td class="num">${v.hits}</td><td class="num">${rub(v.rub_per_100_clients)}</td></tr>`).join("")}
        <tr><td>Все новые покупки этих клиентов</td><td class="num">${m.all_targets.targets}</td><td class="num">${rub(m.all_targets.rub_per_100_clients)}</td></tr></table>
      <p class="hint" style="margin-top:8px">Цены нынешние, количество лицензий в данных не указано, у части товаров цены нет. Это покупки, которые клиент сделал и без нашего блока: число показывает уместность совета, а не прирост продаж.</p>`;
  }
  /** Из чего состоят попадания: вариант уже купленного продукта, другая версия или новый для клиента продукт. */
  function breakdownHtml(r) {
    const b = r.breakdown || {};
    const order = ["популярность", "совместные покупки", "наша модель"].filter((k) => b[k] && b[k]["A B2B"]);
    if (!order.length) return "";
    const KINDS = [["вариант", "Другой вариант уже купленного"], ["версия", "Другая версия или редакция"], ["новый продукт", "Новый для клиента продукт"]];
    const title = { "популярность": "Популярность (организаторы)", "совместные покупки": "Совместные покупки (организаторы)", "наша модель": "<b>Наша модель</b>" };
    const sum = (o) => KINDS.reduce((a, [k]) => a + o[k], 0);
    const targets = b[order[0]]["A B2B"].targets;
    const row = (k) => {
      const x = b[k]["A B2B"];
      return `<tr><td>${title[k]}</td>${KINDS.map(([kk]) => `<td class="num">${x.hits[kk]} <small>· ${pct(x.parts[kk])}</small></td>`).join("")}
        <td class="num">${sum(x.hits)} <small>· ${pct(x.p3)}</small></td></tr>`;
    };
    const ours = b["наша модель"] && b["наша модель"]["A B2B"], pop = b["популярность"] && b["популярность"]["A B2B"];
    const note = ours && pop
      ? `На новых для клиента продуктах наша модель угадала ${ours.hits["новый продукт"]} покупок из ${targets["новый продукт"]}, популярность — ${pop.hits["новый продукт"]}.` : "";
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
        <div class="tile"><dt>Стоимость запроса клиента</dt><dd>0 ₽</dd><small>языковая модель на запрос не вызывается</small></div>
      </dl>
      <h3>Связи по источникам</h3>
      <table class="links"><tr><th>Источник</th><th class="num">Связей</th></tr>${Object.entries(bySource).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${nf.format(v)}</td></tr>`).join("")}</table>
      <h3>Связи по видам</h3>
      <table class="links"><tr><th>Вид связи</th><th class="num">Связей</th></tr>${Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td><span class="typ" style="--slot:${slotVar(slotOf(k))}"><i class="dot"></i>${esc(E.TYPE_TITLE[k] || k)}</span></td><td class="num">${nf.format(v)}</td></tr>`).join("")}</table>`;
    const rp = data.meta.replace;
    if (rp && rp.gone) {
      const found = rp.direct + rp.sibling + rp.klass;
      html += `<h3>Замена зарубежных программ, снятых с продажи</h3>
        <p class="hint">В кабинете магазина кнопка «Подобрать замену» отправляет заявку менеджеру с ответом через 1–2 рабочих дня. Здесь замена находится сразу — для ${nf.format(found)} программ из ${nf.format(rp.gone)} (${pct(found / rp.gone, 0)}).</p>
        <table class="links"><tr><th>Откуда замена</th><th class="num">Программ</th></tr>
          <tr><td>Прямой российский аналог из таблицы замещения</td><td class="num">${nf.format(rp.direct)}</td></tr>
          <tr><td>Российский продукт того же класса, названный в таблице аналогом другого продукта того же вендора</td><td class="num">${nf.format(rp.sibling)}</td></tr>
          <tr><td>Российский продукт того же класса из реестра — самый слабый вид совета</td><td class="num">${nf.format(rp.klass)}</td></tr></table>`;
    }
    $("#numbers").innerHTML = html;
    $$("#numbers [data-tip]").forEach((el) => {
      el.addEventListener("mousemove", (ev) => showTip(ev, `<span>${esc(el.dataset.tip)}</span>`));
      el.addEventListener("mouseleave", hideTip);
    });
  }

  // =========================== вкладка «Агент» ===========================
  const TOOL_RU = { find_classes: "поиск класса", search_catalog: "поиск по каталогу", get_product: "карточка товара", class_companions: "спутники класса", "итог": "итог" };
  const LINK_RU = { addon_for: "дополнение к", requires: "нужен для работы", complements: "стоит предложить вместе", upgrade_of: "редакция в линейке с", new_version_of: "версия в линейке с", analog_of: "российский аналог для" };
  let agentIdx = 0;
  function foundText(st) {
    const r = st.result;
    if (st.tool === "итог") return "паспорт товара и связи";
    if (Array.isArray(r)) {
      if (!r.length) return "ничего не найдено";
      const names = r.map((x) => x.name).filter(Boolean).slice(0, 4).map((x) => (x.length > 46 ? x.slice(0, 44) + "…" : x));
      return `найдено ${r.length}: ${names.join("; ")}`;
    }
    if (r && r.error) return r.error;
    if (r && r.name) return `${r.name}${r.class ? " · " + r.class : ""}${r.sale ? " · " + r.sale : ""}`;
    return "";
  }
  function renderAgent() {
    const view = $("#agent-view");
    if (!agent || !agent.runs.length) {
      view.innerHTML = '<p class="note">Журналов агента пока нет: <code>python -m recsys.agent.exam</code>, затем <code>python -m recsys.agent.site</code>.</p>';
      return;
    }
    const e = agent.exam || {}, pr = agent.per_run;
    const rows = [];
    if (e.runs) {
      rows.push(["Прогонов в экзамене", e.runs]);
      rows.push(["Класс совпал с магазином", pct(e.class_exact, 0)]);
      const k = e.by_kind || {};
      if (k["дополнение"]) rows.push(["Дополнения: агент нашёл базовый продукт", `${k["дополнение"].agent} из ${k["дополнение"].runs}`]);
      if (k["линейка"]) rows.push(["Линейки: сосед найден агентом или правилом", `${k["линейка"].agent_or_rule} из ${k["линейка"].runs}`]);
      else if (e.related_found != null) rows.push([`Связанный товар найден (из ${e.related_runs})`, pct(e.related_found, 0)]);
      rows.push(["Запросов к модели на товар", String(e.requests_per_run.toFixed(1)).replace(".", ",")]);
      rows.push(["Секунд на товар, медиана", Math.round(e.seconds_median || e.seconds_per_run)]);
    }
    if (pr) rows.push([`Стоимость прогона (${pr.price})`, String(pr.rub).replace(".", ",") + " ₽"]);
    $("#agent-exam").innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
    $("#agent-runs").innerHTML = agent.runs.map((r, i) => {
      const t = r.truth || {};
      const okClass = t.class && r.passport.class_name === t.class;
      return `<li data-i="${i}" class="${i === agentIdx ? "is-cur" : ""}">${esc(r.card.name)}
        <small>${t.kind ? esc(t.kind) + " · " : ""}${r.truth ? `<span class="mark mark--${okClass ? "yes" : "no"}">${okClass ? "класс совпал" : "класс другой"}</span>` : '<span class="mark mark--yes">живой запуск</span>'} · связей ${r.links.length} · ${r.requests || "?"} запр., ${Math.round(r.seconds)} с</small></li>`;
    }).join("");
    $$("#agent-runs li").forEach((li) => li.addEventListener("click", () => { agentIdx = +li.dataset.i; renderAgent(); }));
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
      <div class="panel__head"><h2>${esc(r.card.name)}</h2><span class="cost">запросов к модели: <b>${r.requests || "?"}</b> · ${Math.round(r.seconds)} с${pr ? ` · около ${String(pr.rub).replace(".", ",")} ₽ один раз` : ""}</span></div>
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
      <h3>Что теперь увидит покупатель этого товара</h3>
      <div class="cards">${afterPlacement(r)}</div>
      <p class="hint" style="margin-top:8px">Совет посчитан на этой странице по графу, в который товар только что поставлен: по классу и по связям, найденным агентом. Модель при этом не вызывается.</p>`;
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
    const res = new E.Recommender(g2, { fill: false }).recommend(
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
    const ping = await getJson("api/ping");
    if (!ping || !ping.agent) return;
    $("#agent-live").hidden = false;
    $("#a-run").addEventListener("click", async () => {
      const body = { name: $("#a-name").value.trim(), vendor: $("#a-vendor").value.trim(), description: $("#a-text").value.trim() };
      if (body.name.length < 3) { $("#a-status").textContent = "Нужно название товара."; return; }
      $("#a-run").disabled = true;
      const t0 = performance.now();
      const timer = setInterval(() => { $("#a-status").textContent = `Агент работает: ${Math.round((performance.now() - t0) / 1000)} с…`; }, 500);
      try {
        const resp = await fetch("api/place", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const res = await resp.json();
        if (!resp.ok) throw new Error(res.error || "ошибка агента");
        const run = { card: res.card, truth: null, passport: res.passport, steps: res.steps, links: res.links, edges: res.edges || [],
                      dropped: res.dropped, seconds: res.seconds, requests: res.model_requests };
        if (!agent) return;
        agent.runs.unshift(run);
        agentIdx = 0;
        $("#a-status").textContent = `Готово: ${Math.round(res.seconds)} с, запросов к модели: ${res.model_requests}.`;
        renderAgent();
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
