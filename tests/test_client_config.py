import json
import tomllib

import pytest

from backend.capabilities.connections.client_config import plan_local_mcp_config


SERVER = {'command': 'D:/apps/记忆 library/python.exe',
          'args': ['D:/apps/记忆 library/run_mcp.py', '--scope', 'default']}


@pytest.mark.parametrize('client,before', [
    ('codex', b'# keep comment\r\nmodel = "existing"\r\n[mcp_servers.other]\r\ncommand = "other"\r\n'),
    ('gemini-cli', b'{"theme":"existing", "mcpServers":{"other":{"command":"other"}}}\r\n'),
])
def test_client_plan_preserves_unrelated_settings_and_exact_rollback(client, before):
    plan = plan_local_mcp_config(client, before, {'ai-memory-library': SERVER})
    after = plan.apply_to(before)
    parsed = tomllib.loads(after.decode()) if client == 'codex' else json.loads(after)
    servers = parsed['mcp_servers' if client == 'codex' else 'mcpServers']
    assert servers['other']['command'] == 'other'
    assert servers['ai-memory-library']['args'] == SERVER['args']
    assert servers['ai-memory-library']['command'] == SERVER['command']
    assert plan.rollback(after) == before
    assert plan.apply_to(after) == after  # retry after successful application
    assert plan.rollback(before) == before
    with pytest.raises(ValueError, match='changed'):
        plan.apply_to(before + b' ')
    with pytest.raises(ValueError, match='changed'):
        plan.rollback(after + b' ')
    assert plan.diff and plan.before_hash != plan.after_hash


@pytest.mark.parametrize('client', ['codex', 'gemini-cli'])
def test_same_binding_is_noop_and_collision_needs_review(client):
    plan = plan_local_mcp_config(client, None, {'ai-memory-library': SERVER})
    assert plan.rollback(plan.after) is None  # original file absent, not empty
    again = plan_local_mcp_config(client, plan.after, {'ai-memory-library': SERVER})
    assert again.after == plan.after
    changed = {**SERVER, 'args': ['--different']}
    with pytest.raises(ValueError, match='already exists'):
        plan_local_mcp_config(client, plan.after, {'ai-memory-library': changed})


@pytest.mark.parametrize('client,before,servers', [
    ('unknown', None, {'ok': SERVER}),
    ('codex', None, {'bad\nname': SERVER}),
    ('codex', b'not valid toml', {'ok': SERVER}),
    ('gemini-cli', b'{"mcpServers":{},"mcpServers":{}}', {'ok': SERVER}),
    ('gemini-cli', b'[]', {'ok': SERVER}),
    ('gemini-cli', b'{"mcpServers":5}', {'ok': SERVER}),
    ('codex', None, {'ok': {**SERVER, 'env': {'KEY': '${SECRET}'}}}),
    ('codex', None, {'ok': {'url': 'https://example.test/mcp'}}),
])
def test_unsupported_or_ambiguous_config_fails_closed(client, before, servers):
    with pytest.raises(ValueError):
        plan_local_mcp_config(client, before, servers)


@pytest.mark.parametrize('client', ['codex', 'gemini-cli'])
def test_registry_binding_plan_uses_pinned_version_and_preserves_unknown_compatibility(tmp_path, client):
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.capabilities.registry.capability_registry import CapabilityRegistry, McpServerDefinition
    from backend.capabilities.connections.agent_connections import AgentConnectionManager
    storage = CanonicalStorage(str(tmp_path/'db'))
    registry = CapabilityRegistry(storage, str(tmp_path/'artifacts'))
    manager = AgentConnectionManager(storage, registry)
    first = registry.register_mcp_server('library', 'test', McpServerDefinition(server_type='LOCAL', **SERVER),
        cap_id='shared', version='1.0.0')
    profile = manager.create_profile(client, client_version='fixture')
    manager.bind_mcp(profile.id, first.id, endpoint_or_install_ref=SERVER['command'], pinned_version='1.0.0')
    registry.register_mcp_server('library', 'test', McpServerDefinition(server_type='LOCAL', **{**SERVER, 'args': ['new']}),
        cap_id='shared', version='2.0.0')
    plan = manager.plan_client_config(profile.id)
    assert 'run_mcp.py' in plan.after.decode()
    assert manager.get_profile(profile.id).compatibility_status == 'UNVERIFIED'
    assert manager.get_profile(profile.id).mcp_bindings[0].pinned_version == '1.0.0'
