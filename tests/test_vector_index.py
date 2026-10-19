import hashlib
from datetime import timedelta

import pytest

from backend.core.retrieval.vector_index import EmbeddingSpace, VectorIndex
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphNode, NodeType, TemporalValidity, utc_now
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


SPACE = EmbeddingSpace('external-test', 'v1', 2)


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'test.db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    return storage, notes, VectorIndex(storage)


def node(storage, object_id, content='original', **kwargs):
    storage.upsert_node(GraphNode(id=object_id, title=object_id,
                                  content=content, node_type=NodeType.FACT, **kwargs))
    return storage.get_node(object_id)


def submit(index, obj, vector=(1, 0), plane='MEMORY', space=SPACE, **changes):
    args = dict(space=space, plane=plane, object_id=obj.id,
                revision=obj.version if isinstance(obj, GraphNode) else obj.revision,
                scope=obj.scope, content_hash=hashlib.sha256(obj.content.encode('utf-8')).hexdigest(),
                vector=vector)
    args.update(changes)
    return index.submit_vector(**args)


def test_cosine_ranking_coverage_and_restart(tmp_path):
    storage, notes, index = setup(tmp_path)
    submit(index, node(storage, 'near'), (3, 4))
    submit(index, node(storage, 'opposite'), (-1, 0))
    submit(index, notes.create_note('orthogonal', '原文'), (0, 1), plane='NOTES')
    node(storage, 'missing')
    result = index.search([1, 0], space=SPACE)
    assert result['items'][0]['id'] == 'near'
    assert result['items'][1]['plane'] == 'NOTES'
    assert result['items'][2]['id'] == 'opposite'
    assert [item['score'] for item in result['items']] == pytest.approx([.6, 0, -1])
    assert result['coverage'] == {'total': 4, 'embedded': 3, 'available': 3, 'missing': 1, 'complete': True}
    assert result['partial'] and result['mode'] == 'exact_cosine'
    restarted = VectorIndex(CanonicalStorage(str(storage.db_path)))
    assert restarted.search([1, 0], space=SPACE)['items'] == result['items']


def test_tied_top_k_uses_same_order_as_complete_ranking(tmp_path):
    storage, notes, index = setup(tmp_path)
    for identity in ['z', 'b', 'a', 'c']:
        submit(index, node(storage, identity))
    complete = index.search([1, 0], space=SPACE, limit=10)['items']
    bounded = index.search([1, 0], space=SPACE, limit=2)['items']
    assert [x['id'] for x in bounded] == [x['id'] for x in complete[:2]] == ['a', 'b']


@pytest.mark.parametrize('vector', [[0, 0], [float('nan'), 1], [1, float('inf')], [1], [1, 2, 3], [True, 1], ['1', 0]])
def test_reject_bad_vectors(tmp_path, vector):
    storage, notes, index = setup(tmp_path)
    obj = node(storage, 'one')
    with pytest.raises(ValueError):
        submit(index, obj, vector)
    with pytest.raises(ValueError):
        index.search(vector, space=SPACE)


def test_large_and_tiny_vectors_normalize_without_overflow(tmp_path):
    storage, notes, index = setup(tmp_path)
    submit(index, node(storage, 'huge'), [1e308, 1e308])
    submit(index, node(storage, 'tiny'), [1e-320, 1e-320])
    assert [x['score'] for x in index.search([1e308, 1e308], space=SPACE)['items']] == pytest.approx([1, 1])


@pytest.mark.parametrize('kwargs', [{'dimension': 4097}, {'dimension': 0}, {'dimension': True}, {'model_id': ''}, {'metric': 'dot'}, {'normalization': 'none'}])
def test_space_validation(kwargs):
    params = dict(model_id='external', model_version='1', dimension=2)
    params.update(kwargs)
    with pytest.raises(ValueError):
        EmbeddingSpace(**params)


def test_spaces_do_not_mix_and_duplicates_are_immutable(tmp_path):
    storage, notes, index = setup(tmp_path)
    obj = node(storage, 'one')
    first = submit(index, obj)
    assert not first['idempotent']
    assert submit(index, obj)['idempotent']
    with pytest.raises(ValueError, match='immutable'):
        submit(index, obj, (2, 0))
    for other in [EmbeddingSpace('other', 'v1', 2), EmbeddingSpace('external-test', 'v2', 2), EmbeddingSpace('external-test', 'v1', 3)]:
        assert index.search([1] + [0] * (other.dimension - 1), space=other)['items'] == []


def test_source_hash_scope_and_revision_verified(tmp_path):
    storage, notes, index = setup(tmp_path)
    obj = node(storage, 'one', ' exact\n原文 ')
    for changes in [dict(scope='other'), dict(revision=99), dict(content_hash='0' * 64), dict(plane='NOTES')]:
        with pytest.raises(ValueError):
            submit(index, obj, **changes)
    submit(index, obj)
    assert index.search([1, 0], space=SPACE, scope='other')['items'] == []
    obj.content = 'updated'
    storage.upsert_node(obj)
    assert index.search([1, 0], space=SPACE)['items'] == []
    missing = index.missing_embedding_sources(space=SPACE)
    assert missing['items'][0]['revision'] == 2
    assert missing['items'][0]['content_hash'] == hashlib.sha256(b'updated').hexdigest()


def test_inactive_expired_future_and_note_revision_not_returned(tmp_path):
    storage, notes, index = setup(tmp_path)
    for object_id in ['inactive', 'expired', 'future', 'superseded']:
        obj = node(storage, object_id)
        submit(index, obj)
        if object_id == 'inactive':
            obj.is_active = False
        else:
            obj.temporal_props = TemporalValidity(**{
                'expired': {'valid_to': utc_now() - timedelta(days=1)},
                'future': {'valid_from': utc_now() + timedelta(days=1)},
                'superseded': {'superseded_by': 'new'},
            }[object_id])
        storage.upsert_node(obj, emit_outbox=False)
    note = notes.create_note('old title', 'old content')
    submit(index, note, plane='NOTES')
    notes.revise_note(note.id, 'new title', 'new content', expected_revision=1)
    result = index.search([1, 0], space=SPACE)
    assert result['items'] == []
    assert result['coverage']['total'] == 1


def test_failed_rebuild_preserves_projection_and_recovery(tmp_path, monkeypatch):
    storage, notes, index = setup(tmp_path)
    submit(index, node(storage, 'one'))
    before = index.status()
    original = index._write_projection
    def fail(*args):
        raise RuntimeError('injected failure')
    monkeypatch.setattr(index, '_write_projection', fail)
    with pytest.raises(RuntimeError):
        index.rebuild()
    assert index.status() == before
    assert index.search([1, 0], space=SPACE)['items'][0]['id'] == 'one'
    monkeypatch.setattr(index, '_write_projection', original)
    with storage.session() as conn:
        conn.execute('DELETE FROM vector_projection')
    assert index.search([1, 0], space=SPACE)['coverage']['available'] == 0
    index.rebuild()
    assert index.search([1, 0], space=SPACE)['items'][0]['id'] == 'one'
    assert index.status()['generation'] == before['generation'] + 1


def test_bounded_missing_pages_and_deadline(tmp_path, monkeypatch):
    storage, notes, index = setup(tmp_path)
    for i in range(9):
        node(storage, f'node-{i}')
    seen, cursor = [], None
    while True:
        page = index.missing_embedding_sources(space=SPACE, limit=2, cursor=cursor)
        seen.extend(x['id'] for x in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert seen == [f'node-{i}' for i in range(9)]
    ticks = iter([0, 1, 2, 3, 4, 5])
    monkeypatch.setattr('backend.core.retrieval.vector_index.time.monotonic', lambda: next(ticks))
    result = index.search([1, 0], space=SPACE, deadline_ms=1)
    assert result['partial'] and 'deadline' in result['partial_reasons']
    assert not result['coverage']['complete']
