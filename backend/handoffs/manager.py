"""Immutable work versions and receipts, scoped through the existing MCP grant."""
from __future__ import annotations

import json
import uuid

from backend.handoffs.models import (WorkHandoffPackage, WorkHandoffPayload, WorkHandoffRecord, canonical_json,
                                    content_hash, markdown_for, package_for, validate)
from backend.notes.notes_manager import RevisionConflict
from backend.schema.models import utc_now


class WorkHandoffManager:
    def __init__(self, storage, scope, principal):
        self.storage, self.scope, self.principal = storage, scope, principal

    @staticmethod
    def _record(row):
        return {'id': row['id'], 'revision': row['revision'], 'scope': row['scope'],
            'created_by': row['created_by'], 'created_at': row['created_at'],
            'payload': json.loads(row['payload_json']), 'content_hash': row['content_hash'],
            'reported_origin': json.loads(row['origin_json']) if row['origin_json'] else None}

    def _get(self, conn, handoff_id, revision=None):
        sql = 'SELECT * FROM work_handoff_revisions WHERE id = ? AND scope = ?'
        params = [handoff_id, self.scope]
        if revision is not None:
            sql += ' AND revision = ?'
            params.append(revision)
        row = conn.execute(sql + ' ORDER BY revision DESC LIMIT 1', params).fetchone()
        if row is None:
            raise LookupError('Handoff is not available in this scope')
        return self._record(row)

    def get(self, handoff_id, revision=None, *, include_receipts=False, receipt_limit=20, receipt_offset=0):
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            record = self._get(conn, handoff_id, revision)
            if include_receipts:
                rows = conn.execute('''SELECT * FROM work_handoff_receipts WHERE handoff_id = ?
                    AND revision = ? AND scope = ? ORDER BY received_at, id LIMIT ? OFFSET ?''',
                    (handoff_id, record['revision'], self.scope, receipt_limit + 1, receipt_offset)).fetchall()
                record['receipts'] = [self._receipt(row) for row in rows[:receipt_limit]]
                record['receipts_has_more'] = len(rows) > receipt_limit
                record['next_receipt_offset'] = receipt_offset + receipt_limit if len(rows) > receipt_limit else None
            return record

    def save(self, payload, request_key, *, handoff_id=None, expected_revision=None, origin=None, import_hash=None):
        value = validate(WorkHandoffPayload, payload)
        fingerprint = content_hash({'payload': value, 'handoff_id': handoff_id,
                                    'expected_revision': expected_revision, 'origin': origin, 'import_hash': import_hash})
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('''SELECT * FROM work_handoff_revisions
                WHERE scope = ? AND created_by = ? AND request_key = ?''',
                (self.scope, self.principal, request_key)).fetchone()
            if old is not None:
                if old['request_hash'] != fingerprint:
                    raise ValueError('request_key was already used for a different submission')
                conn.execute('COMMIT')
                return self._record(old)
            if handoff_id is None:
                handoff_id, revision = 'handoff-' + uuid.uuid4().hex, 1
            else:
                current = self._get(conn, handoff_id)
                if current['revision'] != expected_revision:
                    raise RevisionConflict(f"Handoff changed to revision {current['revision']}; read it before merging")
                revision = current['revision'] + 1
                origin = current['reported_origin']
            now = utc_now().isoformat()
            # Successful stored versions must also satisfy the portable record
            # contract, including locally supplied scope and principal bounds.
            validate(WorkHandoffRecord, {'id': handoff_id, 'revision': revision, 'scope': self.scope,
                'created_by': self.principal, 'created_at': now, 'payload': value,
                'content_hash': content_hash(value), 'reported_origin': origin})
            conn.execute('''INSERT INTO work_handoff_revisions
                (id, revision, scope, created_by, created_at, payload_json, content_hash,
                 origin_json, request_key, request_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (handoff_id, revision, self.scope, self.principal, now, canonical_json(value),
                 content_hash(value), canonical_json(origin) if origin else None, request_key, fingerprint))
            self.storage._insert_outbox_event(conn, aggregate_type='WORK_HANDOFF', aggregate_id=handoff_id,
                event_type='WORK_HANDOFF_REVISED' if revision > 1 else 'WORK_HANDOFF_CREATED',
                payload={'id': handoff_id, 'revision': revision, 'scope': self.scope, 'created_by': self.principal})
            record = self._get(conn, handoff_id, revision)
            conn.execute('COMMIT')
            return record

    def list(self, *, limit=20, offset=0, target_system=None):
        with self.storage.session() as conn:
            sql = '''SELECT r.* FROM work_handoff_revisions r WHERE r.scope = ?
                AND r.revision = (SELECT MAX(v.revision) FROM work_handoff_revisions v WHERE v.id = r.id)'''
            params = [self.scope]
            if target_system is not None:
                sql += " AND json_extract(r.payload_json, '$.target_system') = ?"
                params.append(target_system)
            rows = conn.execute(sql + ' ORDER BY r.created_at DESC, r.id ASC LIMIT ? OFFSET ?',
                                (*params, limit + 1, offset)).fetchall()
            more = len(rows) > limit
            return {'items': [self._record(r) for r in rows[:limit]], 'has_more': more,
                    'next_offset': offset + limit if more else None}

    def export(self, handoff_id, *, revision=None, format='json'):
        record = self.get(handoff_id, revision)
        package = package_for(record)
        if format not in ('json', 'markdown'):
            raise ValueError('Unsupported export format')
        return {'handoff_id': record['id'], 'revision': record['revision'], 'format': format,
            'content_hash': record['content_hash'],
            'content': json.dumps(package, ensure_ascii=False, indent=2) if format == 'json' else markdown_for(package)}

    def import_package(self, package, request_key):
        package = validate(WorkHandoffPackage, package)
        record = package['handoff']
        if content_hash(record['payload']) != record['content_hash']:
            raise ValueError('Imported payload hash does not match')
        origin = record['reported_origin'] or {
            k: record[k] for k in ('id', 'revision', 'scope', 'created_by', 'created_at', 'content_hash')}
        # The importing principal is authoritative for local attribution. Origin
        # metadata is merely reported by a file; a hash is not authentication.
        return self.save(record['payload'], request_key, origin=origin, import_hash=content_hash(package))

    @staticmethod
    def _receipt(row):
        return {**dict(row), 'meaning': 'RECEIVED_ONLY'}

    def acknowledge(self, handoff_id, revision, expected_hash):
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = self._get(conn, handoff_id, revision)
            if record['content_hash'] != expected_hash:
                raise ValueError('Receipt content hash does not match the specified revision')
            old = conn.execute('''SELECT * FROM work_handoff_receipts WHERE handoff_id = ?
                AND revision = ? AND received_by = ? AND scope = ?''',
                (handoff_id, revision, self.principal, self.scope)).fetchone()
            if old is not None:
                conn.execute('COMMIT')
                return self._receipt(old)
            if self._get(conn, handoff_id)['revision'] != revision:
                raise RevisionConflict('A newer handoff exists; read it before acknowledging')
            receipt_id = 'receipt-' + uuid.uuid4().hex
            conn.execute('''INSERT INTO work_handoff_receipts
                (id, handoff_id, revision, scope, received_by, content_hash, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (receipt_id, handoff_id, revision, self.scope, self.principal, expected_hash, utc_now().isoformat()))
            self.storage._insert_outbox_event(conn, aggregate_type='WORK_HANDOFF', aggregate_id=handoff_id,
                event_type='WORK_HANDOFF_RECEIVED', payload={'id': handoff_id, 'revision': revision,
                    'scope': self.scope, 'received_by': self.principal, 'receipt_id': receipt_id})
            row = conn.execute('SELECT * FROM work_handoff_receipts WHERE id = ?', (receipt_id,)).fetchone()
            conn.execute('COMMIT')
            return self._receipt(row)
