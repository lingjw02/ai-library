import pytest
from tests.test_harness_plugin import bridge_for, seed


def test_per_read_budget_is_bounded_and_does_not_mutate_defaults(tmp_path):
    bridge = bridge_for(tmp_path)
    seed(bridge.service, content='SQLite evidence. ' * 100)
    packet = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'one',
                           'max_context_chars': 3200, 'max_items': 1, 'excerpt_chars': 80})
    assert len(packet['context_text']) <= 3200
    assert packet['accounting']['max_context_characters'] == 3200
    assert bridge.max_context_chars == 6000
    assert packet['evidence_count'] == 1
    with pytest.raises(ValueError):
        bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'one', 'max_items': 100})
    bridge.close()


def test_observe_only_own_registered_chat_and_does_not_create_one(tmp_path):
    bridge = bridge_for(tmp_path)
    seed(bridge.service)
    assert bridge.handle({'operation': 'observe', 'session_key': 'new'})['session'] is None
    bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'one'})
    bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'two'})
    seen = bridge.handle({'operation': 'observe', 'session_key': 'one'})
    assert seen['session']['id'] == bridge.sessions['one']
    assert seen['session']['state'] == 'idle'
    assert seen['session']['position']['object_id']
    assert bridge.sessions['two'] not in str(seen)
    assert seen['session']['events'][-1]['action'] == 'search'
    bridge.close()
