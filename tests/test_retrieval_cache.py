from backend.cache.cache_manager import CacheManager
from tests.test_retrieval_integrity import setup


def test_actual_cache_hits_and_new_knowledge_invalidates_negative_result(tmp_path):
    _, notes, coordinator = setup(tmp_path)
    coordinator.retrieve('SQLite')
    coordinator.retrieve('SQLite')
    assert coordinator.cache_mgr.stats()['context']['hits'] == 1
    notes.create_note('SQLite', 'SQLite new evidence')
    assert 'new evidence' in coordinator.retrieve('SQLite').combined_prompt


def test_cache_never_aliases_mutable_result_and_scope_epoch_guards_fills():
    cache = CacheManager()
    epoch = cache.generation('a')
    value = {'items': [1]}
    assert cache.put_retrieval('one', value, scope='a', expected_generation=epoch)
    cached = cache.get_retrieval('one')
    cached['items'].append(2)
    assert cache.get_retrieval('one') == value
    cache.invalidate_scope('a')
    assert not cache.put_retrieval('old', value, scope='a', expected_generation=epoch)
    assert cache.stats()['provider']['cached_tokens'] is None


def test_authorization_fingerprint_separates_context_cache(tmp_path):
    _, notes, coordinator = setup(tmp_path)
    notes.create_note('SQLite', 'SQLite information')
    coordinator.retrieve('SQLite', authorization_fingerprint='caller-a')
    coordinator.retrieve('SQLite', authorization_fingerprint='caller-b')
    assert coordinator.cache_mgr.stats()['context']['hits'] == 0
