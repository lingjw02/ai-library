"""Validation must survive candidate promotion and every canonical write path."""
from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.candidates.candidate_bridge import CandidateBridge
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphNode, GraphEdge, NodeType, RelationshipType
from backend.schema.registry import SchemaRegistry
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw')))
    note = notes.create_note('Historical storage', 'ArchiveAlpha used SQLite during 2000.', scope='a')
    return storage, notes, note, CandidateBridge(storage)


@pytest.mark.parametrize('temporal', [
    {'valid_from': '2001-01-01T00:00:00Z', 'valid_to': '2000-01-01T00:00:00Z'},
    {'valid_from': '2000-01-01T00:00:00Z', 'valid_to': '2000-01-01T00:00:00Z'},
    {'valid_to': '2001-01-01T00:00:00'},
])
def test_invalid_candidate_time_is_quarantined_without_entering_memory(tmp_path, temporal):
    storage, _, note, bridge = setup(tmp_path)
    ok, quarantine_id, error = bridge.submit_candidate(note.id, 1, note.content, NodeType.FACT,
        {'content_hash': note.content_hash, 'temporal_props': temporal}, scope='a')
    assert not ok and error
    assert storage.list_nodes(scope='a') == []
    assert bridge.get_candidate(quarantine_id) is None
    quarantined = storage.list_quarantine()[0]
    assert quarantined.submission['payload']['temporal_props'] == temporal


def test_promotion_preserves_namespaces_and_expiry_without_forged_verification(tmp_path):
    storage, notes, note, bridge = setup(tmp_path)
    payload = {'content_hash': note.content_hash,
        'core_props': {'entity': 'ArchiveAlpha'}, 'source_props': {'source_label': 'Original note'},
        'graph_props': {'category': 'storage'}, 'memory_props': {'rationale': 'Historical configuration'},
        'custom_props': {'review_hint': 'Keep historical'}, 'depth': 'D1', 'importance': 0.8,
        'temporal_props': {'valid_from': '2000-01-01T00:00:00Z', 'valid_to': '2001-01-01T00:00:00Z'},
        'verification_props': {'status': 'VERIFIED', 'reviewer': 'forged'}, 'maturity': 'L5'}
    ok, candidate, _ = bridge.submit_candidate(note.id, 1, note.content, NodeType.FACT, payload, scope='a')
    assert ok
    node = bridge.accept_candidate(candidate, reviewer='real-reviewer')
    for key in ('core_props', 'source_props', 'graph_props', 'memory_props', 'custom_props'):
        assert getattr(node, key) == payload[key]
    assert node.temporal_props.valid_to == datetime(2001, 1, 1, tzinfo=timezone.utc)
    assert node.depth.value == 'D1' and node.importance == 0.8
    assert node.maturity.value == 'L2' and node.authority.value == 'INFERRED'
    assert node.verification_props['status'] == 'UNVERIFIED'
    assert node.verification_props['reviewer'] == 'real-reviewer'
    assert node.provenance.content_hash == note.content_hash
    current = RetrievalCoordinator(storage, notes).retrieve('ArchiveAlpha', scope='a', user_specified_plane='MEMORY')
    assert 'ArchiveAlpha used SQLite' not in current.dynamic_context
    assert notes.get_note(note.id).content == note.content


def test_invalid_candidate_payload_at_acceptance_rolls_back_all_promotion_effects(tmp_path):
    storage, _, note, bridge = setup(tmp_path)
    _, candidate, _ = bridge.submit_candidate(note.id, 1, note.content, NodeType.FACT, {}, scope='a')
    with storage.session() as conn:
        before_events = conn.execute('SELECT COUNT(*) FROM transactional_outbox').fetchone()[0]
        conn.execute('UPDATE candidates SET payload_json = ? WHERE id = ?',
            (json.dumps({'temporal_props': {'valid_to': '2001-01-01'}}), candidate))
    with pytest.raises(ValueError):
        bridge.accept_candidate(candidate)
    assert bridge.get_candidate(candidate).status.value == 'VALIDATED'
    assert storage.list_nodes(scope='a') == []
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM node_revisions').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM system_memory_logsheet').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM transactional_outbox').fetchone()[0] == before_events


def test_mutated_node_is_revalidated_before_history_or_outbox_changes(tmp_path):
    storage, *_ = setup(tmp_path)
    node = GraphNode(id='fact', node_type=NodeType.FACT, title='Fact', content='Original', scope='a')
    storage.upsert_node(node)
    with storage.session() as conn:
        before_events = conn.execute('SELECT COUNT(*) FROM transactional_outbox').fetchone()[0]
    node.confidence = 10
    with pytest.raises(ValueError):
        storage.upsert_node(node, expected_revision=1)
    assert storage.get_node('fact').version == 1
    assert storage.get_node('fact').confidence == 0.5
    assert len(storage.list_node_revisions('fact', scope='a')) == 1
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM transactional_outbox').fetchone()[0] == before_events


@pytest.mark.parametrize('mutation', ['time', 'weight'])
def test_invalid_edge_cannot_enter_canonical_or_history(tmp_path, mutation):
    storage, *_ = setup(tmp_path)
    for node_id in ('one', 'two'):
        storage.upsert_node(GraphNode(id=node_id, node_type=NodeType.FACT, title=node_id, content=node_id, scope='a'))
    edge = GraphEdge(id='edge', source_id='one', target_id='two', relation=RelationshipType.RELATED_TO, scope='a')
    if mutation == 'weight':
        edge.weight = float('inf')
    else:
        edge.valid_from = datetime(2001, 1, 1, tzinfo=timezone.utc)
        edge.valid_to = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        storage.upsert_edge(edge)
    assert storage.get_edges_for_node('one') == []
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM edge_revisions').fetchone()[0] == 0


def test_rest_invalid_memory_returns_actionable_error_and_keeps_service_available(tmp_path):
    app = create_app(str(tmp_path/'api.db'), str(tmp_path/'raw'), str(tmp_path/'art'))
    with TestClient(app, raise_server_exceptions=False) as client:
        body = {'id': 'bad', 'node_type': 'Fact', 'title': 'Invalid', 'content': 'Claim',
                'temporal_props': {'valid_from': '2001-01-01T00:00:00Z', 'valid_to': '2000-01-01T00:00:00Z'}}
        assert client.post('/api/memory/nodes', json=body).status_code == 422
        assert client.post('/api/memory/nodes', json={'id': 'missing-fields'}).status_code == 422
        assert client.get('/api/memory/nodes').json() == []
        assert client.get('/api/health').status_code == 200


def test_validator_failure_becomes_quarantine_instead_of_unhandled_exception():
    registry = SchemaRegistry()
    def unavailable(_):
        raise RuntimeError('validator unavailable')
    registry.register_validator(NodeType.FACT, unavailable)
    result = registry.validate_node_data({'id': 'fact', 'node_type': 'Fact', 'title': 'Fact', 'content': 'Claim'})
    assert not result.is_valid
    assert result.quarantine_record.payload['content'] == 'Claim'
    assert 'validator unavailable' in result.error_message
