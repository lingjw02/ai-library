import pytest
from backend.schema.registry import SchemaRegistry
from backend.schema.models import GraphNode, NodeType
from backend.storage.canonical.sqlite_storage import CanonicalStorage


@pytest.mark.parametrize('node_type', list(NodeType))
def test_every_v2_type_has_enforced_distinct_contract(node_type):
    registry = SchemaRegistry()
    result = registry.validate_node_data({'id': 'new', 'node_type': node_type.value, 'title': 'Typed',
        'content': 'Claim', 'schema_version': 2})
    assert not result.is_valid
    assert result.quarantine_record is not None
    assert registry.contracts()['types'][node_type.value]['version'] == 2


def test_v1_remains_explicit_legacy_and_unknown_versions_do_not_downgrade():
    registry = SchemaRegistry()
    value = dict(id='x', node_type='Entity', title='Entity', content='Description')
    result = registry.validate_node_data(value)
    assert result.is_valid and result.node.schema_version == 1
    value['schema_version'] = 99
    assert not registry.validate_node_data(value).is_valid
    value.update(schema_version=2, core_props={'entity_type': 'organization', 'aliases': ['Company']})
    assert registry.validate_node_data(value).is_valid
    value['core_props']['aliases'] = 'Company'
    assert not registry.validate_node_data(value).is_valid


def test_migration_is_preview_bound_preserves_old_revision_and_rejects_stale(tmp_path):
    from backend.schema.migrations import SchemaMigrator
    storage = CanonicalStorage(str(tmp_path / 'db'))
    storage.upsert_node(GraphNode(id='entity', node_type=NodeType.ENTITY, title='Entity', content='Original', scope='a'))
    migrator = SchemaMigrator(storage)
    fields = {'core_props': {'entity_type': 'project', 'aliases': []}}
    preview = migrator.preview('entity', scope='a', expected_revision=1, fields=fields)
    assert storage.get_node('entity').schema_version == 1
    result = migrator.commit('entity', scope='a', expected_revision=1, fields=fields, preview_hash=preview['preview_hash'])
    assert result['schema_version'] == 2 and result['version'] == 2
    assert storage.get_node_revision('entity', 1, scope='a').schema_version == 1
    with pytest.raises(ValueError):
        migrator.commit('entity', scope='a', expected_revision=1, fields=fields, preview_hash=preview['preview_hash'])
    with pytest.raises(ValueError):
        migrator.preview('entity', scope='a', expected_revision=2, fields={'authority': 'EXPLICIT'})
