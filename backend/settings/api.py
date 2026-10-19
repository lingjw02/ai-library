"""Local-owner settings boundary: MCP grant administration, backups, preferences
and system status.

This router belongs to the trusted local-owner REST surface. It is deliberately
absent from the scoped MCP tool list, and AccessPolicy.update_grant additionally
refuses to run while an Agent access context is active, so a connected Agent
cannot widen its own permissions through this endpoint. Snapshot operations go
through backend.storage.recovery, which applies the same owner-only guard.
"""
from __future__ import annotations

import contextlib
import json
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.auth.policy import AccessPolicy
from backend.schema.models import utc_now
from backend.storage.recovery import create_snapshot, restore_snapshot, verify_snapshot
from backend.settings.usage_rules import RulesConflict, UsageRulesStore

AccessLevel = Literal['full', 'read-only', 'revoked']

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_VERSION = '1.4.0'

# Preferences are non-canonical local UI/automation state, kept in a small JSON
# file beside the database rather than in the schema. None of it is knowledge, so
# it is intentionally excluded from snapshots.
PREFERENCE_SCHEMA: dict[str, dict[str, Any]] = {
    'ui': {
        'theme': {'type': 'enum', 'allowed': ('dark', 'light')},
        'fontScale': {'type': 'enum', 'allowed': ('small', 'standard', 'large')},
        'universeQuality': {'type': 'enum', 'allowed': ('high', 'medium', 'low')},
    },
    'automation': {
        'autoIndexRebuild': {'type': 'bool'},
        'notifyOnIngest': {'type': 'bool'},
        'notifyOnConflict': {'type': 'bool'},
        'autoRecordToLibrary': {'type': 'bool'},
    },
}

PREFERENCE_DEFAULTS: dict[str, dict[str, Any]] = {
    'ui': {'theme': 'dark', 'fontScale': 'standard', 'universeQuality': 'high'},
    'automation': {
        'autoIndexRebuild': False,
        'notifyOnIngest': True,
        'notifyOnConflict': True,
        'autoRecordToLibrary': False,
    },
}


class UpdateGrantRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    access: AccessLevel
    expected_revision: StrictInt = Field(ge=1)
    # ISO-8601 timestamp with timezone, or null to remove the expiration.
    expires_at: Optional[str] = Field(default=None, max_length=64)


class PreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ui: Optional[dict] = None
    automation: Optional[dict] = None


class CreateBackupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: Optional[str] = Field(default=None, max_length=60)


class UsageRulesPublishRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=1)
    rules: dict[str, Any]


def _access_label(grant: dict) -> str:
    if not grant.get('can_read'):
        return 'revoked'
    return 'full' if grant.get('can_write') else 'read-only'


def _grant_view(grant: dict) -> dict:
    view = dict(grant)
    view['can_read'] = bool(grant.get('can_read'))
    view['can_write'] = bool(grant.get('can_write'))
    view['access'] = _access_label(grant)
    return view


def _mcp_config_example(data_dir: Path) -> dict:
    root = _PROJECT_ROOT.as_posix()
    return {
        'mcpServers': {
            'ai-memory-library': {
                'command': f'{root}/.venv/Scripts/python.exe',
                'args': [
                    f'{root}/scripts/run_mcp.py',
                    '--data-dir', data_dir.as_posix(),
                    '--scope', 'default',
                    '--principal', 'your-agent-name',
                ],
            },
        },
    }


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def _prefs_path(data_dir: Path) -> Path:
    return data_dir / 'preferences.json'


def _valid_preference(rule: dict, value: Any) -> bool:
    if rule['type'] == 'bool':
        return isinstance(value, bool)
    return isinstance(value, str) and value in rule['allowed']


def _read_preferences(data_dir: Path) -> dict:
    """Return defaults deep-merged with whatever is safely readable on disk."""
    merged = {group: dict(values) for group, values in PREFERENCE_DEFAULTS.items()}
    path = _prefs_path(data_dir)
    if not path.exists():
        return merged
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return merged
    if not isinstance(stored, dict):
        return merged
    for group, values in PREFERENCE_SCHEMA.items():
        section = stored.get(group)
        if not isinstance(section, dict):
            continue
        for key, rule in values.items():
            if key in section and _valid_preference(rule, section[key]):
                merged[group][key] = section[key]
    return merged


def _merge_preferences(data_dir: Path, incoming: dict) -> dict:
    """Validate the incoming patch, then persist the merged result atomically."""
    current = _read_preferences(data_dir)
    for group, patch in incoming.items():
        if group not in PREFERENCE_SCHEMA:
            raise ValueError(f'Unknown preference group: {group}')
        if not isinstance(patch, dict):
            raise ValueError(f'Preference group {group} must be an object')
        for key, value in patch.items():
            rule = PREFERENCE_SCHEMA[group].get(key)
            if rule is None:
                raise ValueError(f'Unknown preference: {group}.{key}')
            if not _valid_preference(rule, value):
                if rule['type'] == 'bool':
                    raise ValueError(f'{group}.{key} must be a boolean')
                raise ValueError(f"{group}.{key} must be one of {', '.join(rule['allowed'])}")
            current[group][key] = value
    path = _prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)
    return current


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
def _backups_dir(data_dir: Path) -> Path:
    # Must live OUTSIDE the data directory: recovery refuses source/destination
    # containment, and a snapshot placed inside the source would recurse.
    return data_dir.parent / 'backups'


def _restore_dir(data_dir: Path) -> Path:
    return data_dir.parent / 'restored'


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob('*'):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _backup_view(name: str, directory: Path) -> dict:
    manifest_path = directory / 'snapshot.json'
    view = {
        'name': name,
        'path': str(directory),
        'created_at': None,
        'files': None,
        'size_bytes': _directory_size(directory),
        'complete': manifest_path.exists(),
    }
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            view['created_at'] = manifest.get('created_at')
            view['files'] = len(manifest.get('files') or {})
        except (OSError, ValueError):
            view['complete'] = False
    return view


def _resolve_backup(data_dir: Path, name: str) -> Path:
    """Resolve a backup directory name, refusing traversal outside backups/."""
    if not name or name != Path(name).name or name.startswith('.'):
        raise ValueError('Invalid backup name')
    directory = _backups_dir(data_dir) / name
    if not directory.is_dir():
        raise FileNotFoundError(f'No such backup: {name}')
    return directory


def _git_revision() -> Optional[str]:
    """Best-effort current commit; omitted entirely when git is unavailable."""
    try:
        result = subprocess.run(
            ['git', '-C', str(_PROJECT_ROOT), 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def _vacuum(db_path: Path) -> None:
    """VACUUM must run outside any transaction; autocommit avoids an open one."""
    with contextlib.closing(sqlite3.connect(str(db_path), isolation_level=None)) as conn:
        conn.execute('PRAGMA trusted_schema = OFF')
        conn.execute('VACUUM')


def create_settings_router(storage, policy: AccessPolicy, coordinator) -> APIRouter:
    router = APIRouter()
    usage_rules = UsageRulesStore(storage)

    def call(operation):
        try:
            return operation()
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RulesConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (TimeoutError, OSError, sqlite3.Error) as exc:
            raise HTTPException(500, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def data_dir() -> Path:
        return Path(storage.db_path).parent

    @router.get('/api/settings/usage-rules')
    def get_usage_rules():
        return call(usage_rules.owner_view)

    @router.post('/api/settings/usage-rules')
    def publish_usage_rules(req: UsageRulesPublishRequest):
        published = call(lambda: usage_rules.publish(req.rules, expected_revision=req.expected_revision))
        return {**call(usage_rules.owner_view), **published}

    @router.get('/api/settings/usage-rules/history')
    def usage_rule_history(limit: int = Query(default=20, ge=1, le=100)):
        return call(lambda: usage_rules.history(limit))

    # ------------------------------------------------------------------
    # MCP connection grants (principal x scope)
    # ------------------------------------------------------------------
    @router.get('/api/settings/access')
    def list_access(scope: Optional[str] = Query(default=None, max_length=200)):
        with storage.session() as conn:
            if scope:
                rows = conn.execute(
                    'SELECT * FROM access_grants WHERE scope = ? ORDER BY principal COLLATE NOCASE',
                    (scope,)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM access_grants ORDER BY scope, principal COLLATE NOCASE').fetchall()
            scopes = [r['scope'] for r in conn.execute(
                'SELECT DISTINCT scope FROM access_grants ORDER BY scope')]
        items = [_grant_view(dict(r)) for r in rows]
        return {'items': items, 'scopes': scopes}

    @router.post('/api/settings/access/{principal}/{scope}')
    def update_access(principal: str, scope: str, req: UpdateGrantRequest):
        grant = call(lambda: policy.get_grant(principal, scope))
        if grant is None:
            raise HTTPException(404, 'No grant for this principal/scope. Grants appear when an MCP client is approved.')
        updated = call(lambda: policy.update_grant(
            principal, scope,
            read=req.access != 'revoked',
            write=req.access == 'full',
            expected_revision=req.expected_revision,
            actor='local-owner',
            expires_at=req.expires_at,
        ))
        return _grant_view(updated)

    @router.get('/api/settings/access/audit')
    def access_audit(principal: Optional[str] = Query(default=None, max_length=200),
                     scope: Optional[str] = Query(default=None, max_length=200),
                     limit: int = Query(default=50, ge=1, le=200)):
        query = 'SELECT * FROM access_audit'
        clauses, params = [], []
        if principal:
            clauses.append('principal = ?')
            params.append(principal)
        if scope:
            clauses.append('scope = ?')
            params.append(scope)
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY recorded_at DESC, rowid DESC LIMIT ?'
        params.append(limit)
        with storage.session() as conn:
            rows = conn.execute(query, params).fetchall()
        items = []
        for row in rows:
            entry = dict(row)
            try:
                entry['details'] = json.loads(entry.pop('details_json') or '{}')
            except (ValueError, TypeError):
                entry['details'] = {}
            items.append(entry)
        return {'items': items}

    # ------------------------------------------------------------------
    # Local preferences (UI + automation)
    # ------------------------------------------------------------------
    @router.get('/api/settings/preferences')
    def get_preferences():
        return {'preferences': _read_preferences(data_dir()), 'schema': PREFERENCE_SCHEMA}

    @router.post('/api/settings/preferences')
    def update_preferences(req: PreferencesUpdateRequest):
        patch = {key: value for key, value in req.model_dump().items() if value is not None}
        if not patch:
            raise HTTPException(422, 'No preference changes supplied')
        return {'preferences': call(lambda: _merge_preferences(data_dir(), patch))}

    # ------------------------------------------------------------------
    # Backups (owner-only consistent snapshots)
    # ------------------------------------------------------------------
    @router.get('/api/settings/backups')
    def list_backups():
        root = _backups_dir(data_dir())
        items = []
        if root.is_dir():
            for entry in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
                if entry.is_dir() and not entry.name.startswith('.'):
                    items.append(_backup_view(entry.name, entry))
        return {'items': items, 'dir': str(root), 'restore_dir': str(_restore_dir(data_dir()))}

    @router.post('/api/settings/backups')
    def create_backup(req: CreateBackupRequest):
        root = _backups_dir(data_dir())
        call(lambda: root.mkdir(parents=True, exist_ok=True))
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        label = (req.label or '').strip().replace('/', '-').replace('\\', '-')
        name = f'snapshot-{stamp}' + (f'-{label}' if label else '')
        destination = root / name
        suffix = 1
        while destination.exists():
            suffix += 1
            destination = root / f'{name}-{suffix}'
        manifest = call(lambda: create_snapshot(data_dir(), destination, timeout_seconds=300))
        view = _backup_view(destination.name, destination)
        view['created_at'] = manifest.get('created_at')
        return view

    @router.post('/api/settings/backups/{name}/verify')
    def verify_backup(name: str):
        directory = call(lambda: _resolve_backup(data_dir(), name))
        manifest = call(lambda: verify_snapshot(directory, timeout_seconds=300))
        return {'name': name, 'verified': True, 'files': len(manifest.get('files') or {}),
                'created_at': manifest.get('created_at'), 'format_version': manifest.get('format_version')}

    @router.post('/api/settings/backups/{name}/restore')
    def restore_backup(name: str):
        directory = call(lambda: _resolve_backup(data_dir(), name))
        target_root = _restore_dir(data_dir())
        call(lambda: target_root.mkdir(parents=True, exist_ok=True))
        destination = target_root / f'{name}-restored'
        suffix = 1
        while destination.exists():
            suffix += 1
            destination = target_root / f'{name}-restored-{suffix}'
        report = call(lambda: restore_snapshot(directory, destination, timeout_seconds=300))
        return {'name': name, 'restored_to': str(destination), 'report': report,
                'note': '恢复到新目录，当前数据目录未被覆盖。'}

    # ------------------------------------------------------------------
    # Maintenance (owner-only)
    # ------------------------------------------------------------------
    @router.post('/api/settings/maintenance/vacuum')
    def vacuum():
        db_path = Path(storage.db_path)
        before = db_path.stat().st_size if db_path.exists() else None
        call(lambda: _vacuum(db_path))
        after = db_path.stat().st_size if db_path.exists() else None
        reclaimed = (before - after) if (before is not None and after is not None) else None
        return {'before_bytes': before, 'after_bytes': after, 'reclaimed_bytes': reclaimed}

    @router.get('/api/settings/export')
    def export_library(limit: int = Query(default=5000, ge=1, le=100000)):
        """Portable JSON export of the knowledge surface (not a snapshot)."""
        with storage.session() as conn:
            notes = [dict(r) for r in conn.execute(
                'SELECT id, revision, title, content, content_hash, author, scope, created_at '
                'FROM notes WHERE is_current = 1 ORDER BY created_at LIMIT ?', (limit,))]
            nodes = [dict(r) for r in conn.execute(
                'SELECT id, node_type, scope, title, content, maturity, depth, authority, '
                'confidence, importance, version, is_active, created_at FROM nodes LIMIT ?', (limit,))]
            edges = [dict(r) for r in conn.execute(
                'SELECT id, source_id, target_id, relation, scope, weight, confidence, '
                'authority, created_at FROM edges LIMIT ?', (limit,))]
        return {
            'format': 'ai-library-export',
            'format_version': 1,
            'exported_at': utc_now().isoformat(),
            'app_version': APP_VERSION,
            'counts': {'notes': len(notes), 'nodes': len(nodes), 'edges': len(edges)},
            'notes': notes,
            'nodes': nodes,
            'edges': edges,
        }

    # ------------------------------------------------------------------
    # System status (read-only)
    # ------------------------------------------------------------------
    @router.get('/api/settings/system')
    def system_status():
        db_path = Path(storage.db_path)
        counts = {}
        with storage.session() as conn:
            for label, table in (('notes', 'notes'), ('memory_nodes', 'nodes'), ('edges', 'edges'),
                                 ('candidates', 'candidates'), ('topics', 'collection_topics'),
                                 ('quarantine', 'quarantine'), ('backlog', 'system_memory_backlog'),
                                 ('embeddings', 'embedding_records'), ('grants', 'access_grants')):
                try:
                    counts[label] = conn.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n']
                except Exception:
                    counts[label] = None
            scopes = [r['scope'] for r in conn.execute(
                "SELECT DISTINCT scope FROM nodes UNION SELECT DISTINCT scope FROM notes ORDER BY scope")]
        try:
            db_size = db_path.stat().st_size
        except OSError:
            db_size = None
        try:
            keyword = coordinator.keyword_index.status()
        except Exception as exc:
            keyword = {'error': str(exc)}
        try:
            vector = coordinator.vector_index.status()
        except Exception as exc:
            vector = {'error': str(exc)}
        return {
            'version': APP_VERSION,
            'data_dir': str(db_path.parent),
            'db_path': str(db_path),
            'db_size_bytes': db_size,
            'project_root': str(_PROJECT_ROOT),
            'counts': counts,
            'scopes': scopes,
            'keyword_index': keyword,
            'vector_index': vector,
            'mcp_config_example': _mcp_config_example(db_path.parent),
        }

    @router.get('/api/settings/about')
    def about():
        db_path = Path(storage.db_path)
        return {
            'app_version': APP_VERSION,
            'project_root': str(_PROJECT_ROOT),
            'data_dir': str(db_path.parent),
            'db_path': str(db_path),
            'db_size_bytes': db_path.stat().st_size if db_path.exists() else None,
            'backups_dir': str(_backups_dir(db_path.parent)),
            'restore_dir': str(_restore_dir(db_path.parent)),
            'preferences_path': str(_prefs_path(db_path.parent)),
            'git_revision': _git_revision(),
            'python_version': platform.python_version(),
            'sqlite_version': sqlite3.sqlite_version,
            'platform': f'{platform.system()} {platform.release()}',
            'executable': sys.executable,
        }

    return router
