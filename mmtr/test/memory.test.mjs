import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import MemorableMemory, { DAY, CHANNELS, DIMENSIONS, channelStrength } from '../index.mjs';

function fixture(t, options = {}) {
  let time = Date.UTC(2026, 0, 10, 12);
  const m = new MemorableMemory(':memory:', { ...options, clock: () => time });
  t.after(() => m.close());
  return { m, now: () => time, advance: days => { time += days * DAY; } };
}
const closeTo = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
const observation = (extra = {}) => ({ fact: '那天雨中在店里聊到十一点', gist: '一起包饺子', affect: '安心，亲切', entities: ['小雨'], themes: ['做饭'], ...extra });

test('independent half-lives and floors are exact above each floor', () => {
  for (const [key, policy] of Object.entries(CHANNELS)) {
    closeTo(channelStrength(key, 0), 1);
    closeTo(channelStrength(key, policy.halfLife), policy.floor + (1 - policy.floor) / 2);
    closeTo(channelStrength(key, 100_000), policy.floor);
  }
  assert.ok(channelStrength('fact', 30) < channelStrength('gist', 30));
  assert.ok(channelStrength('gist', 30) < channelStrength('affect', 30));
});

test('ordinary facts are physically compressed while emotional bias remains', t => {
  const { m, advance } = fixture(t);
  const id = m.remember(observation());
  advance(60);
  const first = m.consolidate();
  assert.ok(first.compressed >= 1);
  const memory = m.getMemory(id);
  assert.equal(memory.channels.find(c => c.channel === 'fact').content, '');
  assert.equal(memory.channels.find(c => c.channel === 'affect').content, '安心，亲切');
  assert.ok(memory.channels.find(c => c.channel === 'affect').strength > 0.55);
  assert.ok(!m.recall('包饺子').includes('十一点'));
  assert.equal(m.consolidate().compressed, 0);
});

test('vector input contains compact gist and metadata, never raw fact', t => {
  const inputs = [];
  const { m } = fixture(t, { embed: input => { inputs.push(input); return [1, 0]; } });
  m.remember(observation({ fact: 'RAW_TRANSCRIPT_98765' }));
  assert.ok(inputs[0].includes('包饺子'));
  assert.ok(!inputs[0].includes('RAW_TRANSCRIPT'));
});

test('graph recalls a zero-vector-overlap birthday agreement from dumplings', t => {
  const embed = input => input.includes('饺子') ? [1, 0] : [0, 1];
  const { m, advance } = fixture(t, { embed });
  const birthday = m.promise({ text: '明年生日来我家过', due: Date.UTC(2028, 0, 1), gist: '生日约定', entity: '小雨', anchors: ['06-18'] });
  advance(10);
  m.remember(observation());
  const result = m.recall('包饺子', { style: 'full' });
  const linked = result.items.find(x => x.id === birthday);
  assert.ok(linked);
  assert.ok(linked.reasons.includes('graph:entity'));
  assert.ok(result.context.includes('明年生日来我家过'));
  assert.equal(result.pipeline.length, 6);
});

test('all four graph edge types are created', t => {
  const { m } = fixture(t);
  const a = m.remember(observation({ anchors: ['06-18'] }));
  const b = m.remember(observation({ anchors: ['06-18'] }));
  assert.deepEqual(m.db.prepare('SELECT kind FROM edges WHERE a=? AND b=? ORDER BY kind').all(a, b).map(x => x.kind), ['calendar', 'entity', 'temporal', 'theme']);
});

test('calendar awakens unrelated memories and handles year boundaries and leap days', t => {
  const { m } = fixture(t, { embed: input => input === 'unrelated' ? [0, 1] : [1, 0] });
  const id = m.remember(observation({ anchors: ['01-11', '02-29'] }));
  const item = m.recall('unrelated', { style: 'full', hops: 0 }).items.find(x => x.id === id);
  assert.ok(item.reasons.includes('calendar'));
  closeTo(m._calendarDistance('01-01', Date.UTC(2026, 11, 31)), 1);
  closeTo(m._calendarDistance('02-29', Date.UTC(2024, 1, 28)), 1);
  assert.ok(m._calendarDistance('02-29', Date.UTC(2025, 1, 28)) > 3);
});

test('relationship has six dimensions, updates only at sleep and respects session cap', t => {
  const { m } = fixture(t);
  const initial = m.relationship('小雨');
  assert.deepEqual(Object.keys(initial), DIMENSIONS);
  for (let i = 0; i < 100; i++) m.touchSession('qq', { entity: '小雨', relationshipDelta: { trust: 0.1, warmth: 0.1 } });
  assert.deepEqual(m.relationship('小雨'), initial);
  m.endSession('qq');
  const after = m.relationship('小雨');
  closeTo(after.trust, initial.trust + 0.12 * (1 - initial.trust));
  assert.equal(m.endSession('qq'), null);
  assert.deepEqual(m.relationship('小雨'), after);
});

test('rate saturation slows intimacy; tension regresses faster; consolidation is idempotent', t => {
  const { m, advance } = fixture(t);
  const base = m.relationship();
  const increments = [];
  for (let i = 0; i < 5; i++) {
    const before = m.relationship().warmth;
    m.touchSession('qq', { relationshipDelta: { warmth: 1, tension: 1 } });
    m.endSession('qq'); increments.push(m.relationship().warmth - before);
  }
  assert.ok(increments[4] < increments[0]);
  const peak = m.relationship();
  advance(14); m.consolidate();
  const after = m.relationship();
  closeTo((after.tension - base.tension) / (peak.tension - base.tension), 0.25);
  assert.ok((after.warmth - base.warmth) / (peak.warmth - base.warmth) > 0.9);
  m.consolidate(); assert.deepEqual(m.relationship(), after);
});

test('promise clarity rises toward due date and due obligations surface without relevance', t => {
  const { m, advance, now } = fixture(t, { embed: input => input === '完全无关' ? [0, 1] : [1, 0] });
  const id = m.promise({ text: '下周五带你吃饺子', due: now() + 10 * DAY, windowMs: 3 * DAY });
  const p = m.getMemory(id).promise;
  const far = m._urgency(p, now()); advance(8);
  assert.ok(m._urgency(p, now()) > far);
  assert.ok(m.recall('完全无关').includes('下周五带你吃饺子'));
});

test('promise text is immutable, never compressed and broken penalty happens exactly once', t => {
  const { m, advance, now } = fixture(t);
  const id = m.promise({ text: '我答应周五带你去看海，七点到门口等你。', due: now() + DAY, graceMs: DAY });
  const before = m.relationship();
  assert.throws(() => m.db.prepare('UPDATE promises SET original=? WHERE memory_id=?').run('changed', id), /immutable/);
  advance(2); assert.equal(m.consolidate().broken, 0);
  advance(0.001); assert.equal(m.consolidate().broken, 1);
  const after = m.relationship();
  assert.ok(after.trust < before.trust && after.warmth < before.warmth && after.tension > before.tension);
  assert.equal(m.consolidate().broken, 0);
  assert.deepEqual(m.relationship(), after);
  assert.equal(m.keep(id), false);
  advance(1000); m.consolidate();
  const memory = m.getMemory(id);
  assert.equal(memory.promise.original, '我答应周五带你去看海，七点到门口等你。');
  assert.ok(memory.channels.every(c => !c.compressed));
});

test('keeping gives one modest reward; late keep cannot evade a broken promise', t => {
  const { m, now, advance } = fixture(t);
  const id = m.promise({ text: '一起去看展', due: now() + DAY });
  const before = m.relationship();
  assert.equal(m.keep(id), true);
  const after = m.relationship();
  assert.ok(after.trust > before.trust && after.trust - before.trust < 0.03);
  assert.equal(m.keep(id), false);
  assert.deepEqual(m.relationship(), after);
  const overdue = m.promise({ text: '昨天答应的事', due: now(), graceMs: 0 });
  advance(0.01); assert.equal(m.keep(overdue), false);
  assert.equal(m.getMemory(overdue).promise.status, 'broken');
});

test('core is permanent, always present, and rejects events and insufficient context budgets', t => {
  const { m, advance } = fixture(t);
  m.setCore('name', '他的名字是小雨');
  m.setCore('self', '我的名字是无名');
  assert.throws(() => m.setCore('event', '他今天问我记不记得'), /episodic/);
  assert.throws(() => m.setCore('event', '今天他问我记不记得', { kind: 'event' }), /core kind/);
  advance(2000); m.consolidate();
  const output = m.recall('unrelated');
  assert.ok(output.includes('他的名字是小雨') && output.includes('我的名字是无名'));
  assert.throws(() => m.recall('x', { budget: 1 }), /core_memory/);
});

test('diminishing recall boosts, next-round cooldown and immutable importance', t => {
  const { m } = fixture(t, { embed: () => [1] });
  const id = m.remember(observation({ importance: 0.72 }));
  const first = m.recall('dumplings', { style: 'full' });
  const b1 = m.db.prepare("SELECT boost FROM channels WHERE memory_id=? AND channel='fact'").get(id).boost;
  const second = m.recall('dumplings', { style: 'full' });
  const b2 = m.db.prepare("SELECT boost FROM channels WHERE memory_id=? AND channel='fact'").get(id).boost;
  assert.ok(b2 - b1 < b1);
  assert.ok(second.items[0].score < first.items[0].score);
  for (let i = 0; i < 50; i++) m.recall('dumplings');
  assert.equal(m.getMemory(id).importance, 0.72);
  assert.ok(m.db.prepare('SELECT MAX(boost) AS b FROM channels').get().b <= 0.15);
});

test('diversity favors different impressions and quiet output carries no engine metadata', t => {
  const { m } = fixture(t, { embed: input => input.includes('散步') ? [0.8, 0.6] : [1, 0] });
  m.remember(observation()); m.remember(observation());
  const different = m.remember(observation({ fact: '', gist: '一起散步', affect: '', entities: ['阿明'], themes: ['公园'] }));
  const result = m.recall('一起', { limit: 2, style: 'full' });
  assert.ok(result.items.some(x => x.id === different));
  assert.ok(!/score|importance|2026-|fact:|gist:|affect:/.test(m.recall('一起')));
});

test('budget skips oversized entries without hiding fitting ones; promises stay whole', t => {
  const { m, now } = fixture(t, { embed: () => [1] });
  m.remember(observation({ fact: 'x'.repeat(1000), gist: '', affect: '' }));
  const small = m.remember({ gist: '小小印象', importance: 0.2 });
  const long = m.promise({ text: '完整承诺'.repeat(100), due: now() });
  const result = m.recall('x', { budget: 30, style: 'full', limit: 2 });
  assert.ok(result.context.length <= 30);
  assert.ok(result.items.some(x => x.id === small));
  assert.ok(result.omittedReminders.includes(long));
  assert.ok(!result.context.includes('完整承诺'));
});

test('inactivity boundary sleeps before the next session and persists unfinished thoughts', t => {
  const { m, advance } = fixture(t, { sessionGapMs: DAY });
  const first = m.touchSession('qq', { summary: '谈到今年想学做饭', openLoops: ['下次问喜欢什么馅'], relationshipDelta: { warmth: 0.1 } });
  const before = m.relationship();
  advance(1); assert.equal(m.touchSession('qq'), first);
  advance(1.01);
  const second = m.touchSession('qq');
  assert.notEqual(second, first);
  assert.ok(m.relationship().warmth > before.warmth);
  assert.ok(m.db.prepare('SELECT ended FROM sessions WHERE id=?').get(first).ended);
  const summary = m.db.prepare("SELECT id FROM memories WHERE kind='summary'").get();
  assert.ok(m.getMemory(summary.id).channels.some(c => c.content.includes('下次问喜欢什么馅')));
  assert.equal(m.db.prepare('SELECT summary FROM sessions WHERE id=?').get(first).summary, '');
});

test('a single SQLite file survives reopening, preserves session and deduplicates external IDs', t => {
  const dir = mkdtempSync(join(tmpdir(), 'mmtr-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'memory.db');
  let now = Date.UTC(2026, 0, 1);
  let m = new MemorableMemory(path, { clock: () => now });
  const id = m.remember(observation({ externalId: 'event:7' }));
  const session = m.touchSession('qq', { relationshipDelta: { warmth: 0.1 } });
  m.setCore('name', '他的名字是小雨'); m.close();
  assert.deepEqual(readdirSync(dir), ['memory.db']);
  m = new MemorableMemory(path, { clock: () => now });
  assert.equal(m.touchSession('qq'), session);
  assert.equal(m.remember(observation({ externalId: 'event:7' })), id);
  assert.equal(m.getMemory(id).channels.length, 3);
  now += DAY; assert.notEqual(m.touchSession('qq'), session);
  assert.ok(m.relationship().warmth > 0.5);
  assert.equal(m.health(), true); m.close();
});

test('all operations isolate scopes, including promises, core, relations and graph', t => {
  const { m, now } = fixture(t);
  const id = m.remember(observation({ scope: 'alice', externalId: 'same' }));
  m.setCore('name', '他叫 Alice', { scope: 'alice' });
  const promise = m.promise({ scope: 'alice', text: '私人的约定', due: now() });
  m.remember(observation({ scope: 'bob', externalId: 'same' }));
  assert.equal(m.getMemory(id, { scope: 'bob' }), null);
  assert.equal(m.keep(promise, { scope: 'bob' }), false);
  assert.equal(m.deleteMemory(id, { scope: 'bob' }), false);
  assert.ok(!m.recall('包饺子', { scope: 'bob' }).includes('私人的约定'));
  assert.deepEqual(m.coreMemory({ scope: 'bob' }), []);
  assert.equal(m.db.prepare('SELECT count(*) AS n FROM edges e JOIN memories a ON a.id=e.a JOIN memories b ON b.id=e.b WHERE a.scope<>b.scope').get().n, 0);
});

test('bad writes roll back and SQL-like text remains data', t => {
  const { m, now } = fixture(t);
  assert.throws(() => m.remember(observation({ anchors: ['02-30'] })), /invalid/);
  assert.throws(() => m.remember(observation({ importance: NaN })), /finite/);
  assert.throws(() => m.remember(observation({ at: now() + DAY })), /future/);
  assert.equal(m.db.prepare('SELECT count(*) AS n FROM memories').get().n, 0);
  assert.throws(() => m.promise({ text: 'x', due: 'not-a-date' }), /finite/);
  assert.throws(() => m.touchSession('qq', { relationshipDelta: { trust: NaN } }), /finite/);
  const id = m.remember({ fact: "'); DROP TABLE memories; --", gist: '普通印象' });
  assert.ok(m.getMemory(id)); assert.equal(m.health(), true);
});

test('bare text has a conservative extractor and semantic extraction is injectable', t => {
  const { m } = fixture(t);
  const id = m.remember({ text: '一起包饺子。说了很多细节' });
  const channels = m.getMemory(id).channels;
  assert.equal(channels.find(c => c.channel === 'gist').content, '一起包饺子');
  assert.equal(channels.find(c => c.channel === 'affect').content, '');
  assert.ok(m.recall('包饺子').includes('一起包饺子'));
  const semantic = new MemorableMemory(':memory:', { extract: () => observation() });
  t.after(() => semantic.close());
  assert.equal(semantic.getMemory(semantic.remember({ text: 'original input' })).channels.find(c => c.channel === 'affect').content, '安心，亲切');
});

test('graph hop limit is enforced and expansion can traverse two different entities', t => {
  const { m, advance } = fixture(t, { embed: input => input.includes('入口') ? [1, 0] : [0, 1] });
  const first = m.remember({ gist: '入口', entities: ['a'] });
  advance(1);
  m.remember({ gist: '中间桥梁', entities: ['a', 'b'] });
  advance(1);
  const last = m.remember({ gist: '联想到另一件事', entities: ['b'] });
  assert.ok(m.recall('入口', { hops: 0, style: 'full' }).items.every(x => x.id === first));
  assert.ok(!m.recall('入口', { hops: 1, style: 'full' }).items.some(x => x.id === last));
  assert.ok(m.recall('入口', { hops: 2, style: 'full' }).items.some(x => x.id === last));
});

test('duplicate prose is pruned and omitted memories do not receive a boost', t => {
  const { m } = fixture(t, { embed: () => [1] });
  const ids = [m.remember({ gist: '同一个印象' }), m.remember({ gist: '同一个印象' })];
  const result = m.recall('x', { style: 'full' });
  assert.equal(result.items.length, 1);
  const recalls = ids.map(id => m.getMemory(id).recalls).sort();
  assert.deepEqual(recalls, [0, 1]);
});

test('edge growth is bounded and calendar health repairs invalid legacy anchors', t => {
  const { m } = fixture(t);
  let id;
  for (let i = 0; i < 80; i++) id = m.remember(observation({ anchors: ['06-18'] }));
  assert.ok(m.db.prepare('SELECT COUNT(*) AS n FROM edges').get().n <= 80 * 64);
  const before = m.db.prepare('SELECT SUM(weight) AS n FROM edges').get().n;
  m.db.prepare('UPDATE memories SET anchors=? WHERE id=?').run('["02-30","06-18"]', id);
  assert.equal(m.consolidate().invalidAnchors, 1);
  assert.deepEqual(m.getMemory(id).anchors, ['06-18']);
  closeTo(m.db.prepare('SELECT SUM(weight) AS n FROM edges').get().n, before);
});

test('stdio bridge reports per-request errors and continues serving', () => {
  const result = spawnSync(process.execPath, [new URL('../bridge.mjs', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')], {
    input: '{"id":1,"method":"notAllowed","args":[]}\n{"id":2,"method":"health","args":[]}\n', encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  const output = result.stdout.trim().split('\n').map(JSON.parse);
  assert.equal(output[0].ok, false); assert.equal(output[1].result, true);
});
