"""Exercise a real SDK client and server over subprocess stdio, not a facade."""
import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from backend.notes.notes_manager import NotesManager
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_mcp.py'


def owner_approval_args(tmp_path, candidate, principal='mcp-agent'):
    """The fixture owner explicitly reviews a candidate; the Agent cannot issue this."""
    from datetime import timedelta
    from backend.auth.approvals import ApprovalTickets, hash_payload
    from backend.candidates.candidate_bridge import CandidateBridge
    from backend.schema.models import utc_now
    storage = CanonicalStorage(str(tmp_path/'knowledge_os.db'))
    bridge = CandidateBridge(storage)
    item = bridge.get_candidate(candidate['id'])
    ticket = ApprovalTickets(storage).issue(principal=principal,scope=item.scope,action='accept_candidate',
        target_id=item.id,target_revision=item.source_revision,payload_hash=hash_payload(bridge.approval_payload(item)),
        reason='Fixture owner reviewed exact claim',expires_at=(utc_now()+timedelta(hours=1)).isoformat())
    return {'candidate_id':item.id,'approval_ticket':ticket['id']}


@pytest.mark.asyncio
async def test_external_embedding_lifecycle_over_real_protocol(tmp_path):
    space = {'model_id': 'fixture-only', 'model_version': 'v1', 'dimension': 2}
    async with connection(tmp_path) as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Rail', 'content': 'railway journey'}))
        sources = decoded(await client.call_tool('list_embedding_sources', {'space': space, 'plane': 'NOTES'}))
        assert sources['source_contract'] == 'sha256_utf8_content_only'
        assert sources['items'][0]['id'] == note['id']
        args = {'space': space, 'plane': 'NOTES', 'object_id': note['id'], 'revision': 1,
                'content_hash': note['content_hash'], 'vector': [1, 0]}
        assert not decoded(await client.call_tool('submit_embedding', args))['idempotent']
        assert decoded(await client.call_tool('submit_embedding', args))['idempotent']
        context = decoded(await client.call_tool('search_knowledge_context', {'query': 'train',
            'plane': 'NOTES', 'embedding_query': {'space': space, 'vector': [1, 0]}}))
        assert 'railway journey' in context['combined_prompt']
        assert context['retrieval']['vector_search'] is True
        assert (await client.call_tool('submit_embedding', {**args, 'scope': 'other'})).is_error
        assert (await client.call_tool('submit_embedding', {**args, 'vector': [1]})).is_error
        decoded(await client.call_tool('revise_user_note', {'note_id': note['id'], 'title': 'Rail',
            'content': 'new content', 'expected_revision': 1}))
        assert (await client.call_tool('submit_embedding', args)).is_error
        sources = decoded(await client.call_tool('list_embedding_sources', {'space': space}))
        assert sources['items'][0]['revision'] == 2
    async with connection(tmp_path, read_only=True) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert 'submit_embedding' not in tools
        assert 'list_embedding_sources' in tools
        assert (await client.call_tool('submit_embedding', args)).is_error


def connection(tmp_path, *, read_only=False, scope='project-a', mode='auto', principal='mcp-agent'):
    args = [str(SCRIPT), '--data-dir', str(tmp_path), '--scope', scope, '--principal', principal]
    if read_only:
        args.append('--read-only')
    return Client(StdioServerParameters(command=sys.executable, args=args, cwd=str(tmp_path)), read_timeout_seconds=15, mode=mode)


@pytest.mark.asyncio
async def test_connection_principal_is_audited_without_turning_agent_notes_into_user_authority(tmp_path):
    async with connection(tmp_path, principal='codex') as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Source', 'content': 'Agent-originated assertion'}))
        assert note['author'] == 'mcp-agent:codex'
        candidate = decoded(await client.call_tool('submit_candidate_claim', {'source_id': note['id'],
            'source_revision': 1, 'source_content_hash': note['content_hash'], 'claim': 'Agent-originated assertion'}))
        decoded(await client.call_tool('accept_candidate', owner_approval_args(tmp_path,candidate,principal='codex')))
        logs = decoded(await client.call_tool('inspect_system_memory_logsheet', {}))
        assert logs[0]['actor'] == {'type': 'agent', 'id': 'codex'}
        context = decoded(await client.call_tool('search_knowledge_context', {'query': 'assertion', 'plane': 'NOTES'}))
        assert '[INFERRED/L0]' in context['dynamic_context']
        assert '[EXPLICIT/' not in context['dynamic_context']


def decoded(result):
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_conflict_reporting_is_scoped_attributed_and_blocks_promotion(tmp_path):
    from backend.schema.models import GraphNode, NodeType
    storage = CanonicalStorage(str(tmp_path/'knowledge_os.db'))
    storage.upsert_node(GraphNode(id='decision', node_type=NodeType.DECISION,
        title='Database', content='Use SQLite', scope='project-a'))
    async with connection(tmp_path, principal='codex') as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Proposal', 'content': 'Use PostgreSQL'}))
        candidate = decoded(await client.call_tool('submit_candidate_claim', {'source_id': note['id'],
            'source_revision': 1, 'source_content_hash': note['content_hash'], 'claim': 'Use PostgreSQL'}))
        args = {'memory_targets': {'decision': 1}, 'candidate_ids': [candidate['id']],
                'kind': 'SEMANTIC_CONFLICT', 'description': 'Database proposal conflicts with current decision'}
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {'report_memory_conflict', 'list_memory_conflicts'} <= names
        assert 'resolve_memory_conflict' not in names
        assert (await client.call_tool('report_memory_conflict', {**args, 'actor': 'user'})).is_error
        assert (await client.call_tool('report_memory_conflict', {**args, 'scope': 'project-b'})).is_error
        conflict = decoded(await client.call_tool('report_memory_conflict', args))
        assert conflict['source_props']['reporter'] == {'type': 'agent', 'id': 'codex'}
        assert decoded(await client.call_tool('report_memory_conflict', args))['id'] == conflict['id']
        assert (await client.call_tool('accept_candidate', owner_approval_args(tmp_path,candidate,principal='codex'))).is_error
        listed = decoded(await client.call_tool('list_memory_conflicts', {'status': 'OPEN'}))
        assert listed['items'][0]['id'] == conflict['id']
        context = decoded(await client.call_tool('search_knowledge_context', {'query': 'decision', 'plane': 'MEMORY'}))
        assert conflict['id'] in context['combined_prompt']
    async with connection(tmp_path, read_only=True) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert 'list_memory_conflicts' in names and 'report_memory_conflict' not in names
        assert (await client.call_tool('report_memory_conflict', args)).is_error
    async with connection(tmp_path, scope='project-b') as client:
        assert decoded(await client.call_tool('list_memory_conflicts', {}))['items'] == []


@pytest.mark.asyncio
async def test_quarantine_repair_is_scoped_and_never_automatically_accepts(tmp_path):
    from backend.candidates.candidate_bridge import CandidateBridge
    from backend.schema.models import NodeType
    storage = CanonicalStorage(str(tmp_path/'knowledge_os.db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw_sources')))
    source = notes.create_note('Source', 'A claim', scope='project-a')
    _, identity, _ = CandidateBridge(storage).submit_candidate(source.id, 1, 'A claim', NodeType.FACT,
        {'confidence': 'bad'}, scope='project-a')
    args = {'quarantine_id': identity, 'expected_revision': 1, 'claim': 'A claim',
            'suggested_node_type': 'Fact', 'payload': {}, 'reason': 'Remove malformed optional value'}
    async with connection(tmp_path, principal='gemini-cli') as client:
        listed = decoded(await client.call_tool('list_quarantine', {}))
        assert listed['items'][0]['id'] == identity
        assert (await client.call_tool('get_quarantine_record', {'quarantine_id': identity, 'scope': 'other'})).is_error
        assert (await client.call_tool('repair_quarantined_candidate', {**args, 'authority': 'EXPLICIT'})).is_error
        result = decoded(await client.call_tool('repair_quarantined_candidate', args))
        assert result['resolved']
        assert decoded(await client.call_tool('list_quarantine', {}))['items'] == []
        candidates = decoded(await client.call_tool('list_candidates', {}))
        assert candidates[0]['status'] == 'VALIDATED' and candidates[0]['authority'] == 'INFERRED'
        logs = decoded(await client.call_tool('inspect_system_memory_logsheet', {}))
        assert logs[0]['actor'] == {'type': 'agent', 'id': 'gemini-cli'}
    async with connection(tmp_path, read_only=True) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert 'get_quarantine_record' in names and 'repair_quarantined_candidate' not in names
        assert (await client.call_tool('repair_quarantined_candidate', args)).is_error


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['auto', 'legacy'])
async def test_list_results_offer_object_structured_content_for_strict_clients(tmp_path, mode):
    async with connection(tmp_path, mode=mode) as client:
        for name in ['list_user_notes', 'list_candidates', 'inspect_system_memory_backlog', 'inspect_system_memory_logsheet']:
            result = await client.call_tool(name, {})
            assert decoded(result) == []
            assert result.structured_content == {'items': []}


@pytest.mark.asyncio
async def test_stdio_note_revision_candidate_and_audit(tmp_path):
    async with connection(tmp_path) as client:
        assert client.server_info.name == 'AI Knowledge OS'
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {'list_user_notes', 'revise_user_note', 'submit_candidate_claim', 'accept_candidate'} <= names
        note = decoded(await client.call_tool('create_user_note', {'title': 'Architecture', 'content': 'SQLite local memory'}))
        assert note['scope'] == 'project-a' and note['author'] == 'mcp-agent'
        revised = decoded(await client.call_tool('revise_user_note', {'note_id': note['id'], 'title': 'Architecture', 'content': 'SQLite stores canonical memory', 'expected_revision': 1}))
        assert revised['revision'] == 2
        conflict = await client.call_tool('revise_user_note', {'note_id': note['id'], 'title': 'stale', 'content': 'do not overwrite', 'expected_revision': 1})
        assert conflict.is_error
        source = decoded(await client.call_tool('get_user_note', {'note_id': note['id'], 'revision': 1}))
        assert source['content'] == 'SQLite local memory'
        args = {'source_id': note['id'], 'source_revision': 2, 'source_content_hash': revised['content_hash'], 'claim': 'SQLite stores canonical memory'}
        candidate = decoded(await client.call_tool('submit_candidate_claim', args))
        assert candidate['success']
        assert decoded(await client.call_tool('submit_candidate_claim', args))['id'] == candidate['id']
        accepted_args = owner_approval_args(tmp_path,candidate)
        memory = decoded(await client.call_tool('accept_candidate', accepted_args))
        assert memory['authority'] == 'INFERRED'
        assert memory['maturity'] == 'L2'
        assert decoded(await client.call_tool('accept_candidate', accepted_args))['id'] == memory['id']
        logs = decoded(await client.call_tool('inspect_system_memory_logsheet', {}))
        assert logs[0]['actor'] == {'type': 'agent', 'id': 'mcp-agent'}
        context = decoded(await client.call_tool('search_knowledge_context', {'query': 'SQLite'}))
        assert 'SQLite' in context['combined_prompt']


@pytest.mark.asyncio
async def test_scope_and_schema_are_enforced_over_protocol(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'knowledge_os.db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw_sources')))
    secret = notes.create_note('private', 'never reveal', scope='project-b')
    async with connection(tmp_path) as client:
        for tool, args in [
            ('get_user_note', {'note_id': secret.id}),
            ('list_note_revisions', {'note_id': secret.id}),
            ('revise_user_note', {'note_id': secret.id, 'title': 'bad', 'content': 'bad', 'expected_revision': 1}),
            ('submit_candidate_claim', {'source_id': secret.id, 'source_revision': 1, 'source_content_hash': secret.content_hash, 'claim': 'bad'}),
            ('create_user_note', {'title': 'bad', 'content': 'bad', 'scope': 'project-b'}),
            ('create_user_note', {'title': 'bad', 'content': 'bad', 'author': 'user'}),
            ('list_user_notes', {'limit': -1}),
        ]:
            result = await client.call_tool(tool, args)
            assert result.is_error, (tool, result)
            assert 'never reveal' not in str(result.content)
        assert decoded(await client.call_tool('list_user_notes', {})) == []
    assert notes.get_note(secret.id).revision == 1


@pytest.mark.asyncio
async def test_read_only_hides_and_refuses_mutations(tmp_path):
    async with connection(tmp_path, read_only=True, mode='legacy') as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert 'get_user_note' in names
        assert 'create_user_note' not in names
        result = await client.call_tool('create_user_note', {'title': 'bad', 'content': 'bad'})
        assert result.is_error
        assert decoded(await client.call_tool('list_user_notes', {})) == []


@pytest.mark.asyncio
async def test_foreign_memory_candidate_and_backlog_ids_are_denied(tmp_path):
    async with connection(tmp_path, scope='project-b') as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Secret source', 'content': 'private claim'}))
        args = {'source_id': note['id'], 'source_revision': 1, 'source_content_hash': note['content_hash'], 'claim': 'private claim'}
        candidate = decoded(await client.call_tool('submit_candidate_claim', args))
        accepted_args = owner_approval_args(tmp_path,candidate)
        memory = decoded(await client.call_tool('accept_candidate', accepted_args))
        item = decoded(await client.call_tool('capture_memory_backlog', {'node_ids': [memory['id']], 'item_type': 'VERIFY_CLAIM', 'reason': 'Private review'}))
    async with connection(tmp_path) as client:
        for tool, arguments in [
            ('get_memory_node', {'node_id': memory['id']}),
            ('accept_candidate', accepted_args),
            ('reject_candidate', {'candidate_id': candidate['id'], 'reason': 'bad'}),
            ('capture_memory_backlog', {'node_ids': [memory['id']], 'item_type': 'VERIFY_CLAIM', 'reason': 'bad'}),
            ('transition_memory_backlog', {'item_id': item['id'], 'status': 'CANCELLED', 'expected_revision': 1}),
        ]:
            result = await client.call_tool(tool, arguments)
            assert result.is_error
            assert 'private claim' not in str(result.content)
        for tool in ('list_candidates', 'inspect_system_memory_backlog', 'inspect_system_memory_logsheet'):
            assert decoded(await client.call_tool(tool, {})) == []
        result = decoded(await client.call_tool('search_knowledge_context', {'query': 'private'}))
        assert 'private claim' not in result['combined_prompt']


@pytest.mark.asyncio
async def test_candidate_cannot_forge_provenance_or_authority(tmp_path):
    async with connection(tmp_path) as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Evidence', 'content': 'a claim'}))
        args = {'source_id': note['id'], 'source_revision': 1, 'source_content_hash': note['content_hash'], 'claim': 'a claim'}
        for override in [{'source_content_hash': '0' * 64}, {'source_revision': 999}, {'authority': 'EXPLICIT'}, {'payload': {'maturity': 'L5_CANONICAL'}}]:
            assert (await client.call_tool('submit_candidate_claim', {**args, **override})).is_error
        candidate = decoded(await client.call_tool('submit_candidate_claim', args))
        decoded(await client.call_tool('reject_candidate', {'candidate_id': candidate['id'], 'reason': 'Claim not independently checked'}))
        assert (await client.call_tool('accept_candidate', owner_approval_args(tmp_path,candidate))).is_error
        log = decoded(await client.call_tool('inspect_system_memory_logsheet', {}))[0]
        assert log['actor'] == {'type': 'agent', 'id': 'mcp-agent'}


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['scope', 'inactive'])
async def test_accept_retry_rechecks_promoted_memory_access(tmp_path, change):
    async with connection(tmp_path) as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Source', 'content': 'original claim'}))
        candidate = decoded(await client.call_tool('submit_candidate_claim', {
            'source_id': note['id'], 'source_revision': 1,
            'source_content_hash': note['content_hash'], 'claim': 'original claim',
        }))
        accepted_args = owner_approval_args(tmp_path,candidate)
        memory = decoded(await client.call_tool('accept_candidate', accepted_args))
        storage = CanonicalStorage(str(tmp_path / 'knowledge_os.db'))
        moved = storage.get_node(memory['id'])
        moved.content = 'private content after access revoked'
        if change == 'scope':
            moved.scope = 'project-b'
        else:
            moved.is_active = False
        storage.upsert_node(moved)
        result = await client.call_tool('accept_candidate', accepted_args)
        assert result.is_error
        assert moved.content not in str(result.content)
        assert (await client.call_tool('get_memory_node', {'node_id': moved.id})).is_error


@pytest.mark.asyncio
async def test_backlog_targets_and_revision_are_scoped(tmp_path):
    async with connection(tmp_path) as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Source', 'content': 'claim'}))
        candidate = decoded(await client.call_tool('submit_candidate_claim', {'source_id': note['id'], 'source_revision': 1, 'source_content_hash': note['content_hash'], 'claim': 'claim'}))
        accepted_args = owner_approval_args(tmp_path,candidate)
        memory = decoded(await client.call_tool('accept_candidate', accepted_args))
        item = decoded(await client.call_tool('capture_memory_backlog', {'node_ids': [memory['id']], 'item_type': 'VERIFY_CLAIM', 'reason': 'Needs source review'}))
        assert item['created_by'] == 'mcp-agent'
        result = decoded(await client.call_tool('transition_memory_backlog', {'item_id': item['id'], 'status': 'BLOCKED', 'expected_revision': item['revision'], 'reason': 'Cannot independently verify', 'blocker': 'Primary source unavailable'}))
        assert result['status'] == 'BLOCKED'
        assert (await client.call_tool('transition_memory_backlog', {'item_id': item['id'], 'status': 'RESOLVED', 'expected_revision': result['revision']})).is_error
        assert (await client.call_tool('capture_memory_backlog', {'node_ids': ['unknown'], 'item_type': 'VERIFY_CLAIM', 'reason': 'bad'})).is_error
        logs = decoded(await client.call_tool('inspect_system_memory_logsheet', {}))
        assert logs[0]['actor']['id'] == 'mcp-agent'


@pytest.mark.asyncio
async def test_persisted_revocation_takes_effect_on_live_connection_and_restart(tmp_path):
    from backend.auth.policy import AccessPolicy
    async with connection(tmp_path) as client:
        note = decoded(await client.call_tool('create_user_note', {'title': 'Source', 'content': 'private evidence'}))
        policy = AccessPolicy(CanonicalStorage(str(tmp_path / 'knowledge_os.db')))
        grant = policy.get_grant('mcp-agent', 'project-a')
        policy.update_grant('mcp-agent', 'project-a', read=False, write=False, expected_revision=grant['revision'], actor='local-owner')
        for name, args in [('get_user_note', {'note_id': note['id']}), ('search_knowledge_context', {'query': 'evidence'}), ('create_user_note', {'title': 'blocked', 'content': 'blocked'})]:
            result = await client.call_tool(name, args)
            assert result.is_error
            assert 'private evidence' not in str(result.content)
    async with connection(tmp_path) as client:
        assert (await client.call_tool('list_user_notes', {})).is_error
