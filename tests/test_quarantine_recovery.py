import json
import pytest

from backend.candidates.candidate_bridge import CandidateBridge
from backend.notes.notes_manager import NotesManager
from backend.schema.models import NodeType
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def setup(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw')))
    source = notes.create_note('Source', 'Source claim', scope='a')
    bridge = CandidateBridge(storage)
    submission = dict(source_id=source.id, source_revision=1, claim='Source claim',
        suggested_node_type=NodeType.FACT, payload={'confidence': 'broken'}, scope='a')
    ok, quarantine_id, _ = bridge.submit_candidate(**submission)
    assert not ok
    return storage, notes, bridge, source, submission, quarantine_id


def test_quarantine_preserves_submission_scope_revision_and_duplicate_identity(tmp_path):
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    assert bridge.submit_candidate(**submission)[1] == identity
    record = storage.list_quarantine()[0]
    assert record.scope == 'a' and record.source_revision == 1 and record.revision == 1
    assert record.submission['payload'] == {'confidence': 'broken'}
    assert len(storage.list_quarantine()) == 1


def test_repair_returns_candidate_without_acceptance_and_keeps_original_bytes(tmp_path):
    from backend.quarantine.quarantine_manager import QuarantineManager
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    manager = QuarantineManager(storage)
    before = manager.get(identity, scope='a')
    with storage.session() as conn:
        raw_before = conn.execute('SELECT payload_json FROM quarantine WHERE id = ?', (identity,)).fetchone()[0]
    repaired = manager.repair(identity, scope='a', expected_revision=1, claim='Source claim',
        suggested_node_type='Fact', payload={'confidence': 0.5}, reason='Corrected numeric confidence', actor='local-owner')
    assert repaired.resolved and repaired.revision == 2
    candidate = bridge.get_candidate(repaired.result_candidate_id)
    assert candidate.status.value == 'VALIDATED' and candidate.authority.value == 'INFERRED'
    assert candidate.payload['content_hash'] == source.content_hash
    assert candidate.source_revision == before.source_revision == 1
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0] == 0
        assert conn.execute('SELECT payload_json FROM quarantine WHERE id = ?', (identity,)).fetchone()[0] == raw_before
        assert conn.execute('SELECT COUNT(*) FROM system_memory_logsheet').fetchone()[0] == 1
    assert manager.list(scope='a')['items'] == []
    assert manager.list(scope='a', resolved=True)['items'][0]['id'] == identity
    with pytest.raises(ValueError, match='revision'):
        manager.repair(identity, scope='a', expected_revision=1, claim='Different',
            suggested_node_type='Fact', payload={}, reason='stale edit', actor='local-owner')


def test_quarantine_recovery_rejects_wrong_scope_invalid_repair_and_rolls_back_audit_failure(tmp_path, monkeypatch):
    from backend.quarantine.quarantine_manager import QuarantineManager
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    manager = QuarantineManager(storage)
    assert manager.list(scope='other')['items'] == []
    with pytest.raises(LookupError):
        manager.get(identity, scope='other')
    args = dict(scope='a', expected_revision=1, claim='Source claim', suggested_node_type='Fact',
                payload={'confidence': 0.4}, reason='Repair type', actor='local-owner')
    with pytest.raises(ValueError):
        manager.repair(identity, **{**args, 'payload': {'confidence': 'still broken'}})
    def fail(*args, **kwargs):
        raise RuntimeError('audit failed')
    monkeypatch.setattr('backend.maintenance.logsheet.MemoryLogsheet.append_entry', fail)
    with pytest.raises(RuntimeError, match='audit failed'):
        manager.repair(identity, **args)
    assert manager.get(identity, scope='a').revision == 1
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM quarantine').fetchone()[0] == 1


def test_legacy_quarantine_is_preserved_without_guessing_scope_or_source_revision(tmp_path):
    import sqlite3
    path = tmp_path/'old.db'
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE quarantine(id TEXT PRIMARY KEY, conflict_type TEXT NOT NULL,
            reason TEXT NOT NULL, source_id TEXT, payload_json TEXT NOT NULL, resolved INTEGER NOT NULL,
            resolution_notes TEXT, quarantined_at TEXT NOT NULL)''')
        conn.execute('INSERT INTO quarantine VALUES(?,?,?,?,?,?,?,?)',
            ('old', 'SCHEMA_CONFLICT', 'bad', 'unknown', json.dumps({'scope': 'untrusted'}), 0, None, '2026-10-01T00:00:00+00:00'))
    storage = CanonicalStorage(str(path))
    record = storage.list_quarantine()[0]
    assert record.scope is None and record.source_revision is None
    assert record.payload == {'scope': 'untrusted'} and record.submission is None


def test_repair_requires_intact_source_and_agent_cannot_repair_explicit_authority(tmp_path):
    from backend.auth.policy import AccessPolicy
    from backend.quarantine.quarantine_manager import QuarantineManager
    from backend.schema.models import MemoryAuthority
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    manager = QuarantineManager(storage)
    args = dict(scope='a', expected_revision=1, claim='Source claim', suggested_node_type='Fact',
                payload={}, reason='Repair numeric field', actor='owner')
    _, explicit_id, _ = bridge.submit_candidate(**{**submission, 'authority': MemoryAuthority.EXPLICIT})
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a', write=True):
        with pytest.raises(PermissionError):
            manager.repair(explicit_id, **args)
    with storage.session() as conn:
        conn.execute('UPDATE notes SET content = ? WHERE id = ?', ('tampered', source.id))
    with pytest.raises(ValueError, match='integrity'):
        manager.repair(identity, **args)
    assert not manager.get(identity, scope='a').resolved


def test_concurrent_repairs_create_one_candidate_and_one_audit_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from backend.quarantine.quarantine_manager import QuarantineManager
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    manager = QuarantineManager(storage)
    def attempt(confidence):
        try:
            return manager.repair(identity, scope='a', expected_revision=1, claim='Source claim',
                suggested_node_type='Fact', payload={'confidence': confidence}, reason='Fix type', actor='owner')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0.4, 0.5]))
    assert sum(result is not None for result in results) == 1
    with storage.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM system_memory_logsheet').fetchone()[0] == 1


def test_rest_repair_exposes_preserved_record_and_returns_candidate_for_review(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    with TestClient(create_app(str(tmp_path/'db'), str(tmp_path/'raw'), str(tmp_path/'artifacts'))) as client:
        url = '/api/quarantine/' + identity
        assert client.get(url, params={'scope': 'other'}).status_code == 404
        assert client.get('/api/quarantine', params={'scope': 'a'}).json()['items'][0]['id'] == identity
        record = client.get(url, params={'scope': 'a'}).json()
        assert record['submission']['payload']['confidence'] == 'broken'
        body = {'scope': 'a', 'expected_revision': 1, 'claim': 'Source claim',
                'suggested_node_type': 'Fact', 'payload': {}, 'reason': 'Remove malformed optional confidence'}
        assert client.post(url + '/repair', json={**body, 'source_id': 'other'}).status_code == 422
        repaired = client.post(url + '/repair', json=body)
        assert repaired.status_code == 200, repaired.text
        assert bridge.get_candidate(repaired.json()['result_candidate_id']).status.value == 'VALIDATED'
        assert client.post(url + '/repair', json=body).status_code == 409


def test_legacy_listing_is_owner_only_and_does_not_adopt_untrusted_scope(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    from backend.auth.policy import AccessPolicy
    from backend.quarantine.quarantine_manager import QuarantineManager
    from backend.schema.models import QuarantineRecord
    storage = CanonicalStorage(str(tmp_path/'db'))
    storage.save_quarantine(QuarantineRecord(id='legacy', payload={'scope': 'a'}, reason='Missing metadata'))
    with TestClient(create_app(str(tmp_path/'db'), str(tmp_path/'raw'), str(tmp_path/'artifacts'))) as client:
        response = client.get('/api/quarantine-legacy')
        assert response.status_code == 200
        assert response.json()['items'][0]['id'] == 'legacy'
        assert client.get('/api/quarantine', params={'scope': 'a'}).json()['items'] == []
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a'):
        with pytest.raises(PermissionError):
            QuarantineManager(storage).list_legacy()


def test_repaired_claim_cannot_be_overridden_by_stale_payload_content(tmp_path):
    from backend.quarantine.quarantine_manager import QuarantineManager
    storage, notes, bridge, source, submission, identity = setup(tmp_path)
    repaired = QuarantineManager(storage).repair(identity, scope='a', expected_revision=1,
        claim='Corrected assertion', suggested_node_type='Fact', payload={'content': 'Old incorrect assertion'},
        reason='Correct assertion after source review', actor='owner')
    candidate = bridge.get_candidate(repaired.result_candidate_id)
    assert candidate.claim == candidate.payload['content'] == 'Corrected assertion'
