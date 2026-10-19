import pytest

from backend.candidates.candidate_bridge import CandidateBridge
from backend.conflicts.conflict_manager import ConflictManager
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphNode, NodeType, MemoryAuthority
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw')))
    bridge = CandidateBridge(storage)
    notes_source = notes.create_note('New assertion', 'Database is PostgreSQL', scope='a')
    _, candidate_id, _ = bridge.submit_candidate(notes_source.id, 1, notes_source.content, NodeType.FACT,
        {'content_hash': notes_source.content_hash}, scope='a')
    storage.upsert_node(GraphNode(id='original', title='Database', content='Database is SQLite',
        node_type=NodeType.DECISION, scope='a', authority=MemoryAuthority.EXPLICIT))
    return storage, notes, bridge, ConflictManager(storage), candidate_id


def report(manager, candidate, **changes):
    arguments = {'scope': 'a', 'memory_targets': {'original': 1}, 'candidate_ids': [candidate],
        'kind': 'SEMANTIC_CONFLICT', 'description': 'Different databases are asserted; no decision change approved',
        'actor': 'external-agent', 'actor_type': 'agent'}
    arguments.update(changes)
    return manager.report(**arguments)


def test_conflicting_candidate_stays_pending_and_original_is_preserved(tmp_path):
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    conflict = report(manager, candidate)
    assert conflict.core_props['status'] == 'OPEN'
    assert conflict.authority == MemoryAuthority.INFERRED
    assert bridge.get_candidate(candidate).status.value == 'PENDING'
    assert report(manager, candidate).id == conflict.id
    with pytest.raises(ValueError, match='conflict'):
        bridge.accept_candidate(candidate)
    original = storage.get_node('original')
    assert original.content == 'Database is SQLite' and original.version == 1
    edges = storage.get_edges_for_node(conflict.id)
    assert any(e.target_id == original.id for e in edges)
    with storage.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE node_type='Conflict'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM system_memory_logsheet").fetchone()[0] == 1


def test_conflict_report_scope_stale_revision_and_audit_failure_do_not_change_candidate(tmp_path, monkeypatch):
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    for changes in [{'scope': 'other'}, {'memory_targets': {'original': 2}}, {'kind': 'SCHEMA_CONFLICT'}]:
        with pytest.raises((ValueError, LookupError)):
            report(manager, candidate, **changes)
    def fail(*args, **kwargs):
        raise RuntimeError('audit failed')
    monkeypatch.setattr('backend.maintenance.logsheet.MemoryLogsheet.append_entry', fail)
    with pytest.raises(RuntimeError):
        report(manager, candidate)
    assert bridge.get_candidate(candidate).status.value == 'VALIDATED'
    with storage.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE node_type='Conflict'").fetchone()[0] == 0


def test_resolution_needs_owner_evidence_and_revision_and_does_not_promote_claims(tmp_path):
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    conflict = report(manager, candidate)
    evidence = notes.create_note('Review', 'The incoming assertion was a proposal, not an approved replacement', scope='a')
    arguments = {'conflict_id': conflict.id, 'scope': 'a', 'expected_revision': 1,
        'status': 'RESOLVED', 'reason': 'Reviewed proposal against approved decision',
        'method': 'Compared exact source revisions',
        'evidence_refs': [{'source_id': evidence.id, 'source_revision': 1, 'content_hash': evidence.content_hash}],
        'actor': 'local-owner', 'actor_type': 'user'}
    with pytest.raises(PermissionError):
        manager.transition(**{**arguments, 'actor_type': 'agent'})
    with pytest.raises(ValueError):
        manager.transition(**{**arguments, 'evidence_refs': []})
    resolved = manager.transition(**arguments)
    assert resolved.version == 2 and resolved.core_props['status'] == 'RESOLVED'
    assert storage.get_node('original').version == 1
    assert bridge.get_candidate(candidate).status.value == 'PENDING'
    with pytest.raises(ValueError):
        manager.transition(**arguments)
    assert storage.get_node_revision(conflict.id, 1, scope='a').core_props['status'] == 'OPEN'
    from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
    artifact = RetrievalCoordinator(storage, notes).retrieve('original', scope='a', user_specified_plane='MEMORY')
    assert '[CONFLICT:' not in artifact.dynamic_context
    report_context = RetrievalCoordinator(storage, notes).retrieve(conflict.id, scope='a', user_specified_plane='MEMORY')
    assert 'Conflict status: RESOLVED' in report_context.dynamic_context


def test_retrieval_warns_about_matching_conflict_beyond_unrelated_scan_window(tmp_path):
    from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    conflict = report(manager, candidate)
    for i in range(110):
        storage.upsert_node(GraphNode(id=f'unrelated-conflict-{i}', node_type=NodeType.CONFLICT,
            title='Unrelated', content='different issue', scope='a', core_props={'conflicting_nodes': ['unrelated']}))
    artifact = RetrievalCoordinator(storage, notes).retrieve('original', scope='a', user_specified_plane='MEMORY')
    assert '[CONFLICT:' in artifact.dynamic_context
    assert conflict.id in artifact.dynamic_context


def test_context_budget_does_not_separate_claim_from_provenance_or_conflict_warning(tmp_path):
    from backend.context.context_compiler import ContextCompiler
    from backend.core.evidence.evidence_packet import EvidencePacketBuilder
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    conflict = report(manager, candidate)
    packet = EvidencePacketBuilder.build('original', [storage.get_node('original')], [conflict], scope='a')
    for budget in range(1, 180):
        artifact = ContextCompiler.compile(packet, token_budget=budget)
        assert artifact.estimated_tokens <= budget
        if 'Database is SQLite' in artifact.combined_prompt:
            assert '[CONFLICT:' in artifact.combined_prompt
            assert conflict.id in artifact.combined_prompt
            assert '(Source:' in artifact.combined_prompt


def test_evidence_does_not_expose_unrelated_or_cross_scope_conflicts(tmp_path):
    from backend.core.evidence.evidence_packet import EvidencePacketBuilder
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    conflict = report(manager, candidate)
    other_scope = conflict.model_copy(update={'id': 'private-report', 'scope': 'private'})
    unrelated = conflict.model_copy(deep=True, update={'id': 'unrelated-report'})
    unrelated.core_props['conflicting_nodes'] = ['unrelated']
    packet = EvidencePacketBuilder.build('original', [storage.get_node('original')],
        [other_scope, unrelated], scope='a')
    assert not packet.items[0].is_conflicted
    assert packet.conflicts_detected == []


def test_owner_rest_conflict_review_requires_evidence_and_fresh_revision(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    storage, notes, bridge, manager, candidate = setup(tmp_path)
    with TestClient(create_app(str(tmp_path/'db'), str(tmp_path/'raw'), str(tmp_path/'artifacts'))) as client:
        body = {'scope': 'a', 'memory_targets': {'original': 1}, 'candidate_ids': [candidate],
                'kind': 'SEMANTIC_CONFLICT', 'description': 'Conflicting databases'}
        response = client.post('/api/conflicts', json=body)
        assert response.status_code == 201, response.text
        conflict = response.json()
        url = '/api/conflicts/' + conflict['id']
        assert client.get(url, params={'scope': 'other'}).status_code == 404
        listing = client.get('/api/conflicts', params={'scope': 'a', 'status': 'OPEN', 'limit': 1}).json()
        assert listing['items'][0]['id'] == conflict['id'] and not listing['has_more']
        assert client.get('/api/conflicts', params={'scope': 'other'}).json()['items'] == []
        assert client.post('/api/candidates/' + candidate + '/accept').status_code == 409
        review = {'scope': 'a', 'expected_revision': 1, 'status': 'RESOLVED', 'reason': 'Existing decision stands',
                  'method': 'Read owner clarification'}
        assert client.post(url + '/transition', json=review).status_code == 422
        note = notes.create_note('Owner clarification', 'Retain SQLite; the new assertion was a proposal', scope='a')
        review['evidence_refs'] = [{'source_id': note.id, 'source_revision': 1, 'content_hash': note.content_hash}]
        assert client.post(url + '/transition', json={**review, 'actor_type': 'user'}).status_code == 422
        closed = client.post(url + '/transition', json=review)
        assert closed.status_code == 200 and closed.json()['core_props']['status'] == 'RESOLVED'
        assert client.post(url + '/transition', json=review).status_code == 409
        assert client.get('/api/conflicts', params={'scope': 'a', 'status': 'OPEN'}).json()['items'] == []
        assert storage.get_node('original').version == 1
        assert bridge.get_candidate(candidate).status.value == 'PENDING'
