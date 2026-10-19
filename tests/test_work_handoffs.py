"""Work exchange is separate from memory authority and actual host execution."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from backend.adapters.mcp.stdio_server import build_service


ROOT = Path(__file__).resolve().parents[1]


def payload():
    return {
        'schema_version': '1.0', 'title': 'Gateway 兼容',
        'goal': '让另一个系统接续工作', 'approved_scope': '统一交接包',
        'target_system': 'antigravity',
        'decisions': [{'statement': '仅做交接', 'state': 'USER_APPROVED',
                       'source_ref': 'user-reply:2026-10-12'}],
        'progress': [{'description': '验证交接链路', 'state': 'IN_PROGRESS', 'evidence': []}],
        'artifacts': [{'path': 'D:/project/work/result.md', 'availability': 'UNCHECKED',
                       'content_hash': None, 'revision': None, 'notes': ''}],
        'verifications': [{'command': 'pytest', 'result': 'NOT_RUN', 'evidence': ''}],
        'blockers': [], 'next_steps': ['读取文件，执行验证'], 'open_questions': [],
    }


def call(service, name, arguments, *, error=False):
    result = service.execute_tool(name, arguments)
    assert bool(result.get('isError')) == error, result
    decoded = json.loads(result['content'][0]['text'])
    if not error:
        assert result['structured_content'] == decoded
    return decoded


def create(service, key='create-1', value=None):
    return call(service, 'create_work_handoff', {'request_key': key, 'payload': value or payload()})


def test_two_systems_share_versions_idempotency_and_receipts_without_changing_memory(tmp_path):
    codex = build_service(tmp_path, 'project-a', False, 'codex')
    other = build_service(tmp_path, 'project-a', False, 'antigravity')
    first = create(codex)
    assert first['revision'] == 1 and first['created_by'] == 'codex'
    assert create(codex) == first
    assert call(other, 'get_work_handoff', {'handoff_id': first['id']})['payload'] == payload()
    receipt_args = {'handoff_id': first['id'], 'revision': 1, 'content_hash': first['content_hash']}
    receipt = call(other, 'acknowledge_work_handoff', receipt_args)
    assert receipt['received_by'] == 'antigravity' and receipt['meaning'] == 'RECEIVED_ONLY'
    assert call(other, 'acknowledge_work_handoff', receipt_args) == receipt
    inspected = call(codex, 'get_work_handoff', {'handoff_id': first['id'], 'include_receipts': True})
    assert inspected['receipts'] == [receipt]
    updated = payload()
    updated['progress'][0] = {'description': '验证交接链路', 'state': 'TESTED', 'evidence': ['run:isolated-fixture']}
    second = call(other, 'revise_work_handoff', {'handoff_id': first['id'], 'expected_revision': 1,
        'request_key': 'resume-1', 'payload': updated})
    assert second['revision'] == 2 and second['created_by'] == 'antigravity'
    assert call(other, 'revise_work_handoff', {'handoff_id': first['id'], 'expected_revision': 1,
        'request_key': 'resume-1', 'payload': updated}) == second
    old = call(codex, 'get_work_handoff', {'handoff_id': first['id'], 'revision': 1})
    assert old == first
    assert call(codex, 'get_work_handoff', {'handoff_id': first['id']}) == second
    assert call(codex, 'list_work_handoffs', {})['items'] == [second]
    assert call(codex, 'list_work_handoffs', {'target_system': 'other'})['items'] == []
    # A new receiver cannot acknowledge a stale version as the current handoff.
    third = build_service(tmp_path, 'project-a', False, 'gemini-cli')
    assert call(third, 'acknowledge_work_handoff', receipt_args, error=True)['error'] == 'REVISION_CONFLICT'
    assert call(other, 'acknowledge_work_handoff', receipt_args) == receipt  # retry remains safe
    with codex.storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM notes').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM system_memory_backlog').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM system_memory_logsheet').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM work_handoff_receipts').fetchone()[0] == 1


def test_conflicts_invalid_claims_and_spoofed_identity_are_rejected(tmp_path):
    service = build_service(tmp_path, 'a', False, 'codex')
    first = create(service)
    changed = payload()
    changed['goal'] = 'different'
    call(service, 'create_work_handoff', {'request_key': 'create-1', 'payload': changed}, error=True)
    call(service, 'revise_work_handoff', {'handoff_id': first['id'], 'expected_revision': 9,
        'request_key': 'stale', 'payload': changed}, error=True)
    for bad in [dict(payload(), schema_version='2.0'), dict(payload(), created_by='user'),
                dict(payload(), goal=' '), dict(payload(), next_steps='run'),
                dict(payload(), decisions=[{'statement': 'approved', 'state': 'USER_APPROVED', 'source_ref': ''}]),
                dict(payload(), progress=[{'description': 'done', 'state': 'DONE', 'evidence': []}]),
                dict(payload(), progress=[{'description': 'tested', 'state': 'TESTED', 'evidence': []}])]:
        call(service, 'create_work_handoff', {'request_key': 'invalid', 'payload': bad}, error=True)
    call(service, 'acknowledge_work_handoff', {'handoff_id': first['id'], 'revision': 1,
        'content_hash': '0' * 64}, error=True)
    call(service, 'create_work_handoff', {'request_key': 'spoof', 'payload': payload(), 'principal': 'user'}, error=True)


def test_scope_readonly_and_persistent_revocation_apply_to_every_handoff_operation(tmp_path):
    source = build_service(tmp_path, 'a', False, 'codex')
    first = create(source)
    private = build_service(tmp_path, 'b', False, 'other')
    assert call(private, 'list_work_handoffs', {})['items'] == []
    for name, args in [
        ('get_work_handoff', {'handoff_id': first['id']}),
        ('export_work_handoff', {'handoff_id': first['id'], 'format': 'json'}),
        ('revise_work_handoff', {'handoff_id': first['id'], 'expected_revision': 1, 'request_key': 'x', 'payload': payload()}),
        ('acknowledge_work_handoff', {'handoff_id': first['id'], 'revision': 1, 'content_hash': first['content_hash']}),
    ]:
        call(private, name, args, error=True)
    readonly = build_service(tmp_path, 'a', True, 'reader')
    names = {t['name'] for t in readonly.get_tool_definitions()}
    assert {'get_work_handoff', 'list_work_handoffs', 'export_work_handoff'} <= names
    assert {'create_work_handoff', 'revise_work_handoff', 'acknowledge_work_handoff', 'import_work_handoff'}.isdisjoint(names)
    call(readonly, 'create_work_handoff', {'request_key': 'readonly', 'payload': payload()}, error=True)
    assert call(readonly, 'get_work_handoff', {'handoff_id': first['id']}) == first
    grant = source.access_policy.get_grant('reader', 'a')
    source.access_policy.update_grant('reader', 'a', read=False, write=False,
        expected_revision=grant['revision'], actor='owner')
    call(readonly, 'get_work_handoff', {'handoff_id': first['id']}, error=True)
    restarted = build_service(tmp_path, 'a', True, 'reader')
    call(restarted, 'list_work_handoffs', {}, error=True)


def test_json_markdown_import_preserves_content_and_marks_origin_as_reported(tmp_path):
    source = build_service(tmp_path / 'source', 'a', False, 'codex')
    first = create(source)
    exported = call(source, 'export_work_handoff', {'handoff_id': first['id'], 'format': 'json'})
    envelope = json.loads(exported['content'])
    assert envelope['untrusted_data'] is True and envelope['grants_execution_permission'] is False
    markdown = call(source, 'export_work_handoff', {'handoff_id': first['id'], 'format': 'markdown'})
    assert 'RECEIVED_ONLY' in markdown['content'] and '读取文件，执行验证' in markdown['content']
    destination = build_service(tmp_path / 'destination', 'other-scope', False, 'antigravity')
    args = {'package': envelope, 'request_key': 'import-1'}
    imported = call(destination, 'import_work_handoff', args)
    assert imported['payload'] == first['payload'] and imported['id'] != first['id']
    assert imported['scope'] == 'other-scope' and imported['created_by'] == 'antigravity'
    assert imported['reported_origin']['id'] == first['id']
    assert call(destination, 'import_work_handoff', args) == imported
    tampered = copy.deepcopy(envelope)
    tampered['handoff']['payload']['goal'] = 'tampered'
    call(destination, 'import_work_handoff', {'package': tampered, 'request_key': 'bad'}, error=True)
    future = dict(envelope, schema_version='2.0')
    call(destination, 'import_work_handoff', {'package': future, 'request_key': 'future'}, error=True)


@pytest.mark.asyncio
async def test_real_stdio_two_principals_continue_the_same_work(tmp_path):
    def client(principal):
        return Client(StdioServerParameters(command=sys.executable, args=[str(ROOT / 'scripts/run_mcp.py'),
            '--data-dir', str(tmp_path), '--scope', 'a', '--principal', principal], cwd=str(tmp_path)), read_timeout_seconds=20)
    async with client('codex') as codex:
        assert 'create_work_handoff' in {t.name for t in (await codex.list_tools()).tools}
        result = await codex.call_tool('create_work_handoff', {'request_key': 'stdio-1', 'payload': payload()})
        assert not result.is_error, result
        first = json.loads(result.content[0].text)
    async with client('antigravity') as other:
        result = await other.call_tool('get_work_handoff', {'handoff_id': first['id']})
        assert not result.is_error, result
        assert json.loads(result.content[0].text) == first
        receipt = await other.call_tool('acknowledge_work_handoff', {'handoff_id': first['id'],
            'revision': 1, 'content_hash': first['content_hash']})
        assert not receipt.is_error, receipt
        assert receipt.structured_content['received_by'] == 'antigravity'
        resumed = dict(payload(), next_steps=['继续下一项验证'])
        revision = await other.call_tool('revise_work_handoff', {'handoff_id': first['id'],
            'expected_revision': 1, 'request_key': 'stdio-resume', 'payload': resumed})
        assert not revision.is_error and revision.structured_content['revision'] == 2


def test_snapshot_preserves_handoffs_and_receipts(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    root = tmp_path / 'data'
    service = build_service(root, 'a', False, 'codex')
    first = create(service)
    call(service, 'acknowledge_work_handoff', {'handoff_id': first['id'], 'revision': 1, 'content_hash': first['content_hash']})
    create_snapshot(root, tmp_path / 'snapshot')
    restore_snapshot(tmp_path / 'snapshot', tmp_path / 'restored')
    restored = build_service(tmp_path / 'restored', 'a', False, 'codex')
    assert call(restored, 'get_work_handoff', {'handoff_id': first['id']}) == first
    with restored.storage.session() as conn:
        assert conn.execute('SELECT received_by FROM work_handoff_receipts').fetchone()[0] == 'codex'


def cli(data, principal, *args, expected=0):
    result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/manage_handoffs.py'),
        '--data-dir', str(data), '--scope', 'a', '--principal', principal, *map(str, args)],
        capture_output=True, text=True, encoding='utf-8', timeout=30, cwd=str(data.parent))
    assert result.returncode == expected, (result.stdout, result.stderr)
    return json.loads(result.stdout) if expected == 0 else result


def test_cli_file_exchange_from_another_cwd_and_no_overwrite(tmp_path):
    work = tmp_path / 'work.json'
    work.write_text(json.dumps(payload(), ensure_ascii=False), encoding='utf-8')
    first = cli(tmp_path / 'source', 'codex', 'create', '--input', work, '--request-key', 'cli-create')
    exported = tmp_path / 'handoff.json'
    cli(tmp_path / 'source', 'codex', 'export', first['id'], '--output', exported, '--format', 'json')
    preserved = exported.read_bytes()
    cli(tmp_path / 'source', 'codex', 'export', first['id'], '--output', exported, '--format', 'json', expected=2)
    assert exported.read_bytes() == preserved
    imported = cli(tmp_path / 'target', 'antigravity', 'import', '--input', exported, '--request-key', 'cli-import')
    assert imported['payload'] == first['payload']
    assert imported['created_by'] == 'antigravity'
    receipt = cli(tmp_path / 'target', 'antigravity', 'ack', imported['id'], '--revision', 1,
                  '--content-hash', imported['content_hash'])
    assert receipt['meaning'] == 'RECEIVED_ONLY'


def test_pagination_bounds_unknown_fields_and_request_key_namespace(tmp_path):
    service = build_service(tmp_path, 'a', False, 'codex')
    a = create(service, 'a')
    b = create(service, 'b')
    page = call(service, 'list_work_handoffs', {'limit': 1})
    assert page['has_more'] and page['next_offset'] == 1
    second = call(service, 'list_work_handoffs', {'limit': 1, 'offset': 1})
    assert not second['has_more'] and second['next_offset'] is None
    assert {page['items'][0]['id'], second['items'][0]['id']} == {a['id'], b['id']}
    other = build_service(tmp_path, 'a', False, 'antigravity')
    assert create(other, 'a')['id'] != a['id']  # another identity owns its keys
    call(service, 'get_work_handoff', {'handoff_id': a['id'], 'scope': 'b'}, error=True)
    call(service, 'create_work_handoff', {'request_key': 'large',
        'payload': dict(payload(), next_steps=['x' * 16000] * 100)}, error=True)
    call(service, 'get_work_handoff', {'handoff_id': a['id'], 'revision': True}, error=True)


def test_concurrent_expected_revision_allows_only_one_success(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    service = build_service(tmp_path, 'a', False, 'codex')
    first = create(service)
    def update(key):
        return service.execute_tool('revise_work_handoff', {'handoff_id': first['id'],
            'expected_revision': 1, 'request_key': key, 'payload': dict(payload(), goal=key)})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['update-a', 'update-b']))
    assert sum(not r.get('isError') for r in results) == 1
    assert call(service, 'get_work_handoff', {'handoff_id': first['id']})['revision'] == 2


def test_large_valid_packages_roundtrip_and_oversize_payloads_are_rejected(tmp_path):
    service = build_service(tmp_path / 'source', 'a', False, 'codex')
    large = dict(payload(), next_steps=['x' * 16000] * 8)
    first = create(service, 'large-valid', large)
    exported = call(service, 'export_work_handoff', {'handoff_id': first['id'], 'format': 'json'})
    target = build_service(tmp_path / 'target', 'a', False, 'antigravity')
    imported = call(target, 'import_work_handoff', {'package': json.loads(exported['content']), 'request_key': 'large-import'})
    assert imported['payload'] == large
    call(service, 'create_work_handoff', {'request_key': 'too-large',
        'payload': dict(payload(), next_steps=['x' * 16000] * 14)}, error=True)


def test_failed_transaction_does_not_publish_a_handoff(tmp_path, monkeypatch):
    service = build_service(tmp_path, 'a', False, 'codex')
    def fail(*args, **kwargs):
        raise RuntimeError('injected outbox failure')
    monkeypatch.setattr(service.storage, '_insert_outbox_event', fail)
    response = call(service, 'create_work_handoff', {'request_key': 'fail', 'payload': payload()}, error=True)
    assert response['error'] == 'INTERNAL_ERROR'
    assert call(service, 'list_work_handoffs', {})['items'] == []


@pytest.mark.parametrize('scope,principal', [('s' * 201, 'codex'), ('a', 'p' * 201)])
def test_unexportable_connection_metadata_is_rejected_before_storage(tmp_path, scope, principal):
    service = build_service(tmp_path, scope, False, principal)
    call(service, 'create_work_handoff', {'request_key': 'bad-metadata', 'payload': payload()}, error=True)
    with service.storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM work_handoff_revisions').fetchone()[0] == 0


def test_multihop_file_exchange_preserves_original_sender_and_rejects_changed_retry(tmp_path):
    first_service = build_service(tmp_path / 'a', 'a', False, 'codex')
    first = create(first_service)
    first_package = json.loads(call(first_service, 'export_work_handoff', {'handoff_id': first['id'], 'format': 'json'})['content'])
    middle_service = build_service(tmp_path / 'b', 'b', False, 'antigravity')
    middle = call(middle_service, 'import_work_handoff', {'request_key': 'b', 'package': first_package})
    second_package = json.loads(call(middle_service, 'export_work_handoff', {'handoff_id': middle['id'], 'format': 'json'})['content'])
    final_service = build_service(tmp_path / 'c', 'c', False, 'gemini-cli')
    final = call(final_service, 'import_work_handoff', {'request_key': 'c', 'package': second_package})
    assert final['reported_origin'] == middle['reported_origin']
    assert final['reported_origin']['id'] == first['id']
    assert final['reported_origin']['created_by'] == 'codex'
    altered = copy.deepcopy(second_package)
    altered['handoff']['created_by'] = 'different-intermediate-sender'
    call(final_service, 'import_work_handoff', {'request_key': 'c', 'package': altered}, error=True)
