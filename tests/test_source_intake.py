"""Source intake invariants against isolated storage and controlled network fixtures."""
import hashlib
import importlib.util
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.policy import AccessPolicy
from backend.collections.manager import CollectionManager
from backend.notes.notes_manager import NotesManager
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def test_source_intake_module_exists():
    assert importlib.util.find_spec('backend.intake') is not None, 'Unified source intake is missing'


@pytest.fixture
def system(tmp_path):
    from backend.intake.manager import IntakeManager
    storage = CanonicalStorage(str(tmp_path / 'canonical.db'))
    raw = RawSourceStore(str(tmp_path / 'raw'))
    notes = NotesManager(storage, raw)
    collections = CollectionManager(storage)
    manager = IntakeManager(storage, notes, raw)
    return manager, storage, notes, collections


def prepare(manager, content='first paragraph\n\nsecond paragraph', **kwargs):
    return manager.prepare('manual_note', content=content, title='Example', **kwargs)


def confirm(manager, preview, key='confirm-1', **kwargs):
    return manager.confirm(preview['preview_id'], expected_revision=preview['preview_revision'],
                           expected_hash=preview['preview_hash'], idempotency_key=key,
                           selection={'start': 0, 'end': len(preview['content'])}, **kwargs)


def count(storage, table):
    with storage.session() as conn:
        return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_preview_does_not_save_and_cancel_erases_temporary_content(system):
    manager, storage, _, _ = system
    preview = prepare(manager)
    assert preview['state'] == 'PREVIEW_READY'
    assert count(storage, 'notes') == count(storage, 'raw_manifests') == 0
    cancelled = manager.cancel(preview['preview_id'])
    assert cancelled['state'] == 'CANCELLED'
    assert cancelled['content'] is None
    with pytest.raises(ValueError):
        confirm(manager, preview)
    assert count(storage, 'notes') == 0


def test_confirmation_is_atomic_idempotent_and_collection_reference_only(system):
    manager, storage, notes, collections = system
    topic = collections.create('Research')
    preview = prepare(manager)
    saved = confirm(manager, preview, collections=[{'id': topic['id'], 'expected_revision': topic['revision']}])
    assert saved['state'] == 'SAVED'
    assert notes.get_note(saved['note_id']).content == preview['content']
    assert confirm(manager, preview, collections=[{'id': topic['id'], 'expected_revision': topic['revision']}]) == saved
    assert count(storage, 'notes') == count(storage, 'collection_members') == 1
    with storage.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transactional_outbox WHERE aggregate_type IN ('NOTE','INTAKE')").fetchone()[0] == 2
    assert manager.cancel(preview['preview_id'])['note_id'] == saved['note_id']
    assert count(storage, 'notes') == 1


@pytest.mark.parametrize('mutation', ['hash', 'revision', 'selection'])
def test_confirmation_rejects_wrong_preview_version_and_range(system, mutation):
    manager, storage, _, _ = system
    preview = prepare(manager)
    values = dict(expected_revision=preview['preview_revision'], expected_hash=preview['preview_hash'],
                  idempotency_key='bad', selection={'start': 0, 'end': len(preview['content'])})
    if mutation == 'hash':
        values['expected_hash'] = '0' * 64
    elif mutation == 'revision':
        values['expected_revision'] += 1
    else:
        values['selection']['end'] += 1
    with pytest.raises(ValueError):
        manager.confirm(preview['preview_id'], **values)
    assert count(storage, 'notes') == 0


def test_expiry_erases_content_and_requires_repreview(system):
    manager, storage, _, _ = system
    preview = prepare(manager)
    with storage.session() as conn:
        conn.execute('UPDATE intake_jobs SET expires_at = 0 WHERE id = ?', (preview['preview_id'],))
    assert manager.get(preview['preview_id'])['state'] == 'EXPIRED'
    assert manager.get(preview['preview_id'])['content'] is None
    with pytest.raises(ValueError):
        confirm(manager, preview)


def test_failed_collection_check_rolls_back_all_saved_objects(system):
    manager, storage, _, collections = system
    other = collections.create('Other', scope='private')
    before_events = count(storage, 'transactional_outbox')
    preview = prepare(manager)
    with pytest.raises(ValueError):
        confirm(manager, preview, collections=[{'id': other['id'], 'expected_revision': 1}])
    assert count(storage, 'notes') == count(storage, 'raw_manifests') == count(storage, 'intake_sources') == 0
    assert count(storage, 'transactional_outbox') == before_events
    assert manager.get(preview['preview_id'])['state'] == 'PREVIEW_READY'


def test_same_file_deduplicates_and_changed_content_preserves_evidence_revisions(system):
    manager, storage, notes, _ = system
    a = manager.prepare('file_import', filename='source.txt', content='original')
    first = confirm(manager, a)
    b = manager.prepare('file_import', filename='source.txt', content='original')
    duplicate = confirm(manager, b, 'confirm-2')
    assert duplicate['note_id'] == first['note_id']
    assert duplicate['revision'] == 1
    changed = manager.prepare('file_import', filename='source.txt', content='new evidence')
    second = confirm(manager, changed, 'confirm-3')
    assert second['note_id'] == first['note_id']
    assert second['revision'] == second['source_revision'] == 2
    assert second['needs_review'] is True
    assert notes.get_note(first['note_id'], 1).content == 'original'
    assert notes.get_note(first['note_id'], 2).content == 'new evidence'
    with storage.session() as conn:
        events = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM transactional_outbox WHERE event_type='SOURCE_REVISED'")]
    assert events[0]['source_revision'] == 2
    assert count(storage, 'system_memory_backlog') == 0


def test_different_owner_and_scope_do_not_leak_preview_or_dedup(system):
    manager, storage, _, _ = system
    preview = manager.prepare('file_import', filename='a.txt', content='secret', owner='alice', scope='private')
    with pytest.raises(LookupError):
        manager.get(preview['preview_id'], owner='bob', scope='private')
    with pytest.raises(LookupError):
        manager.get(preview['preview_id'], owner='alice', scope='default')
    first = confirm(manager, preview, owner='alice', scope='private')
    another = manager.prepare('file_import', filename='a.txt', content='secret', owner='bob', scope='private')
    second = confirm(manager, another, 'second', owner='bob', scope='private')
    assert first['note_id'] != second['note_id']


def test_authenticated_access_cannot_spoof_owner_scope_or_write_after_revocation(system):
    manager, storage, _, _ = system
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent:a', 'default')
    with policy.activate('agent:a', 'default', write=True):
        preview = prepare(manager)
        assert preview['owner'] == 'agent:a'
        with pytest.raises(PermissionError):
            prepare(manager, owner='local-owner')
        with pytest.raises(PermissionError):
            prepare(manager, scope='private')
    grant = policy.get_grant('agent:a', 'default')
    policy.update_grant('agent:a', 'default', read=True, write=False, expected_revision=grant['revision'], actor='owner')
    with policy.activate('agent:a', 'default'):
        with pytest.raises(PermissionError):
            confirm(manager, preview)
    assert count(storage, 'notes') == 0


def test_external_html_is_text_and_raw_user_comments_are_separate(system):
    manager, storage, notes, _ = system
    html = '<html><head><title>Article</title><script>bad()</script></head><body><p>Safe text</p><script>steal secrets</script></body></html>'
    preview = manager.prepare('file_import', filename='article.html', content=html)
    assert 'steal secrets' not in preview['content'] and 'bad()' not in preview['content']
    saved = confirm(manager, preview, user_comment='My opinion')
    item = manager.get_item(saved['note_id'])
    assert item['source_type'] == 'file_import'
    assert item['user_comments'][0]['content'] == 'My opinion'
    assert notes.get_note(saved['note_id']).content == 'Safe text'
    assert item['published_at'] is None
    assert manager.raw_store.get_source(saved['source_id'], 1)['content'] == html


def test_snippet_has_coverage_and_positions_and_key_cannot_change_payload(system):
    manager, storage, _, _ = system
    preview = prepare(manager, content='0123456789')
    saved = manager.confirm(preview['preview_id'], expected_revision=1, expected_hash=preview['preview_hash'],
                            idempotency_key='snippet', selection={'start': 2, 'end': 5})
    item = manager.get_item(saved['note_id'])
    assert item['selected_content'] == '234'
    assert item['coverage'] == 'selected_excerpt'
    assert item['selection'] == {'start': 2, 'end': 5}
    with pytest.raises(ValueError):
        confirm(manager, preview, 'snippet')


def test_supported_formats_and_retry_failed_parse_are_honest(system):
    manager, storage, _, _ = system
    support = manager.supported()
    assert support['file_extensions'] == ['.txt', '.md', '.html', '.htm']
    failed = manager.prepare('file_import', filename='a.pdf', content='%PDF-file')
    assert failed['state'] == 'FAILED' and failed['error_code'] == 'UNSUPPORTED_FORMAT'
    with pytest.raises(ValueError):
        manager.retry(failed['preview_id'])
    assert count(storage, 'notes') == 0


def test_index_readiness_is_derived_from_actual_projection(system):
    from backend.core.retrieval.keyword_index import KeywordIndex
    manager, storage, _, _ = system
    index = KeywordIndex(storage)
    preview = prepare(manager)
    saved = confirm(manager, preview)
    assert saved['state'] == 'SAVED'
    assert manager.get(preview['preview_id'])['state'] == 'INDEXING'
    index.rebuild()
    assert manager.get(preview['preview_id'])['state'] == 'SEARCH_READY'


def test_router_prepare_confirm_status_contract(system):
    from backend.intake.api import create_intake_router
    manager, storage, notes, _ = system
    app = FastAPI()
    app.include_router(create_intake_router(storage, notes))
    with TestClient(app) as client:
        supported = client.get('/api/intake/support').json()
        assert 'web_capture' in supported['source_types']
        response = client.post('/api/intake/prepare', json={'source_type': 'manual_note', 'content': 'User note', 'title': 'Title'})
        assert response.status_code == 201
        preview = response.json()
        result = client.post(f"/api/intake/{preview['preview_id']}/confirm", json={
            'expected_revision': 1, 'expected_hash': preview['preview_hash'], 'idempotency_key': 'api-one',
            'selection': {'start': 0, 'end': len(preview['content'])}})
        assert result.status_code == 200
        assert result.json()['state'] == 'SAVED'
        assert client.get(f"/api/intake/items/{result.json()['note_id']}").json()['source_type'] == 'manual_note'


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1/', 'http://[::1]/',
                               'http://169.254.169.254/', 'http://user:pass@example.com/',
                               'http://2130706433/', 'http://example.com:22/', 'http://localhost/'])
def test_fetch_rejects_unsafe_urls_before_transport(url):
    from backend.intake.fetch import SafeWebFetcher
    calls = []
    fetcher = SafeWebFetcher(resolver=lambda host, port: ['127.0.0.1'], transport=lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        fetcher.fetch(url)
    assert calls == []


def test_fetch_dns_pin_redirect_validation_and_bounded_response():
    from backend.intake.fetch import SafeWebFetcher, FetchResponse
    calls = []
    def transport(url, address, timeout, limit):
        calls.append((url, address))
        if 'start' in url:
            return FetchResponse(302, {'location': 'http://private.example/secret'}, b'')
        return FetchResponse(200, {'content-type': 'text/html'}, b'<p>Hello</p>')
    def resolver(host, port):
        return ['10.0.0.1'] if host == 'private.example' else ['93.184.216.34']
    fetcher = SafeWebFetcher(resolver=resolver, transport=transport)
    with pytest.raises(ValueError):
        fetcher.fetch('https://public.example/start', check_robots=False)
    assert calls == [('https://public.example/start', '93.184.216.34')]
    response = fetcher.fetch('https://public.example/article', check_robots=False)
    assert response['body'] == b'<p>Hello</p>'
    assert response['published_at'] is None
    assert calls[-1][1] == '93.184.216.34'


def test_fetch_rejects_mixed_dns_and_oversize_decompression():
    from backend.intake.fetch import SafeWebFetcher, FetchResponse
    import gzip
    with pytest.raises(ValueError):
        SafeWebFetcher(resolver=lambda *args: ['93.184.216.34', '::1']).fetch('https://public.example')
    huge = gzip.compress(b'a' * 2000)
    fetcher = SafeWebFetcher(max_bytes=1000, resolver=lambda *args: ['93.184.216.34'],
                             transport=lambda *args: FetchResponse(200, {'content-type': 'text/plain', 'content-encoding': 'gzip'}, huge))
    with pytest.raises(ValueError):
        fetcher.fetch('https://public.example', check_robots=False)


def test_web_capture_keeps_fetch_provenance_and_respects_access_rules(system):
    from backend.intake.fetch import SafeWebFetcher, FetchResponse
    manager, storage, _, _ = system
    def transport(url, *args):
        if url.endswith('/robots.txt'):
            return FetchResponse(200, {'content-type': 'text/plain'}, b'User-agent: *\nDisallow: /secret\n')
        return FetchResponse(200, {'content-type': 'text/html'}, b'<title>Web</title><p>Imported article</p>')
    manager.fetcher = SafeWebFetcher(resolver=lambda *args: ['93.184.216.34'], transport=transport)
    denied = manager.prepare('web_capture', url='https://public.example/secret')
    assert denied['state'] == 'FAILED'
    preview = manager.prepare('web_capture', url='https://public.example/article')
    assert preview['state'] == 'PREVIEW_READY'
    saved = confirm(manager, preview)
    item = manager.get_item(saved['note_id'])
    assert item['provenance']['requested_url'] == item['provenance']['final_url'] == 'https://public.example/article'
    assert item['provenance']['http_status'] == 200
    assert item['published_at'] is None
