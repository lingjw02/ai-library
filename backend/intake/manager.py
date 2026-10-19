"""Durable preview/confirmation boundary over Notes, raw manifests and outbox."""
import hashlib
import json
import os
from pathlib import PurePath
import sqlite3
import time
import uuid

from backend.auth.policy import current_access
from backend.intake.fetch import SafeWebFetcher, normalize_url
from backend.intake.parsing import extract, FILE_EXTENSIONS, ParseError
from backend.schema.models import utc_now


class IntakeConflict(ValueError):
    pass


SCHEMA = '''
CREATE TABLE IF NOT EXISTS intake_jobs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL,
    source_type TEXT NOT NULL, source_identity TEXT NOT NULL,
    state TEXT NOT NULL, preview_revision INTEGER NOT NULL,
    preview_hash TEXT, raw_body BLOB, extracted_content TEXT,
    metadata_json TEXT NOT NULL, expires_at REAL NOT NULL,
    result_json TEXT, confirmed_request_hash TEXT, error_code TEXT, error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS intake_job_owner ON intake_jobs(owner, scope, created_at);
CREATE TABLE IF NOT EXISTS intake_sources (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL,
    source_identity TEXT NOT NULL, source_type TEXT NOT NULL, note_id TEXT NOT NULL,
    current_source_revision INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(owner, scope, source_identity));
CREATE TABLE IF NOT EXISTS intake_source_revisions (
    source_id TEXT NOT NULL REFERENCES intake_sources(id), revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL, provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(source_id, revision));
CREATE TABLE IF NOT EXISTS intake_items (
    note_id TEXT NOT NULL, note_revision INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES intake_sources(id), source_revision INTEGER NOT NULL,
    selection_json TEXT NOT NULL, coverage TEXT NOT NULL, needs_review INTEGER NOT NULL,
    PRIMARY KEY(note_id, note_revision),
    FOREIGN KEY(note_id, note_revision) REFERENCES notes(id, revision));
CREATE TABLE IF NOT EXISTS intake_comments (
    id TEXT PRIMARY KEY, note_id TEXT NOT NULL, note_revision INTEGER NOT NULL,
    owner TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(note_id, note_revision) REFERENCES notes(id, revision));
CREATE TABLE IF NOT EXISTS intake_confirmations (
    owner TEXT NOT NULL, scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL, result_json TEXT NOT NULL,
    PRIMARY KEY(owner, scope, idempotency_key));
CREATE TABLE IF NOT EXISTS intake_memory_review_flags (
    source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
    memory_id TEXT NOT NULL, memory_version INTEGER NOT NULL,
    reason TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(source_id, source_revision, memory_id, memory_version));
'''


def _hash(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf-8')).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class IntakeManager:
    MAX_BYTES = 2 * 1024 * 1024
    MAX_TEMP_BYTES = 64 * 1024 * 1024
    PREVIEW_TTL = 30 * 60
    MAX_ACTIVE_OWNER = 20
    MAX_RETRIES = 3

    def __init__(self, storage, notes_mgr, raw_store=None, *, fetcher=None):
        self.storage = storage
        self.notes_mgr = notes_mgr
        self.raw_store = raw_store or notes_mgr.raw_store
        self.raw_store.canonical_storage = storage
        self.fetcher = fetcher or SafeWebFetcher(max_bytes=self.MAX_BYTES)
        # Owner startup only. Agent operations never perform schema mutations.
        if current_access.get() is not None:
            raise PermissionError('Intake manager must be initialized by the local owner at startup')
        with storage.session() as conn:
            conn.executescript(SCHEMA)
        self.cleanup_expired()

    @staticmethod
    def _identity(owner, scope):
        context = current_access.get()
        owner = owner or (context.principal if context else 'local-owner')
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            raise ValueError('Owner is required and must be at most 200 characters')
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 200:
            raise ValueError('Scope is required and must be at most 200 characters')
        if context and (scope != context.scope or owner != context.principal):
            raise PermissionError('Intake owner and scope must match the active access grant')
        return owner, scope

    @staticmethod
    def supported():
        return {'source_types': ['manual_note', 'web_capture', 'file_import'],
                'file_extensions': FILE_EXTENSIONS,
                'web_schemes': ['http', 'https'], 'web_ports': [80, 443],
                'max_source_bytes': IntakeManager.MAX_BYTES, 'preview_ttl_seconds': IntakeManager.PREVIEW_TTL,
                'availability': {'conversation_export': 'Use the existing conversation adapter; unavailable in source intake.',
                                 'skill_source': 'Use capability registration; never installs or executes a skill.',
                                 'mcp_definition': 'Use capability registration; never connects automatically.'},
                'deferred': ['authenticated_pages', 'batch_crawling', 'scheduled_monitoring', 'pdf', 'office', 'archives'],
                'retention_policy': 'Bounded original bytes plus selected text after explicit confirmation; temporary previews expire.',
                'indexing': 'Keyword projection readiness is reported separately from durable saving.',
                'trust': 'External source text is unverified evidence; no automatic Candidate Bridge promotion.'}

    def _row(self, conn, job_id, owner, scope):
        row = conn.execute('SELECT * FROM intake_jobs WHERE id=? AND owner=? AND scope=?', (job_id, owner, scope)).fetchone()
        if not row:
            raise LookupError('Intake preview not found in this owner and scope')
        row = dict(row)
        if row['state'] in ('QUEUED', 'FETCHING', 'PARSING', 'PREVIEW_READY', 'FAILED') and row['expires_at'] <= time.time():
            row.update(state='EXPIRED', raw_body=None, extracted_content=None, preview_hash=None)
            context = current_access.get()
            if context is None or context.write:
                conn.execute("UPDATE intake_jobs SET state='EXPIRED', raw_body=NULL, extracted_content=NULL, preview_hash=NULL WHERE id=?", (job_id,))
        return row

    def _view(self, row):
        metadata = json.loads(row['metadata_json'])
        result = json.loads(row['result_json']) if row.get('result_json') else {}
        return {'preview_id': row['id'], 'job_id': row['id'], 'owner': row['owner'], 'scope': row['scope'],
                'source_type': row['source_type'], 'source_identity': row['source_identity'],
                'state': row['state'], 'preview_revision': row['preview_revision'], 'preview_hash': row['preview_hash'],
                'content': row['extracted_content'], 'expires_at': row['expires_at'],
                'error_code': row['error_code'], 'error_message': row['error_message'],
                'attempts': row['attempts'], 'metadata': metadata, **result}

    def cleanup_expired(self):
        if current_access.get() is not None:
            raise PermissionError('Global temporary cleanup is an owner operation')
        with self.storage.session() as conn:
            return conn.execute("UPDATE intake_jobs SET state='EXPIRED', raw_body=NULL, extracted_content=NULL, preview_hash=NULL WHERE expires_at<=? AND state IN ('QUEUED','FETCHING','PARSING','PREVIEW_READY','FAILED')", (time.time(),)).rowcount

    def prepare(self, source_type, *, content=None, filename=None, url=None, title='', owner=None, scope='default', source_key=None):
        owner, scope = self._identity(owner, scope)
        if source_type not in ('manual_note', 'file_import', 'web_capture'):
            raise ValueError('Unsupported source type; query /api/intake/support for available adapters')
        if not isinstance(title, str) or len(title) > 500:
            raise ValueError('Title must be at most 500 characters')
        if source_key is not None and (not isinstance(source_key, str) or not source_key.strip() or len(source_key) > 500):
            raise ValueError('Optional source key must be nonempty and at most 500 characters')
        if source_type == 'web_capture':
            # Validate before creating a job, and validate again at fetch time.
            identity = 'web:' + normalize_url(url)
            if content is not None or filename is not None:
                raise ValueError('Web capture accepts a URL without file content')
        else:
            if url is not None:
                raise ValueError('Text/file capture does not accept a URL')
            if not isinstance(content, (str, bytes)):
                raise ValueError('Text or uploaded file bytes are required')
            raw = content.encode('utf-8') if isinstance(content, str) else content
            if len(raw) > self.MAX_BYTES:
                raise ValueError('Source exceeds the byte limit')
            if source_type == 'file_import':
                if not isinstance(filename, str) or not filename or len(filename) > 255 or any(c in filename for c in ('/', '\\', '\x00')) or filename in ('.', '..'):
                    raise ValueError('Original filename must be a plain filename without path components')
                if any(ord(c) < 32 for c in filename):
                    raise ValueError('Filename contains control characters')
                identity = 'file:' + (source_key or filename)
            else:
                identity = 'manual:' + (source_key or uuid.uuid4().hex)
        job_id, now = 'intake-' + uuid.uuid4().hex, utc_now().isoformat()
        metadata = {'title': title, 'filename': filename, 'url': url, 'source_key': source_key,
                    'retention_policy': 'bounded_original_response_and_selected_text', 'published_at': None,
                    'trust': 'user_authored' if source_type == 'manual_note' else 'untrusted_external_source'}
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            active = conn.execute("SELECT COUNT(*) FROM intake_jobs WHERE owner=? AND scope=? AND expires_at>? AND state IN ('QUEUED','FETCHING','PARSING','PREVIEW_READY')", (owner, scope, time.time())).fetchone()[0]
            if active >= self.MAX_ACTIVE_OWNER:
                raise ValueError('Too many active previews; confirm or cancel an existing preview')
            conn.execute('INSERT INTO intake_jobs(id,owner,scope,source_type,source_identity,state,preview_revision,metadata_json,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?,?,?)',
                         (job_id, owner, scope, source_type, identity, 'QUEUED', _json(metadata), time.time() + self.PREVIEW_TTL, now, now))
            conn.execute('COMMIT')
        return self._capture(job_id, owner, scope, raw if source_type != 'web_capture' else None)

    def _capture(self, job_id, owner, scope, raw=None):
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._row(conn, job_id, owner, scope)
            if row['state'] != 'QUEUED':
                conn.execute('COMMIT')
                return self._view(row)
            metadata = json.loads(row['metadata_json'])
            conn.execute('UPDATE intake_jobs SET state=?, updated_at=? WHERE id=?',
                         ('FETCHING' if row['source_type'] == 'web_capture' else 'PARSING', utc_now().isoformat(), job_id))
            conn.execute('COMMIT')
        try:
            if row['source_type'] == 'web_capture':
                fetched = self.fetcher.fetch(metadata['url'])
                raw = fetched.pop('body')
                metadata.update(fetched)
                metadata['captured_at'] = utc_now().isoformat()
                mime = fetched['mime_type']
                charset = fetched.get('charset', 'utf-8')
            elif row['source_type'] == 'file_import':
                suffix = PurePath(metadata['filename']).suffix.lower()
                if suffix not in FILE_EXTENSIONS:
                    raise ParseError('Supported file formats are .txt, .md and .html/.htm', 'UNSUPPORTED_FORMAT')
                mime = 'text/html' if suffix in ('.html', '.htm') else ('text/markdown' if suffix == '.md' else 'text/plain')
                charset = 'utf-8'
                metadata['detected_format'] = suffix
                metadata['captured_at'] = utc_now().isoformat()
            else:
                mime, charset = 'text/markdown', 'utf-8'
            parsed = extract(raw, mime, charset, self.MAX_BYTES)
            text = parsed.pop('content')
            metadata.update(parsed)
            metadata.update(mime_type=mime, source_content_hash=_hash(raw), extracted_content_hash=_hash(text))
            if not metadata['title']:
                metadata['title'] = metadata['document_title'] or metadata['filename'] or 'Untitled note'
            preview_hash = _hash(_json({'metadata': metadata, 'raw_hash': _hash(raw), 'text_hash': _hash(text), 'revision': row['preview_revision']}))
            with self.storage.session() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = self._row(conn, job_id, owner, scope)
                if current['state'] not in ('FETCHING', 'PARSING') or current['preview_revision'] != row['preview_revision']:
                    conn.execute('COMMIT')
                    return self._view(current)
                occupied = conn.execute('SELECT COALESCE(SUM(LENGTH(raw_body)+LENGTH(extracted_content)),0) FROM intake_jobs').fetchone()[0]
                if occupied + len(raw) + len(text.encode('utf-8')) > self.MAX_TEMP_BYTES:
                    raise ParseError('Temporary capture storage limit reached', 'RESOURCE_LIMIT')
                conn.execute("UPDATE intake_jobs SET state='PREVIEW_READY',raw_body=?,extracted_content=?,metadata_json=?,preview_hash=?,error_code=NULL,error_message=NULL,updated_at=? WHERE id=?", (raw, text, _json(metadata), preview_hash, utc_now().isoformat(), job_id))
                conn.execute('COMMIT')
        except Exception as exc:
            if isinstance(exc, PermissionError):
                raise
            # Fetch errors are ordinary intake records, never Memory maintenance.
            code = getattr(exc, 'code', 'CAPTURE_FAILED')
            message = str(exc)[:1000] if isinstance(exc, ValueError) else 'Capture failed; retry or import a supported text file manually.'
            with self.storage.session() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = self._row(conn, job_id, owner, scope)
                if current['state'] in ('FETCHING', 'PARSING'):
                    conn.execute("UPDATE intake_jobs SET state='FAILED',raw_body=NULL,extracted_content=NULL,error_code=?,error_message=?,updated_at=? WHERE id=?", (code, message, utc_now().isoformat(), job_id))
                conn.execute('COMMIT')
        return self.get(job_id, owner=owner, scope=scope)

    def get(self, job_id, *, owner=None, scope='default'):
        owner, scope = self._identity(owner, scope)
        with self.storage.session() as conn:
            row = self._row(conn, job_id, owner, scope)
            if row['result_json']:
                result = json.loads(row['result_json'])
                state, indexing_status, error = self._index_state(conn, result['note_id'], result['revision'])
                result.update(state=state, saved=True, indexing_status=indexing_status, indexing_error=error)
                row['result_json'] = _json(result)
            return self._view(row)

    @staticmethod
    def _index_state(conn, note_id, revision):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='keyword_index_state'").fetchone():
            return 'SAVED', 'PENDING', None
        state = conn.execute('SELECT * FROM keyword_index_state WHERE id=1').fetchone()
        found = conn.execute("SELECT 1 FROM keyword_documents WHERE generation=? AND plane='NOTES' AND object_id=? AND revision=?", (state['generation'], note_id, revision)).fetchone()
        if found:
            return 'SEARCH_READY', 'READY', None
        if state['last_error']:
            return 'SAVED', 'FAILED', state['last_error']
        return 'INDEXING', 'PENDING', None

    def cancel(self, job_id, *, owner=None, scope='default'):
        owner, scope = self._identity(owner, scope)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._row(conn, job_id, owner, scope)
            if not row['result_json'] and row['state'] != 'EXPIRED':
                conn.execute("UPDATE intake_jobs SET state='CANCELLED',raw_body=NULL,extracted_content=NULL,preview_hash=NULL,updated_at=? WHERE id=?", (utc_now().isoformat(), job_id))
                row.update(state='CANCELLED', raw_body=None, extracted_content=None, preview_hash=None)
            conn.execute('COMMIT')
            return self._view(row)

    def retry(self, job_id, *, owner=None, scope='default'):
        owner, scope = self._identity(owner, scope)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._row(conn, job_id, owner, scope)
            if row['source_type'] != 'web_capture' or row['state'] not in ('FAILED', 'PREVIEW_READY') or row['attempts'] >= self.MAX_RETRIES:
                raise ValueError('Only a failed or unconfirmed webpage can be retried up to three times; files require new input')
            conn.execute("UPDATE intake_jobs SET state='QUEUED',preview_revision=preview_revision+1,preview_hash=NULL,raw_body=NULL,extracted_content=NULL,attempts=attempts+1,expires_at=? WHERE id=?", (time.time() + self.PREVIEW_TTL, job_id))
            conn.execute('COMMIT')
        # Explicit retries have bounded backoff; permission is checked again by capture.
        time.sleep(min(.2 * row['attempts'], .5))
        return self._capture(job_id, owner, scope)

    def _blob_manifest(self, conn, source_id, revision, raw, mime, now):
        content_hash = _hash(raw)
        blob = self.raw_store.base_dir / f'{content_hash}.raw'
        if blob.exists():
            if _hash(blob.read_bytes()) != content_hash:
                raise ValueError('Existing raw blob failed integrity verification')
        else:
            staged = blob.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            try:
                with staged.open('xb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(staged, blob)
            finally:
                staged.unlink(missing_ok=True)
        conn.execute('INSERT INTO raw_manifests VALUES (?,?,?,?,?,?,?)', (source_id, revision, str(blob.resolve()), content_hash, len(raw), mime, now))

    def confirm(self, job_id, *, expected_revision, expected_hash, idempotency_key, selection,
                collections=None, user_comment='', owner=None, scope='default'):
        owner, scope = self._identity(owner, scope)
        collections = collections or []
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError('An idempotency key of at most 200 characters is required')
        if not isinstance(selection, dict) or set(selection) != {'start', 'end'} or any(type(selection[k]) is not int for k in selection):
            raise ValueError('Confirm requires explicit integer start/end selection offsets')
        if type(expected_revision) is not int or expected_revision < 1 or not isinstance(expected_hash, str):
            raise ValueError('Preview revision and hash are required')
        if not isinstance(user_comment, str) or len(user_comment.encode('utf-8')) > 65536:
            raise ValueError('User comment exceeds the limit')
        if not isinstance(collections, list) or len(collections) > 100:
            raise ValueError('At most 100 authorized collection references may be added')
        for ref in collections:
            if not isinstance(ref, dict) or set(ref) != {'id', 'expected_revision'} or not isinstance(ref['id'], str) or type(ref['expected_revision']) is not int:
                raise ValueError('Collection references require id and expected_revision')
        request = {'preview_id': job_id, 'revision': expected_revision, 'hash': expected_hash,
                   'selection': selection, 'collections': collections, 'user_comment': user_comment}
        fingerprint = _hash(_json(request))
        now = utc_now().isoformat()
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            prior = conn.execute('SELECT * FROM intake_confirmations WHERE owner=? AND scope=? AND idempotency_key=?', (owner, scope, idempotency_key)).fetchone()
            if prior:
                if prior['request_hash'] != fingerprint:
                    raise IntakeConflict('Idempotency key already belongs to a different confirmation')
                conn.execute('COMMIT')
                return json.loads(prior['result_json'])
            row = self._row(conn, job_id, owner, scope)
            if row['result_json']:
                if row['confirmed_request_hash'] != fingerprint:
                    raise IntakeConflict('Preview was saved with a different selection; prepare a new preview')
                result = json.loads(row['result_json'])
                conn.execute('INSERT INTO intake_confirmations VALUES (?,?,?,?,?)', (owner, scope, idempotency_key, fingerprint, row['result_json']))
                conn.execute('COMMIT')
                return result
            if row['state'] != 'PREVIEW_READY':
                raise IntakeConflict('Preview is not ready or has expired; prepare and review it again')
            if row['preview_revision'] != expected_revision or row['preview_hash'] != expected_hash:
                raise IntakeConflict('Preview changed; review the current preview before saving')
            text = row['extracted_content']
            if not (0 <= selection['start'] < selection['end'] <= len(text)):
                raise ValueError('Selection is empty or outside the preview content')
            selected = text[selection['start']:selection['end']]
            metadata = json.loads(row['metadata_json'])
            # Check all membership permissions/CAS before referencing any blobs.
            if collections and not conn.execute("SELECT 1 FROM sqlite_master WHERE name='collection_topics'").fetchone():
                raise ValueError('Collections are unavailable')
            for ref in collections:
                topic = conn.execute('SELECT revision FROM collection_topics WHERE id=? AND scope=?', (ref['id'], scope)).fetchone()
                if not topic:
                    raise ValueError('Collection not found in the authorized scope')
                if topic['revision'] != ref['expected_revision']:
                    raise IntakeConflict('Collection changed; reload before adding the source')
            conn.execute("UPDATE intake_jobs SET state='COMMITTING' WHERE id=?", (job_id,))
            source = conn.execute('SELECT * FROM intake_sources WHERE owner=? AND scope=? AND source_identity=?', (owner, scope, row['source_identity'])).fetchone()
            source_changed = False
            source_id = source['id'] if source else 'source-' + uuid.uuid4().hex
            note_id = source['note_id'] if source else 'note-' + uuid.uuid4().hex
            source_revision = source['current_source_revision'] if source else 1
            raw_hash = _hash(row['raw_body'])
            if source:
                old_source = conn.execute('SELECT * FROM intake_source_revisions WHERE source_id=? AND revision=?', (source_id, source_revision)).fetchone()
                # A source reverting to old bytes is still a new observed revision.
                source_changed = old_source['content_hash'] != raw_hash
                if source_changed:
                    source_revision += 1
                    conn.execute('UPDATE intake_sources SET current_source_revision=? WHERE id=?', (source_revision, source_id))
            else:
                conn.execute('INSERT INTO intake_sources VALUES (?,?,?,?,?,?,?,?)', (source_id, owner, scope, row['source_identity'], row['source_type'], note_id, 1, now))
            if not source or source_changed:
                self._blob_manifest(conn, source_id, source_revision, row['raw_body'], metadata['mime_type'], now)
                conn.execute('INSERT INTO intake_source_revisions VALUES (?,?,?,?,?)', (source_id, source_revision, raw_hash, _json(metadata), now))
            current = conn.execute('SELECT * FROM notes WHERE id=? AND is_current=1 AND scope=?', (note_id, scope)).fetchone()
            current_item = conn.execute('SELECT * FROM intake_items WHERE note_id=? AND note_revision=?', (note_id, current['revision'])).fetchone() if current else None
            same_item = bool(current and current['content_hash'] == _hash(selected) and current['title'] == metadata['title'] and current_item and current_item['selection_json'] == _json(selection) and current_item['source_revision'] == source_revision)
            revision = current['revision'] if same_item else (current['revision'] + 1 if current else 1)
            coverage = 'extracted_text' if selection == {'start': 0, 'end': len(text)} else 'selected_excerpt'
            if not same_item:
                self.notes_mgr._store_raw(conn, note_id, revision, selected, _hash(selected), now)
                conn.execute('UPDATE notes SET is_current=0 WHERE id=?', (note_id,))
                conn.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,1,?)', (note_id, revision, metadata['title'], selected, _hash(selected), scope, owner, now))
                conn.execute('INSERT INTO intake_items VALUES (?,?,?,?,?,?,?)', (note_id, revision, source_id, source_revision, _json(selection), coverage, int(source_changed)))
                self.storage._insert_outbox_event(conn, aggregate_type='NOTE', aggregate_id=note_id,
                                                 event_type='NOTE_REVISED' if current else 'NOTE_CREATED',
                                                 payload={'note_id': note_id, 'revision': revision, 'scope': scope,
                                                          'source_type': row['source_type'], 'source_id': source_id, 'source_revision': source_revision})
            if user_comment:
                conn.execute('INSERT INTO intake_comments VALUES (?,?,?,?,?,?)', (uuid.uuid4().hex, note_id, revision, owner, user_comment, now))
            memberships = []
            for ref in collections:
                added = conn.execute('INSERT OR IGNORE INTO collection_members VALUES (?,?,?,?)', (ref['id'], 'note', note_id, now)).rowcount
                if added:
                    conn.execute('UPDATE collection_topics SET revision=revision+1,updated_at=? WHERE id=? AND scope=?', (now, ref['id'], scope))
                memberships.append(ref['id'])
            affected = []
            if source_changed:
                for node in conn.execute('SELECT id,version,data_json FROM nodes WHERE scope=? AND is_active=1', (scope,)):
                    data = json.loads(node['data_json'])
                    provenance = data.get('provenance') or {}
                    if provenance.get('source_id') in (note_id, source_id):
                        conn.execute('INSERT OR IGNORE INTO intake_memory_review_flags VALUES (?,?,?,?,?,?)',
                                     (source_id, source_revision, node['id'], node['version'], 'External source has a new revision; accepted memory was preserved', now))
                        affected.append(node['id'])
                self.storage._insert_outbox_event(conn, aggregate_type='SOURCE', aggregate_id=source_id, event_type='SOURCE_REVISED',
                                                 payload={'source_id': source_id, 'source_revision': source_revision, 'note_id': note_id, 'scope': scope,
                                                          'needs_review': True, 'affected_memory_ids': affected})
            self.storage._insert_outbox_event(conn, aggregate_type='INTAKE', aggregate_id=job_id, event_type='SOURCE_INTAKE_SAVED',
                                             payload={'source_id': source_id, 'source_revision': source_revision, 'note_id': note_id, 'revision': revision,
                                                      'scope': scope, 'source_type': row['source_type'], 'collections': memberships})
            result = {'preview_id': job_id, 'job_id': job_id, 'state': 'SAVED', 'saved': True, 'indexing_status': 'PENDING',
                      'note_id': note_id, 'revision': revision, 'source_id': source_id, 'source_revision': source_revision,
                      'source_type': row['source_type'], 'coverage': coverage, 'needs_review': source_changed,
                      'affected_memory_ids': affected, 'collections': memberships, 'deduplicated': same_item}
            encoded = _json(result)
            # Temporary original bytes are now referenced by durable manifests.
            conn.execute("UPDATE intake_jobs SET state='SAVED',raw_body=NULL,extracted_content=NULL,result_json=?,confirmed_request_hash=?,updated_at=? WHERE id=?", (encoded, fingerprint, now, job_id))
            conn.execute('INSERT INTO intake_confirmations VALUES (?,?,?,?,?)', (owner, scope, idempotency_key, fingerprint, encoded))
            conn.execute('COMMIT')
            return result

    def get_item(self, note_id, *, revision=None, owner=None, scope='default'):
        owner, scope = self._identity(owner, scope)
        with self.storage.session() as conn:
            source = conn.execute('SELECT * FROM intake_sources WHERE note_id=? AND owner=? AND scope=?', (note_id, owner, scope)).fetchone()
            if not source:
                raise LookupError('Imported item not found in this owner and scope')
            query = 'SELECT * FROM notes WHERE id=? AND scope=? AND ' + ('revision=?' if revision else 'is_current=1')
            params = (note_id, scope, revision) if revision else (note_id, scope)
            note = conn.execute(query, params).fetchone()
            item = conn.execute('SELECT * FROM intake_items WHERE note_id=? AND note_revision=?', (note_id, note['revision'])).fetchone() if note else None
            if not item:
                raise LookupError('Imported item revision not found')
            raw = conn.execute('SELECT * FROM intake_source_revisions WHERE source_id=? AND revision=?', (source['id'], item['source_revision'])).fetchone()
            metadata = json.loads(raw['provenance_json'])
            comments = [dict(r) for r in conn.execute('SELECT id,content,created_at FROM intake_comments WHERE note_id=? AND note_revision=? AND owner=? ORDER BY created_at,id', (note_id, note['revision'], owner))]
            return {'note_id': note_id, 'revision': note['revision'], 'source_id': source['id'], 'source_revision': item['source_revision'],
                    'source_type': source['source_type'], 'scope': scope, 'owner': owner, 'title': note['title'],
                    'selected_content': note['content'], 'coverage': item['coverage'], 'selection': json.loads(item['selection_json']),
                    'provenance': metadata, 'published_at': metadata.get('published_at'), 'user_comments': comments,
                    'ai_analysis': [], 'needs_review': bool(item['needs_review']), 'trust': metadata['trust']}
