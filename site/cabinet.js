/* Макет встраивания: как советник выглядел бы внутри личного кабинета магазина.
 *
 * Кабинет передаёт советнику то, что знает о клиенте (тип, покупки с датами и сроками), и рисует ответ
 * в своих местах: блок «Вам может подойти» — те же поля, что сейчас (ссылка, название, вендор, цена),
 * плюс объяснение и вид совета; строка лицензий — продление с советом о старшей редакции и готовая
 * замена ушедшего продукта вместо заявки менеджеру. Советы считает тот же движок, что на стенде
 * (engine.js), по открытым данным (data/graph.json); языковая модель на запрос не вызывается.
 * Это не настоящий кабинет: клиенты — примеры стенда (data/examples.json).
 */
(async function () {
  "use strict";
  const E = window.Engine;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nf = new Intl.NumberFormat("ru-RU");
  const getJson = async (url) => { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (e) { return null; } };

  const data = await getJson("data/graph.json");
  const examples = (await getJson("data/examples.json")) || [];
  if (!data || !examples.length) {
    $("main").innerHTML = '<section class="panel">Нет data/graph.json или data/examples.json — соберите данные: <code>python -m recsys.export</code></section>';
    return;
  }

  // решения менеджера — общие со стендом: отклонённая связь выпадает, принятая замена по классу видна клиенту
  let votes = {};
  try { votes = JSON.parse(localStorage.getItem("sovetnik.votes.v1") || "{}"); } catch (e) { votes = {}; }
  const edges = data.edges.filter((e) => votes[`${e[0]}|${e[1]}|${e[2]}`] !== "no");
  // принятое менеджером из предложений агента каталога («Связи на проверку») — тот же след, что на стенде
  let agentStore = {};
  try { agentStore = JSON.parse(localStorage.getItem("sovetnik.agent.v1") || "{}"); } catch (e) { agentStore = {}; }
  const nodeIds = new Set(data.nodes.map((n) => n.id));
  const agentNodes = new Map(), agentEdges = [];
  for (const [k, p] of Object.entries(agentStore)) {
    if (votes[k] !== "ok" || !p || !p.edge) continue;
    if (p.node && !nodeIds.has(p.node.id)) agentNodes.set(p.node.id, p.node);
    agentEdges.push(p.edge);
  }
  const graph = new E.Graph(Object.assign({}, data, {
    nodes: data.nodes.concat(Array.from(agentNodes.values())), edges: edges.concat(agentEdges) }));
  const byVote = (v) => new Set(Object.keys(votes).filter((k) => votes[k] === v && /^(c[lrp]|pk)\|/.test(k)));
  const approved = byVote("ok"), rejected = byVote("no");
  const engine = (extra) => new E.Recommender(graph, Object.assign({ fill: false, unverified: false, approved, rejected }, extra));

  const node = (f) => graph.nodes.get(f) || {};
  const className = (n) => (n && n.cls && data.classes[n.cls]) || "";
  const fmtDate = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join(".") : "—");
  const addMonths = (iso, m) => new Date(E.addMonths(E.parseDate(iso), m)).toISOString().slice(0, 10);
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const fmtLong = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-"); return `${+d} ${MONTHS[+m - 1]} ${y}`; };
  const days = (from, to) => Math.round((Date.parse(to) - Date.parse(from)) / 864e5);
  const price = (n) => (n.price ? `от ${nf.format(Math.round(n.price))} ₽` : n.sale !== 1 && n.req === 1 ? "цена по запросу" : "");
  // те же метки перехода, что ставит блок кабинета: показы и переходы считаются теми же средствами
  const shopUrl = (url, slot, pos) => (url ? url + (url.includes("?") ? "&" : "?")
    + `utm_source=lk.softline.ru&utm_medium=internal&utm_campaign=reco&utm_content=sovetnik_${slot}&utm_term=${pos}` : "");
  const invoice = (slot, pos, name) => `<button class="btn" type="button" data-invoice data-slot="${esc(slot)}" data-pos="${pos}" `
    + `data-item="${esc(name)}">Запросить счёт</button>`;
  const fromPurchase = (r, fam) => (r.paths || []).some((p) => p.src === fam);
  // совет по связи класса или замена по классу, которую менеджер ещё не принял
  const pending = (r) => (r.paths || []).length > 0 && r.paths.every((p) => (p.class || p.type === "analog_class") && !approved.has(p.key));

  // ?family=вендор/семейство[&type=b2c] — клиент с одной этой покупкой: так видно любой товар глазами его владельца
  const qs = new URLSearchParams(location.search);
  const fam = qs.get("family");
  if (fam && graph.nodes.has(fam)) {
    const today = (examples[0] && examples[0].client.today) || new Date().toISOString().slice(0, 10);
    examples.unshift({ title: `Клиент с одной покупкой: ${node(fam).name}`, client: {
      type: qs.get("type") === "b2c" ? "b2c" : "b2b", today, purchases: [{ family: fam, date: addMonths(today, -6), term: 12 }] } });
  }
  const sel = $("#client");
  sel.innerHTML = examples.map((x, i) => `<option value="${i}">${esc(x.title)}</option>`).join("");
  const start = Number(qs.get("client") || 0);
  sel.value = String(Math.min(Math.max(Number.isFinite(start) ? start : 0, 0), examples.length - 1));
  sel.addEventListener("change", () => show(+sel.value));
  show(+sel.value);

  function show(i) {
    const ex = examples[i];
    const client = JSON.parse(JSON.stringify(ex.client));
    client.today = client.today || new Date().toISOString().slice(0, 10);
    const [head, tail] = ex.title.split(":");
    const name = /^(Организация|Частное лицо)$/.test(head.trim()) && tail ? tail.trim() : head.trim();
    const title = name.charAt(0).toUpperCase() + name.slice(1);
    $("#who").innerHTML = `<b>${esc(title)}</b><span>${client.type === "b2b" ? "Организация" : "Частное лицо"}</span>`;
    // виджет «Ключи и дистрибутивы» — как в кабинете: ключи доступны по действующим лицензиям
    const live = client.purchases.filter((p) => !p.term || days(client.today, addMonths(p.date, p.term)) >= 0).length;
    $("#w-keys").textContent = live ? `Доступно по ${live} ${live % 10 === 1 && live % 100 !== 11 ? "продукту" : "продуктам"}` : "Действующих лицензий нет";
    $("#today").textContent = `на ${fmtDate(client.today)}`;

    // клиент видит только подтверждённое; продление — в строке своей лицензии, а не в блоке советов
    const res = engine().recommend(client, 6);
    const renew = res.recs.filter((r) => r.slot === "renew");
    const personal = res.recs.filter((r) => r.slot !== "renew").slice(0, 3);
    const pops = engine({ fill: true }).recommend(client, 8).recs.filter((r) => r.slot === "popular")
      .slice(0, client.purchases.length ? 1 : 3);
    // замена ушедшего продукта: прямой аналог или принятая менеджером — сразу; по классу без решения — ждёт
    const replacements = engine({ unverified: true }).recommend(client, 10).recs.filter((r) => r.slot === "replace");
    renderLicenses(client, renew, replacements);
    renderBlock(client, personal, pops);
    $("#contract").textContent = JSON.stringify({
      status: personal.length ? "shown" : "hidden",
      data: personal.map((r, k) => ({ link: shopUrl(r.url, r.slot, k + 1), name: r.name, vendorName: r.vendor,
        price: node(r.family).price || null, slot: r.slot, reason: r.why })),
    }, null, 1);
  }

  function renderLicenses(client, renew, replacements) {
    if (!client.purchases.length) {
      $("#licenses").innerHTML = '<div class="empty">Покупок пока нет.</div>';
      return;
    }
    const rows = client.purchases.map((p, k) => {
      const n = node(p.family);
      const end = p.term ? addMonths(p.date, p.term) : null;
      const left = end ? days(client.today, end) : null;
      const status = end == null ? '<span class="st st--none">Срок не указан</span>'
        : (left < 0 ? '<span class="st st--bad">Истёк</span>' : left <= 120 ? '<span class="st st--warn">Истекает</span>'
          : '<span class="st st--ok">Активен</span>') + `<div class="pdate">${fmtLong(end)}</div>`;
      let action = "";
      const r = renew.find((x) => fromPurchase(x, p.family));
      const rep = replacements.find((x) => fromPurchase(x, p.family));
      if (r) {
        action = `<a class="btn btn--fill" href="${esc(shopUrl(r.url, "renew", k + 1))}" target="_blank" rel="noopener">Продлить</a> ${invoice("renew", k + 1, r.name)}`
          + `<div class="hint">${esc(r.why)}</div>`
          + (r.also ? `<div class="hint"><strong>Заодно.</strong> ${esc(r.also)}</div>` : "");
      } else if (rep && ((left != null && left < 0) || n.sale !== 1)) {
        const wait = pending(rep);
        action = `<button class="btn" data-repl="${k}">Подобрать замену</button>
          <div class="hint">${wait ? "Замена ждёт подтверждения менеджера" : "Доступна замена — готова сразу"}</div>
          <article class="repl ${wait ? "repl--wait" : ""}" id="repl-${k}" hidden>
            ${wait ? "<strong>Замена подготовлена и ждёт подтверждения менеджера.</strong> " : "<strong>Замена готова сразу.</strong> "}
            «<span class="card__name">${esc(rep.name)}</span>», ${esc(price(node(rep.family)))}. ${esc(rep.why)}
            ${wait ? '<div class="hint">Менеджер подтверждает такую замену одним нажатием в очереди советника — для всех клиентов сразу. Сейчас заявка ждёт ответа 1–2 рабочих дня.</div>'
              : `<div style="margin-top:6px"><a class="btn btn--fill" href="${esc(shopUrl(rep.url, "replace", k + 1))}" target="_blank" rel="noopener">Открыть в магазине</a></div>`}
          </article>`;
      } else if (left != null && left < 0) {
        action = '<span class="hint">Срок истёк. Продление этого продукта советник не нашёл.</span>';
      }
      return `<tr><td><div class="pvend">${esc(n.vendor || (p.family.split("/")[0] || "").replace(/^./, (c) => c.toUpperCase()))} <span>№ ${k + 1}</span></div>
        <div class="pname card__name">${esc(n.name || p.family)}</div>
        <div class="ptags">${esc(`${className(n) ? className(n) + ", к" : "К"}уплен ${fmtDate(p.date)}${p.term ? ` на ${p.term} мес.` : ""}`)}</div></td>
        <td>${status}</td><td class="pact">${action}</td></tr>`;
    });
    $("#licenses").innerHTML = `<table><tr><th>Информация о продукте</th><th>Срок истечения</th><th></th></tr>${rows.join("")}</table>`;
    document.querySelectorAll("[data-repl]").forEach((b) => b.addEventListener("click", () => {
      const box = $(`#repl-${b.dataset.repl}`);
      box.hidden = !box.hidden;
    }));
  }

  // основание совета — лицензия клиента, от которой он идёт, и её срок: «почему именно мне»
  function basisOf(client, r, prev) {
    const KIND = { next: "Старшая редакция для", missing: "Нужно к", kit: "Если ещё нет — к", replace: "Замена для" };
    const src = ((r.paths || [])[0] || {}).src;
    if (!src) return `<div class="adv__basis">${esc(r.slotTitle || "")}</div>`;
    if (src === prev) return `<div class="adv__basis">${esc(KIND[r.slot] || "К вашей покупке")} этой же лицензии</div>`;
    const p = client.purchases.find((x) => x.family === src);
    const end = p && p.term ? addMonths(p.date, p.term) : null;
    const left = end ? days(client.today, end) : null;
    const term = end == null ? "" : left < 0 ? `<span class="badge badge--gone">истекла ${fmtDate(end)}</span>`
      : left <= 120 ? `<span class="badge badge--soon">истекает через ${left} дн.</span>`
        : `<span class="badge badge--ok">активна до ${fmtDate(end)}</span>`;
    return `<div class="adv__basis">${esc(KIND[r.slot] || "К вашей покупке")}<span class="adv__src">${esc(node(src).name || src)}</span>${term}</div>`;
  }

  function renderBlock(client, personal, pops) {
    let prev = null;
    const rows = personal.map((r, k) => {
      const basis = basisOf(client, r, prev);
      prev = ((r.paths || [])[0] || {}).src || null;
      const n = node(r.family);
      const pr = price(n);
      const checks = (r.label ? [`<li>${esc(r.label)}</li>`] : []).concat((r.paths || []).slice(0, 3).map((p) => `<li>из покупки «${esc(node(p.src).name || p.src)}»: `
        + `${esc((E.TYPE_TITLE || {})[p.type] || p.type)}${p.source ? `, ${esc((E.LABEL || {})[p.source] || p.source)}` : ""}</li>`)).join("");
      return `<article class="adv__row">
        ${basis}
        <div class="adv__main">
          <div class="adv__name card__name">${esc(r.name)}</div>
          <div class="adv__vendor">${esc(r.vendor || "")}</div>
          <p class="adv__why">${esc(r.why)}</p>
          ${checks ? `<details><summary>Как проверено</summary><ul>${checks}</ul></details>` : ""}
        </div>
        <div class="adv__buy">
          ${pr ? `<div class="adv__price">${esc(pr)}</div>` : ""}
          ${invoice(r.slot, k + 1, r.name).replace('class="btn"', 'class="btn btn--fill"')}
          <a class="adv__more" href="${esc(shopUrl(r.url, r.slot, k + 1))}" target="_blank" rel="noopener">Подробнее о товаре</a>
        </div>
      </article>`;
    }).join("");
    $("#block").innerHTML = personal.length ? `<div class="adv">${rows}</div>`
      : `<div class="empty">${client.purchases.length ? "Личных советов по вашим покупкам пока нет: показываем только проверенное." : "Покупок пока нет, поэтому личных советов нет."}</div>`;
    $("#popular").innerHTML = !pops.length ? "" : `<section class="also"><h3 class="also__title">Часто докупают</h3>
        <p class="also__note">Это не личный совет, а то, что чаще всего берут клиенты вашего типа.</p>`
      + pops.map((r, k) => `<div class="also__row row"><div><b>${esc(r.name)}</b><span>${esc(r.why)}</span></div>
        <a class="btn" href="${esc(shopUrl(r.url, "popular", k + 1))}" target="_blank" rel="noopener">Открыть</a>
        ${invoice("popular", k + 1, r.name)}</div>`).join("") + "</section>";
  }
})();
