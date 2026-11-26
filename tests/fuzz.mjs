// Нагрузка и случайные входы для движка страницы (site/engine.js).
// 20 000 случайных клиентов, в том числе с испорченными данными: неизвестные товары, пустые и будущие
// даты, отрицательные сроки, сотни покупок. Движок не должен падать и не должен нарушать свои правила.
// Запуск:  node tests/fuzz.mjs [число клиентов]
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const { Graph, Recommender } = require("../site/engine.js");
const data = JSON.parse(readFileSync(new URL("../site/data/graph.json", import.meta.url), "utf8"));
const graph = new Graph(data);
const N = +(process.argv[2] || 20000);

let seed = 12345;                       // свой генератор: прогон повторяется один в один
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
const ids = data.nodes.map((n) => n.id);
const linked = Array.from(new Set(data.edges.map((e) => e[0])));
const DATES = ["2026-11-20", "2025-01-15", "2024-02-29", "2031-05-05", "1999-12-31", "", null, undefined, "вчера", "2026-13-45"];
const TERMS = [null, undefined, 0, 1, 12, 36, 120, -5, 1e9, "12", NaN];

function client() {
  const kind = rnd();
  const count = kind < 0.08 ? 0 : kind < 0.9 ? 1 + Math.floor(rnd() * 4) : kind < 0.99 ? 5 + Math.floor(rnd() * 20) : 200;
  const purchases = [];
  for (let i = 0; i < count; i++) {
    const r = rnd();
    const family = r < 0.55 ? pick(linked) : r < 0.97 ? pick(ids) : pick(["нет/такого", "", "a/b/c", "<script>"]);
    purchases.push({ family, date: rnd() < 0.85 ? `202${4 + Math.floor(rnd() * 3)}-${String(1 + Math.floor(rnd() * 12)).padStart(2, "0")}-15` : pick(DATES),
                     term: rnd() < 0.8 ? pick([null, 12, 12, 24, 36, 0]) : pick(TERMS) });
  }
  return { type: rnd() < 0.5 ? "b2b" : "b2c", today: rnd() < 0.95 ? "2026-11-26" : pick(["2024-01-01", "2030-01-01"]), purchases };
}

const SLOTS = new Set(["renew", "next", "missing", "replace", "popular"]);
const bad = {};
const note = (what, c, extra) => { (bad[what] = bad[what] || []).push(JSON.stringify({ c: { ...c, purchases: c.purchases.slice(0, 4) }, extra }).slice(0, 400)); };
const engines = [new Recommender(graph, { fill: true }), new Recommender(graph, { fill: false }), new Recommender(graph, { fill: true, minConfidence: 0.8 })];
let total = 0, withThree = 0, empty = 0, worst = 0, crashes = 0;
const t0 = process.hrtime.bigint();
for (let i = 0; i < N; i++) {
  const c = client();
  const rec = engines[i % engines.length];
  let res;
  const s = process.hrtime.bigint();
  try { res = rec.recommend(c, 3); } catch (e) { crashes++; note("падение: " + String(e.message).slice(0, 80), c); continue; }
  const ms = Number(process.hrtime.bigint() - s) / 1e6;
  if (ms > worst) worst = ms;
  total++;
  const recs = res.recs;
  if (recs.length === 3) withThree++;
  if (!recs.length) empty++;
  if (recs.length > 3) note("советов больше трёх", c, recs.length);
  const own = new Set(c.purchases.map((p) => p.family));
  const ownsB2bOnly = c.purchases.some((p) => (graph.nodes.get(p.family) || {}).b2b === 1);
  const seen = new Set();
  for (const r of recs) {
    if (seen.has(r.family)) note("повтор товара", c, r.family);
    seen.add(r.family);
    if (own.has(r.family) && r.slot !== "renew") note("совет из купленного", c, [r.family, r.slot]);
    if (!graph.available(r.family, c.type)) note("совет недоступен клиенту", c, r.family);
    const n = graph.nodes.get(r.family) || {};
    // частному лицу товар «только для организаций» можно предложить в одном случае: он продлевает или
    // развивает такой же товар, который у клиента уже есть
    if (c.type === "b2c" && n.b2b === 1 && !ownsB2bOnly) note("частному лицу — товар только для организаций", c, r.family);
    if (!SLOTS.has(r.slot)) note("неизвестный вид совета", c, r.slot);
    if (!r.why || r.why.length < 20 || /undefined|NaN|null|Invalid/.test(r.why)) note("плохое объяснение", c, r.why);
    if (!Array.isArray(r.paths)) note("нет путей", c, r.family);
    if (!(r.score >= 0 && r.score <= 1.5)) note("оценка вне шкалы", c, r.score);
  }
  if (i % engines.length === 0 && recs.length < 3) note("при заполнении меньше трёх советов", c, recs.length);
}
const sec = Number(process.hrtime.bigint() - t0) / 1e9;
console.log(`клиентов: ${N}, обработано: ${total}, падений: ${crashes}`);
console.log(`три совета: ${withThree}, ни одного: ${empty}`);
console.log(`скорость: ${Math.round(total / sec)} клиентов в секунду, в среднем ${(sec / total * 1000).toFixed(3)} мс, самый долгий ${worst.toFixed(1)} мс`);
const kinds = Object.keys(bad);
for (const k of kinds) { console.log(`\n${bad[k].length} × ${k}`); for (const x of bad[k].slice(0, 3)) console.log("   ", x); }
console.log(kinds.length ? "\nнарушения найдены" : "нарушений нет");
process.exit(kinds.length ? 1 : 0);
