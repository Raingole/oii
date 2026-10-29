import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


def search_terms(text):
    text = str(text).lower()
    words = re.findall(r'[a-z0-9_]{2,}', text)
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        words.extend(run[i:i + 2] for i in range(max(0, len(run) - 1)))
    return list(dict.fromkeys(words))[:256]


class KnowledgeStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                  id TEXT PRIMARY KEY,scope TEXT NOT NULL,url TEXT NOT NULL,title TEXT NOT NULL,
                  data_source TEXT NOT NULL,summary TEXT NOT NULL,created REAL NOT NULL,
                  UNIQUE(scope,url));
                CREATE TABLE IF NOT EXISTS chunks (
                  id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,text TEXT NOT NULL,start REAL,end REAL);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(terms);
                CREATE TABLE IF NOT EXISTS interests (
                  scope TEXT NOT NULL,topic TEXT NOT NULL,evidence TEXT NOT NULL,updated REAL NOT NULL,
                  last_learned REAL NOT NULL DEFAULT 0,PRIMARY KEY(scope,topic));
                CREATE TABLE IF NOT EXISTS learning_state (key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY,scope TEXT NOT NULL,video TEXT NOT NULL,status TEXT NOT NULL,
                  created REAL NOT NULL,error TEXT NOT NULL DEFAULT '',UNIQUE(scope,video));
                CREATE TABLE IF NOT EXISTS media (
                  scope TEXT NOT NULL,hash TEXT NOT NULL,model TEXT NOT NULL,payload TEXT NOT NULL,
                  created REAL NOT NULL,PRIMARY KEY(scope,hash,model));
                CREATE TABLE IF NOT EXISTS meme_assets (
                  scope TEXT NOT NULL,hash TEXT NOT NULL,image BLOB NOT NULL,payload TEXT NOT NULL,
                  created REAL NOT NULL,used REAL NOT NULL DEFAULT 0,PRIMARY KEY(scope,hash));
            ''')

    def connect(self):
        # Every method closes explicitly; sqlite context managers only commit.
        from contextlib import contextmanager
        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            try:
                db.row_factory = sqlite3.Row
                yield db
                db.commit()
            finally:
                db.close()
        return connection()

    def state(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT INTO learning_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload', (key, json.dumps(value, ensure_ascii=False)))
                return value
            row = db.execute('SELECT payload FROM learning_state WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else {}

    def interest(self, scope, topic, evidence, now=None):
        with self.connect() as db:
            db.execute('INSERT INTO interests(scope,topic,evidence,updated) VALUES (?,?,?,?) ON CONFLICT(scope,topic) DO UPDATE SET evidence=excluded.evidence,updated=excluded.updated', (scope, topic[:80], evidence[:160], now or time.time()))
            db.execute('DELETE FROM interests WHERE rowid IN (SELECT rowid FROM interests WHERE scope=? ORDER BY updated DESC LIMIT -1 OFFSET 30)', (scope,))

    def next_interest(self, scope=None):
        with self.connect() as db:
            if scope:
                row = db.execute('SELECT * FROM interests WHERE scope=? AND updated>? ORDER BY last_learned ASC,updated DESC LIMIT 1', (scope,time.time()-14*86400)).fetchone()
            else:
                row = db.execute('SELECT * FROM interests WHERE updated>? ORDER BY last_learned ASC,updated DESC LIMIT 1', (time.time() - 14 * 86400,)).fetchone()
            return dict(row) if row else None

    def mark_interest(self, scope, topic, now):
        with self.connect() as db:
            db.execute('UPDATE interests SET last_learned=? WHERE scope=? AND topic=?', (now, scope, topic))

    def enqueue(self, scope, video):
        ident = hashlib.sha256(f'{scope}\0{video}'.encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,'queued',?,'')", (ident, scope, video, time.time()))
            db.execute("UPDATE jobs SET status='queued',error='' WHERE id=? AND status='failed'", (ident,))
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
            return dict(row)

    def next_job(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            return dict(row) if row else None

    def finish_job(self, ident, status, error=''):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,error=? WHERE id=?', (status, error[:100], ident))

    def has_source(self, scope, url):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM sources WHERE scope=? AND url=?', (scope, url)).fetchone() is not None

    def add_source(self, scope, url, title, data_source, summary, chunks):
        ident = hashlib.sha256(f'{scope}\0{url}'.encode()).hexdigest()[:24]
        with self.connect() as db:
            if db.execute('SELECT 1 FROM sources WHERE id=?', (ident,)).fetchone():
                return ident
            db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)', (ident, scope, url, title[:300], data_source, summary[:3000], time.time()))
            for chunk in chunks[:80]:
                content = chunk['text'][:1200]
                row = db.execute('INSERT INTO chunks(source_id,text,start,end) VALUES (?,?,?,?)', (ident, content, chunk.get('start'), chunk.get('end')))
                db.execute('INSERT INTO chunk_search(rowid,terms) VALUES (?,?)', (row.lastrowid, ' '.join(search_terms(content))))
        return ident

    def search(self, scope, query, limit=3, budget=2200, data_source=None):
        terms = search_terms(query)[:12]
        if not terms:
            return []
        expression = ' OR '.join('"' + word.replace('"', '""') + '"' for word in terms)
        with self.connect() as db:
            rows = db.execute('''SELECT c.*,s.url,s.title,s.data_source,s.summary FROM chunk_search
                JOIN chunks c ON c.id=chunk_search.rowid JOIN sources s ON s.id=c.source_id
                WHERE chunk_search MATCH ? AND s.scope=? AND (? IS NULL OR s.data_source=?) ORDER BY bm25(chunk_search) LIMIT ?''',
                (expression, scope, data_source, data_source, min(10, max(1, limit)))).fetchall()
        result, used, noted = [], 0, set()
        for row in rows:
            item = dict(row)
            remaining = budget - used
            if remaining <= 0:
                break
            item['summary'] = item['summary'][:min(400,remaining//3)] if item['source_id'] not in noted else ''
            noted.add(item['source_id'])
            item['text'] = item['text'][:remaining-len(item['summary'])]
            used += len(item['text'])+len(item['summary'])
            item['timestamp_url'] = item['url'] + (('&' if '?' in item['url'] else '?') + 't=' + str(int(item['start'])) if item['start'] is not None else '')
            result.append(item)
        return result

    def random_reference(self, scope, source_types=None):
        """Return one recent, source-backed learning chunk for contextual reflection."""
        allowed=tuple(source_types or ('subtitle','ai_subtitle','asr','bilibili_meme_transcript','storyboard_sample'))
        if not allowed:
            return None
        marks=','.join('?' for _ in allowed)
        with self.connect() as db:
            row=db.execute(f'''SELECT c.text,c.start,s.url,s.title,s.data_source,s.created
                FROM chunks c JOIN sources s ON s.id=c.source_id
                WHERE s.scope=? AND s.data_source IN ({marks})
                ORDER BY s.created DESC, c.id DESC LIMIT 80''',(scope,*allowed)).fetchall()
        if not row:
            return None
        import random
        item=dict(random.choice(row))
        item['timestamp_url']=item['url']+(
            (('&' if '?' in item['url'] else '?')+'t='+str(int(item['start'])))
            if item.get('start') is not None else '')
        item['text']=str(item.get('text') or '')[:900]
        return item

    def library(self, scope, limit=200):
        limit=min(200,max(1,int(limit)))
        with self.connect() as db:
            sources=db.execute('''SELECT id,url,title,data_source,summary,created FROM sources
                WHERE scope=? ORDER BY created DESC LIMIT ?''',(scope,limit)).fetchall()
            items=[]
            for source in sources:
                chunks=db.execute('SELECT id,text,start,end FROM chunks WHERE source_id=? ORDER BY id LIMIT 8',(source['id'],)).fetchall()
                bounded=[{**dict(chunk),'text':str(chunk['text'])[:900]} for chunk in chunks]
                items.append({**dict(source),'chunks':bounded})
            assets=db.execute('SELECT hash,payload,created,used FROM meme_assets WHERE scope=? ORDER BY created DESC LIMIT 100',(scope,)).fetchall()
            counts={
                'sources':db.execute('SELECT COUNT(*) FROM sources WHERE scope=?',(scope,)).fetchone()[0],
                'meme_entries':db.execute('''SELECT COUNT(*) FROM chunks c JOIN sources s ON s.id=c.source_id
                    WHERE s.scope=? AND s.data_source='bilibili_meme_transcript' ''',(scope,)).fetchone()[0],
                'video_notes':db.execute('''SELECT COUNT(*) FROM sources WHERE scope=? AND data_source!='bilibili_meme_transcript' ''',(scope,)).fetchone()[0],
                'images':db.execute('SELECT COUNT(*) FROM meme_assets WHERE scope=?',(scope,)).fetchone()[0],
            }
        return {'scope':scope,'counts':counts,'sources':items,
            'images':[{'hash':row['hash'],'payload':json.loads(row['payload']),'created':row['created'],'used':row['used']} for row in assets]}

    def image_asset(self, scope, digest):
        with self.connect() as db:
            row=db.execute('SELECT image,payload FROM meme_assets WHERE scope=? AND hash=?',(scope,digest)).fetchone()
        return {'image':bytes(row['image']),'payload':json.loads(row['payload'])} if row else None

    def media(self, scope, digest, model, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO media VALUES (?,?,?,?,?)', (scope, digest, model, json.dumps(value, ensure_ascii=False), time.time()))
                db.execute('DELETE FROM media WHERE rowid NOT IN (SELECT rowid FROM media ORDER BY created DESC LIMIT 1000)')
                return value
            row = db.execute('SELECT payload FROM media WHERE scope=? AND hash=? AND model=?', (scope, digest, model)).fetchone()
            return json.loads(row[0]) if row else None

    def save_meme_asset(self, scope, digest, image, payload, limit=300):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meme_assets(scope,hash,image,payload,created,used) VALUES (?,?,?,?,?,COALESCE((SELECT used FROM meme_assets WHERE scope=? AND hash=?),0))',
                (scope,digest,image,json.dumps(payload,ensure_ascii=False),time.time(),scope,digest))
            db.execute('DELETE FROM meme_assets WHERE rowid IN (SELECT rowid FROM meme_assets WHERE scope=? ORDER BY created DESC LIMIT -1 OFFSET ?)',(scope,max(1,int(limit))))

    def find_meme_asset(self, scope, query):
        terms=set(search_terms(query))
        if not terms:
            return None
        with self.connect() as db:
            rows=db.execute('SELECT hash,image,payload,created,used FROM meme_assets WHERE scope=?',(scope,)).fetchall()
            best=None; best_score=0
            for row in rows:
                payload=json.loads(row['payload'])
                corpus=' '.join([str(payload.get(k,'')) for k in ('description','visible_text','intent','context','usage_scenarios','tags','kind')])
                matched=terms.intersection(search_terms(corpus))
                score=len(matched)/(len(terms) or 1)
                if score>best_score or (score==best_score and best and row['used']<best['used']):
                    best={**dict(row),'payload':payload};best_score=score
            if best and best_score>=0.12:
                db.execute('UPDATE meme_assets SET used=? WHERE scope=? AND hash=?',(time.time(),scope,best['hash']))
                return {'hash':best['hash'],'image':bytes(best['image']),'payload':best['payload'],'score':best_score}
            return None

    def status(self, scope=None):
        with self.connect() as db:
            where, args = (' WHERE scope=?', (scope,)) if scope else ('', ())
            return {name: db.execute(f'SELECT COUNT(*) FROM {name}' + where, args).fetchone()[0] for name in ('sources', 'interests', 'jobs', 'media', 'meme_assets')}
