/**
 * Журнал кабинета: показы советов, переходы в магазин и запросы счёта — кликабельность и конверсия.
 * Пишет только страница кабинета (то, что видит клиент); стенд читает итог для панели «Журнал кабинета».
 * Есть сервер стенда — событие уходит в его журнал (data/private/events.jsonl, не публикуется);
 * нет сервера (открытый стенд) — хранится в этом браузере. Номеров клиентов в событиях нет:
 * вид совета и место берутся из меток ссылки (те же увидит аналитика магазина), товар — из карточки.
 */
(function () {
  "use strict";
  const KEY = "sovetnik.events.v1";
  const STATIC = /\.github\.io$/.test(location.hostname);
  const CABINET = /cabinet\.html$/.test(location.pathname);
  const SLOTS = ["renew", "next", "missing", "kit", "replace", "popular"];
  const LINK = 'a[href*="utm_content=sovetnik_"]';
  const view = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);   // один заход на страницу
  const seen = new Set();
  let server = !STATIC;

  const local = () => { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; } };
  function keep(ev) {
    try { const a = local(); a.push(ev); localStorage.setItem(KEY, JSON.stringify(a.slice(-5000))); } catch (e) { /* хранилище закрыто */ }
  }
  const quiet = () => location.hash === "#sim";      // симуляция ИИ-клиентов — не действия людей, в журнал не идёт

  function send(ev) {
    if (!CABINET || quiet() || SLOTS.indexOf(ev.slot) < 0) return;
    Object.assign(ev, { view, ts: new Date().toISOString() });
    keep(ev);
    if (!server) return;
    fetch("api/event", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ev), keepalive: true })
      .then((r) => { if (!r.ok) server = false; }).catch(() => { server = false; });
  }

  function info(a) {
    const m = /utm_content=sovetnik_([a-z]+)&utm_term=(\d+)/.exec(a.getAttribute("href") || "");
    if (!m) return null;
    const box = a.closest("article, .row, tr");
    const name = box && box.querySelector(".card__name, b");
    return { slot: m[1], pos: +m[2], item: ((name && name.textContent) || "").trim().slice(0, 200) };
  }

  function scan() {
    if (quiet()) return;
    document.querySelectorAll("#licenses, #block, #popular").forEach((root) => root.querySelectorAll(LINK).forEach((a) => {
      if (a.closest("[hidden]")) return;               // замена в свёрнутом окне ещё не показана
      const x = info(a);
      if (!x || seen.has(x.slot + "|" + x.item)) return;
      seen.add(x.slot + "|" + x.item);
      send(Object.assign({ kind: "show" }, x));
    }));
  }

  if (CABINET) {
    let timer = 0;
    const later = () => { clearTimeout(timer); timer = setTimeout(scan, 250); };
    new MutationObserver(later).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    window.addEventListener("hashchange", later);
    later();
    document.addEventListener("click", (e) => {
      const t = e.target.closest && e.target.closest(LINK + ", [data-invoice]");
      if (!t) return;
      if (t.hasAttribute("data-invoice")) {
        send({ kind: "invoice", slot: t.dataset.slot, pos: +t.dataset.pos || 0, item: t.dataset.item || "" });
        t.textContent = "Счёт запрошен";
        t.disabled = true;
        return;
      }
      const x = info(t);
      if (x) send(Object.assign({ kind: "click" }, x));
    }, true);
  }

  /** Итог — как recsys/events.py: показ одной карточки за заход — один раз; переходы и запросы счёта — каждый. */
  function summarize(events) {
    const shown = new Set();
    const rows = {};
    SLOTS.forEach((s) => { rows[s] = { shows: 0, clicks: 0, invoices: 0 }; });
    events.forEach((e) => {
      const r = rows[e.slot];
      if (!r) return;
      if (e.kind === "show") {
        const k = [e.view, e.slot, e.item].join("|");
        if (shown.has(k)) return;
        shown.add(k);
        r.shows += 1;
      } else if (e.kind === "click") r.clicks += 1;
      else if (e.kind === "invoice") r.invoices += 1;
    });
    const total = { shows: 0, clicks: 0, invoices: 0 };
    const bySlot = {};
    SLOTS.forEach((s) => {
      const r = rows[s];
      if (r.shows || r.clicks || r.invoices) bySlot[s] = r;
      total.shows += r.shows; total.clicks += r.clicks; total.invoices += r.invoices;
    });
    return { total, by_slot: bySlot, views: new Set(events.filter((e) => e.kind === "show").map((e) => e.view)).size };
  }

  async function stats() {
    if (!STATIC) {
      try {
        const r = await fetch("api/events", { cache: "no-store" });
        if (r.ok) return Object.assign(await r.json(), { where: "журнал сервера стенда" });
      } catch (e) { /* сервера нет — считаем по этому браузеру */ }
    }
    return Object.assign(summarize(local()), { where: "этот браузер" });
  }

  window.Track = { stats, summarize };
})();
