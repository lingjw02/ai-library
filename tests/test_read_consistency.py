from datetime import timedelta

from backend.schema.models import GraphNode, NodeType, utc_now
from tests.test_retrieval_integrity import setup


def test_historical_reads_retained_revision_without_future_knowledge(tmp_path):
    storage, _, coordinator = setup(tmp_path)
    node = GraphNode(id='past', node_type=NodeType.FACT, title='SQLite', content='SQLite original')
    storage.upsert_node(node)
    cutoff = utc_now()
    node.content = 'SQLite future'
    storage.upsert_node(node)
    result = coordinator.search('SQLite', plane='MEMORY', read_mode='historical_as_of', as_of=cutoff)
    assert [item['content'] for item in result['items']] == ['SQLite original']
    assert result['consistency']['historical_snapshot']


def test_unmet_minimum_is_reported_and_context_does_not_claim_complete(tmp_path):
    _, notes, coordinator = setup(tmp_path)
    notes.create_note('SQLite', 'SQLite source')
    result = coordinator.retrieve('SQLite', read_mode='at_least_revision', minimum_revision=100000)
    assert result.retrieval['consistency']['minimum_satisfied'] is False
    assert result.retrieval['coverage'] == 'partial'


def test_expired_node_is_removed_from_warm_context(tmp_path, monkeypatch):
    storage, _, coordinator = setup(tmp_path)
    now = utc_now()
    node = GraphNode(id='time', node_type=NodeType.FACT, title='SQLite', content='SQLite ephemeral',
                     temporal_props={'valid_to': now + timedelta(seconds=5)})
    storage.upsert_node(node)
    assert 'ephemeral' in coordinator.retrieve('SQLite').combined_prompt
    monkeypatch.setattr('backend.core.retrieval.consistency.utc_now', lambda: now + timedelta(seconds=10))
    assert 'ephemeral' not in coordinator.retrieve('SQLite').combined_prompt
