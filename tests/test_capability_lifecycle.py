import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.capabilities.connections.agent_connections import AgentConnectionManager
from backend.capabilities.registry.capability_registry import CapabilityRegistry, SkillBundle
from backend.storage.canonical.sqlite_storage import CanonicalStorage


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    registry = CapabilityRegistry(storage, str(tmp_path/'artifacts'))
    manager = AgentConnectionManager(storage, registry)
    for version in ['1', '2']:
        registry.register_skill(SkillBundle('shared', 'fixture', 'test-only', '# inert', version=version))
    return storage, registry, manager


def test_upgrade_rollback_and_unbind_preserve_shared_versions(tmp_path):
    storage, registry, manager = setup(tmp_path)
    a, b = manager.create_profile('codex'), manager.create_profile('gemini-cli')
    a = manager.bind_skill(a.id, 'shared', '1', expected_revision=a.revision)
    b = manager.bind_skill(b.id, 'shared', '1', expected_revision=b.revision)
    assert manager.bind_skill(a.id, 'shared', '1', expected_revision=a.revision).revision == a.revision
    upgraded = manager.bind_skill(a.id, 'shared', '2', expected_revision=a.revision)
    assert upgraded.revision == a.revision + 1
    assert manager.get_profile(b.id).skill_bindings[0].pinned_version == '1'
    assert upgraded.compatibility_status == 'UNVERIFIED'
    rolled = manager.bind_skill(a.id, 'shared', '1', expected_revision=upgraded.revision)
    unbound = manager.unbind_capability(a.id, 'shared', 'SKILL', expected_revision=rolled.revision)
    assert unbound.skill_bindings == []
    assert manager.get_profile(b.id).skill_bindings[0].pinned_version == '1'
    for version in ['1', '2']:
        assert (Path(registry.get_capability('shared', version).manifest['artifact_path'])/'SKILL.md').exists()
    events = manager.get_profile_audit(a.id)
    assert {event['event_type'] for event in events} >= {'PROFILE_CREATED', 'SKILL_BOUND', 'SKILL_UNBOUND'}
    assert all(event['actor']['id'] == 'local-owner' for event in events)
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM system_memory_logsheet').fetchone()[0] == 0


def test_stale_profile_mutation_and_audit_failure_are_atomic(tmp_path, monkeypatch):
    storage, registry, manager = setup(tmp_path)
    profile = manager.create_profile('codex')
    bound = manager.bind_skill(profile.id, 'shared', '1', expected_revision=profile.revision)
    with pytest.raises(ValueError, match='revision'):
        manager.unbind_capability(profile.id, 'shared', 'SKILL', expected_revision=profile.revision)
    with storage.session() as conn:
        before = conn.execute('SELECT COUNT(*) FROM transactional_outbox').fetchone()[0]
    def broken(*args, **kwargs):
        raise RuntimeError('injected audit failure')
    monkeypatch.setattr(registry, '_record_audit', broken)
    with pytest.raises(RuntimeError):
        manager.bind_skill(profile.id, 'shared', '2', expected_revision=bound.revision)
    recovered = manager.get_profile(profile.id)
    assert recovered.skill_bindings[0].pinned_version == '1'
    assert recovered.revision == bound.revision
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM transactional_outbox').fetchone()[0] == before


def test_concurrent_binding_edits_have_one_winner_and_matching_audit(tmp_path):
    storage, registry, manager = setup(tmp_path)
    profile = manager.create_profile('codex')
    def edit(version):
        try:
            return manager.bind_skill(profile.id, 'shared', version, expected_revision=1).skill_bindings[0].pinned_version
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ['1', '2']))
    assert results.count(None) == 1
    winner = next(x for x in results if x is not None)
    assert manager.get_profile(profile.id).skill_bindings[0].pinned_version == winner
    audit = manager.get_profile_audit(profile.id)
    assert len(audit) == 2  # creation and exactly one successful binding
    assert audit[0]['details']['after']['skills'][0]['pinned_version'] == winner


def test_old_profile_schema_gets_only_current_revision(tmp_path):
    storage, registry, manager = setup(tmp_path)
    # Simulate old persisted table; no fabricated historical revisions.
    with storage.session() as conn:
        conn.execute('DROP TABLE agent_profiles')
        conn.execute('''CREATE TABLE agent_profiles(id TEXT PRIMARY KEY, client_kind TEXT,
            client_version TEXT, device_id TEXT, skill_bindings_json TEXT, mcp_bindings_json TEXT,
            compatibility_status TEXT, created_at TEXT, updated_at TEXT)''')
        conn.execute("INSERT INTO agent_profiles VALUES('old','codex','UNKNOWN','local','[]','[]','UNVERIFIED','old','old')")
    migrated = AgentConnectionManager(storage, registry)
    assert migrated.get_profile('old').revision == 1


def test_rest_binding_diff_and_unbind_are_revision_guarded(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    with TestClient(create_app(str(tmp_path/'api.db'), str(tmp_path/'raw'), str(tmp_path/'artifacts'))) as client:
        cap = client.post('/api/capabilities/mcp', json={'name': 'library', 'description': 'fixture',
            'server_def': {'server_type': 'LOCAL', 'command': 'python', 'args': ['run_mcp.py']}}).json()
        profile = client.post('/api/agent-profiles', json={'client_kind': 'codex'}).json()
        assert profile['client_version'] == 'UNKNOWN'
        url = f"/api/agent-profiles/{profile['id']}/bindings/mcp/{cap['id']}"
        body = {'pinned_version': cap['version'], 'installation_or_endpoint_ref': 'python',
                'expected_revision': profile['revision']}
        bound = client.put(url, json=body)
        assert bound.status_code == 200
        assert client.put(url, json=body).status_code == 409
        plan = client.post(f"/api/agent-profiles/{profile['id']}/client-plan", json={'before': '# existing comment\n'})
        assert plan.status_code == 200
        assert '[mcp_servers.library]' in plan.json()['proposed']
        assert plan.json()['applied'] is False
        removed = client.delete(url, params={'expected_revision': bound.json()['revision']})
        assert removed.status_code == 200 and removed.json()['mcp_bindings'] == []
        assert client.get('/api/capabilities/' + cap['id']).status_code == 200
        audit = client.get(f"/api/agent-profiles/{profile['id']}/audit").json()
        assert audit[0]['event_type'] == 'MCP_UNBOUND'
