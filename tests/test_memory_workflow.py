"""Regression coverage for the actual Notes -> Candidate -> Memory workflow."""
import pytest
from fastapi.testclient import TestClient
from backend.adapters.api.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / 'memory.db'), str(tmp_path / 'raw'), str(tmp_path / 'art')))


def candidate(client, authority='INFERRED'):
    note = client.post('/api/notes', json={'title': '项目记忆', 'content': '本项目使用本地记忆库。'}).json()
    response = client.post('/api/candidates', json={
        'source_id': note['id'], 'source_revision': 1,
        'claim': note['content'], 'suggested_node_type': 'Concept',
        'payload': {'title': note['title']}, 'authority': authority,
    })
    assert response.json()['success']
    return note, response.json()['id']


def test_revision_conflict_preserves_both_original_and_current(client):
    note, _ = candidate(client)
    url = f"/api/notes/{note['id']}/revisions"
    body = {'title': '新标题', 'content': '第二版', 'expected_revision': 1}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json={**body, 'content': '过期草稿'}).status_code == 409
    assert client.get(f"/api/notes/{note['id']}").json()['content'] == '第二版'
    assert client.get(f"/api/notes/{note['id']}?revision=1").json()['content'] == note['content']


def test_revision_requires_precondition(client):
    note, _ = candidate(client)
    assert client.post(f"/api/notes/{note['id']}/revisions", json={'title': 'x', 'content': 'y'}).status_code == 422


def test_accept_is_idempotent_with_real_source_edge_and_audit(client):
    note, cid = candidate(client)
    first = client.post(f'/api/candidates/{cid}/accept').json()['promoted_node']
    again = client.post(f'/api/candidates/{cid}/accept').json()['promoted_node']
    assert again['id'] == first['id']
    assert first['provenance']['content_hash'] == note['content_hash']
    edges = client.get(f"/api/memory/nodes/{first['id']}/edges").json()
    assert len(edges) == 1 and edges[0]['relation'] == 'DERIVED_FROM'
    source = client.get(f"/api/memory/nodes/{edges[0]['target_id']}").json()
    assert source['node_type'] == 'Source'
    assert source['provenance']['source_id'] == note['id']
    logs = client.get('/api/maintenance/logsheet').json()
    assert len(logs) == 1 and logs[0]['event_type'] == 'MEMORY_CHANGED'
    assert client.get(f"/api/notes/{note['id']}").json() == note


def test_accept_does_not_mean_verified(client):
    _, cid = candidate(client, authority='EXPLICIT')
    node = client.post(f'/api/candidates/{cid}/accept').json()['promoted_node']
    assert node['maturity'] == 'L2'


def test_rejected_candidate_cannot_be_accepted(client):
    _, cid = candidate(client)
    assert client.post(f'/api/candidates/{cid}/reject?reason=Insufficient+evidence').status_code == 200
    assert client.post(f'/api/candidates/{cid}/accept').status_code == 409
    logs = client.get('/api/maintenance/logsheet').json()
    assert logs[0]['reason'] == 'Insufficient evidence'


def test_accepted_candidate_cannot_be_rejected(client):
    _, cid = candidate(client)
    client.post(f'/api/candidates/{cid}/accept')
    assert client.post(f'/api/candidates/{cid}/reject').status_code == 409


@pytest.mark.parametrize('change', [{'source_revision': 99}, {'scope': 'other'}, {'source_id': 'missing'}])
def test_missing_or_cross_scope_provenance_cannot_enter_memory(client, change):
    note, _ = candidate(client)
    response = client.post('/api/candidates', json={
        'source_id': note['id'], 'claim': 'Unverified claim', 'payload': {}, **change,
    })
    if response.status_code == 200 and response.json()['success']:
        accepted = client.post(f"/api/candidates/{response.json()['id']}/accept")
        assert accepted.status_code in (404, 409, 422)
    else:
        assert response.status_code in (404, 409, 422) or response.json()['success'] is False
    assert client.get('/api/memory/nodes').json() == []


def test_missing_rejection_returns_not_found(client):
    assert client.post('/api/candidates/missing/reject').status_code == 404


def test_simultaneous_accepts_create_one_memory(client):
    from concurrent.futures import ThreadPoolExecutor
    _, cid = candidate(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post(f'/api/candidates/{cid}/accept'), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()['promoted_node']['id'] for r in responses}) == 1
    assert len(client.get('/api/maintenance/logsheet').json()) == 1


def test_log_failure_rolls_back_entire_promotion(client, monkeypatch):
    from backend.maintenance.logsheet import MemoryLogsheet
    _, cid = candidate(client)
    def fail(*args, **kwargs):
        raise RuntimeError('Injected audit failure')
    monkeypatch.setattr(MemoryLogsheet, 'append_entry', fail)
    with pytest.raises(RuntimeError, match='Injected'):
        client.post(f'/api/candidates/{cid}/accept')
    assert client.get('/api/memory/nodes').json() == []
    assert client.get('/api/memory/edges').json() == []
    assert client.get('/api/candidates').json()[0]['status'] == 'VALIDATED'


def test_duplicate_intake_returns_same_candidate(client):
    note, _ = candidate(client)
    body = {'source_id': note['id'], 'claim': 'Review this', 'payload': {'title': 'Review'}}
    first = client.post('/api/candidates', json=body).json()
    again = client.post('/api/candidates', json=body).json()
    assert first['id'] == again['id']


def test_raw_manifest_commits_with_note_and_survives_reopen(tmp_path):
    import hashlib
    from pathlib import Path
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.storage.raw.raw_store import RawSourceStore
    from backend.notes.notes_manager import NotesManager, RevisionConflict
    storage = CanonicalStorage(str(tmp_path / 'db'))
    manager = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    note = manager.create_note('原文', '  保留原文空白。\n')
    reopened = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    reopened.revise_note(note.id, '修订', '新内容', expected_revision=1)
    with pytest.raises(RevisionConflict):
        manager.revise_note(note.id, '旧窗口', '冲突内容', expected_revision=1)
    with storage.session() as conn:
        manifests = conn.execute('SELECT * FROM raw_manifests ORDER BY revision').fetchall()
    assert len(manifests) == 2
    assert Path(manifests[0]['file_path']).read_text(encoding='utf-8') == note.content
    for manifest in manifests:
        assert hashlib.sha256(Path(manifest['file_path']).read_bytes()).hexdigest() == manifest['content_hash']
    assert manager.raw_store.get_source(note.id)['content'] == '新内容'
    assert manager.raw_store.get_source(note.id, revision=1)['content'] == note.content


def test_backlog_changes_produce_atomic_audit_records(client):
    item = client.post('/api/maintenance/backlog', json={
        'type': 'VERIFY_CLAIM', 'targets': [{'id': 'mem-review'}], 'reason': 'Verify source evidence',
    }).json()
    created_logs = client.get(f"/api/maintenance/logsheet?backlog_id={item['id']}").json()
    assert len(created_logs) == 1 and created_logs[0]['event_type'] == 'ITEM_CREATED'
    resolved = client.post(f"/api/maintenance/backlog/{item['id']}/transition", json={
        'status': 'RESOLVED', 'reason': 'Checked against source', 'resolution': {'note': 'Source agrees'},
        'expected_revision': item['revision'],
    })
    assert resolved.status_code == 200
    logs = client.get(f"/api/maintenance/logsheet?backlog_id={item['id']}").json()
    assert len(logs) == 2 and logs[0]['event_type'] == 'ITEM_RESOLVED'


def test_backlog_stale_transition_rejected(client):
    item = client.post('/api/maintenance/backlog', json={'type': 'VERIFY_CLAIM', 'targets': [], 'reason': 'Review'}).json()
    url = f"/api/maintenance/backlog/{item['id']}/transition"
    assert client.post(url, json={'status': 'BLOCKED', 'blocker': 'Missing source', 'expected_revision': 1}).status_code == 200
    assert client.post(url, json={'status': 'READY', 'expected_revision': 1}).status_code == 409


def test_candidate_claiming_wrong_source_hash_cannot_be_accepted(client):
    note, _ = candidate(client)
    response = client.post('/api/candidates', json={
        'source_id': note['id'], 'claim': 'A claim attributed to the wrong source',
        'payload': {'content_hash': 'wrong-source-hash'},
    }).json()
    assert client.post(f"/api/candidates/{response['id']}/accept").status_code == 409
    assert client.get('/api/memory/nodes').json() == []
