import hashlib
import pytest

from backend.auth.policy import AccessPolicy
from backend.notes.notes_manager import NotesManager
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def env(tmp_path):
    from backend.lineage.manager import LineageManager
    storage = CanonicalStorage(str(tmp_path / 'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    note = notes.create_note('Conversation', 'Original source', scope='a')
    return storage, notes, note, LineageManager(storage)


def payload(note, session='root', events=None, **kwargs):
    return dict(source_system='native-test', session_id=session, source_revision=1,
        source_ref=dict(source_id=note.id, source_revision=note.revision, content_hash=note.content_hash),
        events=events or [dict(event_id='turn-1', role='user', content='Original source')], **kwargs)


def test_snapshots_are_exact_idempotent_and_native_ancestry_is_verified(tmp_path):
    _, _, note, lineage = env(tmp_path)
    root = lineage.import_snapshot(payload(note), scope='a')
    assert lineage.import_snapshot(payload(note), scope='a') == root
    child = lineage.import_snapshot(payload(note, 'fork', parent_session_id='root',
        fork_anchor_event_id='turn-1', events=[dict(event_id='turn-2', role='assistant', content='Answer')]), scope='a')
    graph = lineage.get_graph(child['id'], scope='a')
    assert graph['parent']['id'] == root['id']
    assert graph['ancestry_status'] == 'source_verified'
    assert graph['coverage'] == 'snapshot'
    assert len(lineage.list_sessions(scope='a')['items']) == 2
    assert lineage.list_sessions(scope='b')['items'] == []


def test_fork_without_exact_source_anchor_is_unknown_not_inferred(tmp_path):
    _, _, note, lineage = env(tmp_path)
    item = lineage.import_snapshot(payload(note, 'fork', parent_session_id='missing',
        fork_anchor_event_id='same-sounding'), scope='a')
    graph = lineage.get_graph(item['id'], scope='a')
    assert graph['parent'] is None
    assert graph['ancestry_status'] == 'unknown'
    assert graph['warnings']


def test_revision_conflict_integrity_and_cross_scope_roll_back(tmp_path):
    storage, _, note, lineage = env(tmp_path)
    item = lineage.import_snapshot(payload(note), scope='a')
    bad = payload(note, events=[dict(event_id='turn-1', role='user', content='Changed')])
    with pytest.raises(ValueError):
        lineage.import_snapshot(bad, scope='a')
    with pytest.raises(ValueError):
        lineage.import_snapshot(payload(note), scope='b')
    bad_hash = payload(note, 'bad')
    bad_hash['source_ref']['content_hash'] = '0' * 64
    with pytest.raises(ValueError):
        lineage.import_snapshot(bad_hash, scope='a')
    assert len(lineage.list_sessions(scope='a')['items']) == 1
    assert lineage.get_graph(item['id'], scope='a')['session']['source_revision'] == 1


def test_shared_native_events_dedup_and_revocation_hides_identity(tmp_path):
    storage, _, note, lineage = env(tmp_path)
    root = lineage.import_snapshot(payload(note), scope='a')
    lineage.import_snapshot(payload(note, 'fork', parent_session_id='root', fork_anchor_event_id='turn-1'), scope='a')
    events = lineage.list_events(root['id'], scope='a')
    assert events['items'][0]['independent_evidence_count'] == 1
    lineage.revoke(root['id'], scope='a', expected_revision=1, reason='Owner revoked retention')
    with pytest.raises(LookupError):
        lineage.get_graph(root['id'], scope='a')
    assert root['id'] not in str(lineage.list_sessions(scope='a'))


def test_agent_cannot_broaden_scope_or_revoke(tmp_path):
    storage, _, note, lineage = env(tmp_path)
    root = lineage.import_snapshot(payload(note), scope='a')
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a', write=True):
        with pytest.raises(PermissionError):
            lineage.list_sessions(scope='b')
        with pytest.raises(PermissionError):
            lineage.revoke(root['id'], scope='a', expected_revision=1, reason='Self-authorized')
