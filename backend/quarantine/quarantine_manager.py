"""Repair quarantined candidate submissions without altering their original data."""
import hashlib

from backend.auth.policy import current_access
from backend.candidates.candidate_bridge import CandidateBridge
from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
from backend.schema.models import MemoryAuthority, NodeType
from backend.storage.canonical.sqlite_storage import CanonicalRevisionConflict


class QuarantineManager:
    def __init__(self, storage):
        self.storage = storage

    def list_legacy(self, *, limit=20, offset=0):
        if current_access.get() is not None:
            raise PermissionError('Unknown-scope records require local owner inspection')
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError('Invalid quarantine pagination')
        with self.storage.session() as conn:
            rows = conn.execute('''SELECT * FROM quarantine WHERE scope IS NULL
                ORDER BY quarantined_at DESC, id DESC LIMIT ? OFFSET ?''', (limit + 1, offset)).fetchall()
        return {'items': [self.storage.quarantine_from_row(row).model_dump(mode='json') for row in rows[:limit]],
                'has_more': len(rows) > limit, 'next_offset': offset + limit if len(rows) > limit else None}

    def _get(self, conn, identity, scope):
        row = conn.execute('SELECT * FROM quarantine WHERE id = ? AND scope = ?', (identity, scope)).fetchone()
        if not row:
            raise LookupError('Quarantine record is unavailable in this scope')
        return self.storage.quarantine_from_row(row)

    def get(self, identity, *, scope):
        with self.storage.session() as conn:
            return self._get(conn, identity, scope)

    def list(self, *, scope, resolved=False, limit=20, offset=0):
        if (type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int
                or not 0 <= offset <= 1_000_000 or type(resolved) is not bool):
            raise ValueError('Invalid quarantine pagination')
        with self.storage.session() as conn:
            rows = conn.execute('''SELECT * FROM quarantine WHERE scope = ? AND resolved = ?
                ORDER BY quarantined_at DESC, id DESC LIMIT ? OFFSET ?''',
                (scope, int(resolved), limit + 1, offset)).fetchall()
        return {'items': [self.storage.quarantine_from_row(row).model_dump(mode='json') for row in rows[:limit]],
                'has_more': len(rows) > limit, 'next_offset': offset + limit if len(rows) > limit else None}

    def audit_health(self, *, scope: str = 'default'):
        """Audit quarantine health metrics, unresolved error distribution, and recovery status."""
        with self.storage.session() as conn:
            total = conn.execute("SELECT COUNT(*) FROM quarantine WHERE scope = ?", (scope,)).fetchone()[0]
            unresolved = conn.execute("SELECT COUNT(*) FROM quarantine WHERE scope = ? AND resolved = 0", (scope,)).fetchone()[0]
            resolved = conn.execute("SELECT COUNT(*) FROM quarantine WHERE scope = ? AND resolved = 1", (scope,)).fetchone()[0]
            reasons = conn.execute("""
                SELECT reason, COUNT(*) as cnt FROM quarantine 
                WHERE scope = ? AND resolved = 0 
                GROUP BY reason ORDER BY cnt DESC LIMIT 10
            """, (scope,)).fetchall()
            return {
                'scope': scope,
                'total': total,
                'unresolved': unresolved,
                'resolved': resolved,
                'common_unresolved_errors': [{'reason': r['reason'], 'count': r['cnt']} for r in reasons],
                'healthy': unresolved == 0
            }

    def repair(self, identity, *, scope, expected_revision, claim, suggested_node_type, payload, reason, actor):
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 200_000:
            raise ValueError('A repaired claim of 1–200000 characters is required')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 8000:
            raise ValueError('A repair reason of 1–8000 characters is required')
        if not isinstance(payload, dict):
            raise ValueError('Repaired payload must be an object')
        node_type = NodeType(suggested_node_type)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = self._get(conn, identity, scope)
            if type(expected_revision) is not int or record.revision != expected_revision:
                raise CanonicalRevisionConflict('Quarantine revision changed; reload before repairing')
            if record.resolved:
                raise ValueError('This quarantine record was already recovered')
            original = record.submission
            if not original or record.source_revision is None:
                raise ValueError('Original source revision is unknown; manually reimport from a verified source')
            authority = MemoryAuthority(original['authority'])
            access = current_access.get()
            if access is not None and authority != MemoryAuthority.INFERRED:
                raise PermissionError('Agent repair is limited to INFERRED submissions')
            source = conn.execute('SELECT * FROM notes WHERE id = ? AND revision = ? AND scope = ?',
                (record.source_id, record.source_revision, scope)).fetchone()
            if not source or hashlib.sha256(source['content'].encode('utf-8')).hexdigest() != source['content_hash']:
                raise ValueError('Original source revision is unavailable or failed integrity validation')
            # A repair changes a candidate proposal, never its source or authority.
            corrected = {**payload, 'content': claim, 'content_hash': source['content_hash'], 'maturity': 'L2'}
            ok, candidate_id, error = CandidateBridge(self.storage).submit_candidate(
                source_id=record.source_id, source_revision=record.source_revision, claim=claim,
                suggested_node_type=node_type, payload=corrected, scope=scope, authority=authority, conn=conn)
            if not ok:
                # Roll back the temporary failed submission together with the repair.
                raise ValueError('Repair still fails validation: ' + (error or 'unknown schema error'))
            conn.execute('''UPDATE quarantine SET resolved = 1, revision = revision + 1,
                resolution_notes = ?, result_candidate_id = ? WHERE id = ? AND revision = ?''',
                (reason, candidate_id, identity, expected_revision))
            self.storage._insert_outbox_event(conn, 'QUARANTINE', identity, 'QUARANTINE_RECOVERED',
                {'scope': scope, 'candidate_id': candidate_id, 'revision': expected_revision + 1})
            MemoryLogsheet(self.storage).append_entry(operation_id=f'{identity}:{expected_revision + 1}',
                event_type=LogsheetEventType.MEMORY_CHANGED,
                actor={'type': 'agent' if access is not None else 'user', 'id': access.principal if access is not None else actor},
                scope=scope, target_refs=[{'id': identity, 'type': 'quarantine', 'after_revision': expected_revision + 1},
                    {'id': candidate_id, 'type': 'candidate'}], reason=reason, result='QUARANTINE_RECOVERED_TO_CANDIDATE',
                evidence_refs=[{'source_id': record.source_id, 'source_revision': record.source_revision,
                                'content_hash': source['content_hash']}],
                idempotency_key=f'{identity}:{expected_revision + 1}:repair', conn=conn)
            result = self._get(conn, identity, scope)
            conn.execute('COMMIT')
            return result
