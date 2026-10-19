from backend.core.retrieval.bm25 import BM25Scorer
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphNode, GraphEdge, NodeType, RelationshipType


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'test.db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    return storage, notes, RetrievalCoordinator(storage, notes)


def test_no_match_does_not_return_unrelated_documents():
    scorer = BM25Scorer()
    scorer.fit({'one': 'SQLite source revisions'})
    assert scorer.score('astronomy') == []


def test_chinese_keyword_matches_inside_sentence():
    scorer = BM25Scorer()
    scorer.fit({'one': '本项目的记忆必须保留原文来源以及版本信息。', 'two': '另一个无关句子。'})
    assert [item[0] for item in scorer.score('原文来源')] == ['one']


def test_context_cache_distinguishes_planes_and_new_revisions(tmp_path):
    storage, notes, coordinator = setup(tmp_path)
    note = notes.create_note('Storage', 'SQLite original note')
    first = coordinator.retrieve('SQLite', user_specified_plane='NOTES')
    assert 'original note' in first.dynamic_context
    memory = coordinator.retrieve('SQLite', user_specified_plane='MEMORY')
    assert 'original note' not in memory.dynamic_context
    notes.revise_note(note.id, 'Storage', 'SQLite revised note', expected_revision=1)
    revised = coordinator.retrieve('SQLite', user_specified_plane='NOTES')
    assert 'revised note' in revised.dynamic_context
    assert 'original note' not in revised.dynamic_context


def test_graph_expansion_excludes_unrelated_and_cross_scope_nodes(tmp_path):
    storage, _, coordinator = setup(tmp_path)
    for node_id, content, scope in [('irrelevant', 'Gardening', 'default'), ('seed', 'SQLite', 'default'), ('private', 'Private phrase', 'private')]:
        storage.upsert_node(GraphNode(id=node_id, node_type=NodeType.CONCEPT, title=content, content=content, scope=scope))
    storage.upsert_edge(GraphEdge(id='edge', source_id='seed', target_id='private', relation=RelationshipType.RELATED_TO))
    artifact = coordinator.retrieve('related to SQLite', user_specified_plane='MEMORY')
    assert 'SQLite' in artifact.dynamic_context
    assert 'Gardening' not in artifact.dynamic_context
    assert 'Private phrase' not in artifact.dynamic_context
