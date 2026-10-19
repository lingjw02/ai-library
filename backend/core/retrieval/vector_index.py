"""Externally supplied embeddings; no model, model download, or network access.

The source contract is SHA256(exact node.content/note.content encoded as UTF-8).
Titles, whitespace transformations and other metadata are deliberately excluded.
Model identity/version/dimension define isolated spaces; every space uses L2
normalization and cosine similarity. Provider/version identity is an external
agent assertion, not something the backend can establish from vector numbers.

Durable immutable submissions survive disposable projection rebuilds. Retrieval
is a deadline-bounded exact scan, not ANN, and makes no semantic quality claims.
Canonical eligibility is checked in the same read snapshot as vector retrieval.
"""
from __future__ import annotations

import hashlib
from bisect import insort
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass

from backend.core.retrieval.keyword_index import currently_valid
from backend.schema.models import GraphNode, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_records (
    embedding_id INTEGER PRIMARY KEY,
    space_key TEXT NOT NULL, plane TEXT NOT NULL CHECK(plane IN ('MEMORY', 'NOTES')),
    object_id TEXT NOT NULL, revision INTEGER NOT NULL, scope TEXT NOT NULL,
    content_hash TEXT NOT NULL, submitted_vector TEXT NOT NULL,
    normalized_vector TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(space_key, plane, object_id, revision)
);
CREATE INDEX IF NOT EXISTS embedding_records_scope
    ON embedding_records(space_key, scope, plane, object_id, revision);
CREATE TABLE IF NOT EXISTS vector_index_state (
    id INTEGER PRIMARY KEY CHECK(id = 1), generation INTEGER NOT NULL
);
INSERT OR IGNORE INTO vector_index_state VALUES(1, 1);
CREATE TABLE IF NOT EXISTS vector_projection (
    generation INTEGER NOT NULL, embedding_id INTEGER NOT NULL,
    normalized_vector TEXT NOT NULL,
    PRIMARY KEY(generation, embedding_id),
    FOREIGN KEY(embedding_id) REFERENCES embedding_records(embedding_id)
);
"""


@dataclass(frozen=True)
class EmbeddingSpace:
    model_id: str
    model_version: str
    dimension: int
    metric: str = 'cosine'
    normalization: str = 'l2'

    def __post_init__(self):
        for value in (self.model_id, self.model_version):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError('model_id and model_version must contain 1–200 characters')
        if type(self.dimension) is not int or not 1 <= self.dimension <= 4096:
            raise ValueError('dimension must be an integer between 1 and 4096')
        if self.metric != 'cosine' or self.normalization != 'l2':
            raise ValueError('Only cosine with l2 normalization is supported')

    @property
    def key(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))


def _space(value):
    if isinstance(value, EmbeddingSpace):
        return value
    if isinstance(value, dict):
        try:
            return EmbeddingSpace(**value)
        except TypeError as exc:
            raise ValueError('Invalid embedding space') from exc
    raise ValueError('space must be an EmbeddingSpace or matching dictionary')


def content_hash(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _vector(vector, dimension):
    if not isinstance(vector, (list, tuple)) or len(vector) != dimension:
        raise ValueError('Vector dimension does not match embedding space')
    try:
        if any(isinstance(x, bool) or not isinstance(x, (float, int)) for x in vector):
            raise ValueError('Vector values must be finite numbers')
        values = [float(x) for x in vector]
    except (OverflowError, TypeError) as exc:
        raise ValueError('Vector values must be finite numbers') from exc
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Vector values must be finite numbers')
    scale = max(abs(x) for x in values)
    if scale == 0:
        raise ValueError('Zero vectors have no cosine direction')
    scaled = [x / scale for x in values]
    norm = math.hypot(*scaled)
    return values, [x / norm for x in scaled]


def _request(scope, plane, limit):
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 200:
        raise ValueError('scope must contain 1–200 characters')
    if plane not in ('MEMORY', 'NOTES', 'BOTH'):
        raise ValueError('plane must be MEMORY, NOTES or BOTH')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')


def _deadline(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 30000:
        raise ValueError('deadline_ms must be between 1 and 30000')
    return float(value)


class VectorIndex:
    def __init__(self, storage, deadline_ms=250):
        self.storage = storage
        self.deadline_ms = _deadline(deadline_ms)
        with storage.session() as conn:
            conn.executescript(SCHEMA)

    def _generation(self, conn):
        return conn.execute('SELECT generation FROM vector_index_state WHERE id = 1').fetchone()[0]

    def status(self):
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            generation = self._generation(conn)
            return {'generation': generation,
                    'submissions': conn.execute('SELECT COUNT(*) FROM embedding_records').fetchone()[0],
                    'projected': conn.execute('SELECT COUNT(*) FROM vector_projection WHERE generation = ?', (generation,)).fetchone()[0],
                    'mode': 'exact_cosine', 'embedding_provider': 'external_agent',
                    'metric': 'cosine', 'normalization': 'l2', 'model_loaded': False}

    def _canonical(self, conn, plane, object_id, scope, now):
        if plane == 'MEMORY':
            row = conn.execute('SELECT * FROM nodes WHERE id = ? AND scope = ? AND is_active = 1', (object_id, scope)).fetchone()
            if not row:
                return None
            node = GraphNode.model_validate_json(row['data_json'])
            # Column state and the canonical serialized representation must agree.
            if not currently_valid(node, now) or node.scope != scope or node.version != row['version'] or node.content != row['content']:
                return None
            return {**node.model_dump(mode='json'), 'plane': plane, 'revision': row['version']}
        row = conn.execute('SELECT * FROM notes WHERE id = ? AND scope = ? AND is_current = 1', (object_id, scope)).fetchone()
        return {**dict(row), 'plane': plane} if row else None

    def _write_projection(self, conn, generation, record):
        conn.execute('INSERT OR REPLACE INTO vector_projection VALUES (?, ?, ?)',
                     (generation, record['embedding_id'], record['normalized_vector']))

    def submit_vector(self, *, space, plane, object_id, revision, scope, content_hash, vector):
        space = _space(space)
        _request(scope, plane, 1)
        if plane == 'BOTH' or not isinstance(object_id, str) or not object_id or len(object_id) > 500:
            raise ValueError('Submission needs one plane and a valid object_id')
        if type(revision) is not int or revision < 1:
            raise ValueError('revision must be a positive integer')
        if not isinstance(content_hash, str) or len(content_hash) != 64 or any(x not in '0123456789abcdef' for x in content_hash):
            raise ValueError('content_hash must be lowercase SHA256 hex')
        values, normalized = _vector(vector, space.dimension)
        encoded = json.dumps(values, separators=(',', ':'), allow_nan=False)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            item = self._canonical(conn, plane, object_id, scope, utc_now())
            if not item or item['revision'] != revision or hashlib.sha256(item['content'].encode('utf-8')).hexdigest() != content_hash:
                raise ValueError('Current source scope, revision or content_hash does not match')
            existing = conn.execute('SELECT * FROM embedding_records WHERE space_key = ? AND plane = ? AND object_id = ? AND revision = ?',
                                    (space.key, plane, object_id, revision)).fetchone()
            if existing:
                if existing['submitted_vector'] != encoded or existing['content_hash'] != content_hash or existing['scope'] != scope:
                    raise ValueError('Embedding submissions are immutable for this source revision and space')
                record = existing
            else:
                result = conn.execute('''INSERT INTO embedding_records(space_key, plane, object_id, revision, scope,
                    content_hash, submitted_vector, normalized_vector, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (space.key, plane, object_id, revision, scope, content_hash, encoded,
                     json.dumps(normalized, separators=(',', ':')), utc_now().isoformat()))
                record = conn.execute('SELECT * FROM embedding_records WHERE embedding_id = ?', (result.lastrowid,)).fetchone()
            self._write_projection(conn, self._generation(conn), record)
            conn.execute('COMMIT')
            return {'embedding_id': record['embedding_id'], 'idempotent': existing is not None,
                    'space': asdict(space), 'plane': plane, 'object_id': object_id, 'revision': revision,
                    'scope': scope, 'content_hash': content_hash}

    def rebuild(self):
        """Atomic generation switch; exceptions roll back and retain the old projection."""
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            generation = self._generation(conn) + 1
            now = utc_now()
            for record in conn.execute('SELECT * FROM embedding_records'):
                item = self._canonical(conn, record['plane'], record['object_id'], record['scope'], now)
                if item and item['revision'] == record['revision'] and content_hash(item['content']) == record['content_hash']:
                    self._write_projection(conn, generation, record)
            conn.execute('UPDATE vector_index_state SET generation = ? WHERE id = 1', (generation,))
            conn.execute('DELETE FROM vector_projection WHERE generation != ?', (generation,))
            conn.execute('COMMIT')
        return self.status()

    def _sources(self, conn, scope, plane, cursor=None, batch_limit=None):
        # Keyset pagination reads bounded current source identities, without
        # repeatedly loading all source text into an external embedding agent.
        parts, params = [], []
        for target in (['MEMORY', 'NOTES'] if plane == 'BOTH' else [plane]):
            table, predicate = ('nodes', 'is_active = 1') if target == 'MEMORY' else ('notes', 'is_current = 1')
            parts.append(f"SELECT '{target}' AS plane, id FROM {table} WHERE scope = ? AND {predicate}")
            params.append(scope)
        sql = 'SELECT * FROM (' + ' UNION ALL '.join(parts) + ')'
        if cursor:
            sql += ' WHERE (plane, id) > (?, ?)'
            params.extend(cursor)
        if batch_limit is not None:
            sql += ' ORDER BY plane, id LIMIT ?'
            params.append(batch_limit)
        return conn.execute(sql, params)

    def _embedding(self, conn, space, item, generation):
        return conn.execute('''SELECT e.*, p.normalized_vector AS projected_vector
            FROM embedding_records e LEFT JOIN vector_projection p
                ON p.embedding_id = e.embedding_id AND p.generation = ?
            WHERE e.space_key = ? AND e.plane = ? AND e.object_id = ?
                AND e.revision = ? AND e.scope = ? AND e.content_hash = ?''',
            (generation, space.key, item['plane'], item['id'], item['revision'], item['scope'], content_hash(item['content']))).fetchone()

    def search(self, query_vector, *, space, scope='default', plane='BOTH', limit=20, deadline_ms=None, allowed_objects=None):
        space = _space(space)
        _request(scope, plane, limit)
        _, query = _vector(query_vector, space.dimension)
        budget = _deadline(self.deadline_ms if deadline_ms is None else deadline_ms)
        end = time.monotonic() + budget / 1000
        now, total, embedded, available = utc_now(), 0, 0, 0
        best, reasons, complete = [], [], True
        with self.storage.session() as conn:
            # Also interrupt expensive SQLite work (e.g. an identity scan), not
            # only Python cosine evaluation. This is a cooperative deadline;
            # connection acquisition and a single source decode may overrun it.
            conn.execute('PRAGMA busy_timeout = ' + str(max(1, int(budget))))
            conn.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
            try:
                conn.execute('BEGIN')
                generation = self._generation(conn)
                for row in self._sources(conn, scope, plane):
                    if time.monotonic() >= end:
                        complete = False
                        break
                    if allowed_objects is not None and (row['plane'], row['id']) not in allowed_objects:
                        continue
                    item = self._canonical(conn, row['plane'], row['id'], scope, now)
                    if not item:
                        continue
                    total += 1
                    record = self._embedding(conn, space, item, generation)
                    if not record:
                        continue
                    embedded += 1
                    if record['projected_vector'] is None:
                        continue
                    values = json.loads(record['projected_vector'])
                    # Rebuildable projection cannot substitute a different
                    # vector for the immutable submission.
                    if record['projected_vector'] != record['normalized_vector']:
                        continue
                    available += 1
                    score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(query, values))))
                    result = {**item, 'score': score}
                    # Keep the same score/identity order at the cutoff as in
                    # the full ranking. The bounded list holds at most 101 rows.
                    entry = (-score, item['plane'], item['id'], result)
                    if len(best) < limit or entry[:3] < best[-1][:3]:
                        insort(best, entry, key=lambda x: x[:3])
                        if len(best) > limit:
                            best.pop()
            except sqlite3.OperationalError as exc:
                if 'interrupt' not in str(exc).lower() and 'locked' not in str(exc).lower():
                    raise
                complete = False
            finally:
                conn.set_progress_handler(None, 0)
        if not complete:
            reasons.append('deadline')
        if total > embedded:
            reasons.append('missing_embeddings')
        if embedded > available:
            reasons.append('projection_unavailable')
        coverage = {'total': total if complete else None, 'embedded': embedded if complete else None,
                    'available': available if complete else None,
                    'missing': total - embedded if complete else None, 'complete': complete}
        items = [entry[3] for entry in best]
        return {'items': items, 'total': available if complete else None,
                'has_more': available > len(items) if complete else None,
                'mode': 'exact_cosine', 'space': asdict(space), 'scope': scope, 'plane': plane,
                'partial': bool(reasons), 'partial_reasons': reasons, 'coverage': coverage,
                'availability': 'available' if available else ('unavailable' if complete else 'unknown'),
                'scanned': {'total': total, 'embedded': embedded, 'available': available},
                'as_of': now.isoformat(), 'historical_snapshot': False}

    def missing_embedding_sources(self, *, space, scope='default', plane='BOTH', limit=100, cursor=None):
        space = _space(space)
        _request(scope, plane, limit)
        if cursor is not None:
            if not isinstance(cursor, (list, tuple)) or len(cursor) != 2 or cursor[0] not in ('MEMORY', 'NOTES') or not isinstance(cursor[1], str) or len(cursor[1]) > 500:
                raise ValueError('cursor must be a [plane, id] pair')
        now, items, last = utc_now(), [], None
        batch_limit = max(100, limit * 4)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            generation = self._generation(conn)
            rows = self._sources(conn, scope, plane, cursor, batch_limit).fetchall()
            for row in rows:
                last = [row['plane'], row['id']]
                item = self._canonical(conn, row['plane'], row['id'], scope, now)
                if item and not self._embedding(conn, space, item, generation):
                    items.append({'plane': item['plane'], 'id': item['id'], 'revision': item['revision'],
                                  'scope': scope, 'content_hash': content_hash(item['content'])})
                if len(items) == limit:
                    break
            more = bool(last and self._sources(conn, scope, plane, last, 1).fetchone())
        return {'items': items, 'next_cursor': last if more else None, 'space': asdict(space),
                'scope': scope, 'plane': plane, 'source_contract': 'sha256_utf8_content_only',
                'as_of': now.isoformat()}
