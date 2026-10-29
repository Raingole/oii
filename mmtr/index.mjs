import { DatabaseSync } from 'node:sqlite';
import { createHash, randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export const DAY = 86_400_000;
export const CHANNELS = Object.freeze({
  fact: Object.freeze({ halfLife: 4, floor: 0.02 }),
  gist: Object.freeze({ halfLife: 12, floor: 0.15 }),
  affect: Object.freeze({ halfLife: 180, floor: 0.55 }),
});
export const DIMENSIONS = Object.freeze(['closeness', 'trust', 'playfulness', 'warmth', 'tension', 'familiarity']);
const BASELINE = Object.freeze({ closeness: 0.2, trust: 0.5, playfulness: 0.2, warmth: 0.5, tension: 0.1, familiarity: 0.1 });
const parse = JSON.parse;
const json = JSON.stringify;
const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));
const unique = values => [...new Set(values)];

function number(value, name, lo = 0, hi = Infinity) {
  if (!Number.isFinite(value) || value < lo || value > hi) throw new TypeError(`${name} must be a finite number in [${lo}, ${hi}]`);
  return value;
}
function text(value, name, max = 100_000) {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new TypeError(`${name} must be nonempty text (max ${max})`);
  return value.trim();
}
function instant(value) {
  const result = typeof value === 'number' ? value : Date.parse(value);
  return number(result, 'time', 0);
}
function tags(values = []) {
  if (!Array.isArray(values) || values.length > 64) throw new TypeError('tags must be arrays of at most 64 items');
  return unique(values.map(x => text(x, 'tag', 200).toLowerCase()));
}
function anchors(values = []) {
  return tags(values).map(value => {
    if (!/^\d{2}-\d{2}$/.test(value)) throw new TypeError('calendar anchors must be MM-DD');
    const date = new Date(`2000-${value}T00:00:00Z`);
    if (!Number.isFinite(+date) || date.toISOString().slice(5, 10) !== value) throw new TypeError('invalid calendar anchor');
    return value;
  });
}
function validateVector(vector) {
  if (!Array.isArray(vector) || !vector.length || vector.length > 8192 || vector.some(x => !Number.isFinite(x))) throw new TypeError('embedding must be a finite, nonempty number array');
  const norm = Math.hypot(...vector);
  return vector.map(x => norm ? x / norm : 0);
}
/** Deterministic lexical fallback, including Chinese bigrams; not a semantic model. */
export function localEmbedding(input, dimensions = 192) {
  number(dimensions, 'dimensions', 1, 8192);
  const v = Array(Math.floor(dimensions)).fill(0);
  const tokens = input.toLowerCase().match(/[a-z0-9_]+|[\p{Script=Han}]/gu) || [];
  const features = [...tokens];
  for (let i = 1; i < tokens.length; i++) features.push(tokens[i - 1] + tokens[i]);
  for (const token of features) {
    const hash = createHash('sha256').update(token).digest();
    v[hash.readUInt32LE() % v.length] += (hash[4] & 1) ? 1 : -1;
  }
  return validateVector(v);
}
const cosine = (a, b) => a.length === b.length ? a.reduce((s, x, i) => s + x * b[i], 0) : 0;

/** Half-life applies to strength above the channel's floor, including recall boosts. */
export function channelStrength(channel, elapsedDays, boost = 0) {
  const policy = CHANNELS[channel];
  if (!policy) throw new TypeError('unknown memory channel');
  return policy.floor + (1 - policy.floor) * Math.pow(2, -Math.max(0, elapsedDays) / policy.halfLife) + Math.max(0, boost);
}

/** Conservative fallback; a host can supply a synchronous semantic extractor. */
export function extractExperience(value) {
  const fact = text(value, 'experience');
  return { fact, gist: fact.split(/[。！？\n.!?]/u)[0].slice(0, 160), affect: '' };
}

export class MemorableMemory {
  constructor(path = ':memory:', options = {}) {
    this.clock = options.clock || Date.now;
    this.embed = options.embed || localEmbedding;
    this.extract = options.extract || extractExperience;
    this.sessionGap = number(options.sessionGapMs ?? 30 * 60_000, 'sessionGapMs', 1);
    this.sessionCap = number(options.sessionCap ?? 0.12, 'sessionCap', 0, 1);
    this.relationHalfLife = number(options.relationshipHalfLifeDays ?? 120, 'relationshipHalfLifeDays', 0.001);
    this.tensionHalfLife = number(options.tensionHalfLifeDays ?? 7, 'tensionHalfLifeDays', 0.001);
    this.baseline = { ...BASELINE, ...options.baseline };
    for (const key of DIMENSIONS) number(this.baseline[key], key, 0, 1);
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    // DELETE journaling leaves a single durable file after each transaction.
    this.db.exec(`PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA journal_mode=DELETE;
      CREATE TABLE IF NOT EXISTS mmtr_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS memories(
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
        created REAL NOT NULL, importance REAL NOT NULL, entities TEXT NOT NULL,
        themes TEXT NOT NULL, anchors TEXT NOT NULL, embedding TEXT NOT NULL,
        recalls INTEGER NOT NULL DEFAULT 0, last_round INTEGER NOT NULL DEFAULT -100,
        external_id TEXT, UNIQUE(scope,external_id));
      CREATE INDEX IF NOT EXISTS mmtr_memory_scope ON memories(scope,created);
      CREATE TABLE IF NOT EXISTS memory_tags(
        memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(memory_id,kind,tag));
      CREATE INDEX IF NOT EXISTS mmtr_tag_lookup ON memory_tags(kind,tag,memory_id);
      CREATE TABLE IF NOT EXISTS channels(
        memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        channel TEXT NOT NULL, content TEXT NOT NULL, boost REAL NOT NULL DEFAULT 0,
        boost_at REAL NOT NULL, compressed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(memory_id,channel));
      CREATE TABLE IF NOT EXISTS edges(
        a TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        b TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, weight REAL NOT NULL, updated REAL NOT NULL,
        PRIMARY KEY(a,b,kind));
      CREATE INDEX IF NOT EXISTS mmtr_edges_b ON edges(b);
      CREATE TABLE IF NOT EXISTS promises(
        memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
        original TEXT NOT NULL, due REAL NOT NULL, grace REAL NOT NULL,
        window REAL NOT NULL, entity TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', resolved REAL);
      CREATE TRIGGER IF NOT EXISTS mmtr_promise_immutable BEFORE UPDATE OF original ON promises
        BEGIN SELECT RAISE(ABORT,'promise original is immutable'); END;
      CREATE TABLE IF NOT EXISTS core_memory(
        scope TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
        PRIMARY KEY(scope,key));
      CREATE TABLE IF NOT EXISTS relationships(
        scope TEXT NOT NULL, entity TEXT NOT NULL, value TEXT NOT NULL,
        baseline TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(scope,entity));
      CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, stream TEXT NOT NULL,
        started REAL NOT NULL, last REAL NOT NULL, ended REAL,
        pending TEXT NOT NULL DEFAULT '{}', summary TEXT NOT NULL DEFAULT '',
        open_loops TEXT NOT NULL DEFAULT '[]');
      CREATE UNIQUE INDEX IF NOT EXISTS mmtr_one_session ON sessions(scope,stream) WHERE ended IS NULL;
      CREATE TABLE IF NOT EXISTS recall_state(scope TEXT PRIMARY KEY, round INTEGER NOT NULL);
    `);
    const version = this.db.prepare('SELECT value FROM mmtr_meta WHERE key=?').get('version');
    if (version && version.value !== '1') { this.db.close(); throw new Error('unsupported MMTR schema version'); }
    this.db.prepare('INSERT OR IGNORE INTO mmtr_meta VALUES (?,?)').run('version', '1');
  }

  _now() { return instant(this.clock()); }
  _scope(scope) { return text(scope ?? 'default', 'scope', 500); }
  _transaction(fn) {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = fn(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  _vector(value) { return validateVector(this.embed(value)); }
  _embeddingText(channels, entities, themes) {
    // Never embed the raw fact/transcript; gist is the caller's compact abstraction.
    return [channels.gist || '', ...entities, ...themes].join(' ').slice(0, 2000);
  }
  _row(id, scope) { return this.db.prepare('SELECT * FROM memories WHERE id=? AND scope=?').get(id, scope); }
  _channels(id, now, created, stored) {
    return (stored || this.db.prepare('SELECT * FROM channels WHERE memory_id=?').all(id)).map(row => ({
      channel: row.channel, content: row.content, compressed: !!row.compressed,
      strength: clamp(channelStrength(row.channel, (now - created) / DAY,
        row.boost * Math.pow(2, -Math.max(0, now - row.boost_at) / (DAY * CHANNELS[row.channel].halfLife)))),
    }));
  }
  _insert(input, scope, now, kind = 'episode') {
    const created = input.at === undefined ? now : instant(input.at);
    if (created > now) throw new TypeError('experience cannot occur in the future');
    const external = input.externalId === undefined ? null : text(input.externalId, 'externalId', 1000);
    if (external) {
      const old = this.db.prepare('SELECT id FROM memories WHERE scope=? AND external_id=?').get(scope, external);
      if (old) return old.id;
    }
    const channels = { fact: input.fact ?? input.text ?? '', gist: input.gist ?? '', affect: input.affect ?? '' };
    for (const key of Object.keys(channels)) {
      if (typeof channels[key] !== 'string' || channels[key].length > 100_000) throw new TypeError(`${key} must be text (max 100000)`);
    }
    if (!Object.values(channels).some(x => x.trim())) throw new TypeError('an experience needs at least one channel');
    const entities = tags(input.entities), themes = tags(input.themes), calendar = anchors(input.anchors);
    const importance = number(input.importance ?? 0.5, 'importance', 0, 1);
    const vector = this._vector(this._embeddingText(channels, entities, themes));
    const id = randomUUID();
    this.db.prepare('INSERT INTO memories(id,scope,kind,created,importance,entities,themes,anchors,embedding,external_id) VALUES (?,?,?,?,?,?,?,?,?,?)')
      .run(id, scope, kind, created, importance, json(entities), json(themes), json(calendar), json(vector), external);
    for (const [key, content] of Object.entries(channels)) this.db.prepare('INSERT INTO channels(memory_id,channel,content,boost_at) VALUES (?,?,?,?)').run(id, key, content, now);
    // At most 16 new neighbours per edge type. Shared users/nearby turns must
    // not create a quadratic clique as the memory grows.
    for (const [edgeKind, values, weight] of [['entity', entities, 1], ['calendar', calendar, 0.85], ['theme', themes, 0.7]]) {
      for (const tag of values) this.db.prepare('INSERT INTO memory_tags VALUES (?,?,?)').run(id, edgeKind, tag);
      if (!values.length) continue;
      const placeholders = values.map(() => '?').join(',');
      const neighbours = this.db.prepare(`SELECT DISTINCT m.id,m.created FROM memory_tags t JOIN memories m ON m.id=t.memory_id
        WHERE m.scope=? AND m.id<>? AND t.kind=? AND t.tag IN (${placeholders}) ORDER BY m.created DESC,m.id LIMIT 16`)
        .all(scope, id, edgeKind, ...values);
      for (const other of neighbours) this.db.prepare('INSERT INTO edges VALUES (?,?,?,?,?)').run(other.id, id, edgeKind, weight, now);
    }
    const neighbours = this.db.prepare('SELECT id FROM memories WHERE scope=? AND id<>? AND created BETWEEN ? AND ? ORDER BY ABS(created-?),id LIMIT 16')
      .all(scope, id, created - 2 * 60 * 60_000, created + 2 * 60 * 60_000, created);
    for (const other of neighbours) this.db.prepare('INSERT INTO edges VALUES (?,?,?,?,?)').run(other.id, id, 'temporal', 0.35, now);
    return id;
  }

  remember(input) {
    if (input.text && input.fact === undefined && input.gist === undefined && input.affect === undefined) {
      const extracted = this.extract(input.text);
      if (!extracted || typeof extracted !== 'object' || typeof extracted.then === 'function') throw new TypeError('extract must synchronously return an experience object');
      input = { ...extracted, ...input };
    }
    const scope = this._scope(input.scope), now = this._now();
    return this._transaction(() => {
      if (input.stream) this._touchSession(scope, text(input.stream, 'stream', 500), now);
      return this._insert(input, scope, now);
    });
  }
  getMemory(id, { scope = 'default' } = {}) {
    scope = this._scope(scope);
    const row = this._row(id, scope);
    if (!row) return null;
    return { ...row, entities: parse(row.entities), themes: parse(row.themes), anchors: parse(row.anchors),
      embedding: parse(row.embedding), channels: this._channels(id, this._now(), row.created),
      promise: this.db.prepare('SELECT * FROM promises WHERE memory_id=?').get(id) || null };
  }
  deleteMemory(id, { scope = 'default' } = {}) {
    return !!this.db.prepare('DELETE FROM memories WHERE id=? AND scope=?').run(id, this._scope(scope)).changes;
  }

  setCore(key, content, { scope = 'default', kind = 'identity' } = {}) {
    scope = this._scope(scope); key = text(key, 'core key', 200); content = text(content, 'core content', 4000);
    if (!['identity', 'preference', 'agreement'].includes(kind)) throw new TypeError('core kind must be identity, preference or agreement');
    // Deliberately conservative. Core facts require an explicit API call, never automatic promotion.
    if (/(今天|昨天|刚才|这次|本次|问我|记不记得|today|yesterday|just now|asked me|do you remember)/i.test(content)) throw new TypeError('episodic events do not belong in core_memory');
    this._transaction(() => {
      const rows = this.coreMemory({ scope }).filter(x => x.key !== key);
      if (rows.reduce((s, x) => s + x.content.length, content.length) + rows.length > 4000) throw new RangeError('core_memory exceeds 4000 character budget');
      this.db.prepare('INSERT INTO core_memory VALUES (?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET kind=excluded.kind,content=excluded.content').run(scope, key, kind, content);
    });
  }
  coreMemory({ scope = 'default' } = {}) {
    return this.db.prepare('SELECT key,kind,content FROM core_memory WHERE scope=? ORDER BY key').all(this._scope(scope));
  }
  deleteCore(key, { scope = 'default' } = {}) {
    return !!this.db.prepare('DELETE FROM core_memory WHERE scope=? AND key=?').run(this._scope(scope), key).changes;
  }

  promise(input) {
    const scope = this._scope(input.scope), now = this._now();
    const original = text(input.text, 'promise text');
    const due = instant(input.due), grace = number(input.graceMs ?? DAY, 'graceMs');
    const window = number(input.windowMs ?? 7 * DAY, 'windowMs', 1);
    const entity = text(input.entity ?? 'user', 'entity', 200).toLowerCase();
    return this._transaction(() => {
      const id = this._insert({ ...input, text: original, fact: original, entities: unique([...(input.entities || []), entity]) }, scope, now, 'promise');
      const row = this._row(id, scope);
      if (row.kind !== 'promise') throw new TypeError('externalId already belongs to an ordinary memory');
      this.db.prepare('INSERT OR IGNORE INTO promises(memory_id,original,due,grace,window,entity) VALUES (?,?,?,?,?,?)').run(id, original, due, grace, window, entity);
      return id;
    });
  }
  _urgency(promise, now) {
    return promise.status === 'pending' ? 0.2 + 0.8 * Math.exp(-Math.max(0, promise.due - now) / promise.window) : 0;
  }
  _relationship(scope, entity, now) {
    const row = this.db.prepare('SELECT * FROM relationships WHERE scope=? AND entity=?').get(scope, entity);
    const baseline = row ? parse(row.baseline) : this.baseline;
    const value = row ? parse(row.value) : { ...baseline };
    if (row) for (const key of DIMENSIONS) value[key] = baseline[key] + (value[key] - baseline[key]) * Math.pow(2, -Math.max(0, now - row.updated) / (DAY * (key === 'tension' ? this.tensionHalfLife : this.relationHalfLife)));
    return { value, baseline };
  }
  relationship(entity = 'user', { scope = 'default' } = {}) {
    return this._relationship(this._scope(scope), text(entity, 'entity', 200).toLowerCase(), this._now()).value;
  }
  _applyRelationship(scope, entity, delta, now) {
    const { value, baseline } = this._relationship(scope, entity, now);
    for (const key of DIMENSIONS) {
      const change = clamp(delta[key] || 0, -this.sessionCap, this.sessionCap);
      value[key] = clamp(value[key] + change * (change >= 0 ? 1 - value[key] : value[key]));
    }
    this.db.prepare('INSERT INTO relationships VALUES (?,?,?,?,?) ON CONFLICT(scope,entity) DO UPDATE SET value=excluded.value,updated=excluded.updated')
      .run(scope, entity, json(value), json(baseline), now);
  }
  _expirePromises(scope, now) {
    const due = this.db.prepare(`SELECT p.* FROM promises p JOIN memories m ON m.id=p.memory_id
      WHERE m.scope=? AND p.status='pending' AND p.due+p.grace<?`).all(scope, now);
    for (const promise of due) {
      this.db.prepare("UPDATE promises SET status='broken',resolved=? WHERE memory_id=? AND status='pending'").run(now, promise.memory_id);
      this._applyRelationship(scope, promise.entity, { trust: -0.1, warmth: -0.06, tension: 0.1 }, now);
    }
    return due.length;
  }
  keep(id, { scope = 'default' } = {}) {
    scope = this._scope(scope); const now = this._now();
    return this._transaction(() => {
      this._expirePromises(scope, now);
      if (!this._row(id, scope)) return false;
      const p = this.db.prepare('SELECT * FROM promises WHERE memory_id=?').get(id);
      if (!p || p.status !== 'pending') return false;
      this.db.prepare("UPDATE promises SET status='kept',resolved=? WHERE memory_id=?").run(now, id);
      this._applyRelationship(scope, p.entity, { trust: 0.03, warmth: 0.02, tension: -0.02 }, now);
      return true;
    });
  }

  _touchSession(scope, stream, now) {
    let row = this.db.prepare('SELECT * FROM sessions WHERE scope=? AND stream=? AND ended IS NULL').get(scope, stream);
    if (row && now - row.last > this.sessionGap) { this._endSession(row, row.last); row = null; }
    if (!row) {
      const id = randomUUID();
      this.db.prepare('INSERT INTO sessions(id,scope,stream,started,last) VALUES (?,?,?,?,?)').run(id, scope, stream, now, now);
      row = this.db.prepare('SELECT * FROM sessions WHERE id=?').get(id);
    }
    this.db.prepare('UPDATE sessions SET last=? WHERE id=?').run(now, row.id);
    return row.id;
  }
  touchSession(stream = 'default', { scope = 'default', relationshipDelta = {}, entity = 'user', summary, openLoops } = {}) {
    scope = this._scope(scope); stream = text(stream, 'stream', 500); entity = text(entity, 'entity', 200).toLowerCase();
    for (const [key, value] of Object.entries(relationshipDelta)) {
      if (!DIMENSIONS.includes(key)) throw new TypeError(`unknown relationship dimension: ${key}`);
      number(value, key, -1, 1);
    }
    if (summary !== undefined && (typeof summary !== 'string' || summary.length > 4000)) throw new TypeError('summary must be text (max 4000)');
    if (openLoops !== undefined && (!Array.isArray(openLoops) || openLoops.length > 20 || openLoops.some(x => typeof x !== 'string' || x.length > 500))) throw new TypeError('openLoops must be up to 20 short strings');
    return this._transaction(() => {
      const id = this._touchSession(scope, stream, this._now());
      const row = this.db.prepare('SELECT * FROM sessions WHERE id=?').get(id);
      const pending = parse(row.pending); pending[entity] ||= {};
      for (const [key, value] of Object.entries(relationshipDelta)) pending[entity][key] = clamp((pending[entity][key] || 0) + value, -this.sessionCap, this.sessionCap);
      this.db.prepare('UPDATE sessions SET pending=?,summary=?,open_loops=? WHERE id=?')
        .run(json(pending), summary ?? row.summary, openLoops === undefined ? row.open_loops : json(openLoops), id);
      return id;
    });
  }
  _endSession(row, now) {
    for (const [entity, delta] of Object.entries(parse(row.pending))) this._applyRelationship(row.scope, entity, delta, now);
    const loops = parse(row.open_loops);
    const summary = [row.summary, ...loops.map(x => `尚未说完：${x}`)].filter(Boolean).join('；');
    if (summary) this._insert({ gist: summary, themes: ['session-summary'], externalId: `sleep:${row.id}` }, row.scope, now, 'summary');
    this.db.prepare("UPDATE sessions SET ended=?,pending='{}',summary='',open_loops='[]' WHERE id=?").run(now, row.id);
    return { sessionId: row.id, summary, openLoops: loops };
  }
  endSession(stream = 'default', { scope = 'default' } = {}) {
    scope = this._scope(scope);
    return this._transaction(() => {
      const row = this.db.prepare('SELECT * FROM sessions WHERE scope=? AND stream=? AND ended IS NULL').get(scope, stream);
      return row ? this._endSession(row, this._now()) : null;
    });
  }

  _calendarDistance(anchor, now) {
    const today = new Date(now), year = today.getUTCFullYear();
    const midnight = Date.UTC(year, today.getUTCMonth(), today.getUTCDate());
    return Math.min(...[year - 1, year, year + 1].map(y => {
      const date = new Date(`${y}-${anchor}T00:00:00Z`);
      return date.toISOString().slice(5, 10) === anchor ? Math.abs(+date - midnight) / DAY : Infinity;
    }));
  }
  _render(row, now, channels, promise) {
    if (promise) return `${promise.status === 'pending' ? '还欠着：' : promise.status === 'kept' ? '已经兑现：' : '曾经失约：'}${promise.original}`;
    return channels
      .filter(c => c.content && (c.channel !== 'fact' || c.strength >= 0.12))
      .map(c => c.content).filter((v, i, arr) => arr.indexOf(v) === i).join('；');
  }

  recall(query, options = {}) {
    if (typeof query !== 'string' || query.length > 100_000) throw new TypeError('query must be text');
    const scope = this._scope(options.scope), now = this._now();
    const budget = Math.floor(number(options.budget ?? 1200, 'budget', 0, 100_000));
    const limit = Math.floor(number(options.limit ?? 8, 'limit', 0, 100));
    const hops = Math.floor(number(options.hops ?? 2, 'hops', 0, 5));
    const style = options.style ?? 'quiet';
    if (!['quiet', 'full'].includes(style)) throw new TypeError('style must be quiet or full');
    const vector = this._vector(query);
    const core = this.coreMemory({ scope });
    const coreText = core.map(x => x.content).join('\n');
    if (coreText.length > budget) throw new RangeError('context budget cannot fit all core_memory; increase budget');
    return this._transaction(() => {
      if (options.stream) this._touchSession(scope, text(options.stream, 'stream', 500), now);
      this._expirePromises(scope, now);
      const state = this.db.prepare('SELECT round FROM recall_state WHERE scope=?').get(scope);
      const round = (state?.round ?? 0) + 1;
      const rows = this.db.prepare('SELECT * FROM memories WHERE scope=?').all(scope);
      const byId = new Map(rows.map(row => [row.id, row]));
      const promises = new Map(this.db.prepare('SELECT p.* FROM promises p JOIN memories m ON m.id=p.memory_id WHERE m.scope=?').all(scope).map(p => [p.memory_id, p]));
      const storedChannels = new Map();
      for (const channel of this.db.prepare('SELECT c.* FROM channels c JOIN memories m ON m.id=c.memory_id WHERE m.scope=?').all(scope)) {
        if (!storedChannels.has(channel.memory_id)) storedChannels.set(channel.memory_id, []);
        storedChannels.get(channel.memory_id).push(channel);
      }
      const channelCache = new Map();
      const channelsFor = id => {
        if (!channelCache.has(id)) channelCache.set(id, this._channels(id, now, byId.get(id).created, storedChannels.get(id)));
        return channelCache.get(id);
      };
      // 1. Vector entry: compact gist/entity/theme vectors only.
      const seeds = rows.map(row => [row.id, Math.max(0, cosine(vector, parse(row.embedding)))])
        .filter(([, score]) => score >= 0.08).sort((a, b) => b[1] - a[1]).slice(0, 24);
      const candidates = new Map(seeds);
      const reasons = new Map(seeds.map(([id]) => [id, ['vector']]));
      // 2. Bounded graph spreading. A memory can lead to something with no query overlap.
      let frontier = seeds;
      for (let depth = 0; depth < hops; depth++) {
        if (!frontier.length) break;
        const ids = frontier.map(([id]) => id), placeholders = ids.map(() => '?').join(',');
        const edges = this.db.prepare(`SELECT * FROM edges WHERE a IN (${placeholders}) OR b IN (${placeholders})`).all(...ids, ...ids);
        const next = new Map();
        const scores = new Map(frontier);
        for (const edge of edges) for (const [source, target] of [[edge.a, edge.b], [edge.b, edge.a]]) {
          const score = scores.get(source);
          if (score === undefined || !byId.has(target)) continue;
          const weight = edge.weight * Math.pow(2, -Math.max(0, now - edge.updated) / (90 * DAY));
          const spread = score * weight * 0.65;
          if (spread > (candidates.get(target) ?? 0) && spread >= 0.02) {
            candidates.set(target, spread); next.set(target, spread);
            reasons.set(target, unique([...(reasons.get(target) || []), `graph:${edge.kind}`]));
          }
        }
        frontier = [...next].sort((a, b) => b[1] - a[1]).slice(0, 64);
      }
      // 3. Calendar awakening and prospective obligations bypass query relevance.
      const reminders = new Set();
      for (const row of rows) {
        const distance = Math.min(...parse(row.anchors).map(anchor => this._calendarDistance(anchor, now)));
        if (distance <= 3) {
          candidates.set(row.id, Math.max(candidates.get(row.id) || 0, 0.7 * (1 - distance / 4)));
          reasons.set(row.id, unique([...(reasons.get(row.id) || []), 'calendar']));
        }
        const p = promises.get(row.id);
        if (p && p.status === 'pending') {
          const urgency = this._urgency(p, now);
          if (now >= p.due - p.window) reminders.add(row.id);
          if (reminders.has(row.id) || candidates.has(row.id)) {
            candidates.set(row.id, Math.max(candidates.get(row.id) || 0, urgency));
            reasons.set(row.id, unique([...(reasons.get(row.id) || []), 'prospective']));
          }
        }
      }
      // 4. Rerank with independently decaying channels, importance, urgency and cooldown.
      const ranked = [...candidates].map(([id, relevance]) => {
        const row = byId.get(id), channels = channelsFor(id).filter(c => c.content);
        const strength = Math.max(0, ...channels.map(c => c.strength));
        const p = promises.get(id);
        const retention = p?.status === 'pending' ? this._urgency(p, now) : strength;
        const cooldown = row.last_round === round - 1 ? 0.35 : 1;
        return { row, score: relevance * (0.3 + 0.7 * retention) * (0.6 + 0.4 * row.importance) * cooldown, content: this._render(row, now, channelsFor(id), p) };
      }).filter(x => x.content).sort((a, b) => b.score - a.score || a.row.id.localeCompare(b.row.id));
      // 5. Greedy diversity pruning (MMR); due promises are considered first.
      const selected = [], pending = [...ranked].sort((a, b) => Number(reminders.has(b.row.id)) - Number(reminders.has(a.row.id)) || b.score - a.score);
      const omittedReminders = [];
      let plannedSize = coreText.length;
      while (pending.length && selected.length < limit) {
        let best = 0, value = -Infinity;
        for (let i = 0; i < pending.length; i++) {
          const candidate = pending[i];
          const similarity = Math.max(0, ...selected.map(x => cosine(parse(candidate.row.embedding), parse(x.row.embedding))));
          const diverse = candidate.score - 0.9 * similarity + (reminders.has(candidate.row.id) ? 2 : 0);
          if (diverse > value) { value = diverse; best = i; }
        }
        const candidate = pending.splice(best, 1)[0];
        if (selected.some(x => x.content === candidate.content)) continue;
        const size = candidate.content.length + (plannedSize ? 1 : 0);
        if (plannedSize + size > budget) {
          if (reminders.has(candidate.row.id)) omittedReminders.push(candidate.row.id);
          continue;
        }
        selected.push(candidate); plannedSize += size;
      }
      for (const candidate of pending) if (reminders.has(candidate.row.id)) omittedReminders.push(candidate.row.id);
      // 6. Assemble bounded prose. Core is reserved in full; promises are never truncated.
      const parts = coreText ? [coreText] : [], items = [];
      let used = coreText.length;
      for (const candidate of selected) {
        const separator = parts.length ? 1 : 0;
        if (candidate.content.length + used + separator > budget) {
          if (reminders.has(candidate.row.id)) omittedReminders.push(candidate.row.id);
          continue;
        }
        parts.push(candidate.content); used += separator + candidate.content.length;
        const row = candidate.row;
        items.push({ id: row.id, kind: row.kind, score: candidate.score, importance: row.importance,
          entities: parse(row.entities), themes: parse(row.themes), anchors: parse(row.anchors),
          createdAt: new Date(row.created).toISOString(), reasons: reasons.get(row.id), content: candidate.content,
          channels: channelsFor(row.id),
          promise: promises.get(row.id) || null });
        // Recall boost decays, is capped and diminishes with use; importance never changes.
        const gain = 0.08 / (1 + row.recalls);
        for (const channel of this.db.prepare('SELECT * FROM channels WHERE memory_id=?').all(row.id)) {
          const boost = channel.boost * Math.pow(2, -Math.max(0, now - channel.boost_at) / (DAY * CHANNELS[channel.channel].halfLife));
          this.db.prepare('UPDATE channels SET boost=?,boost_at=? WHERE memory_id=? AND channel=?').run(Math.min(0.15, boost + gain), now, row.id, channel.channel);
        }
        this.db.prepare('UPDATE memories SET recalls=recalls+1,last_round=? WHERE id=?').run(round, row.id);
      }
      this.db.prepare('INSERT INTO recall_state VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET round=excluded.round').run(scope, round);
      const context = parts.join('\n');
      const selectedIds = items.map(item => item.id);
      const marks = selectedIds.map(() => '?').join(',');
      const connections = selectedIds.length ? this.db.prepare(`SELECT a,b,kind,weight FROM edges WHERE a IN (${marks}) AND b IN (${marks})`).all(...selectedIds, ...selectedIds) : [];
      return style === 'quiet' ? context : { context, core, items, connections, omittedReminders, pipeline: ['vector', 'spread', 'calendar', 'rerank', 'diversity', 'assemble'] };
    });
  }

  consolidate({ scope = 'default' } = {}) {
    scope = this._scope(scope); const now = this._now();
    return this._transaction(() => {
      const result = { broken: this._expirePromises(scope, now), compressed: 0, edges: 0, invalidAnchors: 0 };
      for (const row of this.db.prepare('SELECT entity FROM relationships WHERE scope=?').all(scope)) this._applyRelationship(scope, row.entity, {}, now);
      const rows = this.db.prepare('SELECT * FROM memories WHERE scope=?').all(scope);
      for (const row of rows) {
        const valid = parse(row.anchors).filter(a => { try { anchors([a]); return true; } catch { result.invalidAnchors++; return false; } });
        if (json(valid) !== row.anchors) {
          this.db.prepare('UPDATE memories SET anchors=? WHERE id=?').run(json(valid), row.id);
          this.db.prepare("DELETE FROM memory_tags WHERE memory_id=? AND kind='calendar'").run(row.id);
          for (const anchor of valid) this.db.prepare('INSERT INTO memory_tags VALUES (?,?,?)').run(row.id, 'calendar', anchor);
        }
        // Original prospective text and all three channels remain untouched.
        if (row.kind === 'promise') continue;
        const channels = this._channels(row.id, now, row.created);
        for (const c of channels) {
          const policy = CHANNELS[c.channel], age = (now - row.created) / DAY;
          if (c.compressed || !c.content || age < policy.halfLife * 3 || c.strength > policy.floor + 0.13) continue;
          const content = c.channel === 'fact' ? '' : c.content.slice(0, c.channel === 'gist' ? 160 : 240);
          this.db.prepare('UPDATE channels SET content=?,compressed=1 WHERE memory_id=? AND channel=?').run(content, row.id, c.channel);
          result.compressed++;
        }
        const current = Object.fromEntries(this.db.prepare('SELECT channel,content FROM channels WHERE memory_id=?').all(row.id).map(c => [c.channel, c.content]));
        this.db.prepare('UPDATE memories SET embedding=? WHERE id=?').run(json(this._vector(this._embeddingText(current, parse(row.entities), parse(row.themes)))), row.id);
      }
      for (const edge of this.db.prepare('SELECT e.* FROM edges e JOIN memories m ON m.id=e.a WHERE m.scope=?').all(scope)) {
        const weight = edge.weight * Math.pow(2, -Math.max(0, now - edge.updated) / (90 * DAY));
        if (weight < 0.01) this.db.prepare('DELETE FROM edges WHERE a=? AND b=? AND kind=?').run(edge.a, edge.b, edge.kind);
        else this.db.prepare('UPDATE edges SET weight=?,updated=? WHERE a=? AND b=? AND kind=?').run(weight, now, edge.a, edge.b, edge.kind);
        result.edges++;
      }
      return result;
    });
  }
  health() { return this.db.prepare('PRAGMA quick_check').get().quick_check === 'ok'; }
  close() { this.db.close(); }
}

export default MemorableMemory;
