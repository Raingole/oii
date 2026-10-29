import MemorableMemory from '../index.mjs';
import { performance } from 'node:perf_hooks';

const count = Number(process.argv[2] || 1000);
if (!Number.isSafeInteger(count) || count < 1 || count > 20_000) throw new RangeError('count must be 1..20000');
const m = new MemorableMemory(':memory:');
const start = performance.now();
for (let i = 0; i < count; i++) m.remember({
  fact: `第 ${i} 次在雨天一起吃饭，具体聊了第 ${i} 号话题。`,
  gist: `一起包饺子 ${i % 30}`, affect: '轻松',
  entities: [`朋友${i % 30}`], themes: [`生活${i % 12}`],
});
const writeMs = performance.now() - start;
const times = [];
for (let i = 0; i < 10; i++) {
  const at = performance.now(); m.recall(`包饺子 ${i}`); times.push(performance.now() - at);
}
times.sort((a, b) => a - b);
console.log(JSON.stringify({ node: process.version, memories: count,
  edges: m.db.prepare('SELECT count(*) AS n FROM edges').get().n,
  writeMs: Math.round(writeMs), recallMedianMs: Math.round(times[5]), recallMaxMs: Math.round(times[9]),
  database: 'in-memory; includes SQLite transactions and recall mutations',
}, null, 2));
m.close();
