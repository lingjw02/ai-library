"""Versioned source-native lineage (§94). Imports assert origin; they do not fork a host."""
from datetime import datetime
import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from backend.auth.policy import current_access, check_connection
from backend.schema.models import utc_now


class SourceRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(min_length=1, max_length=200)
    source_revision: StrictInt = Field(ge=1)
    content_hash: str = Field(pattern='^[0-9a-f]{64}$')


class NativeEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(min_length=1, max_length=200)
    role: Literal['user', 'assistant', 'system', 'tool', 'unknown'] = 'unknown'
    content: str = Field(max_length=200_000)
    occurred_at: datetime | None = None


class Snapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_system: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    source_revision: StrictInt = Field(ge=1)
    source_ref: SourceRef
    parent_session_id: str | None = Field(default=None, min_length=1, max_length=200)
    fork_anchor_event_id: str | None = Field(default=None, min_length=1, max_length=200)
    events: list[NativeEvent] = Field(max_length=1000)
    coverage: Literal['partial', 'snapshot'] = 'snapshot'


def guard(scope, conn=None, write=False):
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 200:
        raise ValueError('A bounded scope is required')
    context = current_access.get()
    if context:
        if context.scope != scope or (write and not context.write):
            raise PermissionError('Scope or write permission denied')
        if conn:
            check_connection(conn, context, write=write)


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class LineageManager:
    def __init__(self, storage):
        self.storage = storage
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS lineage_sessions (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_system TEXT NOT NULL,
                    native_session_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    source_revision INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    data_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(scope, source_system, native_session_id));
                CREATE TABLE IF NOT EXISTS lineage_revisions (
                    session_ref TEXT NOT NULL, source_revision INTEGER NOT NULL,
                    snapshot_hash TEXT NOT NULL, data_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    PRIMARY KEY(session_ref, source_revision));
                CREATE INDEX IF NOT EXISTS lineage_scope ON lineage_sessions(scope, revoked);
            ''')

    def _visible(self, conn, row):
        if not row or row['revoked']:
            return False
        source = json.loads(row['data_json'])['source_ref']
        note = conn.execute('SELECT scope, content_hash FROM notes WHERE id=? AND revision=?',
            (source['source_id'], source['source_revision'])).fetchone()
        return bool(note and note['scope'] == row['scope'] and note['content_hash'] == source['content_hash'])

    def _get(self, conn, session_ref, scope):
        guard(scope, conn)
        row = conn.execute('SELECT * FROM lineage_sessions WHERE id=? AND scope=?', (session_ref, scope)).fetchone()
        if not self._visible(conn, row):
            raise LookupError('Authorized session snapshot unavailable')
        return row

    @staticmethod
    def _public(row):
        data = json.loads(row['data_json'])
        return {k: data[k] for k in ('source_system', 'session_id', 'source_revision', 'source_ref', 'coverage')} | {
            'id': row['id'], 'revision': row['revision'], 'snapshot_hash': row['snapshot_hash'],
            'recorded_at': row['updated_at'], 'origin_verification': 'imported_snapshot_not_live_host'}

    def import_snapshot(self, snapshot, *, scope='default', expected_revision=None):
        guard(scope, write=True)
        data = Snapshot.model_validate(snapshot).model_dump(mode='json')
        if data['parent_session_id'] == data['session_id']:
            raise ValueError('A session cannot fork itself')
        if len({event['event_id'] for event in data['events']}) != len(data['events']):
            raise ValueError('Duplicate native event IDs in snapshot')
        if len(stable(data).encode('utf-8')) > 2_000_000:
            raise ValueError('Snapshot exceeds 2 MB import budget')
        for event in data['events']:
            if event['occurred_at'] and datetime.fromisoformat(event['occurred_at']).tzinfo is None:
                raise ValueError('Source event times require timezone or null')
        digest = hashlib.sha256(stable(data).encode('utf-8')).hexdigest()
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            guard(scope, conn, write=True)
            source = data['source_ref']
            note = conn.execute('SELECT scope,content_hash FROM notes WHERE id=? AND revision=?',
                (source['source_id'], source['source_revision'])).fetchone()
            if not note or note['scope'] != scope or note['content_hash'] != source['content_hash']:
                raise ValueError('Exact source revision/hash unavailable in this scope')
            row = conn.execute('SELECT * FROM lineage_sessions WHERE scope=? AND source_system=? AND native_session_id=?',
                (scope, data['source_system'], data['session_id'])).fetchone()
            if row:
                if row['revoked']:
                    raise PermissionError('Revoked source cannot be silently reimported')
                old = conn.execute('SELECT snapshot_hash FROM lineage_revisions WHERE session_ref=? AND source_revision=?',
                    (row['id'], data['source_revision'])).fetchone()
                if old:
                    if old['snapshot_hash'] != digest:
                        raise ValueError('Same native source revision has different content')
                    if data['source_revision'] != row['source_revision']:
                        raise ValueError('Historical replay cannot replace current snapshot')
                    conn.execute('COMMIT')
                    return self._public(row)
                if expected_revision != row['revision'] or data['source_revision'] <= row['source_revision']:
                    raise ValueError('Snapshot revision conflict; reload before importing')
                ref, revision = row['id'], row['revision'] + 1
            else:
                if expected_revision not in (None, 0):
                    raise ValueError('No existing session at expected revision')
                ref, revision = 'session-' + uuid.uuid4().hex, 1
            # Validate cycles against source-declared ancestry, without inferring missing parents.
            ancestor, seen = data['parent_session_id'], {data['session_id']}
            for _ in range(128):
                if not ancestor:
                    break
                if ancestor in seen:
                    raise ValueError('Native session ancestry cycle')
                seen.add(ancestor)
                parent = conn.execute('SELECT data_json FROM lineage_sessions WHERE scope=? AND source_system=? AND native_session_id=? AND revoked=0',
                    (scope, data['source_system'], ancestor)).fetchone()
                ancestor = json.loads(parent['data_json']).get('parent_session_id') if parent else None
            else:
                raise ValueError('Ancestry exceeds depth budget')
            now = utc_now().isoformat()
            conn.execute('INSERT INTO lineage_sessions VALUES (?,?,?,?,?,?,0,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, source_revision=excluded.source_revision,data_json=excluded.data_json,snapshot_hash=excluded.snapshot_hash,updated_at=excluded.updated_at',
                (ref, scope, data['source_system'], data['session_id'], revision, data['source_revision'], stable(data), digest, now))
            conn.execute('INSERT INTO lineage_revisions VALUES (?,?,?,?,?)', (ref, data['source_revision'], digest, stable(data), now))
            self.storage._insert_outbox_event(conn, 'Lineage', ref, 'LINEAGE_IMPORTED',
                {'scope': scope, 'object_id': ref, 'revision': revision, 'source_revision': data['source_revision'], 'schema_version': 'lineage-v1'})
            result = self._public(self._get(conn, ref, scope))
            conn.execute('COMMIT')
            return result

    def list_sessions(self, *, scope='default', offset=0, limit=50):
        guard(scope)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid page budget')
        with self.storage.session() as conn:
            guard(scope, conn)
            rows = conn.execute('SELECT * FROM lineage_sessions WHERE scope=? AND revoked=0 ORDER BY id LIMIT ? OFFSET ?',
                (scope, limit + 1, offset)).fetchall()
            visible = [self._public(row) for row in rows[:limit] if self._visible(conn, row)]
            return {'items': visible, 'next_offset': offset + limit if len(rows) > limit else None, 'coverage': 'authorized_snapshots'}

    def get_graph(self, session_ref, *, scope='default'):
        with self.storage.session() as conn:
            row = self._get(conn, session_ref, scope)
            data, parent = json.loads(row['data_json']), None
            if data['parent_session_id'] and data['fork_anchor_event_id']:
                candidate = conn.execute('SELECT * FROM lineage_sessions WHERE scope=? AND source_system=? AND native_session_id=?',
                    (scope, data['source_system'], data['parent_session_id'])).fetchone()
                if self._visible(conn, candidate):
                    if data['fork_anchor_event_id'] in {e['event_id'] for e in json.loads(candidate['data_json'])['events']}:
                        parent = self._public(candidate)
            children = []
            for child in conn.execute('SELECT * FROM lineage_sessions WHERE scope=? AND source_system=? AND revoked=0 LIMIT 1000',
                    (scope, data['source_system'])):
                child_data = json.loads(child['data_json'])
                if child_data['parent_session_id'] == data['session_id'] and self._visible(conn, child):
                    if child_data['fork_anchor_event_id'] in {e['event_id'] for e in data['events']}:
                        children.append(self._public(child))
            unknown = bool(data['parent_session_id'] and not parent)
            return {'session': self._public(row), 'parent': parent, 'children': children,
                'ancestry_status': 'source_verified' if parent else ('unknown' if unknown else 'root_or_unknown'),
                'fork_anchor_event_id': data['fork_anchor_event_id'] if parent else None,
                'coverage': 'snapshot', 'warnings': ['parent_or_anchor_unavailable'] if unknown else []}

    def list_events(self, session_ref, *, scope='default', offset=0, limit=50, source_revision=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid page budget')
        with self.storage.session() as conn:
            row = self._get(conn, session_ref, scope)
            data = json.loads(row['data_json'])
            if source_revision is not None:
                old = conn.execute('SELECT data_json FROM lineage_revisions WHERE session_ref=? AND source_revision=?',
                    (session_ref, source_revision)).fetchone()
                if not old:
                    raise LookupError('Exact native revision not retained')
                data = json.loads(old['data_json'])
                source = data['source_ref']
                note = conn.execute('SELECT scope,content_hash FROM notes WHERE id=? AND revision=?',
                    (source['source_id'], source['source_revision'])).fetchone()
                if not note or note['scope'] != scope or note['content_hash'] != source['content_hash']:
                    raise LookupError('Historical source unavailable')
            items = []
            for event in data['events'][offset:offset + limit]:
                event_hash = hashlib.sha256(event['content'].encode('utf-8')).hexdigest()
                # Distinct scopes and origin systems never merge. Native event identity,
                # not content similarity, deduplicates a shared ancestor observation.
                key = hashlib.sha256(stable([scope, data['source_system'], event['event_id'], event_hash]).encode()).hexdigest()
                items.append(event | {'content_hash': event_hash, 'lineage_evidence_key': key, 'independent_evidence_count': 1})
            return {'items': items, 'source_revision': data['source_revision'], 'source_ref': data['source_ref'],
                'next_offset': offset + limit if offset + limit < len(data['events']) else None, 'coverage': data['coverage']}

    def trace_source(self, source_id, *, scope='default'):
        guard(scope)
        with self.storage.session() as conn:
            guard(scope, conn)
            if not conn.execute('SELECT 1 FROM notes WHERE id=? AND scope=?', (source_id, scope)).fetchone():
                raise LookupError('Authorized source unavailable')
            sessions = [self._public(r) for r in conn.execute('SELECT * FROM lineage_sessions WHERE scope=? AND revoked=0 LIMIT 1000', (scope,))
                if json.loads(r['data_json'])['source_ref']['source_id'] == source_id and self._visible(conn, r)]
            revoked = conn.execute("SELECT 1 FROM lineage_sessions WHERE scope=? AND revoked=1 AND json_extract(data_json,'$.source_ref.source_id')=?", (scope, source_id)).fetchone()
            if revoked:
                raise LookupError('Source lineage has been revoked')
            candidates = [dict(r) for r in conn.execute('SELECT id,source_revision,status FROM candidates WHERE scope=? AND source_id=? LIMIT 100', (scope, source_id))]
            memories = [dict(r) for r in conn.execute("SELECT id,version,json_extract(data_json,'$.provenance.source_revision') AS source_revision FROM nodes WHERE scope=? AND is_active=1 AND json_extract(data_json,'$.provenance.source_id')=? LIMIT 100", (scope, source_id))]
            return {'sessions': sessions, 'candidates': candidates, 'memories': memories, 'coverage': 'bounded_source_references'}

    def revoke(self, session_ref, *, scope, expected_revision, reason):
        if current_access.get() is not None:
            raise PermissionError('Only the local owner may revoke retained sources')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 8000:
            raise ValueError('A bounded revocation reason is required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._get(conn, session_ref, scope)
            if type(expected_revision) is not int or row['revision'] != expected_revision:
                raise ValueError('Revocation revision conflict')
            conn.execute('UPDATE lineage_sessions SET revoked=1,revision=revision+1,updated_at=? WHERE id=?', (utc_now().isoformat(), session_ref))
            source = json.loads(row['data_json'])['source_ref']
            self.storage._insert_outbox_event(conn, 'Lineage', session_ref, 'LINEAGE_REVOKED',
                {'scope': scope, 'revision': expected_revision + 1, 'source_id': source['source_id'], 'reason': reason})
            conn.execute('COMMIT')
            return {'status': 'REVOKED', 'revision': expected_revision + 1}
