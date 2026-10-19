"""Owner publication and live MCP policy delivery use isolated databases."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.adapters.mcp.stdio_server import build_service
from backend.auth.policy import AccessContext, current_access


@pytest.fixture
def environment(tmp_path):
    client = TestClient(create_app(str(tmp_path / 'knowledge_os.db'),
                                   str(tmp_path / 'raw_sources'), str(tmp_path / 'artifacts')))
    return client, tmp_path


def current(client):
    result = client.get('/api/settings/usage-rules')
    assert result.status_code == 200, result.text
    return result.json()


def publish(client, state, **changes):
    return client.post('/api/settings/usage-rules', json={
        'expected_revision': state['revision'], 'rules': {**state['rules'], **changes}})


def test_confirmed_defaults_and_optional_rules_not_silently_enabled(environment):
    client, _ = environment
    state = current(client)
    assert state['revision'] == 1
    assert state['rules']['read_scope'] == 'all_authorized'
    assert state['rules']['subtopic_min_items'] == 6
    assert state['rules']['multitopic_mode'] == 'per_topic_summary'
    assert state['rules']['import_sources'] == 'all_imported'
    assert state['rules']['connection_failure'] == 'independent_only'
    assert state['rules']['conflict_handling'] == 'pause_related'
    assert state['rules']['approval_mode'] == 'per_task'
    assert len(state['content_hash']) == 64
    assert state['suggestions'] and all(s['enabled'] is False for s in state['suggestions'])
    assert set(state['rules']) == {f['key'] for f in state['fields']}


def test_publication_cas_history_noop_and_restore_previous_values(environment):
    client, _ = environment
    first = current(client)
    result = publish(client, first, subtopic_min_items=8)
    assert result.status_code == 200, result.text
    second = result.json()
    assert second['revision'] == 2 and second['content_hash'] != first['content_hash']
    assert publish(client, first, subtopic_min_items=9).status_code == 409
    assert current(client)['rules']['subtopic_min_items'] == 8
    assert publish(client, second).json()['revision'] == 2
    restored = publish(client, second, **first['rules']).json()
    assert restored['revision'] == 3 and restored['rules'] == first['rules']
    history = client.get('/api/settings/usage-rules/history').json()['items']
    assert [item['revision'] for item in history] == [3, 2, 1]
    assert history[1]['rules']['subtopic_min_items'] == 8


@pytest.mark.parametrize('changes', [
    {'subtopic_min_items': True}, {'subtopic_min_items': 1}, {'subtopic_min_items': 501},
    {'read_on_start': 'false'}, {'read_on_start': None}, {'read_scope': 'unrestricted'},
    {'approved': True},
])
def test_invalid_edits_do_not_publish(environment, changes):
    client, _ = environment
    before = current(client)
    assert publish(client, before, **changes).status_code == 422
    assert current(client)['revision'] == 1


def test_simultaneous_edits_cannot_overwrite_each_other(environment):
    client, _ = environment
    first = current(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: publish(client, first, subtopic_min_items=n), [7, 8]))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert current(client)['revision'] == 2


def test_connected_agent_cannot_publish_owner_rules(environment):
    client, root = environment
    service = build_service(root, 'default', False, 'codex')
    state = current(client)
    from backend.settings.usage_rules import UsageRulesStore
    token = current_access.set(AccessContext('codex', 'default', True))
    try:
        with pytest.raises(PermissionError):
            UsageRulesStore(service.storage).publish(
                {**state['rules'], 'subtopic_min_items': 9}, expected_revision=1)
    finally:
        current_access.reset(token)
    assert current(client)['revision'] == 1


def test_already_running_readonly_agent_receives_updates_without_changing_legacy_json(environment):
    client, root = environment
    service = build_service(root, 'default', True, 'readonly-agent')
    definitions = {d['name'] for d in service.get_tool_definitions()}
    assert 'get_usage_rules' in definitions and 'publish_usage_rules' not in definitions
    first = service.execute_tool('list_user_notes', {})
    assert not first.get('isError') and first['content'][0]['text'] == '[]'
    assert first['_meta']['ai-library/usage-rules']['revision'] == 1
    assert len(first['content']) == 2
    second = service.execute_tool('list_user_notes', {})
    assert len(second['content']) == 1
    state = current(client)
    publish(client, state, subtopic_min_items=12)
    updated = service.execute_tool('get_usage_rules', {})
    assert not updated.get('isError')
    assert updated['structured_content']['revision'] == 2
    assert updated['structured_content']['rules']['subtopic_min_items'] == 12
    assert updated['_meta']['ai-library/usage-rules']['revision'] == 2
    deliveries = current(client)['deliveries']
    delivery = next(d for d in deliveries if d['principal'] == 'readonly-agent')
    assert delivery['revision'] == 2
    assert delivery['status'] == 'SENT_ONLY'
    from backend.auth.policy import AccessPolicy
    access = AccessPolicy(service.storage)
    grant = access.get_grant('readonly-agent', 'default')
    access.update_grant('readonly-agent', 'default', read=False, write=False,
                        expected_revision=grant['revision'], actor='local-owner')
    denied = service.execute_tool('get_usage_rules', {})
    assert denied['isError'] is True and '_meta' not in denied


@pytest.mark.asyncio
async def test_live_stdio_connection_receives_published_version(environment):
    import json
    import sys
    from pathlib import Path
    from mcp import Client, StdioServerParameters
    client, root = environment
    params = StdioServerParameters(command=sys.executable, args=[
        str(Path(__file__).resolve().parents[1] / 'scripts/run_mcp.py'),
        '--data-dir', str(root), '--read-only', '--principal', 'protocol-probe'],
        env={'AI_LIBRARY_DATA_DIR': str(root / 'module-default')})
    async with Client(params, read_timeout_seconds=15) as agent:
        response = await agent.call_tool('get_usage_rules', {})
        assert not response.is_error
        initial = json.loads(response.content[0].text)
        assert initial['revision'] == 1
        before = current(client)
        assert publish(client, before, subtopic_min_items=10).status_code == 200
        response = await agent.call_tool('get_usage_rules', {})
        assert not response.is_error
        updated = json.loads(response.content[0].text)
        assert updated['revision'] == 2 and updated['rules']['subtopic_min_items'] == 10
        legacy = await agent.call_tool('list_user_notes', {})
        assert legacy.content[0].text == '[]'


@pytest.mark.asyncio
@pytest.mark.parametrize('deny', ['revoked', 'expired'])
async def test_initial_handshake_does_not_disclose_published_rules_to_denied_agent(environment, deny):
    import sys
    from pathlib import Path
    from mcp import Client, StdioServerParameters
    client, root = environment
    service = build_service(root, 'private', True, 'denied-agent')
    first = current(client)
    updated = publish(client, first, subtopic_min_items=23).json()
    grant = service.access_policy.get_grant('denied-agent', 'private')
    service.access_policy.update_grant('denied-agent', 'private', read=deny != 'revoked', write=False,
                                      expected_revision=grant['revision'], actor='local-owner',
                                      expires_at='2000-01-01T00:00:00+00:00' if deny == 'expired' else None)
    params = StdioServerParameters(command=sys.executable, args=[
        str(Path(__file__).resolve().parents[1] / 'scripts/run_mcp.py'),
        '--data-dir', str(root), '--scope', 'private', '--read-only', '--principal', 'denied-agent'],
        env={'AI_LIBRARY_DATA_DIR': str(root / 'module-default')})
    async with Client(params, read_timeout_seconds=15) as agent:
        assert updated['content_hash'] not in (agent.instructions or '')
        assert '至少 23 条' not in (agent.instructions or '')
        assert (await agent.call_tool('get_usage_rules', {})).is_error


def test_snapshot_and_restore_preserve_rule_history(environment):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.settings.usage_rules import UsageRulesStore
    client, root = environment
    first = current(client)
    updated = publish(client, first, subtopic_min_items=11).json()
    snapshot = root.parent / (root.name + '-snapshot')
    restored = root.parent / (root.name + '-restored')
    create_snapshot(root, snapshot)
    restore_snapshot(snapshot, restored)
    rules = UsageRulesStore(CanonicalStorage(str(restored / 'knowledge_os.db')))
    assert rules.get()['content_hash'] == updated['content_hash']
    assert [item['revision'] for item in rules.history()['items']] == [2, 1]


def test_importing_mcp_transport_does_not_initialize_a_web_database(tmp_path):
    import os
    import subprocess
    import sys
    module_data = tmp_path / 'must-not-exist'
    process = subprocess.run([sys.executable, '-c',
        'from backend.adapters import KnowledgeOsMcpServer; import backend.adapters.mcp.stdio_server'],
        env={**os.environ, 'AI_LIBRARY_DATA_DIR': str(module_data)}, capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert not module_data.exists(), 'Importing MCP must not initialize the default web application database'
