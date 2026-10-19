import pytest
from backend.schema.models import GraphNode, NodeType, TemporalValidity, Provenance
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.schema.models import utc_now
from datetime import timedelta


def env(tmp_path):
    from backend.maintenance.health import KnowledgeHealth
    storage = CanonicalStorage(str(tmp_path / 'db'))
    storage.upsert_node(GraphNode(id='a', node_type=NodeType.FACT, scope='x', title='One', content='Unverified claim'))
    storage.upsert_node(GraphNode(id='b', node_type=NodeType.FACT, scope='x', title='Two', content='Unverified claim',
        provenance=Provenance(source_id='missing', content_hash='0'*64)))
    storage.upsert_node(GraphNode(id='stale', node_type=NodeType.FACT, scope='x', title='Stale', content='Old claim',
        temporal_props=TemporalValidity(valid_to=utc_now()-timedelta(days=1))))
    return storage, KnowledgeHealth(storage)


def test_health_reports_measured_debt_without_inventing_accuracy(tmp_path):
    storage, health = env(tmp_path)
    report = health.report(scope='x')
    assert report['metrics']['accuracy'] is None
    assert report['metrics']['retrieval_quality'] is None
    assert report['metrics']['unverified_count'] == 3
    assert report['metrics']['expired_count'] == 1
    assert report['metrics']['missing_source_count'] == 1
    assert report['metrics']['possible_duplicate_count'] == 1
    assert report['coverage'] == 'complete_scope'
    assert health.report(scope='x', limit=1)['coverage'] == 'partial_scope'
    assert health.report(scope='y')['metrics']['node_count'] == 0
    assert len(storage.list_nodes(scope='x')) == 3


def test_review_queue_is_confirmed_version_bound_deduplicated_and_non_destructive(tmp_path):
    storage, health = env(tmp_path)
    report = health.report(scope='x')
    result = health.queue_review(scope='x', report_hash=report['report_hash'], selected_ids=['a', 'stale'])
    assert len(result['items']) == 2
    assert health.queue_review(scope='x', report_hash=report['report_hash'], selected_ids=['a', 'stale']) == result
    node = storage.get_node('a')
    node.content = 'Changed'
    storage.upsert_node(node, expected_revision=1)
    with pytest.raises(ValueError):
        health.queue_review(scope='x', report_hash=report['report_hash'], selected_ids=['a'])
    assert len(storage.list_nodes(scope='x')) == 3
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM system_memory_backlog').fetchone()[0] == 2


def test_idle_planning_is_bounded_and_never_runs_model_or_deletes(tmp_path):
    storage, health = env(tmp_path)
    report = health.report(scope='x')
    health.queue_review(scope='x', report_hash=report['report_hash'], selected_ids=['a', 'stale'])
    plan = health.idle_plan(scope='x', max_jobs=1, token_budget=500)
    assert len(plan['jobs']) == 1
    assert plan['execution'] == 'external_agent_required'
    assert health.idle_plan(scope='x', max_jobs=1, token_budget=0)['jobs'] == []
    assert len(storage.list_nodes(scope='x')) == 3
