"""Local-owner settings surface: MCP grant administration and system status."""
import pytest
from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.auth.policy import AccessContext, AccessPolicy, current_access
from backend.storage.canonical.sqlite_storage import CanonicalStorage


@pytest.fixture
def setup(tmp_path):
    db = str(tmp_path / 'db')
    args = (db, str(tmp_path / 'raw'), str(tmp_path / 'art'))
    client = TestClient(create_app(*args))
    policy = AccessPolicy(CanonicalStorage(db))
    policy.ensure_launch_grant('codex', 'default')
    policy.ensure_launch_grant('gemini', 'default')
    return client, policy


def grant(client, principal):
    items = client.get('/api/settings/access').json()['items']
    return next(i for i in items if i['principal'] == principal)


def test_list_grants_reports_access_label(setup):
    client, _ = setup
    body = client.get('/api/settings/access').json()
    assert body['scopes'] == ['default']
    assert {i['principal'] for i in body['items']} == {'codex', 'gemini'}
    assert grant(client, 'codex')['access'] == 'full'
    assert grant(client, 'codex')['can_write'] is True


def test_update_grant_is_revision_checked_and_audited(setup):
    client, _ = setup
    current = grant(client, 'codex')
    updated = client.post('/api/settings/access/codex/default', json={
        'access': 'read-only', 'expected_revision': current['revision'], 'expires_at': None})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body['access'] == 'read-only'
    assert body['can_read'] is True and body['can_write'] is False
    assert body['revision'] == current['revision'] + 1

    stale = client.post('/api/settings/access/codex/default', json={
        'access': 'full', 'expected_revision': current['revision'], 'expires_at': None})
    assert stale.status_code == 422

    audit = client.get('/api/settings/access/audit', params={'principal': 'codex'}).json()['items']
    assert audit[0]['event'] == 'GRANT_CHANGED'
    assert audit[0]['actor'] == 'local-owner'
    assert audit[0]['details']['can_write'] is False


def test_revoke_and_restore(setup):
    client, _ = setup
    rev = grant(client, 'gemini')['revision']
    revoked = client.post('/api/settings/access/gemini/default', json={
        'access': 'revoked', 'expected_revision': rev, 'expires_at': None}).json()
    assert revoked['access'] == 'revoked' and revoked['can_read'] is False
    restored = client.post('/api/settings/access/gemini/default', json={
        'access': 'full', 'expected_revision': revoked['revision'], 'expires_at': None}).json()
    assert restored['access'] == 'full' and restored['can_read'] is True


def test_unknown_principal_and_invalid_level(setup):
    client, _ = setup
    assert client.post('/api/settings/access/nobody/default', json={
        'access': 'full', 'expected_revision': 1}).status_code == 404
    assert client.post('/api/settings/access/codex/default', json={
        'access': 'admin', 'expected_revision': 1}).status_code == 422


def test_agent_context_cannot_administer_grants(setup):
    _, policy = setup
    token = current_access.set(AccessContext('codex', 'default', True))
    try:
        with pytest.raises(PermissionError):
            policy.update_grant('codex', 'default', read=True, write=True,
                                expected_revision=1, actor='local-owner')
    finally:
        current_access.reset(token)


def test_system_status_exposes_counts_and_mcp_template(setup):
    client, _ = setup
    client.post('/api/notes', json={'title': '原文', 'content': '保留原文'})
    body = client.get('/api/settings/system').json()
    assert body['version']
    assert body['counts']['notes'] == 1
    assert body['scopes'] == ['default']
    assert 'keyword_index' in body and 'vector_index' in body
    template = body['mcp_config_example']['mcpServers']['ai-memory-library']
    assert template['args'][0].endswith('scripts/run_mcp.py')
    assert '--scope' in template['args']


# ---------------------------------------------------------------------------
# Preferences, backups, maintenance, about
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated(tmp_path):
    """Nest the data directory one level down so backups land inside tmp_path.

    The database must be named knowledge_os.db directly inside the data
    directory: backend.storage.recovery locates the canonical database by that
    exact name when building a snapshot.
    """
    data = tmp_path / 'data'
    data.mkdir()
    return TestClient(create_app(str(data / 'knowledge_os.db'), str(data / 'raw'), str(data / 'art')))


def test_preferences_defaults_then_persist(isolated):
    client = isolated
    defaults = client.get('/api/settings/preferences').json()['preferences']
    assert defaults['ui'] == {'theme': 'dark', 'fontScale': 'standard', 'universeQuality': 'high'}
    assert defaults['automation']['notifyOnIngest'] is True

    saved = client.post('/api/settings/preferences', json={
        'ui': {'theme': 'light', 'fontScale': 'large'},
        'automation': {'autoRecordToLibrary': True}}).json()['preferences']
    assert saved['ui']['theme'] == 'light'
    assert saved['ui']['universeQuality'] == 'high'      # untouched keys survive
    assert saved['automation']['autoRecordToLibrary'] is True

    reread = client.get('/api/settings/preferences').json()['preferences']
    assert reread['ui']['fontScale'] == 'large'
    assert reread['automation']['autoRecordToLibrary'] is True


def test_preferences_reject_unknown_and_invalid(isolated):
    client = isolated
    assert client.post('/api/settings/preferences', json={'ui': {'theme': 'neon'}}).status_code == 422
    assert client.post('/api/settings/preferences', json={'ui': {'nope': 1}}).status_code == 422
    assert client.post('/api/settings/preferences', json={'nope': {}}).status_code == 422
    assert client.post('/api/settings/preferences', json={}).status_code == 422
    assert client.post('/api/settings/preferences', json={'ui': {'theme': True}}).status_code == 422


def test_backup_create_verify_restore_and_list(isolated):
    client = isolated
    client.post('/api/notes', json={'title': '备份前', 'content': '内容'})
    assert client.get('/api/settings/backups').json()['items'] == []

    created = client.post('/api/settings/backups', json={'label': 'unit'})
    assert created.status_code == 200, created.text
    view = created.json()
    assert view['name'].startswith('snapshot-') and view['name'].endswith('-unit')
    assert view['complete'] is True and view['files'] >= 1 and view['created_at']

    listed = client.get('/api/settings/backups').json()['items']
    assert [i['name'] for i in listed] == [view['name']]

    verified = client.post(f"/api/settings/backups/{view['name']}/verify").json()
    assert verified['verified'] is True and verified['format_version'] == 1

    restored = client.post(f"/api/settings/backups/{view['name']}/restore")
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert 'restored' in body['restored_to']
    from pathlib import Path
    assert Path(body['restored_to']).is_dir()


def test_backup_unknown_name_and_traversal_are_refused(isolated):
    client = isolated
    assert client.post('/api/settings/backups/nope/verify').status_code == 404
    assert client.post('/api/settings/backups/..%2Fevil/verify').status_code in (404, 405)
    assert client.post('/api/settings/backups/.hidden/verify').status_code in (404, 422)


def test_export_and_vacuum_and_about(isolated):
    client = isolated
    client.post('/api/notes', json={'title': '导出目标', 'content': '正文'})

    exported = client.get('/api/settings/export').json()
    assert exported['format'] == 'ai-library-export'
    assert exported['counts']['notes'] == 1
    assert exported['notes'][0]['title'] == '导出目标'

    vacuumed = client.post('/api/settings/maintenance/vacuum').json()
    assert vacuumed['after_bytes'] is not None
    assert vacuumed['reclaimed_bytes'] is not None

    about = client.get('/api/settings/about').json()
    assert about['app_version'] and about['sqlite_version'] and about['python_version']
    assert about['data_dir'] and about['db_path']
    assert about['backups_dir'].endswith('backups')
    assert about['preferences_path'].endswith('preferences.json')
