from datetime import timedelta

import pytest

from backend.core.retrieval.keyword_index import KeywordIndex
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphNode, NodeType, TemporalValidity, utc_now
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'test.db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    return storage, notes, KeywordIndex(storage)


def node(storage, id, content='needle', **kwargs):
    return storage.upsert_node(GraphNode(id=id, title=id, content=content, node_type=NodeType.FACT, **kwargs))


def test_search_covers_old_documents_and_exact_ids(tmp_path):
    storage, notes, index = setup(tmp_path)
    node(storage, 'old-memory', '珍贵原文来源')
    original = notes.create_note('old-note', '珍贵笔记内容')
    for i in range(125):
        node(storage, f'filler-{i}', 'unrelated')
    for i in range(55):
        notes.create_note('new-note', 'unrelated')
    index.rebuild()
    coordinator = RetrievalCoordinator(storage, notes)
    assert '珍贵原文来源' in coordinator.retrieve('原文来源', user_specified_plane='MEMORY').combined_prompt
    assert '珍贵笔记内容' in coordinator.retrieve('笔记内容', user_specified_plane='NOTES').combined_prompt
    result = index.search(original.id, scope='default')
    assert result['items'][0]['id'] == original.id
    assert result['mode'] == 'fts5'
    assert index.search('" OR * NOT (', scope='default')['items'] == []


def test_lag_falls_back_then_replay_is_idempotent_and_durable(tmp_path):
    storage, notes, index = setup(tmp_path)
    note = notes.create_note('old', 'oldword')
    index.rebuild()
    checkpoint = index.status()['checkpoint']
    notes.revise_note(note.id, 'new', 'newword', expected_revision=1)
    assert index.search('newword')['mode'] == 'canonical_fallback'
    assert index.search('oldword')['items'] == []
    index.drain()
    assert index.search('newword')['mode'] == 'fts5'
    # Replaying a prefix reads current canonical revisions, never old payloads.
    with storage.session() as conn:
        conn.execute('UPDATE keyword_index_state SET checkpoint = ?', (checkpoint,))
    index.drain()
    restarted = KeywordIndex(CanonicalStorage(str(storage.db_path)))
    assert len(restarted.search('newword')['items']) == 1
    assert restarted.status()['lag'] == 0


def test_scope_deactivation_time_and_unsignalled_write(tmp_path):
    storage, notes, index = setup(tmp_path)
    node(storage, 'public')
    node(storage, 'private', scope='private')
    node(storage, 'expired', temporal_props=TemporalValidity(valid_to=utc_now() - timedelta(seconds=1)))
    node(storage, 'future', temporal_props=TemporalValidity(valid_from=utc_now() + timedelta(days=1)))
    index.rebuild()
    assert [n['id'] for n in index.search('needle')['items']] == ['public']
    changed = storage.get_node('public')
    changed.is_active = False
    storage.upsert_node(changed, emit_outbox=False)
    assert index.search('needle')['items'] == []
    notes.create_note('different', 'needle', scope='private')
    assert index.search('needle', scope='default')['items'] == []


def test_poison_event_blocks_prefix_and_rebuild_recovers(tmp_path, monkeypatch):
    storage, notes, index = setup(tmp_path)
    index.rebuild()
    checkpoint = index.status()['checkpoint']
    notes.create_note('first', 'needle')
    notes.create_note('second', 'needle')
    original = index._apply_event
    def fail(*args):
        raise RuntimeError('injected projection error')
    monkeypatch.setattr(index, '_apply_event', fail)
    for _ in range(5):
        index.drain()
    state = index.status()
    assert state['checkpoint'] == checkpoint
    assert state['blocked'] is True
    assert state['retry_count'] == 3
    with storage.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dead_letter_queue WHERE job_type = 'KEYWORD_INDEX'").fetchone()[0] == 1
    assert len(index.search('needle')['items']) == 2
    monkeypatch.setattr(index, '_apply_event', original)
    index.rebuild()
    assert index.status()['blocked'] is False
    assert index.status()['lag'] == 0
    assert index.search('needle')['mode'] == 'fts5'


def test_failed_rebuild_preserves_old_generation(tmp_path, monkeypatch):
    storage, notes, index = setup(tmp_path)
    notes.create_note('one', 'needle')
    index.rebuild()
    before = index.status()
    def fail(*args, **kwargs):
        raise RuntimeError('injected rebuild error')
    monkeypatch.setattr(index, '_write_document', fail)
    with pytest.raises(RuntimeError):
        index.rebuild()
    assert index.status()['generation'] == before['generation']
    assert len(index.search('needle')['items']) == 1


def test_fts_failure_falls_back_to_canonical(tmp_path):
    storage, notes, index = setup(tmp_path)
    notes.create_note('one', 'needle')
    index.rebuild()
    with storage.session() as conn:
        conn.execute('DROP TABLE keyword_fts')
    result = index.search('needle')
    assert result['mode'] == 'canonical_fallback'
    assert len(result['items']) == 1


def test_rebuild_retires_derived_rows_and_never_raw_revisions(tmp_path):
    storage, notes, index = setup(tmp_path)
    note = notes.create_note('one', 'needle')
    index.rebuild()
    notes.revise_note(note.id, 'two', 'current', expected_revision=1)
    index.rebuild()
    index.rebuild()
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM keyword_documents').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM keyword_fts').fetchone()[0] == 1
    assert notes.get_note(note.id, 1).content == 'needle'
    assert index.search('needle')['items'] == []


def test_temporal_expiration_without_writes_and_graph_boundary(tmp_path):
    from backend.schema.models import GraphEdge, RelationshipType
    storage, notes, index = setup(tmp_path)
    node(storage, 'seed', 'needle')
    node(storage, 'expired', 'SECRET', temporal_props=TemporalValidity(valid_to=utc_now() - timedelta(seconds=1)))
    storage.upsert_edge(GraphEdge(id='link', source_id='seed', target_id='expired', relation=RelationshipType.RELATED_TO))
    index.rebuild()
    coordinator = RetrievalCoordinator(storage, notes)
    assert 'SECRET' not in coordinator.retrieve('related to needle').combined_prompt


@pytest.mark.parametrize('budget', [1, 5, 60, 75, 100, 1024])
def test_compiled_estimate_respects_total_budget(tmp_path, budget):
    storage, notes, index = setup(tmp_path)
    notes.create_note('title', 'needle ' * 1000)
    artifact = RetrievalCoordinator(storage, notes).retrieve('needle ' * 100, token_budget=budget)
    assert artifact.estimated_tokens <= budget
    assert artifact.budget_unit == 'estimated_tokens'


def test_api_worker_lifecycle_and_document_query(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    import time
    app = create_app(str(tmp_path / 'api.db'), str(tmp_path / 'raw'), str(tmp_path / 'artifacts'))
    with TestClient(app) as client:
        created = client.post('/api/notes', json={'title': 'needle', 'content': 'body', 'scope': 'qa'}).json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get('/api/retrieval/index').json()
            if result['ready'] and result['lag'] == 0:
                break
            time.sleep(0.05)
        assert result['ready'] and result['lag'] == 0
        match = client.get('/api/retrieval/documents', params={'query': 'needle', 'scope': 'qa', 'plane': 'NOTES'}).json()
        assert match['items'][0]['id'] == created['id']
        assert match['mode'] == 'fts5'
        assert client.get('/api/retrieval/documents', params={'query': 'needle', 'scope': 'default'}).json()['items'] == []
        assert client.post('/api/retrieval/search', json={'query': '  '}).status_code == 422
        assert client.post('/api/retrieval/index/rebuild').json()['generation'] >= 2
