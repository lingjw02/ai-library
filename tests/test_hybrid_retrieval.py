import hashlib
import sqlite3
import pytest

from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.core.retrieval.vector_index import EmbeddingSpace
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphNode, NodeType
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore

SPACE = {'model_id': 'fixture-external', 'model_version': 'test-only', 'dimension': 2}


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw')))
    coordinator = RetrievalCoordinator(storage, notes)
    for id, text, vector in [('lexical', 'train train train', [0, 1]), ('semantic', 'railway journey', [1, 0])]:
        storage.upsert_node(GraphNode(id=id, node_type=NodeType.FACT, title=id, content=text))
        coordinator.vector_index.submit_vector(space=SPACE, plane='MEMORY', object_id=id,
            revision=1, scope='default', content_hash=hashlib.sha256(text.encode()).hexdigest(), vector=vector)
    return storage, coordinator


def test_vector_only_match_enters_rrf_without_loading_a_model(tmp_path):
    storage, coordinator = setup(tmp_path)
    query = {'space': SPACE, 'vector': [1, 0]}
    result = coordinator.search('train', plane='MEMORY', embedding_query=query)
    assert {x['id'] for x in result['items']} == {'lexical', 'semantic'}
    assert result['mode'] == 'hybrid_rrf'
    assert result['vector']['coverage']['missing'] == 0
    artifact = coordinator.retrieve('train', user_specified_plane='MEMORY', embedding_query=query)
    assert 'railway journey' in artifact.combined_prompt
    assert artifact.retrieval['vector_search'] is True
    assert coordinator.vector_index.status()['model_loaded'] is False


def test_vector_failure_explains_keyword_fallback(tmp_path, monkeypatch):
    storage, coordinator = setup(tmp_path)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('injected unavailable projection')
    monkeypatch.setattr(coordinator.vector_index, 'search', fail)
    result = coordinator.search('train', embedding_query={'space': SPACE, 'vector': [1, 0]})
    assert [x['id'] for x in result['items']] == ['lexical']
    assert result['vector']['availability'] == 'unavailable'
    assert result['vector']['partial'] is True


def test_fusion_revalidates_after_source_changes_between_rankers(tmp_path, monkeypatch):
    storage, coordinator = setup(tmp_path)
    original = coordinator.vector_index.search
    def change_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        node = storage.get_node('semantic')
        node.scope = 'private'
        storage.upsert_node(node)
        return result
    monkeypatch.setattr(coordinator.vector_index, 'search', change_after_read)
    result = coordinator.search('train', embedding_query={'space': SPACE, 'vector': [1, 0]})
    assert 'semantic' not in [x['id'] for x in result['items']]
    assert result['canonical_changed'] is True


def test_explicit_both_plane_overrides_query_heuristic(tmp_path):
    storage, coordinator = setup(tmp_path)
    coordinator.notes_mgr.create_note('train notes', 'decision source text')
    storage.upsert_node(GraphNode(id='decision', node_type=NodeType.FACT, title='decision', content='decision memory text'))
    artifact = coordinator.retrieve('decision', user_specified_plane='BOTH')
    assert artifact.retrieval['plane'] == 'BOTH'
    assert 'decision source text' in artifact.combined_prompt
    assert 'decision memory text' in artifact.combined_prompt
    with pytest.raises(ValueError):
        coordinator.retrieve('decision', user_specified_plane='not-a-plane')


def test_rest_accepts_external_vectors_and_validates_query(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    with TestClient(create_app(str(tmp_path/'api.db'), str(tmp_path/'raw'), str(tmp_path/'artifacts'))) as client:
        note = client.post('/api/notes', json={'title': 'Rail', 'content': 'railway journey'}).json()
        req = {'space': SPACE, 'plane': 'NOTES', 'object_id': note['id'], 'revision': 1,
               'content_hash': note['content_hash'], 'vector': [1, 0]}
        assert client.post('/api/retrieval/embeddings', json=req).status_code == 200
        search = client.post('/api/retrieval/search', json={'query': 'train', 'user_specified_plane': 'NOTES',
            'embedding_query': {'space': SPACE, 'vector': [1, 0]}})
        assert search.status_code == 200
        assert 'railway journey' in search.json()['combined_prompt']
        assert client.post('/api/retrieval/embeddings', json={**req, 'revision': 99}).status_code == 422
        assert client.post('/api/retrieval/search', json={'query': 'train', 'embedding_query': {'space': SPACE, 'vector': [0, 0]}}).status_code == 422
        assert client.get('/api/retrieval/vector-index').json()['model_loaded'] is False
