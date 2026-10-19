"""Local-owner view definitions; execution reads the caller's current scope snapshot."""
from datetime import timedelta
import json
import sqlite3
import time
import uuid

from backend.auth.policy import current_access
from backend.notes.notes_manager import RevisionConflict
from backend.schema.models import utc_now
from backend.views.models import SavedQuery, aware_time, timestamp_microseconds


class ViewNotFound(LookupError):
    pass


class ViewConflict(RevisionConflict):
    pass


OWNER = 'local-owner'


class SavedViewManager:
    def __init__(self, storage):
        self.storage = storage
        self._owner()
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS saved_views (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, scope TEXT NOT NULL,
                    name TEXT NOT NULL, revision INTEGER NOT NULL, archived INTEGER NOT NULL,
                    query_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS saved_views_scope ON saved_views(scope, owner_id, archived);
                CREATE TABLE IF NOT EXISTS saved_view_revisions (
                    view_id TEXT NOT NULL REFERENCES saved_views(id), revision INTEGER NOT NULL,
                    definition_json TEXT NOT NULL, PRIMARY KEY(view_id, revision));
                CREATE TABLE IF NOT EXISTS saved_view_audit (
                    view_id TEXT NOT NULL REFERENCES saved_views(id), revision INTEGER NOT NULL,
                    scope TEXT NOT NULL, actor TEXT NOT NULL, operation TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY(view_id, revision));
            ''')

    @staticmethod
    def _owner():
        if current_access.get() is not None:
            raise PermissionError('Saved-view definitions require the local owner')

    @staticmethod
    def _scope(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError('Scope must be nonempty and at most 200 characters')
        value = value.strip()
        context = current_access.get()
        if context is not None and context.scope != value:
            raise PermissionError('Saved view is unavailable in this connection scope')
        return value

    @staticmethod
    def _page(offset, limit):
        if type(offset) is not int or not 0 <= offset <= 1_000_000 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid pagination budget')

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('Name must be nonempty and at most 200 characters')
        return name.strip()

    @staticmethod
    def _definition(row):
        return {'id': row['id'], 'owner_id': row['owner_id'], 'scope': row['scope'],
                'name': row['name'], 'revision': row['revision'], 'archived': bool(row['archived']),
                'query': json.loads(row['query_json']), 'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def _get(self, conn, view_id, scope):
        row = conn.execute('SELECT * FROM saved_views WHERE id = ? AND scope = ? AND owner_id = ?',
                           (view_id, scope, OWNER)).fetchone()
        if row is None:
            raise ViewNotFound('Saved view is unavailable in this scope')
        return self._definition(row)

    def _record(self, conn, definition, operation):
        serialized = json.dumps(definition, ensure_ascii=False, sort_keys=True)
        conn.execute('INSERT INTO saved_view_revisions VALUES (?, ?, ?)',
                     (definition['id'], definition['revision'], serialized))
        conn.execute('INSERT INTO saved_view_audit VALUES (?, ?, ?, ?, ?, ?)',
                     (definition['id'], definition['revision'], definition['scope'], OWNER,
                      operation, definition['updated_at']))
        self.storage._insert_outbox_event(conn, 'SAVED_VIEW', definition['id'], operation,
            {'scope': definition['scope'], 'owner_id': OWNER, 'revision': definition['revision']})

    def create(self, *, name, query, scope='default'):
        self._owner()
        scope, name = self._scope(scope), self._name(name)
        query = SavedQuery.model_validate(query).model_dump(mode='json')
        now, identity = utc_now().isoformat(), 'view-' + uuid.uuid4().hex
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO saved_views VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?)',
                         (identity, OWNER, scope, name, json.dumps(query, ensure_ascii=False, sort_keys=True), now, now))
            definition = self._get(conn, identity, scope)
            self._record(conn, definition, 'SAVED_VIEW_CREATED')
            conn.execute('COMMIT')
            return definition

    def update(self, view_id, *, expected_revision, scope='default', name=None, query=None, archived=None):
        self._owner()
        scope = self._scope(scope)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('expected_revision must be a positive integer')
        if name is None and query is None and archived is None:
            raise ValueError('Provide at least one definition change')
        if archived is not None and type(archived) is not bool:
            raise ValueError('archived must be a boolean')
        if query is not None:
            query = SavedQuery.model_validate(query).model_dump(mode='json')
        if name is not None:
            name = self._name(name)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = self._get(conn, view_id, scope)
            if current['revision'] != expected_revision:
                raise ViewConflict('Saved view changed; reload its current definition')
            desired = {**current, 'name': current['name'] if name is None else name,
                       'query': current['query'] if query is None else query,
                       'archived': current['archived'] if archived is None else archived}
            if desired == current:
                conn.execute('COMMIT')
                return current
            desired.update(revision=current['revision'] + 1, updated_at=utc_now().isoformat())
            conn.execute('UPDATE saved_views SET name = ?, query_json = ?, archived = ?, revision = ?, updated_at = ? WHERE id = ?',
                (desired['name'], json.dumps(desired['query'], ensure_ascii=False, sort_keys=True), desired['archived'],
                 desired['revision'], desired['updated_at'], view_id))
            self._record(conn, desired, 'SAVED_VIEW_UPDATED')
            conn.execute('COMMIT')
            return desired

    def get(self, view_id, *, scope='default'):
        scope = self._scope(scope)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            return self._get(conn, view_id, scope)

    def list(self, *, scope='default', workspace=None, include_archived=False, offset=0, limit=50):
        scope = self._scope(scope)
        self._page(offset, limit)
        if workspace is not None and workspace not in ('notes', 'memory', 'backlog', 'logsheet'):
            raise ValueError('Unsupported workspace')
        conditions, params = ['scope = ?', 'owner_id = ?'], [scope, OWNER]
        if not include_archived:
            conditions.append('archived = 0')
        if workspace is not None:
            conditions.append("json_extract(query_json, '$.workspace') = ?")
            params.append(workspace)
        where = ' AND '.join(conditions)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            total = conn.execute('SELECT COUNT(*) FROM saved_views WHERE ' + where, params).fetchone()[0]
            rows = conn.execute('SELECT * FROM saved_views WHERE ' + where + ' ORDER BY name COLLATE NOCASE, id LIMIT ? OFFSET ?',
                                [*params, limit, offset]).fetchall()
            return {'items': [self._definition(row) for row in rows], 'total': total,
                    'offset': offset, 'limit': limit, 'has_more': offset + len(rows) < total}

    def history(self, view_id, *, scope='default', offset=0, limit=50):
        scope = self._scope(scope)
        self._page(offset, limit)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            self._get(conn, view_id, scope)
            total = conn.execute('SELECT COUNT(*) FROM saved_view_revisions WHERE view_id = ?', (view_id,)).fetchone()[0]
            rows = conn.execute('SELECT definition_json FROM saved_view_revisions WHERE view_id = ? ORDER BY revision DESC LIMIT ? OFFSET ?',
                                (view_id, limit, offset)).fetchall()
            return {'items': [json.loads(row[0]) for row in rows], 'total': total,
                    'offset': offset, 'limit': limit, 'has_more': offset + len(rows) < total}

    @staticmethod
    def _source(workspace):
        return {
            'notes': "SELECT id,title,content,revision,scope,author,content_hash,created_at,created_at AS updated_at FROM notes WHERE is_current = 1",
            'memory': "SELECT *,version AS revision FROM nodes WHERE is_active = 1",
            'backlog': 'SELECT * FROM system_memory_backlog',
            'logsheet': 'SELECT *,entry_id AS id,recorded_at AS updated_at FROM system_memory_logsheet',
        }[workspace]

    def execute(self, view_id, *, expected_revision, scope='default', offset=0, limit=None):
        scope = self._scope(scope)
        self._page(offset, 1 if limit is None else limit)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('expected_revision must be a positive integer')
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            definition = self._get(conn, view_id, scope)
            if definition['revision'] != expected_revision or definition['archived']:
                raise ViewConflict('Saved view changed or is archived; reload before executing')
            # Unknown stored schemas/fields fail closed; never fall back to an unfiltered view.
            query = SavedQuery.model_validate(definition['query'])
            limit = query.limit if limit is None else limit
            if limit > query.limit:
                raise ValueError('Execution cannot exceed the saved page budget')
            conn.create_function('saved_view_time', 1, timestamp_microseconds, deterministic=True)
            now, clauses, params = utc_now(), ['o.scope = ?'], [scope]
            if query.workspace == 'memory':
                clauses.extend([
                    "COALESCE(json_extract(o.data_json, '$.temporal_props.superseded_by'), '') = ''",
                    "(json_extract(o.data_json, '$.temporal_props.valid_from') IS NULL OR saved_view_time(json_extract(o.data_json, '$.temporal_props.valid_from')) <= saved_view_time(?))",
                    "(json_extract(o.data_json, '$.temporal_props.valid_to') IS NULL OR saved_view_time(json_extract(o.data_json, '$.temporal_props.valid_to')) > saved_view_time(?))",
                ])
                params.extend([now.isoformat(), now.isoformat()])
            for field, value in query.filters.items():
                if field == 'query':
                    text_columns = {'notes': ['title', 'content'], 'memory': ['title', 'content'],
                                    'backlog': ['reason', 'blocker'], 'logsheet': ['reason', 'result']}[query.workspace]
                    clauses.append('(' + ' OR '.join(f"instr(lower(COALESCE(o.{col}, '')), lower(?)) > 0" for col in text_columns) + ')')
                    params.extend([value] * len(text_columns))
                elif field == 'topic_id':
                    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_topics'").fetchone()
                    if not exists or not conn.execute('SELECT 1 FROM collection_topics WHERE id = ? AND scope = ?', (value, scope)).fetchone():
                        raise ViewNotFound('Saved view collection dependency is unavailable')
                    clauses.append('EXISTS (SELECT 1 FROM collection_members m WHERE m.topic_id = ? AND m.kind = ? AND m.object_id = o.id)')
                    params.extend([value, 'note' if query.workspace == 'notes' else 'memory'])
                elif field == 'verification_status':
                    clauses.append("COALESCE(json_extract(o.data_json, '$.verification_props.status'), 'UNVERIFIED') = ?")
                    params.append(value)
                else:
                    # SavedQuery validation limits field to the hardcoded workspace allowlist.
                    clauses.append(f'o.{field} = ?')
                    params.append(value)
            if query.updated_within_days is not None:
                clauses.extend(['saved_view_time(o.updated_at) >= saved_view_time(?)', 'saved_view_time(o.updated_at) <= saved_view_time(?)'])
                params.extend([(now - timedelta(days=query.updated_within_days)).isoformat(), now.isoformat()])
            for value, operator in [(query.updated_after, '>='), (query.updated_before, '<')]:
                if value is not None:
                    clauses.append(f'saved_view_time(o.updated_at) {operator} saved_view_time(?)')
                    params.append(aware_time(value).isoformat())
            sorts = []
            for sort in query.sort:
                expression = ('CASE o.priority WHEN \'critical\' THEN 3 WHEN \'high\' THEN 2 WHEN \'normal\' THEN 1 ELSE 0 END'
                              if sort.field == 'priority' else
                              f'saved_view_time(o.{sort.field})' if sort.field.endswith('_at') else f'o.{sort.field}')
                sorts.append(f'{expression} {sort.direction}')
            if not any(sort.field == 'id' for sort in query.sort):
                sorts.append('o.id ASC')
            sql = ' FROM (' + self._source(query.workspace) + ') o WHERE ' + ' AND '.join(clauses)
            deadline = time.monotonic() + 2
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            try:
                total = conn.execute('SELECT COUNT(*)' + sql, params).fetchone()[0]
                rows = conn.execute('SELECT o.*' + sql + ' ORDER BY ' + ', '.join(sorts) + ' LIMIT ? OFFSET ?',
                                    [*params, limit, offset]).fetchall()
            except sqlite3.OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Saved view query exceeded its execution budget') from exc
                raise
            finally:
                conn.set_progress_handler(None, 0)
            items = [self._item(query.workspace, row) for row in rows]
            return {'view_id': view_id, 'view_revision': definition['revision'], 'workspace': query.workspace,
                    'scope': scope, 'items': items, 'total': total, 'offset': offset, 'limit': limit,
                    'has_more': offset + len(items) < total, 'evaluated_at': now.isoformat(),
                    'read_mode': 'current_snapshot', 'historical_as_of': False}

    @staticmethod
    def _item(workspace, row):
        if workspace == 'memory':
            return json.loads(row['data_json'])
        item = dict(row)
        for key in list(item):
            if key.endswith('_json'):
                raw = item.pop(key)
                item[key[:-5]] = json.loads(raw) if raw is not None else None
        return item
