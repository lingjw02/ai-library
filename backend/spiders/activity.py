"""Bounded temporary activity shared by local MCP processes and owner UI.

The sidecar contains references, never prompts or content. It is not canonical
history; chat end deletes its events, process leases purge crash remnants.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import re
import sqlite3
import time
import uuid

from backend.auth.policy import AccessPolicy
from backend.spiders.references import current_ref, current_topic


APPLICATIONS = {'codex':'Codex','antigravity':'Antigravity','gemini':'Gemini',
                'doubao':'豆包','deepseek':'DeepSeek','qwen':'Qwen','claude':'Claude'}


def application_identity(principal, application=None):
    if application is not None:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',application):
            raise ValueError('Application must be a lowercase local launch identifier')
        key = application
    else:
        token = re.split(r'[:/]',principal.lower(),maxsplit=1)[0]
        key = 'gemini' if token == 'gemini-cli' else token
        if key not in APPLICATIONS:
            key = 'unknown-' + hashlib.sha256(principal.encode()).hexdigest()[:12]
    return key, APPLICATIONS.get(key, '未识别应用' if key.startswith('unknown-') else key), key if key in APPLICATIONS else 'unknown'


class SpiderActivity:
    def __init__(self, storage):
        self.storage = storage
        self.path = storage.db_path.parent / '_runtime' / 'spider-mecha.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as conn:
            conn.executescript('''
              PRAGMA journal_mode=WAL;
              PRAGMA secure_delete=ON;
              CREATE TABLE IF NOT EXISTS spider_apps(
                id TEXT NOT NULL,scope TEXT NOT NULL,name TEXT NOT NULL,style TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'unknown',PRIMARY KEY(id,scope));
              CREATE TABLE IF NOT EXISTS spider_connections(
                id TEXT PRIMARY KEY,principal TEXT NOT NULL,scope TEXT NOT NULL,application TEXT NOT NULL,
                heartbeat REAL NOT NULL,disconnected INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS spider_sessions(
                id TEXT PRIMARY KEY,connection_id TEXT NOT NULL REFERENCES spider_connections(id) ON DELETE CASCADE,
                session_key TEXT NOT NULL,label TEXT NOT NULL,identity_kind TEXT NOT NULL,state TEXT NOT NULL,
                position TEXT NOT NULL DEFAULT '{}',mode TEXT NOT NULL DEFAULT 'fly',origin TEXT,
                UNIQUE(connection_id,session_key));
              CREATE TABLE IF NOT EXISTS spider_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL REFERENCES spider_sessions(id) ON DELETE CASCADE,
                tool TEXT NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL,topic_id TEXT,from_topic_id TEXT,
                kind TEXT,object_id TEXT,revision INTEGER,timestamp REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS spider_events_session ON spider_events(session_id,seq);
            ''')
            if 'state' not in {r['name'] for r in conn.execute('PRAGMA table_info(spider_apps)')}:
                conn.execute("ALTER TABLE spider_apps ADD COLUMN state TEXT NOT NULL DEFAULT 'unknown'")
            try:
                self._sweep(conn,time.time())
            except sqlite3.OperationalError:
                # Optional cleanup is retried by live heartbeats. A contended
                # activity sidecar must not block ordinary content reads.
                pass

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path,timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA secure_delete=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def connect(self, connection_id, principal, scope, identity):
        with self.db() as conn:
            self._sweep(conn,time.time())
            conn.execute('INSERT OR IGNORE INTO spider_apps(id,scope,name,style) VALUES (?,?,?,?)',(identity[0],scope,identity[1],identity[2]))
            conn.execute('INSERT OR IGNORE INTO spider_connections VALUES (?,?,?,?,?,0)',
                         (connection_id,principal,scope,identity[0],time.time()))

    def heartbeat(self, connection_id, *, now=None):
        with self.db() as conn:
            self._sweep(conn,time.time())
            conn.execute('UPDATE spider_connections SET heartbeat=?,disconnected=0 WHERE id=?',
                         (time.time() if now is None else now,connection_id))

    def begin(self, connection_id, session_key, label, *, identity_kind='chat'):
        digest = hashlib.sha256(f'{identity_kind}:{session_key}'.encode()).hexdigest()
        with self.db() as conn:
            old = conn.execute('SELECT id FROM spider_sessions WHERE connection_id=? AND session_key=?',
                               (connection_id,digest)).fetchone()
            if old:
                return {'session_id':old['id'],'identity_kind':identity_kind}
            if conn.execute('SELECT COUNT(*) FROM spider_sessions WHERE connection_id=?',(connection_id,)).fetchone()[0] >= 32:
                raise ValueError('End unused sessions before registering more')
            identity = uuid.uuid4().hex
            conn.execute('INSERT INTO spider_sessions(id,connection_id,session_key,label,identity_kind,state) VALUES(?,?,?,?,?,?)',
                         (identity,connection_id,digest,label,identity_kind,'active' if identity_kind=='chat' else 'unknown'))
            return {'session_id':identity,'identity_kind':identity_kind}

    def get(self, connection_id, session_id):
        with self.db() as conn:
            row = conn.execute('SELECT * FROM spider_sessions WHERE id=? AND connection_id=?',
                               (session_id,connection_id)).fetchone()
            if not row:
                raise LookupError('Session is unavailable on this connection')
            result = dict(row)
            result['position'] = json.loads(result['position'])
            return result

    def choose(self, connection_id, session_id=None):
        if session_id:
            return self.get(connection_id,session_id)['id']
        with self.db() as conn:
            chats = conn.execute("SELECT id FROM spider_sessions WHERE connection_id=? AND identity_kind='chat'",(connection_id,)).fetchall()
        if len(chats) == 1:
            return chats[0]['id']
        return self.begin(connection_id,'__connection__','连接 · 会话未登记',identity_kind='connection')['session_id']

    def set_state(self, connection_id, session_id, state):
        self.get(connection_id,session_id)
        with self.db() as conn:
            conn.execute('UPDATE spider_sessions SET state=? WHERE id=?',(state,session_id))
            self._application_state(conn,connection_id)
        return {'session_id':session_id,'state':state}

    def end(self, connection_id, session_id):
        self.get(connection_id,session_id)
        with self.db() as conn:
            conn.execute('DELETE FROM spider_sessions WHERE id=?',(session_id,))
            self._application_state(conn,connection_id)
        return {'session_id':session_id,'state':'ended','history_retained':False}

    def disconnect(self, connection_id):
        with self.db() as conn:
            conn.execute('DELETE FROM spider_sessions WHERE connection_id=?',(connection_id,))
            conn.execute('UPDATE spider_connections SET disconnected=1 WHERE id=?',(connection_id,))
            self._sweep(conn,time.time())
        # Checkpoint outside the deletion transaction. Active pages observe the
        # empty application dock; no session history survives.
        with self.db() as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def event(self, session_id, tool, action, status, *, position=None, from_topic_id=None, mode=None, origin=None, update_position=True, _conn=None):
        pos = position or {}
        with (self.db() if _conn is None else nullcontext(_conn)) as conn:
            if not conn.execute('SELECT 1 FROM spider_sessions WHERE id=?',(session_id,)).fetchone():
                return
            conn.execute('INSERT INTO spider_events(session_id,tool,action,status,topic_id,from_topic_id,kind,object_id,revision,timestamp) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (session_id,tool,action,status,pos.get('topic_id'),from_topic_id,pos.get('kind'),pos.get('object_id'),pos.get('revision'),time.time()))
            if status == 'started':
                conn.execute("UPDATE spider_sessions SET state=CASE WHEN identity_kind='chat' THEN 'active' ELSE 'unknown' END WHERE id=?",(session_id,))
            if status == 'completed' and position is not None and update_position:
                conn.execute("UPDATE spider_sessions SET position=?,mode=COALESCE(?,mode),origin=COALESCE(?,origin),state=CASE WHEN identity_kind='chat' THEN 'active' ELSE 'unknown' END WHERE id=?",
                             (json.dumps(pos),mode,origin,session_id))
            conn.execute('''DELETE FROM spider_events WHERE session_id=? AND seq NOT IN
              (SELECT seq FROM spider_events WHERE session_id=? ORDER BY seq DESC LIMIT 100)''',(session_id,session_id))

    def snapshot(self, scope):
        now = time.time()
        with self.db() as conn:
            self._sweep(conn,now)
            apps = [dict(r) for r in conn.execute('SELECT * FROM spider_apps WHERE scope=? ORDER BY id',(scope,))]
            rows = [dict(r) for r in conn.execute('''SELECT s.*,c.principal,c.scope,c.application,c.heartbeat FROM spider_sessions s
              JOIN spider_connections c ON c.id=s.connection_id WHERE c.scope=? ORDER BY s.id LIMIT 512''',(scope,))]
            events = {r['id']:[dict(e) for e in conn.execute('SELECT * FROM spider_events WHERE session_id=? ORDER BY seq',(r['id'],))] for r in rows}
        by_app = {a['id']:{**a,'sessions':[]} for a in apps}
        policy = AccessPolicy(self.storage)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            def visible(pos):
                if pos.get('topic_id') and not current_topic(conn,scope,pos['topic_id']):
                    return None
                if pos.get('object_id') and not current_ref(conn,scope,pos.get('kind'),pos['object_id']):
                    return None
                return pos
            for row in rows:
                try:
                    policy.check(row['principal'],scope)
                except PermissionError:
                    continue
                pos = visible(json.loads(row['position'])) or {}
                clean = []
                for event in events[row['id']]:
                    event.pop('session_id',None)
                    if not visible(event):
                        continue
                    if event['from_topic_id'] and not current_topic(conn,scope,event['from_topic_id']):
                        event['from_topic_id'] = None
                    clean.append(event)
                state = 'disconnected' if row['heartbeat'] < now-30 else row['state']
                by_app[row['application']]['sessions'].append({'id':row['id'],'label':row['label'],
                    'identity_kind':row['identity_kind'],'state':state,'position':pos,'mode':row['mode'],'events':clean})
        return {'applications':list(by_app.values()),'server_time':now,'scope':scope}

    @staticmethod
    def _application_state(conn, connection_id):
        row=conn.execute('SELECT application,scope FROM spider_connections WHERE id=?',(connection_id,)).fetchone()
        if not row:
            return
        states=[r['state'] for r in conn.execute('''SELECT s.state FROM spider_sessions s JOIN spider_connections c ON c.id=s.connection_id
          WHERE c.application=? AND c.scope=? AND c.disconnected=0''',(row['application'],row['scope']))]
        state='idle' if not states or all(s=='idle' for s in states) else 'active' if 'active' in states else 'unknown'
        conn.execute('UPDATE spider_apps SET state=? WHERE id=? AND scope=?',(state,row['application'],row['scope']))

    @staticmethod
    def _sweep(conn, now):
        expired=conn.execute('SELECT DISTINCT application,scope FROM spider_connections WHERE heartbeat<? OR disconnected=1',(now-120,)).fetchall()
        if not expired:
            return
        conn.execute('DELETE FROM spider_connections WHERE heartbeat<? OR disconnected=1',(now-120,))
        for row in expired:
            if not conn.execute('SELECT 1 FROM spider_connections WHERE application=? AND scope=? LIMIT 1',(row['application'],row['scope'])).fetchone():
                conn.execute("UPDATE spider_apps SET state='disconnected' WHERE id=? AND scope=?",(row['application'],row['scope']))
