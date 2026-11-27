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
    const nm = (f) => (g.nodes.get(f) || {}).name || f;
    const card = (r, k) => {
      const src = ((r.paths || [])[0] || {}).src;
      const checks = (r.label ? [r.label] : []).concat((r.paths || []).slice(0, 3).map((p) => `из покупки «${nm(p.src)}»: `
        + `${(E.TYPE_TITLE || {})[p.type] || p.type}${p.source ? `, ${(E.LABEL || {})[p.source] || p.source}` : ""}`));
      return { link: utm(r.url, r.slot, k + 1), name: r.name, vendorName: r.vendor, price: (g.nodes.get(r.family) || {}).price || null,
        slot: r.slot, reason: r.why, basis: src ? { family: src, name: nm(src) } : null, checks };
    };
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

  // ---- вид: как блок советов в макете кабинета — строки «основание → совет → цена»; свои классы с приставкой svt-,
  // шрифт — от страницы кабинета; цвета магазина: текст #121519, серый #768297, линии #e4e7ec, бордовый #a50f2d
  function css() {
    if (document.getElementById("svt-css")) return;
    const s = document.createElement("style");
    s.id = "svt-css";
    s.textContent = `.svt{font:inherit;color:#121519;line-height:1.45}
.svt-adv{border-top:1px solid #e4e7ec}
.svt-adv__row{display:grid;grid-template-columns:minmax(170px,.85fr) minmax(0,2fr) minmax(170px,.7fr);gap:28px;padding:20px 4px;border-bottom:1px solid #e4e7ec;align-items:start}
.svt-adv__basis{font-size:13px;color:#768297}
.svt-adv__basis b{display:block;margin:3px 0 4px;font-size:14px;font-weight:500;color:#121519}
.svt-badge{display:inline-block;border-radius:8px;padding:2px 6px;font-size:13px;line-height:18px;font-weight:500;border:.8px solid}
.svt-badge--ok{background:#e6f7e9;border-color:#cfe4d3;color:#006817}.svt-badge--soon{background:#fcf6df;border-color:#f7e6bb;color:#c65300}
.svt-badge--gone{background:#ffebf0;border-color:#f3ced4;color:#b2041b}
.svt-more{align-self:flex-start;font-size:14px;font-weight:500;color:#a50f2d;text-decoration:none}.svt-more:hover{text-decoration:underline}
.svt-adv__name{font-size:16px;font-weight:600;line-height:1.35}
.svt-adv__vendor{margin-top:2px;font-size:13px;color:#768297}
.svt-adv__why{margin:10px 0 0;max-width:64ch;font-size:14px;line-height:1.55}
.svt-adv details{margin-top:8px}.svt-adv summary{cursor:pointer;font-size:13px;color:#768297}
.svt-adv details[open] summary,.svt-adv summary:hover{color:#121519}
.svt-adv details ul{margin:6px 0 0;padding-left:18px;font-size:13px;line-height:1.5;color:#768297}
.svt-adv__buy{display:flex;flex-direction:column;gap:8px}
.svt-adv__price{margin-bottom:4px;font-size:20px;font-weight:600;white-space:nowrap}
.svt-btn{display:inline-flex;align-items:center;justify-content:center;border:1px solid #a50f2d;color:#a50f2d;background:#fff;border-radius:8px;padding:6px 12px;font:inherit;font-size:14px;font-weight:500;cursor:pointer;text-decoration:none;white-space:nowrap}
.svt-btn--fill{background:#a50f2d;color:#fff}.svt-btn:disabled{opacity:.6;cursor:default}
.svt-also{margin-top:28px}.svt-also h3{margin:0;font-size:16px;font-weight:600}
.svt-also p{margin:4px 0 10px;font-size:13px;color:#768297}
.svt-also__row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:14px 4px;border-top:1px solid #e4e7ec}
.svt-also__row b{font-weight:500}.svt-also__row span{display:block;margin-top:2px;font-size:13px;color:#768297}
.svt-note{font-size:13px;color:#768297;margin-top:8px}
.svt-h2{margin:0;font-size:24px;line-height:32px;font-weight:600}.svt-lead{margin:4px 0 14px;font-size:14px;color:#768297}
@media (max-width:760px){.svt-adv__row{grid-template-columns:1fr;gap:10px}.svt-adv__buy{flex-direction:row;flex-wrap:wrap;align-items:center}.svt-also__row{grid-template-columns:1fr}}`;
    document.head.appendChild(s);
  }

  const price = (c) => (c.price ? `от ${nf.format(Math.round(c.price))} ₽` : "цена по запросу");
  const go = (c, pos, fill, text) => `<a class="svt-btn ${fill ? "svt-btn--fill" : ""} svt-go" href="${esc(c.link)}" target="_blank" rel="noopener"
      data-slot="${esc(c.slot)}" data-pos="${pos}" data-item="${esc(c.name)}">${text}</a>`;
  const inv = (c, pos, fill) => `<button class="svt-btn ${fill ? "svt-btn--fill" : ""} svt-inv" type="button" data-slot="${esc(c.slot)}" data-pos="${pos}" data-item="${esc(c.name)}">Запросить счёт</button>`;
  const more = (c, pos) => `<a class="svt-more svt-go" href="${esc(c.link)}" target="_blank" rel="noopener" data-slot="${esc(c.slot)}" data-pos="${pos}" data-item="${esc(c.name)}">Подробнее о товаре</a>`;
  const KIND = { next: "Старшая редакция для", missing: "Нужно к", kit: "Если ещё нет — к", replace: "Замена для", renew: "Продление" };

  // основание — лицензия клиента, от которой идёт совет, и её срок: «почему именно мне»
  function basis(client, c, prev) {
    const b = c.basis;
    if (!b) return `<div class="svt-adv__basis">${esc(TITLE[c.slot] || "")}</div>`;
    if (b.family === prev) return `<div class="svt-adv__basis">${esc(KIND[c.slot] || "К вашей покупке")} этой же лицензии</div>`;
    const p = ((client && client.purchases) || []).find((x) => x.family === b.family);
    let term = "";
    if (p && p.date && p.term) {
      const d = new Date(p.date.slice(0, 10) + "T00:00:00");
      d.setMonth(d.getMonth() + Number(p.term));
      const left = Math.round((d - new Date(((client.today || new Date().toISOString()).slice(0, 10)) + "T00:00:00")) / 864e5);
      const end = [String(d.getDate()).padStart(2, "0"), String(d.getMonth() + 1).padStart(2, "0"), d.getFullYear()].join(".");  // местная дата, без сдвига в UTC
      term = left < 0 ? `<span class="svt-badge svt-badge--gone">истекла ${end}</span>`
        : left <= 120 ? `<span class="svt-badge svt-badge--soon">истекает через ${left} дн.</span>` : `<span class="svt-badge svt-badge--ok">активна до ${end}</span>`;
    }
    return `<div class="svt-adv__basis">${esc(KIND[c.slot] || "К вашей покупке")}<b>${esc(b.name)}</b>${term}</div>`;
  }

  function render(el, res, client, opts) {
    const control = res.group === "control";
    const head = opts && opts.title === false ? "" : `<h2 class="svt-h2">Вам может подойти</h2>
      <p class="svt-lead">Подобрано по вашим лицензиям: у каждого совета видно, к какой покупке он и почему.</p>`;
    let prev = null;
    const rows = res.renew.concat(res.data).map((c, k) => {
      const row = `<article class="svt-adv__row">${basis(client, c, prev)}
        <div class="svt-adv__main"><div class="svt-adv__name">${esc(c.name)}</div><div class="svt-adv__vendor">${esc(c.vendorName || "")}</div>
          <p class="svt-adv__why">${esc(c.reason)}</p>
          ${(c.checks || []).length ? `<details><summary>Как проверено</summary><ul>${c.checks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}</div>
        <div class="svt-adv__buy">${c.price ? `<div class="svt-adv__price">${esc(price(c))}</div>` : ""}${c.slot === "renew" ? go(c, k + 1, true, "Продлить") + inv(c, k + 1, false) : inv(c, k + 1, true) + more(c, k + 1)}</div>
      </article>`;
      prev = (c.basis || {}).family || null;
      return row;
    }).join("");
    const also = res.popular.map((c, k) => `<div class="svt-also__row"><div><b>${esc(c.name)}</b><span>${esc(c.reason)}</span></div>
      ${go(c, k + 1, false, "Открыть")}${inv(c, k + 1)}</div>`).join("");
    el.innerHTML = `<div class="svt">${control ? "" : head}
      ${rows ? `<div class="svt-adv">${rows}</div>` : ""}
      ${!control && !rows ? '<div class="svt-note">Личных советов по этим покупкам пока нет: показываем только проверенное.</div>' : ""}
      ${also ? `<section class="svt-also"><h3>${control ? "Нынешний блок кабинета" : "Часто докупают"}</h3>
        <p>${control ? "Контрольная группа пилота: здесь остаётся нынешний блок; на стенде вместо него — то, что чаще всего берут." : "Это не личный совет, а то, что чаще всего берут клиенты вашего типа."}</p>${also}</section>` : ""}
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
    render(el, res, client || {}, opts || {});
    return res;
  }

  window.Sovetnik = { mount, track };
})();
