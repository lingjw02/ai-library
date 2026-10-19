"""Persisted MCP grants. Launch scope is a ceiling, never an implicit regrant.

The local browser/OS owner is the administrator. This is a tool permission
boundary, not protection from processes able to edit the database directly.
Object scope checks remain at service boundaries. Write grants are rechecked
after taking SQLite's transaction lock so a committed revocation wins races.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from backend.schema.models import utc_now


@dataclass(frozen=True)
class AccessContext:
    principal: str
    scope: str
    write: bool


current_access = ContextVar('memory_access', default=None)
_UNCHANGED = object()


def normalized_expiry(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError) as exc:
        raise ValueError('Expiration must be an ISO timestamp with timezone, or null') from exc


def check_connection(conn, context, write=False):
    row = sqlite3.Connection.execute(conn, 'SELECT can_read, can_write, expires_at FROM access_grants WHERE principal = ? AND scope = ?',
                                    (context.principal, context.scope)).fetchone()
    if not row or not row['can_read'] or (write and (not context.write or not row['can_write'])):
        raise PermissionError('Connection permission revoked or insufficient')
    if row['expires_at'] is not None:
        try:
            expires = normalized_expiry(row['expires_at'])
        except ValueError as exc:
            raise PermissionError('Connection expiration is invalid') from exc
        if datetime.fromisoformat(expires) <= utc_now():
            raise PermissionError('Connection permission expired')


class GuardedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.access_context = current_access.get()

    def execute(self, sql, parameters=()):
        context = self.access_context
        if context is None:
            return super().execute(sql, parameters)
        verb = sql.lstrip().split(None, 1)[0].upper().rstrip(';')
        if verb == 'BEGIN':
            result = super().execute(sql, parameters)
            try:
                check_connection(self, context, write='IMMEDIATE' in sql.upper() or 'EXCLUSIVE' in sql.upper())
            except Exception:
                super().execute('ROLLBACK')
                raise
            return result
        if verb in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE'):
            implicit = not self.in_transaction
            if implicit:
                super().execute('BEGIN IMMEDIATE')
            try:
                check_connection(self, context, write=True)
                result = super().execute(sql, parameters)
                if implicit:
                    check_connection(self, context, write=True)
                    super().execute('COMMIT')
                return result
            except Exception:
                if implicit and self.in_transaction:
                    super().execute('ROLLBACK')
                raise
        if verb in ('CREATE', 'ALTER', 'DROP', 'ATTACH', 'DETACH', 'VACUUM'):
            raise PermissionError('Schema operations are unavailable to Agent tools')
        if verb in ('COMMIT', 'END') and self.in_transaction:
            check_connection(self, context, write=context.write)
        return super().execute(sql, parameters)

    def commit(self):
        if self.access_context is not None and self.in_transaction:
            check_connection(self, self.access_context, write=self.access_context.write)
        return super().commit()

    def executemany(self, sql, parameters):
        if self.access_context is None:
            return super().executemany(sql, parameters)
        raise PermissionError('Batch SQL requires an explicitly authorized service transaction')

    def executescript(self, sql):
        if self.access_context is not None:
            raise PermissionError('SQL scripts are unavailable to Agent tools')
        return super().executescript(sql)


class AccessPolicy:
    def __init__(self, storage):
        self.storage = storage
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS access_grants (
                    principal TEXT NOT NULL, scope TEXT NOT NULL,
                    can_read INTEGER NOT NULL, can_write INTEGER NOT NULL,
                    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT,
                    PRIMARY KEY(principal, scope));
                CREATE TABLE IF NOT EXISTS access_audit (
                    id TEXT PRIMARY KEY, principal TEXT NOT NULL, scope TEXT NOT NULL,
                    revision INTEGER NOT NULL, actor TEXT NOT NULL,
                    event TEXT NOT NULL, details_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
            ''')
            conn.execute('BEGIN IMMEDIATE')
            if 'expires_at' not in {row['name'] for row in conn.execute('PRAGMA table_info(access_grants)')}:
                conn.execute('ALTER TABLE access_grants ADD COLUMN expires_at TEXT')
            conn.execute('COMMIT')

    def _audit(self, conn, principal, scope, revision, actor, event, read, write, expires_at=None):
        conn.execute('INSERT INTO access_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (uuid.uuid4().hex, principal, scope, revision, actor, event,
                      json.dumps({'can_read': bool(read), 'can_write': bool(write), 'expires_at': expires_at}), utc_now().isoformat()))

    def ensure_launch_grant(self, principal, scope):
        if not principal.strip() or not scope.strip():
            raise ValueError('Principal and scope are required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM access_grants WHERE principal = ? AND scope = ?', (principal, scope)).fetchone():
                conn.execute('INSERT INTO access_grants(principal, scope, can_read, can_write, revision, updated_at) VALUES (?, ?, 1, 1, 1, ?)', (principal, scope, utc_now().isoformat()))
                self._audit(conn, principal, scope, 1, 'local-launch', 'INITIAL_GRANT', True, True)
            conn.execute('COMMIT')

    def get_grant(self, principal, scope):
        with self.storage.session() as conn:
            row = conn.execute('SELECT * FROM access_grants WHERE principal = ? AND scope = ?', (principal, scope)).fetchone()
            return dict(row) if row else None

    def update_grant(self, principal, scope, *, read, write, expected_revision, actor, expires_at=_UNCHANGED):
        if current_access.get() is not None:
            raise PermissionError('Agents cannot administer grants')
        if write and not read:
            raise ValueError('Write permission requires read permission')
        if type(read) is not bool or type(write) is not bool or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('Permissions must be booleans and revision must be a positive integer')
        if expires_at is not _UNCHANGED:
            expires_at = normalized_expiry(expires_at)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT revision, expires_at FROM access_grants WHERE principal = ? AND scope = ?', (principal, scope)).fetchone()
            if not row or row['revision'] != expected_revision:
                raise ValueError('Grant revision changed; reload before editing')
            revision = row['revision'] + 1
            expiration = row['expires_at'] if expires_at is _UNCHANGED else expires_at
            conn.execute('UPDATE access_grants SET can_read = ?, can_write = ?, revision = ?, updated_at = ?, expires_at = ? WHERE principal = ? AND scope = ?',
                         (int(read), int(write), revision, utc_now().isoformat(), expiration, principal, scope))
            self._audit(conn, principal, scope, revision, actor, 'GRANT_CHANGED', read, write, expiration)
            conn.execute('COMMIT')
        return self.get_grant(principal, scope)

    def check(self, principal, scope, write=False):
        # Fresh connection, including for the post-read response check.
        with self.storage.session() as conn:
            check_connection(conn, AccessContext(principal, scope, write), write=write)

    @contextmanager
    def activate(self, principal, scope, write=False):
        self.check(principal, scope, write=write)
        token = current_access.set(AccessContext(principal, scope, write))
        try:
            yield
        finally:
            current_access.reset(token)
