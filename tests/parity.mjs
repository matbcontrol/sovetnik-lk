// Проверка: движок страницы (site/engine.js) отвечает так же, как движок на Python.
// Запуск:  python -m recsys.export  &&  node tests/parity.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const { Graph, Recommender } = require("../site/engine.js");

const graph = new Graph(JSON.parse(readFileSync(new URL("../site/data/graph.json", import.meta.url), "utf8")));
const cases = JSON.parse(readFileSync(new URL("../site/data/parity.json", import.meta.url), "utf8"));
// половина контрольных клиентов считается «глазами менеджера» — с непроверенными связями по классу
const engines = { false: new Recommender(graph, { fill: true }), true: new Recommender(graph, { fill: true, unverified: true }) };

let bad = 0, withAdvice = 0;
for (const [i, c] of cases.entries()) {
  const got = engines[!!c.unverified].recommend(c.client, 3).recs;
  if (c.expect.length) withAdvice++;
  const same = got.length === c.expect.length && got.every((r, j) => {
    const e = c.expect[j];
    return r.family === e.family && r.slot === e.slot && Math.abs(r.score - e.score) < 2e-4
      && r.why === e.why && r.alsoFamily === e.also;
  });
  if (!same) {
    bad++;
    if (bad <= 5) {
      console.log(`расхождение на клиенте ${i}:`, JSON.stringify(c.client));
      console.log("  ожидалось:", JSON.stringify(c.expect.map((e) => [e.family, e.slot, e.score, e.why])));
      console.log("  получено: ", JSON.stringify(got.map((r) => [r.family, r.slot, r.score, r.why])));
    }
  }
}
console.log(`контрольных клиентов: ${cases.length}, с советами: ${withAdvice}, расхождений: ${bad}`);
process.exit(bad ? 1 : 0);
