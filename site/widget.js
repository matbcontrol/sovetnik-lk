/**
 * Виджет советника для страницы кабинета: один тег script и один вызов.
 *
 *   <div id="sovetnik"></div>
 *   <script src="https://<сервер советника>/widget.js"></script>
 *   <script>Sovetnik.mount("#sovetnik", { type: "b2b", today: "2026-11-26", client_id: "<псевдоним>",
 *                                         purchases: [{ family: "<вендор/семейство>", date: "2026-05-11", term: 12 }] });</script>
 *
 * Виджет спрашивает POST /api/advice у сервера, с которого загружен, и рисует до трёх советов с объяснением,
 * продление и строку «Часто докупают». Показы, переходы и «Запросить счёт» уходят в журнал (POST /api/event)
 * с меткой группы пилота. Контрольной группе виджет ничего не рисует — кабинет оставляет ей нынешний блок
 * (для стенда можно попросить заменитель: { control: "render" }). Сервера нет (открытый стенд) — совет считает
 * тот же движок в браузере (engine.js и data/graph.json), события хранятся в браузере.
 */
(function () {
  "use strict";
  const me = document.currentScript;
  const BASE = me ? me.src.replace(/[^/]*(\?.*)?$/, "") : "";
  const STATIC = /\.github\.io$/.test(new URL(BASE || location.href).hostname);
  const KEY = "sovetnik.events.v1";
  const SALT = "pilot-1";
  const TITLE = { renew: "Пора продлить", next: "Следующая ступень", missing: "К вашему продукту", kit: "Типовой набор",
    replace: "Замена", popular: "Часто докупают" };
  const newView = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  let view = newView();                                // заход: одно открытие блока; показ карточки за заход — один раз
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nf = new Intl.NumberFormat("ru-RU");
  const utm = (url, slot, pos) => (url ? url + (url.includes("?") ? "&" : "?")
    + `utm_source=lk.softline.ru&utm_medium=internal&utm_campaign=reco&utm_content=sovetnik_${slot}&utm_term=${pos}` : "");
  let server = !STATIC;

  // ---- журнал: на сервер советника; без сервера — в браузер (так его видит панель стенда)
  function track(kind, x, group) {
    const ev = Object.assign({ kind, group: group === "control" ? "control" : "test", view, ts: new Date().toISOString() }, x);
    if (server) {
      fetch(BASE + "api/event", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ev), keepalive: true })
        .catch(() => {});
      return;
    }
    try {
      const a = JSON.parse(localStorage.getItem(KEY) || "[]");
      a.push(ev);
      localStorage.setItem(KEY, JSON.stringify(a.slice(-5000)));
    } catch (e) { /* хранилище закрыто */ }
  }

  // ---- совет: сервер советника; если его нет — тот же движок в браузере
  let enginePromise = null;
  function loadEngine() {
    if (!enginePromise) {
      enginePromise = new Promise((ok, fail) => {
        if (window.Engine) return ok();
        const s = document.createElement("script");
        s.src = BASE + "engine.js";
        s.onload = ok;
        s.onerror = () => fail(new Error("нет engine.js"));
        document.head.appendChild(s);
      }).then(() => fetch(BASE + "data/graph.json")).then((r) => r.json()).then((data) => new window.Engine.Graph(data));
    }
    return enginePromise;
  }

  async function group(clientId, share) {              // как recsys/recommend/block.py: sha256(соль:клиент), первые 4 байта
    if (!clientId || !(share > 0)) return "test";
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${SALT}:${clientId}`));
    return new DataView(buf).getUint32(0) / 2 ** 32 < share ? "control" : "test";
  }

  async function inBrowser(client) {
    const g = await loadEngine();                      // первый раз — загрузка движка и графа, около секунды
    const t0 = performance.now();                        // время ответа — без загрузки, как у сервера
    const E = window.Engine;
    const rec = (extra) => new E.Recommender(g, Object.assign({ fill: false, unverified: false, approved: new Set(), rejected: new Set() }, extra));
    const c = { type: client.type || "b2b", today: client.today || new Date().toISOString().slice(0, 10), purchases: client.purchases || [] };
    const card = (r, k) => ({ link: utm(r.url, r.slot, k + 1), name: r.name, vendorName: r.vendor,
      price: (g.nodes.get(r.family) || {}).price || null, slot: r.slot, reason: r.why });
    const popular = (n) => rec({ fill: true }).recommend(c, 8).recs.filter((r) => r.slot === "popular").slice(0, n);
    const arm = await group(String(client.client_id || "").slice(0, 64), Number(client.control_share) || 0);
    if (arm === "control") {
      return { status: "control", group: "control", data: [], renew: [], popular: popular(3).map(card), model_calls: 0,
        ms: +(performance.now() - t0).toFixed(2) };
    }
    const recs = rec().recommend(c, 6).recs;
    const personal = recs.filter((r) => r.slot !== "renew").slice(0, 3);
    return { status: personal.length ? "shown" : "hidden", group: "test", data: personal.map(card),
      renew: recs.filter((r) => r.slot === "renew").map(card), popular: popular(c.purchases.length ? 1 : 3).map(card),
      model_calls: 0, ms: +(performance.now() - t0).toFixed(2) };
  }

  async function advise(client) {
    if (server) {
      try {
        const r = await fetch(BASE + "api/advice", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(client) });
        if (r.ok) return Object.assign(await r.json(), { where: "сервер советника" });
        if (r.status === 400) throw new Error((await r.json()).error || "ошибка в запросе");
      } catch (e) {
        if (!(e instanceof TypeError)) throw e;          // TypeError — сервера нет; остальное — ошибка в данных
      }
      server = false;
    }
    return Object.assign(await inBrowser(client), { where: "движок в браузере" });
  }

  // ---- вид: свои классы с приставкой svt-, шрифт и цвет — от страницы кабинета
  function css() {
    if (document.getElementById("svt-css")) return;
    const s = document.createElement("style");
    s.id = "svt-css";
    s.textContent = `.svt{font:inherit;color:inherit}
.svt-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.svt-card{border:1px solid #d9dde3;border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px;background:#fff}
.svt-kind{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#5b6573}
.svt-name{font-weight:600}.svt-vendor{font-size:12px;color:#5b6573}
.svt-why{font-size:13px;margin:0}
.svt-foot{margin-top:auto;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.svt-price{flex:1 0 100%;font-weight:600;white-space:nowrap}
.svt-btn{display:inline-block;border:1px solid #1f5fa8;color:#1f5fa8;background:#fff;border-radius:6px;padding:4px 10px;font:inherit;font-size:13px;cursor:pointer;text-decoration:none}
.svt-btn--fill{background:#1f5fa8;color:#fff}.svt-btn:disabled{opacity:.6;cursor:default}
.svt-h{font-size:14px;font-weight:600;margin:14px 0 4px}.svt-h small{font-weight:400;color:#5b6573}
.svt-row{display:flex;gap:10px;align-items:center;border-top:1px solid #eceff3;padding:8px 0;flex-wrap:wrap}
.svt-row b{flex:1 1 200px}.svt-note{font-size:12px;color:#5b6573;margin-top:8px}`;
    document.head.appendChild(s);
  }

  const price = (c) => (c.price ? `от ${nf.format(Math.round(c.price))} ₽` : "цена по запросу");
  const buttons = (c, pos, fill) => `<a class="svt-btn ${fill ? "svt-btn--fill" : ""} svt-go" href="${esc(c.link)}" target="_blank" rel="noopener"
      data-slot="${esc(c.slot)}" data-pos="${pos}" data-item="${esc(c.name)}">Подробнее</a>
    <button class="svt-btn svt-inv" type="button" data-slot="${esc(c.slot)}" data-pos="${pos}" data-item="${esc(c.name)}">Запросить счёт</button>`;
  const row = (c, k) => `<div class="svt-row"><b>${esc(c.name)}</b><span class="svt-vendor">${esc(price(c))}</span>${buttons(c, k + 1, false)}</div>`;

  function render(el, res) {
    const control = res.group === "control";
    const cards = res.data.map((c, k) => `<article class="svt-card"><div class="svt-kind">${esc(TITLE[c.slot] || c.slot)}</div>
      <div class="svt-name">${esc(c.name)}</div><div class="svt-vendor">${esc(c.vendorName)}</div>
      <p class="svt-why">${esc(c.reason)}</p><div class="svt-foot"><span class="svt-price">${esc(price(c))}</span>${buttons(c, k + 1, true)}</div></article>`).join("");
    el.innerHTML = `<div class="svt">
      ${control ? '<div class="svt-h">Нынешний блок кабинета <small>контрольная группа пилота; на стенде вместо него — «Часто докупают»</small></div>' : ""}
      ${res.renew.length ? `<div class="svt-h">${TITLE.renew}</div>${res.renew.map(row).join("")}` : ""}
      ${cards ? `<div class="svt-cards">${cards}</div>` : ""}
      ${!control && !cards ? '<div class="svt-note">Личных советов по этим покупкам пока нет: показываем только проверенное.</div>' : ""}
      ${res.popular.length ? `${control ? "" : '<div class="svt-h">Часто докупают <small>не личный совет</small></div>'}${res.popular.map(row).join("")}` : ""}
    </div>`;
    el.querySelectorAll(".svt-go").forEach((a) => track("show", { slot: a.dataset.slot, pos: +a.dataset.pos, item: a.dataset.item }, res.group));
    el.onclick = (e) => {
      const t = e.target.closest(".svt-go, .svt-inv");
      if (!t) return;
      const x = { slot: t.dataset.slot, pos: +t.dataset.pos, item: t.dataset.item };
      if (t.classList.contains("svt-inv")) {
        track("invoice", x, res.group);
        t.textContent = "Счёт запрошен";
        t.disabled = true;
      } else track("click", x, res.group);
    };
  }

  /** Нарисовать блок советника в el. Ответ советника возвращается: по полю group кабинет решает, что показать сам. */
  async function mount(target, client, opts) {
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) return null;
    view = newView();
    css();
    el.innerHTML = '<div class="svt svt-note">Советник считает…</div>';
    let res;
    try {
      res = await advise(client || {});
    } catch (e) {
      el.innerHTML = `<div class="svt svt-note">Советник не ответил: ${esc(e.message)}</div>`;
      return null;
    }
    if (res.group === "control" && !(opts && opts.control === "render")) {
      el.innerHTML = "";
      return res;
    }
    render(el, res);
    return res;
  }

  window.Sovetnik = { mount, track };
})();
