import json
import sqlite3
import time
from contextlib import contextmanager


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > 2:
                raise RuntimeError('数据库版本较新，请使用新版程序，禁止旧程序写入')
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, owner TEXT NOT NULL,
                  name TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                  width INTEGER, height INTEGER, source_hash TEXT, profile TEXT NOT NULL,
                  edit TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,job_id TEXT,kind TEXT,
                  details TEXT,created REAL);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created);
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            for name, definition in [('priority','INTEGER NOT NULL DEFAULT 0'),('project',"TEXT NOT NULL DEFAULT ''"),
                                     ('cancel_requested','INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
            db.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if not row:
            return None
        value = dict(row)
        for key in ('profile', 'edit'):
            if value.get(key):
                value[key] = json.loads(value[key])
        return value

    def add(self, job):
        job = dict(job)
        job['profile'] = json.dumps(job['profile'], ensure_ascii=False)
        job['created'] = job['updated'] = time.time()
        with self.connect() as db:
            keys = ','.join(job)
            db.execute(f"INSERT INTO jobs ({keys}) VALUES ({','.join('?' for _ in job)})", list(job.values()))

    def get(self, jid):
        with self.connect() as db:
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())

    def list(self, limit=500, offset=0):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ? OFFSET ?', (limit, offset))]

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def submit(self, jid, revision, edit):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,status FROM jobs WHERE id=?', (jid,)).fetchone()
            if not row or row['revision'] != revision or row['status'] in ('QUEUED', 'RUNNING'):
                raise Conflict('任务已被其他人修改或正在处理，请刷新后再试')
            db.execute('UPDATE jobs SET edit=?,revision=revision+1,status=?,error=NULL,cancel_requested=0,updated=? WHERE id=?',
                       (json.dumps(edit), 'QUEUED', time.time(), jid))
            db.execute('INSERT INTO events(job_id,kind,details,created) VALUES (?,?,?,?)',
                       (jid, 'SUBMIT', json.dumps({'revision': revision + 1}), time.time()))

    def claim(self, last_owner=None, skip_lama=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='QUEUED' AND (?=0 OR COALESCE(json_extract(edit,'$.engine'),'opencv')!='lama') ORDER BY priority DESC,(owner=?) ASC,updated ASC LIMIT 1", (int(skip_lama),last_owner or '')).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='RUNNING',updated=? WHERE id=?", (time.time(), row['id']))
            job = self.decode(row)
            job['status'] = 'RUNNING'
            return job

    def finish(self, jid, revision, status, error=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'CANCELLED' ELSE ? END,error=?,updated=? WHERE id=? AND revision=? AND status='RUNNING'",
                       (status, error, time.time(), jid, revision))
            db.execute('INSERT INTO events(job_id,kind,details,created) VALUES (?,?,?,?)',
                       (jid, status, json.dumps({'revision': revision, 'error': error}), time.time()))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='INTERRUPTED',error='上次服务中断，请重试',updated=? WHERE status='RUNNING'", (time.time(),))

    def approve(self, jid, revision):
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET status='COMPLETED',updated=? WHERE id=? AND revision=? AND status='REVIEW'", (time.time(), jid, revision)).rowcount
            if not changed:
                raise Conflict('只能确认当前版本的待复核结果')
            db.execute('INSERT INTO events(job_id,kind,details,created) VALUES (?,?,?,?)',
                       (jid, 'APPROVED', str(revision), time.time()))

    def page(self, query='', status='', project='', batch='', limit=100, offset=0):
        where, args = ['1=1'], []
        for column,value in [('status',status),('project',project),('batch_id',batch)]:
            if value:
                where.append(column+'=?');args.append(value)
        if query:
            where.append('(name LIKE ? OR owner LIKE ? OR batch_id LIKE ?)')
            args += ['%'+query+'%']*3
        clause=' AND '.join(where)
        with self.connect() as db:
            total=db.execute('SELECT COUNT(*) FROM jobs WHERE '+clause,args).fetchone()[0]
            rows=db.execute('SELECT * FROM jobs WHERE '+clause+' ORDER BY created DESC LIMIT ? OFFSET ?',args+[limit,offset])
            result=[self.decode(r) for r in rows]
            counts={r[0]:r[1] for r in db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')}
        return {'total':total,'items':result,'counts':counts}

    def action(self, jid, revision, action):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not row or row['revision']!=revision:
                raise Conflict('任务版本已变化，请刷新')
            status=row['status']
            if action=='pause' and status=='QUEUED':
                db.execute("UPDATE jobs SET status='PAUSED',updated=? WHERE id=?",(time.time(),jid))
            elif action=='resume' and status=='PAUSED':
                db.execute("UPDATE jobs SET status='QUEUED',updated=? WHERE id=?",(time.time(),jid))
            elif action=='cancel' and status=='RUNNING':
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?',(jid,))
            elif action=='cancel' and status in ('QUEUED','PAUSED','MANUAL','FAILED','INTERRUPTED'):
                db.execute("UPDATE jobs SET status='CANCELLED',updated=? WHERE id=?",(time.time(),jid))
            else:
                raise Conflict('当前状态不允许此操作')
            db.execute('INSERT INTO events(job_id,kind,details,created) VALUES (?,?,?,?)',(jid,action,'',time.time()))

    def has_busy(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE status IN ('RUNNING','QUEUED') LIMIT 1").fetchone())
