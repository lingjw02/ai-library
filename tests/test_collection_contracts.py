import pytest
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.collections.manager import CollectionManager
from backend.capabilities.registry.capability_registry import CapabilityRegistry, SkillBundle


def test_capability_membership_is_scoped_versioned_reference_and_never_installs(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    manager = CollectionManager(storage)
    registry = CapabilityRegistry(storage, str(tmp_path / 'art'))
    registry.register_skill(SkillBundle('skill', 'A skill', 'Description', '# Instructions', version='1.0'))
    topic = manager.create('Capabilities', scope='a')
    member = manager.membership(topic['id'], 'a', 'capability', 'skill', 1)
    assert member['revision'] == 2
    assert manager.capability_members(topic['id'], scope='a')['items'][0]['id'] == 'skill'
    assert manager.capability_members(topic['id'], scope='a')['items'][0]['installed'] is False
    with pytest.raises(ValueError):
        manager.membership(topic['id'], 'b', 'capability', 'skill', 2)
    manager.membership(topic['id'], 'a', 'capability', 'skill', 2, remove=True)
    assert manager.capability_members(topic['id'], scope='a')['items'] == []
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_profiles').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM collection_organization_audit').fetchone()[0] >= 3
        assert conn.execute("SELECT COUNT(*) FROM transactional_outbox WHERE aggregate_type='COLLECTION'").fetchone()[0] >= 3


def test_clustering_only_accepts_real_members_and_does_not_fabricate_content(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    manager = CollectionManager(storage)
    topic = manager.create('Parent', scope='a')
    with pytest.raises(ValueError):
        manager.apply_clustering(topic['id'], 'Child', ['unrelated-or-missing'], scope='a')
    assert manager.list_topics(scope='a')['total'] == 1
