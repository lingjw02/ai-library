import pytest
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys

from backend.schema.models import utc_now

from backend.auth.policy import AccessPolicy
from backend.notes.notes_manager import NotesManager
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent-a', 'a')
    return storage, notes, policy


def test_revocation_persists_across_restarts_and_cannot_self_restore(tmp_path):
    storage, notes, policy = setup(tmp_path)
    policy.check('agent-a', 'a', write=True)
    grant = policy.get_grant('agent-a', 'a')
    policy.update_grant('agent-a', 'a', read=False, write=False, expected_revision=grant['revision'], actor='local-owner')
    restarted = AccessPolicy(storage)
    restarted.ensure_launch_grant('agent-a', 'a')
    with pytest.raises(PermissionError):
        restarted.check('agent-a', 'a')
    with pytest.raises(PermissionError):
        restarted.check('agent-a', 'other')


def test_scope_grant_write_guard_and_atomic_revocation(tmp_path):
    storage, notes, policy = setup(tmp_path)
    with policy.activate('agent-a', 'a', write=True):
        note = notes.create_note('first', 'original', scope='a')
    # Even a connection opened before revocation checks again at transaction entry.
    with policy.activate('agent-a', 'a', write=True):
        stale = storage.get_connection()
    grant = policy.get_grant('agent-a', 'a')
    policy.update_grant('agent-a', 'a', read=True, write=False, expected_revision=grant['revision'], actor='local-owner')
    try:
        with pytest.raises(PermissionError):
            stale.execute('BEGIN IMMEDIATE')
    finally:
        stale.close()
    with policy.activate('agent-a', 'a'):
        assert notes.get_note(note.id).content == 'original'
        with pytest.raises(PermissionError):
            notes.revise_note(note.id, 'bad', 'bad', expected_revision=1)
    assert notes.get_note(note.id).revision == 1


def test_grant_revision_and_audit_share_transaction(tmp_path):
    storage, notes, policy = setup(tmp_path)
    grant = policy.get_grant('agent-a', 'a')
    policy.update_grant('agent-a', 'a', read=True, write=False, expected_revision=grant['revision'], actor='local-owner')
    with pytest.raises(ValueError):
        policy.update_grant('agent-a', 'a', read=True, write=True, expected_revision=grant['revision'], actor='local-owner')
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM access_audit').fetchone()[0] == 2


def test_expired_grant_denies_reads_and_writes_without_regrant_on_restart(tmp_path, monkeypatch):
    storage, notes, policy = setup(tmp_path)
    now = utc_now()
    expires = (now + timedelta(minutes=1)).isoformat()
    policy.update_grant('agent-a', 'a', read=True, write=True, expected_revision=1,
                        actor='local-owner', expires_at=expires)
    with policy.activate('agent-a', 'a', write=True):
        stale = storage.get_connection()
    monkeypatch.setattr('backend.auth.policy.utc_now', lambda: now + timedelta(minutes=2))
    with pytest.raises(PermissionError):
        policy.check('agent-a', 'a')
    try:
        with pytest.raises(PermissionError):
            stale.execute('BEGIN IMMEDIATE')
    finally:
        stale.close()
    restarted = AccessPolicy(storage)
    restarted.ensure_launch_grant('agent-a', 'a')
    with pytest.raises(PermissionError):
        restarted.check('agent-a', 'a', write=True)


def test_expiry_is_preserved_unless_explicitly_changed_and_checked_at_commit(tmp_path, monkeypatch):
    storage, notes, policy = setup(tmp_path)
    now = utc_now()
    expires = (now + timedelta(minutes=1)).isoformat()
    policy.update_grant('agent-a', 'a', read=True, write=True, expected_revision=1,
                        actor='owner', expires_at=expires)
    policy.update_grant('agent-a', 'a', read=True, write=True, expected_revision=2, actor='owner')
    assert policy.get_grant('agent-a', 'a')['expires_at'] == expires
    note = notes.create_note('original', 'fixture content', scope='a')
    with policy.activate('agent-a', 'a', write=True):
        conn = storage.get_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE notes SET title = ? WHERE id = ?', ('uncommitted', note.id))
            monkeypatch.setattr('backend.auth.policy.utc_now', lambda: now + timedelta(minutes=2))
            with pytest.raises(PermissionError):
                conn.commit()
            conn.rollback()
        finally:
            conn.close()
    policy.update_grant('agent-a', 'a', read=True, write=True, expected_revision=3, actor='owner', expires_at=None)
    policy.check('agent-a', 'a', write=True)
    assert notes.get_note(note.id).title == 'original'


def test_owner_cli_can_revoke_and_restore_with_cas_and_no_new_database(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'knowledge_os.db'))
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('codex', 'default')
    script = Path(__file__).resolve().parents[1]/'scripts/manage_mcp_access.py'
    command = [sys.executable, str(script), '--data-dir', str(tmp_path), '--principal', 'codex', '--scope', 'default']
    revoked = subprocess.run(command + ['set', '--access', 'revoked', '--expected-revision', '1'], capture_output=True, text=True)
    assert revoked.returncode == 0, revoked.stderr
    assert json.loads(revoked.stdout)['can_read'] == 0
    rejected = subprocess.run(command + ['set', '--access', 'full', '--expected-revision', '1'], capture_output=True, text=True)
    assert rejected.returncode != 0
    restored = subprocess.run(command + ['set', '--access', 'read-only', '--expected-revision', '2'], capture_output=True, text=True)
    assert restored.returncode == 0, restored.stderr
    policy.check('codex', 'default')
    with pytest.raises(PermissionError):
        policy.check('codex', 'default', write=True)
    nonexistent = tmp_path/'not-created'
    rejected = subprocess.run([sys.executable, str(script), '--data-dir', str(nonexistent), '--principal', 'codex', 'show'], capture_output=True)
    assert rejected.returncode != 0 and not nonexistent.exists()
