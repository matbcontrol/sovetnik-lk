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
  // решения менеджера по связям классов и заменам по классу: ключи «cl|класс|класс» и «cr|класс|товар»
  let approved = new Set(), rejected = new Set();
  function rebuild() {
    const edges = data.edges.filter((e) => votes[ekey(e)] !== "no");
    graph = new E.Graph(Object.assign({}, data, { edges }));
    const byClass = (v) => new Set(Object.keys(votes).filter((k) => votes[k] === v && /^(c[lr]|pk)\|/.test(k)));
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
  /** Тот же ответ в виде, который блок кабинета получает сейчас, плюс объяснение и вид совета. */
  function renderContract(rows) {
    const el = $("#contract");
    el.hidden = false;
    el.innerHTML = `<summary>Ответ в формате кабинета (JSON)</summary>
      <p class="hint small" style="margin-top:6px">Поля link, name, vendorName и price блок кабинета получает и сейчас; reason и slot — новые: объяснение совета и его вид.</p>
      <pre>${esc(JSON.stringify({ status: rows.length ? "shown" : "hidden", data: rows }, null, 1))}</pre>`;
  }
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
    const rec = new E.Recommender(graph, recOpts({ fill: false, minConfidence: state.minConf }));
    const res = rec.recommend({ type: state.type, today: state.today, purchases: state.purchases }, 5);
    // продление выносим в отдельную строку, три места в блоке — для новых советов
    const renew = res.recs.filter((r) => r.slot === "renew").slice(0, 2);
    last = { recs: res.recs.filter((r) => r.slot !== "renew").slice(0, 3), bans: res.bans };
    renderRenew(renew.map((r) => ({ name: r.name, text: r.why + (r.also ? " Заодно: " + r.also : ""), url: r.url })));
    // «часто докупают» — отдельной строкой: первый популярный товар, который не повторяет советы и не лежит у клиента
    const filled = new E.Recommender(graph, recOpts({ fill: true, minConfidence: state.minConf }))
      .recommend({ type: state.type, today: state.today, purchases: state.purchases }, 6);
    const pops = filled.recs.filter((r) => r.slot === "popular").slice(0, state.purchases.length ? 1 : 3);
    renderPopular(pops.map((r) => ({ name: r.name, text: "Не личный совет. " + r.why, url: r.url })));
    const waiting = new Set(res.bans.filter((b) => b.reason === E.PENDING).map((b) => b.key)).size;
    const pend = $("#pending");
    pend.hidden = !waiting;
    const w10 = waiting % 10, w100 = waiting % 100;
    const waits = w10 === 1 && w100 !== 11 ? "совет ждёт" : w10 >= 2 && w10 <= 4 && (w100 < 12 || w100 > 14) ? "совета ждут" : "советов ждут";
    pend.innerHTML = `Ещё ${waiting} ${waits} решения менеджера — подбор по связи классов, «комплекты» и замены по классу: у них проверена не сама пара с товаром клиента. <button class="linkbtn" id="to-queue">Открыть очередь</button>`;
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
  function showProduct(n) {
    const out = graph.out.get(n.id) || [], inc = graph.inc.get(n.id) || [];
    const line = Array.from(graph.lineOf(n.id));
    const [st, cls] = saleState(n);
    const rec = new E.Recommender(graph, recOpts({ fill: false }));
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
  for (const e of data.edges) {
    const kit = e[3] === "разметка: комплект";           // комплект клиент увидит только после решения менеджера
    queue.push({ key: kit ? `pk|${e[0]}|${e[1]}` : ekey(e), group: kit ? "kit" : "edge", type: e[2], from: nodeNameAll(e[0]), to: nodeNameAll(e[1]), source: E.LABEL[e[3]] || e[3],
                 conf: e[4], ev: (e[5] || "").replace(/; https?:\S+$/, ""), auto: !kit, urlFrom: (allNodes.get(e[0]) || {}).url, urlTo: (allNodes.get(e[1]) || {}).url });
  }
  for (const l of data.class_links || []) {
    const labelled = l.source === "разметка";
    const top = ((data.class_rank || {})[l.dst_class] || [])[0];
    queue.push({ key: `cl|${l.src_class}|${l.dst_class}`, group: "class", type: l.type || "complements", from: `Класс «${clsName(l.src_class)}»`,
                 to: `Класс «${clsName(l.dst_class)}»` + (top ? ` — например, ${nodeNameAll(top)}` : ""),
                 source: labelled ? (l.verdict === "kit" ? "разметка: комплект" : "разметка: нужно") : "модель по API", conf: l.confidence, ev: l.reason || "", auto: false });
  }
  for (const [cid, fams] of new E.Graph(data).ruByClass) {
    if (!goneByClass[cid]) continue;
    for (const dst of fams) {
      queue.push({ key: `cr|${cid}|${dst}`, group: "replace", type: "analog_class", from: `Класс «${clsName(cid)}»: зарубежные программы, снятые с продажи (${goneByClass[cid]})`,
                   to: nodeNameAll(dst), source: "реестр российского ПО", conf: 0.5, ev: "", auto: false, urlTo: (allNodes.get(dst) || {}).url });
    }
  }
  const waitingRows = () => queue.filter((r) => !r.auto && !votes[r.key]);
  const statusOf = (r) => (votes[r.key] === "ok" ? "принято" : votes[r.key] === "no" ? "отклонено" : r.auto ? "показывается клиенту" : "ждёт проверки");
  const types = Array.from(new Set(data.edges.map((e) => e[2])));
  const sources = Array.from(new Set(queue.map((r) => r.source)));
  $("#f-type").innerHTML = '<option value="pending">ждут проверки менеджера</option><option value="class">связи между классами</option><option value="kit">комплекты к товарам</option><option value="replace">замены по классу</option>'
    + '<option value="advice">связи товаров (без «той же линейки»)</option><option value="">все</option>'
    + types.map((t) => `<option value="${esc(t)}">товары: ${esc(E.TYPE_TITLE[t] || t)}</option>`).join("");
  $("#f-source").innerHTML = '<option value="">любой</option>' + sources.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  ["f-type", "f-source", "f-state", "f-q"].forEach((id) => $("#" + id).addEventListener("input", () => { page = 0; renderLinks(); }));
  function filteredLinks() {
    const t = $("#f-type").value, src = $("#f-source").value, st = $("#f-state").value, q = $("#f-q").value.trim().toLowerCase();
    return queue.filter((r) => {
      const v = votes[r.key];
      if (t === "pending") { if (r.auto || v) return false; }
      else if (t === "class" || t === "replace" || t === "kit") { if (r.group !== t) return false; }
      else if (t === "advice") { if (r.group !== "edge" || r.type === "same_line") return false; }
      else if (t && (r.group !== "edge" || r.type !== t)) return false;
      if (src && r.source !== src) return false;
      if (st && (v || "new") !== st) return false;
      if (q && !(`${r.from} ${r.to}`.toLowerCase().includes(q))) return false;
      return true;
    });
  }
  function saveVotes() {
    localStorage.setItem(VOTES_KEY, JSON.stringify(votes));
    rebuild(); renderLinks(); render();
  }
  function renderLinks() {
    const rows = filteredLinks();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    page = Math.min(page, pages - 1);
    const part = rows.slice(page * PAGE, page * PAGE + PAGE);
    const kind = (r) => (r.group === "class" ? "классы: " : r.group === "replace" ? "" : "") + (E.TYPE_TITLE[r.type] || r.type);
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
    return `
      <h2>Проверка на истории по протоколу организаторов</h2>
      <p class="hint" style="margin-top:6px">Обезличенный датасет организаторов${r.dataset ? `, версия ${esc(String(r.dataset).replace(/^dataset_v?|\.xlsx$/g, ""))}` : ""}. Советы даны по покупкам до ${esc(r.protocol.train_until)}, засчитываются новые для клиента товары, купленные с ${esc(r.protocol.test_from)}. Модель и её режимы обучались только на периоде до даты разреза.</p>
      <dl class="tiles">
        <div class="tile"><dt>Угадано из трёх советов, организации</dt><dd>${pct(full["A B2B"])}</dd><small>базовые способы организаторов: ${pct(org["популярность"]["A B2B"])} и ${pct(org["совместные покупки"]["A B2B"])}</small></div>
        <div class="tile"><dt>То же, холодный старт</dt><dd>${pct(full["B B2B"])}</dd><small>базовые способы: ${pct(org["популярность"]["B B2B"])} и ${pct(org["совместные покупки"]["B B2B"])}</small></div>
        <div class="tile"><dt>Если спрятать историю пар купленного товара</dt><dd>${pct(reg.new_item.p3["A B2B"])}</dd><small>так моделируется новинка; без знаний каталога ${pct(reg.new_item_no_catalog.p3["A B2B"])}</small></div>
        <div class="tile"><dt>Fill Rate по протоколу, группы A и B</dt><dd>100 %</dd><small>у совместных покупок ${pct(0.923, 1)} и ${pct(0.872, 1)}; совет по собственному основанию — у ${fillAll == null ? "—" : pct(fillAll, 0)} клиентов с покупками. Показ блока в кабинете (${pct(r.current_fill)}) считается по визитам и с этим не сравним</small></div>
      </dl>
      <div class="charts">
        <figure><figcaption>Precision@3, организации (группа A)</figcaption>${chartA}</figure>
        <figure><figcaption>Если у купленного товара нет истории продаж — «соло» или новинка</figcaption>${chartNew}</figure>
      </div>
      <p class="hint">Серые полосы — базовые способы, синие — наша модель; отрезок — 95-процентный интервал. Шкала у обоих графиков одна, от 0 до 20 %.</p>
      <h3>Все группы</h3>
      <div class="tablewrap"><table class="links"><tr><th>Способ</th>${head.map((h) => `<th class="num">${h}</th>`).join("")}</tr>
        ${row("Популярность (организаторы)", org["популярность"])}${row("Совместные покупки (организаторы)", org["совместные покупки"])}
        ${r.same_line && r.same_line.p3 ? row("Правило «та же линейка», без обучения", r.same_line.p3) : ""}
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
        + (b["та же линейка"] ? `, правило «та же линейка» — ${b["та же линейка"]["A B2B"].hits["новый продукт"]}. Простое правило почти не уступает модели на продлениях и других вариантах купленного; прибавка модели — новые продукты.` : ".") : "";
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

  /** Проверка пар «купил А — советовать ли Б»: сколько чего размечено и что это меняет в блоке. */
  function verdictsHtml(vs) {
    const KINDS = [["need", "нужно"], ["kit", "комплект"], ["has", "уже есть"], ["same", "то же самое"], ["unrelated", "не связано"]];
    const ve = report && report.verdict_effect;
    const lines = ve ? ve.verdicts : null;
    const sum = (o) => KINDS.reduce((a, [k]) => a + ((o || {})[k] || 0), 0);
    const cell = (o, k) => (o ? nf.format(o[k] || 0) : "—");
    let h = `<h3>Проверка пар «купил А — советовать ли Б»</h3>
      <p class="hint">Статистика продаж не отличает «нужно» от «и так есть»: после антивируса чаще всего покупают Windows, потому что её покупают все. Поэтому каждую пару заранее судит модель по правилам хорошего менеджера. В личные советы идёт только «нужно» — с фразой «зачем»; «комплект» на истории не сбылся ни разу и остаётся подсказкой менеджеру. На обращение клиента модель не вызывается: вердикт — строка таблицы.</p>
      <table class="links"><tr><th>Вердикт</th><th class="num">Пары классов каталога</th><th class="num">Пары товаров каталога</th><th class="num">Пары линеек из продаж</th></tr>
      ${KINDS.map(([k, t]) => `<tr><td>${t}${k === "need" ? " — идёт в личные советы" : k === "kit" ? " — ждёт решения менеджера" : ""}</td><td class="num">${cell(vs.classes, k)}</td><td class="num">${cell(vs.families, k)}</td><td class="num">${cell(lines, k)}</td></tr>`).join("")}
      <tr><td><b>Всего</b></td><td class="num"><b>${nf.format(sum(vs.classes))}</b></td><td class="num"><b>${nf.format(sum(vs.families))}</b></td><td class="num"><b>${lines ? nf.format(sum(lines)) : "—"}</b></td></tr></table>`;
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
    }
    return h;
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
        <div class="tile"><dt>Модель на обращение клиента</dt><dd>0 вызовов</dd><small>с агентом каталога и разметкой пар для новых товаров — около 0,05 ₽ на обращение</small></div>
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
    html += `<h3>Очередь менеджера</h3>
      <p class="hint">Сейчас блок построен примерно на 6 000 ручных связок «товар — товар». Здесь клиент сразу видит только пары товаров, проверенные заранее и получившие вердикт «нужно»; любую из них менеджер отклоняет одним нажатием на вкладке «Связи». Решения менеджера ждёт то, где проверена не сама пара с товаром клиента или где совет не сбывается на истории: связи между классами, «комплекты» и замены по классу — сейчас ${nf.format(waitingRows().length)} строк.</p>`;
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
    const e = agent.exam || {}, nh = agent.exam_nohints || {}, pr = agent.per_run;
    const rows = [];
    const of = (x, n) => `${Math.round(x * n)} из ${n}`;
    if (e.runs) {
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
      return `<li data-i="${i}" class="${i === agentIdx ? "is-cur" : ""}">${esc(r.card.name)}
        <small>${t.kind ? esc(t.kind) + " · " : ""}${r.truth ? `<span class="mark mark--${okClass ? "yes" : "no"}">${okClass ? "класс совпал" : "класс другой"}</span>` : '<span class="mark mark--yes">живой запуск</span>'} · связей ${r.links.length} · ${r.requests || "?"} запр., ${r.seconds >= 0.5 ? Math.round(r.seconds) + " с" : "ответы из кеша"}</small></li>`;
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
