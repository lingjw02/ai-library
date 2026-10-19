"""Rebuildable SQLite keyword projection; canonical data remains authoritative.

The consumer advances only a committed prefix. Rebuild switches generations in
one transaction. Searches use a read snapshot and fall back when lagging; they
also compare canonical identities/revisions to catch writes without an outbox.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import timezone

from backend.core.retrieval.bm25 import BM25Scorer
from backend.schema.models import GraphNode, utc_now

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_index_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), generation INTEGER NOT NULL DEFAULT 0,
    checkpoint INTEGER NOT NULL DEFAULT 0, failed_seq INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
INSERT OR IGNORE INTO keyword_index_state(id) VALUES (1);
CREATE TABLE IF NOT EXISTS keyword_documents (
    rowid INTEGER PRIMARY KEY, generation INTEGER NOT NULL,
    plane TEXT NOT NULL, object_id TEXT NOT NULL, scope TEXT NOT NULL,
    revision INTEGER NOT NULL,
    UNIQUE(generation, plane, object_id)
);
CREATE INDEX IF NOT EXISTS keyword_documents_scope ON keyword_documents(generation, scope, plane);
CREATE VIRTUAL TABLE IF NOT EXISTS keyword_fts USING fts5(tokens);
"""


def currently_valid(node: GraphNode, now=None) -> bool:
    now = now or utc_now()
    props = node.temporal_props
    def aware(value):
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
    start, end = aware(props.valid_from), aware(props.valid_to)
    return bool(node.is_active and not props.superseded_by
                and (start is None or start <= now) and (end is None or now < end))


class KeywordIndex:
    MAX_RETRIES = 3

    def __init__(self, storage):
        self.storage = storage
        with storage.session() as conn:
            conn.executescript(SCHEMA)

    def _state(self, conn):
        return dict(conn.execute('SELECT * FROM keyword_index_state WHERE id = 1').fetchone())

    def _status(self, conn):
        state = self._state(conn)
        lag = conn.execute('SELECT COUNT(*) FROM outbox_sequence WHERE seq > ?', (state['checkpoint'],)).fetchone()[0]
        return {**state, 'lag': lag, 'blocked': state['retry_count'] >= self.MAX_RETRIES,
                'ready': state['generation'] > 0}

    def status(self):
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            return self._status(conn)

    def _delete_document(self, conn, generation, plane, object_id):
        rows = conn.execute('SELECT rowid FROM keyword_documents WHERE generation = ? AND plane = ? AND object_id = ?',
                            (generation, plane, object_id)).fetchall()
        for row in rows:
            conn.execute('DELETE FROM keyword_fts WHERE rowid = ?', (row['rowid'],))
            conn.execute('DELETE FROM keyword_documents WHERE rowid = ?', (row['rowid'],))

    def _write_document(self, conn, generation, plane, row):
        self._delete_document(conn, generation, plane, row['id'])
        revision = row['version'] if plane == 'MEMORY' else row['revision']
        cursor = conn.execute('INSERT INTO keyword_documents(generation, plane, object_id, scope, revision) VALUES (?, ?, ?, ?, ?)',
                              (generation, plane, row['id'], row['scope'], revision))
        tokens = ' '.join(BM25Scorer.tokenize(f"{row['id']} {row['title']} {row['content']}"))
        conn.execute('INSERT INTO keyword_fts(rowid, tokens) VALUES (?, ?)', (cursor.lastrowid, tokens))

    def rebuild(self):
        """Writer snapshot + atomic publish; an exception leaves the old generation intact."""
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            generation = self._state(conn)['generation'] + 1
            for plane, table, predicate in [('MEMORY', 'nodes', 'is_active = 1'), ('NOTES', 'notes', 'is_current = 1')]:
                for row in conn.execute(f'SELECT * FROM {table} WHERE {predicate}'):
                    self._write_document(conn, generation, plane, row)
            checkpoint = conn.execute('SELECT COALESCE(MAX(seq), 0) FROM outbox_sequence').fetchone()[0]
            conn.execute('UPDATE keyword_index_state SET generation = ?, checkpoint = ?, failed_seq = NULL, retry_count = 0, last_error = NULL WHERE id = 1',
                         (generation, checkpoint))
            # Disposable projection rows only. Old readers retain their WAL
            # snapshot; rollback also restores the previous generation.
            conn.execute('DELETE FROM keyword_fts WHERE rowid IN (SELECT rowid FROM keyword_documents WHERE generation != ?)', (generation,))
            conn.execute('DELETE FROM keyword_documents WHERE generation != ?', (generation,))
            conn.execute("UPDATE dead_letter_queue SET resolved = 1 WHERE job_type = 'KEYWORD_INDEX' AND resolved = 0")
            conn.execute('COMMIT')
        return self.status()

    def _apply_event(self, conn, generation, event):
        plane = {'NODE': 'MEMORY', 'NOTE': 'NOTES'}.get(event['aggregate_type'])
        if plane is None:
            return  # Edges/candidates/logsheet do not contain searchable text.
        table, predicate = ('nodes', 'is_active = 1') if plane == 'MEMORY' else ('notes', 'is_current = 1')
        row = conn.execute(f'SELECT * FROM {table} WHERE id = ? AND {predicate}', (event['aggregate_id'],)).fetchone()
        if row:
            self._write_document(conn, generation, plane, row)
        else:
            self._delete_document(conn, generation, plane, event['aggregate_id'])

    def drain(self, limit=100):
        """Bounded work; failures persist outside the rolled-back event transaction."""
        for _ in range(limit):
            event = None
            try:
                with self.storage.session() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    state = self._state(conn)
                    if not state['generation'] or state['retry_count'] >= self.MAX_RETRIES:
                        return self._status(conn)
                    event = conn.execute('SELECT s.seq, o.* FROM outbox_sequence s JOIN transactional_outbox o ON o.id = s.event_id WHERE s.seq > ? ORDER BY s.seq LIMIT 1',
                                         (state['checkpoint'],)).fetchone()
                    if event is None:
                        return self._status(conn)
                    self._apply_event(conn, state['generation'], event)
                    conn.execute('UPDATE keyword_index_state SET checkpoint = ?, failed_seq = NULL, retry_count = 0, last_error = NULL WHERE id = 1', (event['seq'],))
                    conn.execute('COMMIT')
            except Exception as exc:
                if event is None:
                    raise
                with self.storage.session() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    latest = self._state(conn)
                    # Another consumer may have successfully handled it meanwhile.
                    if latest['checkpoint'] < event['seq']:
                        retries = min(self.MAX_RETRIES, (latest['retry_count'] if latest['failed_seq'] == event['seq'] else 0) + 1)
                        error = type(exc).__name__
                        conn.execute('UPDATE keyword_index_state SET failed_seq = ?, retry_count = ?, last_error = ? WHERE id = 1', (event['seq'], retries, error))
                        if retries == self.MAX_RETRIES:
                            conn.execute('INSERT OR IGNORE INTO dead_letter_queue(id, job_type, payload_json, error_message, retry_count, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                                         (f"keyword-{event['id']}", 'KEYWORD_INDEX', json.dumps({'event_id': event['id'], 'sequence': event['seq']}), error, retries, utc_now().isoformat()))
                    conn.execute('COMMIT')
                break
        return self.status()

    def _projection_matches(self, conn, generation, scope, planes):
        for plane in planes:
            table, revision, predicate = ('nodes', 'version', 'is_active = 1') if plane == 'MEMORY' else ('notes', 'revision', 'is_current = 1')
            expected = f'SELECT id, {revision}, scope FROM {table} WHERE {predicate} AND scope = ?'
            actual = 'SELECT object_id, revision, scope FROM keyword_documents WHERE generation = ? AND plane = ? AND scope = ?'
            if conn.execute(f'{expected} EXCEPT {actual} LIMIT 1', (scope, generation, plane, scope)).fetchone():
                return False
            if conn.execute(f'{actual} EXCEPT {expected} LIMIT 1', (generation, plane, scope, scope)).fetchone():
                return False
        if conn.execute('SELECT 1 FROM keyword_documents d LEFT JOIN keyword_fts f ON f.rowid = d.rowid WHERE d.generation = ? AND f.rowid IS NULL LIMIT 1', (generation,)).fetchone():
            return False
        return True

    def _canonical_item(self, conn, plane, object_id, scope, now, node_type=None):
        if plane == 'MEMORY':
            row = conn.execute('SELECT data_json FROM nodes WHERE id = ? AND scope = ? AND is_active = 1', (object_id, scope)).fetchone()
            if not row:
                return None
            node = GraphNode.model_validate_json(row['data_json'])
            if not currently_valid(node, now) or (node_type and node.node_type.value != node_type):
                return None
            return {**node.model_dump(mode='json'), 'plane': plane}
        row = conn.execute('SELECT * FROM notes WHERE id = ? AND scope = ? AND is_current = 1', (object_id, scope)).fetchone()
        return {**dict(row), 'plane': plane} if row else None

    def search(self, query, scope='default', plane='BOTH', limit=20, node_type=None, *, allowed_objects=None):
        if plane not in ('MEMORY', 'NOTES', 'BOTH'):
            raise ValueError('plane must be MEMORY, NOTES or BOTH')
        if not 1 <= limit <= 100 or not query.strip() or len(query) > 2000:
            raise ValueError('query must contain 1–2000 characters and limit must be 1–100')
        planes = ['MEMORY', 'NOTES'] if plane == 'BOTH' else [plane]
        now = utc_now()
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            state = self._status(conn)
            try:
                ready = state['ready'] and not state['lag'] and not state['blocked'] and self._projection_matches(conn, state['generation'], scope, planes)
            except sqlite3.DatabaseError:
                ready = False
            mode = 'fts5' if ready else 'canonical_fallback'
            matches = {}
            for target in planes:
                if allowed_objects is not None and (target, query.strip()) not in allowed_objects:
                    continue
                exact = self._canonical_item(conn, target, query.strip(), scope, now, node_type)
                if exact:
                    matches[(target, exact['id'])] = exact
            tokens = list(dict.fromkeys(BM25Scorer.tokenize(query)))[:128]
            if ready and tokens:
                expression = ' OR '.join('"' + token.replace('"', '""') + '"' for token in tokens)
                placeholders = ','.join('?' for _ in planes)
                try:
                    rows = conn.execute(f'SELECT d.plane, d.object_id FROM keyword_fts f JOIN keyword_documents d ON d.rowid = f.rowid WHERE keyword_fts MATCH ? AND d.generation = ? AND d.scope = ? AND d.plane IN ({placeholders}) ORDER BY bm25(keyword_fts), d.object_id',
                                        (expression, state['generation'], scope, *planes))
                    for row in rows:
                        if allowed_objects is not None and (row['plane'], row['object_id']) not in allowed_objects:
                            continue
                        item = self._canonical_item(conn, row['plane'], row['object_id'], scope, now, node_type)
                        if item:
                            matches[(row['plane'], item['id'])] = item
                except sqlite3.DatabaseError:
                    ready = False
                    mode = 'canonical_fallback'
            if not ready:
                corpus, items = {}, {}
                for target in planes:
                    table, predicate = ('nodes', 'is_active = 1') if target == 'MEMORY' else ('notes', 'is_current = 1')
                    for row in conn.execute(f'SELECT id FROM {table} WHERE scope = ? AND {predicate}', (scope,)):
                        if allowed_objects is not None and (target, row['id']) not in allowed_objects:
                            continue
                        item = self._canonical_item(conn, target, row['id'], scope, now, node_type)
                        if item:
                            key = target + ':' + item['id']
                            corpus[key] = f"{item['id']} {item['title']} {item['content']}"
                            items[key] = item
                scorer = BM25Scorer()
                scorer.fit(corpus)
                for key, _ in scorer.score(query, top_k=len(items)):
                    item = items[key]
                    matches[(item['plane'], item['id'])] = item
            values = list(matches.values())
            return {'items': values[:limit], 'total': len(values), 'has_more': len(values) > limit,
                    'mode': mode, 'index': state, 'scope': scope, 'plane': plane,
                    'as_of': now.isoformat(), 'historical_snapshot': False}


class KeywordIndexWorker:
    def __init__(self, index, interval=0.5):
        self.index = index
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name='keyword-index', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if not self.index.status()['ready']:
                    self.index.rebuild()
                else:
                    self.index.drain()
            except Exception:
                logger.exception('Keyword index worker failed; retrieval retains canonical fallback')
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)
