"""Persist externally reported conflicts without overwriting participating claims.

Semantic detection belongs to the external Agent. The local owner records a
resolution with evidence; neither action promotes maturity or changes a decision.
"""
import hashlib
import json

from backend.auth.policy import current_access
from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
from backend.schema.models import (
    ConflictType, GraphNode, GraphEdge, NodeType, RelationshipType, MemoryAuthority,
    MemoryMaturity, MemoryDepth, utc_now,
)
from backend.storage.canonical.sqlite_storage import CanonicalRevisionConflict


TERMINAL = {'RESOLVED', 'SUPERSEDED', 'IGNORED'}
STATUSES = {'OPEN', 'UNDER_REVIEW', *TERMINAL}


def has_open_candidate_conflict(conn, candidate_id, scope):
    return conn.execute("""SELECT 1 FROM nodes, json_each(nodes.data_json, '$.core_props.conflicting_candidates') AS participant
        WHERE nodes.node_type = 'Conflict' AND nodes.scope = ? AND nodes.is_active = 1
        AND participant.value = ?
        AND COALESCE(json_extract(nodes.data_json, '$.core_props.status'), 'OPEN') NOT IN ('RESOLVED','SUPERSEDED','IGNORED')
        LIMIT 1""", (scope, candidate_id)).fetchone() is not None


class ConflictManager:
    def __init__(self, storage):
        self.storage = storage

    def get(self, conflict_id, *, scope):
        with self.storage.session() as conn:
            node = self._node(conn, conflict_id, scope)
            if node.node_type != NodeType.CONFLICT:
                raise LookupError('Conflict is unavailable in this scope')
            return node

    def detect_candidate_conflicts(self, candidate_id: str, *, scope: str):
        """Find active conflicts that involve this candidate."""
        with self.storage.session() as conn:
            rows = conn.execute("""
                SELECT nodes.data_json FROM nodes, json_each(nodes.data_json, '$.core_props.conflicting_candidates') AS participant
                WHERE nodes.node_type = 'Conflict' AND nodes.scope = ? AND nodes.is_active = 1
                AND participant.value = ?
                AND COALESCE(json_extract(nodes.data_json, '$.core_props.status'), 'OPEN') NOT IN ('RESOLVED','SUPERSEDED','IGNORED')
            """, (scope, candidate_id)).fetchall()
            return [GraphNode.model_validate_json(r['data_json']).model_dump(mode='json') for r in rows]

    def list(self, *, scope, status=None, node_id=None, limit=20, offset=0):
        if (type(limit) is not int or not 1 <= limit <= 100 or
                type(offset) is not int or not 0 <= offset <= 1_000_000):
            raise ValueError('Invalid conflict pagination')
        if status is not None and status not in STATUSES:
            raise ValueError('Unsupported conflict status')
        query = "SELECT data_json FROM nodes WHERE node_type = 'Conflict' AND scope = ? AND is_active = 1"
        params = [scope]
        if status is not None:
            query += " AND COALESCE(json_extract(data_json, '$.core_props.status'), 'OPEN') = ?"
            params.append(status)
        if node_id is not None:
            query += " AND EXISTS (SELECT 1 FROM json_each(nodes.data_json, '$.core_props.conflicting_nodes') p WHERE p.value = ?)"
            params.append(node_id)
        with self.storage.session() as conn:
            rows = conn.execute(query + ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?',
                                (*params, limit + 1, offset)).fetchall()
        return {'items': [GraphNode.model_validate_json(row['data_json']).model_dump(mode='json') for row in rows[:limit]],
                'has_more': len(rows) > limit, 'next_offset': offset + limit if len(rows) > limit else None}

    @staticmethod
    def _node(conn, identity, scope):
        row = conn.execute('SELECT data_json FROM nodes WHERE id = ? AND scope = ? AND is_active = 1', (identity, scope)).fetchone()
        if not row:
            raise LookupError('Conflict participant is unavailable in this scope')
        return GraphNode.model_validate_json(row['data_json'])

    @staticmethod
    def _source(conn, reference, scope):
        if not isinstance(reference, dict) or set(reference) != {'source_id', 'source_revision', 'content_hash'}:
            raise ValueError('Evidence needs source_id, source_revision and content_hash')
        source = conn.execute('SELECT * FROM notes WHERE id = ? AND revision = ? AND scope = ?',
            (reference['source_id'], reference['source_revision'], scope)).fetchone()
        if (not source or source['content_hash'] != reference['content_hash'] or
                hashlib.sha256(source['content'].encode('utf-8')).hexdigest() != reference['content_hash']):
            raise ValueError('Evidence source revision/hash is unavailable in this scope')

    def report(self, *, scope, memory_targets, candidate_ids, kind, description,
               actor, actor_type='agent', severity='normal'):
        kind = ConflictType(kind)
        if kind == ConflictType.SCHEMA_CONFLICT:
            raise ValueError('Schema errors belong in quarantine, not semantic conflicts')
        if not isinstance(description, str) or not description.strip() or len(description) > 8000:
            raise ValueError('Conflict description must contain 1–8000 characters')
        if not isinstance(scope, str) or not scope.strip() or actor_type not in ('user', 'agent'):
            raise ValueError('Valid scope and reporter type are required')
        if severity not in ('low', 'normal', 'high'):
            raise ValueError('Unknown severity')
        if (not isinstance(memory_targets, dict) or not isinstance(candidate_ids, list)
                or not all(isinstance(x, str) and x for x in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)
                or not 2 <= len(memory_targets) + len(candidate_ids) <= 10
                or not all(isinstance(k, str) and k and type(v) is int and v >= 1 for k, v in memory_targets.items())):
            raise ValueError('A conflict needs 2–10 distinct participants with exact memory revisions')
        identity = json.dumps([scope, sorted(memory_targets.items()), sorted(candidate_ids), kind.value, description, severity],
                              ensure_ascii=False, separators=(',', ':'))
        conflict_id = 'conf-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            participants = []
            for node_id, revision in sorted(memory_targets.items()):
                node = self._node(conn, node_id, scope)
                if node.version != revision:
                    raise CanonicalRevisionConflict('Participant revision changed; read it before reporting a conflict')
                if node.node_type == NodeType.CONFLICT:
                    raise ValueError('Conflict participants must be claims, not other conflict reports')
                participants.append({'type': 'memory', 'id': node.id, 'revision': node.version,
                                     'provenance': node.provenance.model_dump(mode='json') if node.provenance else None})
            for candidate_id in sorted(candidate_ids):
                row = conn.execute('SELECT * FROM candidates WHERE id = ? AND scope = ?', (candidate_id, scope)).fetchone()
                if not row or row['status'] not in ('VALIDATED', 'UNDER_REVIEW', 'PENDING'):
                    raise ValueError('Candidate is unavailable or already terminal')
                payload = json.loads(row['payload_json'])
                reference = {'source_id': row['source_id'], 'source_revision': row['source_revision'],
                             'content_hash': payload.get('content_hash')}
                self._source(conn, reference, scope)
                participants.append({'type': 'candidate', 'id': candidate_id, **reference})
            existing = conn.execute('SELECT data_json FROM nodes WHERE id = ?', (conflict_id,)).fetchone()
            if existing:
                recovered = GraphNode.model_validate_json(existing[0])
                if recovered.scope != scope or not recovered.is_active:
                    raise LookupError('Existing report is unavailable')
                return recovered
            now = utc_now().isoformat()
            conflict = GraphNode(id=conflict_id, node_type=NodeType.CONFLICT, scope=scope,
                title=description[:100], content=description,
                authority=MemoryAuthority.INFERRED, maturity=MemoryMaturity.L1_INDEXED,
                depth=MemoryDepth.D3_FACT_RELATION,
                core_props={'status': 'OPEN', 'severity': severity, 'kind': kind.value, 'detected_at': now,
                    'conflicting_nodes': sorted(memory_targets), 'conflicting_candidates': sorted(candidate_ids)},
                source_props={'participants': participants, 'reporter': {'type': actor_type, 'id': actor}},
                verification_props={'status': 'UNVERIFIED'})
            self.storage.upsert_node(conflict, conn=conn, expected_revision=0)
            for node_id in sorted(memory_targets):
                self.storage.upsert_edge(GraphEdge(id=f'{conflict_id}:{node_id}', source_id=conflict_id,
                    target_id=node_id, relation=RelationshipType.RELATED_TO, scope=scope,
                    authority=MemoryAuthority.INFERRED, properties={'role': 'conflict_participant',
                        'target_revision': memory_targets[node_id]}), conn=conn, expected_revision=0)
            for candidate_id in candidate_ids:
                conn.execute("UPDATE candidates SET status = 'PENDING', updated_at = ? WHERE id = ?", (now, candidate_id))
                self.storage._insert_outbox_event(conn, 'CANDIDATE', candidate_id, 'CANDIDATE_PENDING',
                    {'candidate_id': candidate_id, 'conflict_id': conflict.id, 'scope': scope})
            MemoryLogsheet(self.storage).append_entry(operation_id=conflict.id, event_type=LogsheetEventType.MEMORY_CHANGED,
                actor={'type': actor_type, 'id': actor}, scope=scope,
                target_refs=[{'id': conflict.id, 'after_revision': 1}], reason=description,
                result='CONFLICT_REPORTED', evidence_refs=participants,
                idempotency_key=f'{conflict.id}:report', conn=conn)
            conn.execute('COMMIT')
            return conflict

    def transition(self, *, conflict_id, scope, expected_revision, status, reason, method='',
                   evidence_refs=None, actor, actor_type='user', superseded_by=None):
        if actor_type != 'user' or current_access.get() is not None:
            raise PermissionError('Conflict resolution requires local owner review; Agents may report conflicts')
        if status not in {'OPEN', 'UNDER_REVIEW', *TERMINAL} or not reason.strip():
            raise ValueError('A supported status and review reason are required')
        evidence_refs = evidence_refs or []
        if len(evidence_refs) > 20:
            raise ValueError('At most 20 evidence references per resolution')
        if status in TERMINAL and (not method.strip() or not evidence_refs):
            raise ValueError('Closing a conflict requires method and exact evidence references')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conflict = self._node(conn, conflict_id, scope)
            if conflict.node_type != NodeType.CONFLICT:
                raise ValueError('Object is not a conflict')
            if type(expected_revision) is not int or conflict.version != expected_revision:
                raise CanonicalRevisionConflict('Conflict revision changed; reload before reviewing')
            # Do not silently apply an old review to changed or inaccessible claims.
            for participant in conflict.source_props.get('participants', []):
                if participant['type'] == 'memory':
                    current = self._node(conn, participant['id'], scope)
                    if current.version != participant['revision'] and status in TERMINAL - {'SUPERSEDED'}:
                        raise CanonicalRevisionConflict('Participant changed; report a new conflict and supersede this one')
            for reference in evidence_refs:
                self._source(conn, reference, scope)
            if status == 'SUPERSEDED':
                replacement = self._node(conn, superseded_by, scope)
                if replacement.id == conflict.id or replacement.node_type != NodeType.CONFLICT or replacement.core_props.get('status') in TERMINAL:
                    raise ValueError('Replacement must be a distinct open conflict in this scope')
            elif superseded_by is not None:
                raise ValueError('superseded_by only applies to SUPERSEDED')
            now = utc_now().isoformat()
            conflict.core_props.update(status=status, resolved_at=now if status in TERMINAL else None,
                resolution_method=method, resolution_reason=reason, superseded_by=superseded_by)
            conflict.verification_props.update(resolution={'actor': actor, 'method': method,
                'evidence_refs': evidence_refs, 'recorded_at': now, 'claim_verification_changed': False})
            self.storage.upsert_node(conflict, conn=conn, expected_revision=expected_revision)
            MemoryLogsheet(self.storage).append_entry(operation_id=f'{conflict.id}:{conflict.version}',
                event_type=LogsheetEventType.MEMORY_CHANGED, actor={'type': 'user', 'id': actor},
                scope=scope, target_refs=[{'id': conflict.id, 'before_revision': expected_revision, 'after_revision': conflict.version}],
                reason=reason, result='CONFLICT_' + status, evidence_refs=evidence_refs,
                idempotency_key=f'{conflict.id}:{conflict.version}:transition', conn=conn)
            conn.execute('COMMIT')
            return conflict
