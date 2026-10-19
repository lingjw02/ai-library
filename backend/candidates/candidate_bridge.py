"""
Candidate Bridge implementation.
Ref: AI Knowledge OS Backend Specification §§5, 11, 16.
Responsible for controlled extraction, validation, quarantine routing,
and atomic promotion from raw Candidate to canonical Memory Plane GraphNode.
"""

from __future__ import annotations
from contextlib import nullcontext
import uuid
import json
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from backend.schema.models import (
    GraphNode,
    GraphEdge,
    NodeType,
    RelationshipType,
    CandidateLifecycleStatus,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
    Provenance,
    utc_now,
)
from backend.schema.registry import SchemaRegistry, global_schema_registry
from backend.storage.canonical.sqlite_storage import CanonicalStorage


class CandidateRecord:
    def __init__(
        self,
        id: str,
        source_id: str,
        source_revision: int,
        claim: str,
        suggested_node_type: NodeType,
        scope: str,
        authority: MemoryAuthority,
        status: CandidateLifecycleStatus,
        payload: Dict[str, Any],
        created_at: str,
        updated_at: str,
    ):
        self.id = id
        self.source_id = source_id
        self.source_revision = source_revision
        self.claim = claim
        self.suggested_node_type = suggested_node_type
        self.scope = scope
        self.authority = authority
        self.status = status
        self.payload = payload
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "claim": self.claim,
            "suggested_node_type": self.suggested_node_type.value,
            "scope": self.scope,
            "authority": self.authority.value,
            "status": self.status.value,
            "payload": self.payload,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def normalize_claim_text(claim: str) -> str:
    """Normalize claim text for robust duplicate detection."""
    import re
    text = re.sub(r'\s+', ' ', (claim or '').strip()).lower()
    text = text.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
    return text


class CandidateBridge:
    @staticmethod
    def approval_payload(candidate):
        return {'candidate_id': candidate.id, 'source_id': candidate.source_id,
            'source_revision': candidate.source_revision, 'claim': candidate.claim,
            'suggested_node_type': candidate.suggested_node_type.value, 'payload': candidate.payload,
            'authority': candidate.authority.value}

    """
    Candidate Bridge controls knowledge ingestion.
    Never directly turns an unvalidated claim into an authoritative fact.
    """

    def __init__(self, storage: CanonicalStorage, schema_registry: Optional[SchemaRegistry] = None):
        self.storage = storage
        self.schema_registry = schema_registry or global_schema_registry

    def find_duplicate_candidate(
        self, source_id: str, claim: str, scope: str = "default", conn=None
    ) -> Optional[CandidateRecord]:
        """Check if an active candidate with identical normalized claim already exists."""
        norm_target = normalize_claim_text(claim)
        with (self.storage.session() if conn is None else nullcontext(conn)) as db_conn:
            rows = db_conn.execute(
                """
                SELECT * FROM candidates 
                WHERE source_id = ? AND scope = ? AND status IN ('VALIDATED', 'PENDING', 'UNDER_REVIEW')
                ORDER BY created_at ASC;
                """,
                (source_id, scope),
            ).fetchall()
            for r in rows:
                if normalize_claim_text(r['claim']) == norm_target:
                    return CandidateRecord(
                        id=r["id"],
                        source_id=r["source_id"],
                        source_revision=r["source_revision"],
                        claim=r["claim"],
                        suggested_node_type=NodeType(r["suggested_node_type"]),
                        scope=r["scope"],
                        authority=MemoryAuthority(r["authority"]),
                        status=CandidateLifecycleStatus(r["status"]),
                        payload=json.loads(r["payload_json"]),
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
        return None

    def submit_candidate(
        self,
        source_id: str,
        source_revision: int,
        claim: str,
        suggested_node_type: NodeType,
        payload: Dict[str, Any],
        scope: str = "default",
        authority: MemoryAuthority = MemoryAuthority.INFERRED,
        conn=None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Submit a candidate extracted from notes or conversation.
        Runs schema validation first:
        - If invalid: routes cleanly to Quarantine, does NOT crash or insert into active candidates.
        - If valid: persists Candidate with status VALIDATED.
        Returns: (success, candidate_or_quarantine_id, error_message)
        """
        existing = self.find_duplicate_candidate(source_id, claim, scope=scope, conn=conn)
        if existing:
            return True, existing.id, None

        identity = json.dumps([source_id, source_revision, claim, suggested_node_type.value, payload, scope, authority.value], sort_keys=True)
        candidate_id = 'cand-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        now_str = utc_now().isoformat()

        # Prepare dummy node structure for validation
        validation_payload = {
            **payload,
            "id": candidate_id,
            "node_type": suggested_node_type.value,
            "scope": scope,
            "title": payload.get("title", claim[:40]),
            "content": payload.get("content", claim),
            "maturity": payload.get("maturity", MemoryMaturity.L2_STRUCTURED.value),
            "depth": payload.get("depth", MemoryDepth.D3_FACT_RELATION.value),
            "authority": authority.value,
        }

        val_result = self.schema_registry.validate_node_data(validation_payload, source_id=source_id)
        if not val_result.is_valid:
            assert val_result.quarantine_record is not None
            record = val_result.quarantine_record
            record.id = candidate_id.replace('cand-', 'quar-', 1)
            record.scope, record.source_revision = scope, source_revision
            record.submission = {'source_id': source_id, 'source_revision': source_revision,
                'claim': claim, 'suggested_node_type': suggested_node_type.value, 'payload': payload,
                'scope': scope, 'authority': authority.value}
            self.storage.save_quarantine(record, conn=conn)
            return False, val_result.quarantine_record.id, val_result.error_message

        # Persist valid candidate
        with (self.storage.session() if conn is None else nullcontext(conn)) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO candidates (
                    id, source_id, source_revision, claim, suggested_node_type,
                    scope, authority, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    candidate_id,
                    source_id,
                    source_revision,
                    claim,
                    suggested_node_type.value,
                    scope,
                    authority.value,
                    CandidateLifecycleStatus.VALIDATED.value,
                    json.dumps(payload),
                    now_str,
                    now_str,
                ),
            )

        return True, candidate_id, None

    def get_candidate(self, candidate_id: str) -> Optional[CandidateRecord]:
        """Fetch candidate by ID."""
        with self.storage.session() as conn:
            row = conn.execute("SELECT * FROM candidates WHERE id = ?;", (candidate_id,)).fetchone()
            if not row:
                return None
            return CandidateRecord(
                id=row["id"],
                source_id=row["source_id"],
                source_revision=row["source_revision"],
                claim=row["claim"],
                suggested_node_type=NodeType(row["suggested_node_type"]),
                scope=row["scope"],
                authority=MemoryAuthority(row["authority"]),
                status=CandidateLifecycleStatus(row["status"]),
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_candidates(
        self,
        status: Optional[CandidateLifecycleStatus] = None,
        scope: Optional[str] = None,
        limit: int = 50,
        source_id: Optional[str] = None,
    ) -> List[CandidateRecord]:
        """List candidates with optional status filter."""
        query = "SELECT * FROM candidates WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if scope:
            query += " AND scope = ?"
            params.append(scope)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)

        with self.storage.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                CandidateRecord(
                    id=r["id"],
                    source_id=r["source_id"],
                    source_revision=r["source_revision"],
                    claim=r["claim"],
                    suggested_node_type=NodeType(r["suggested_node_type"]),
                    scope=r["scope"],
                    authority=MemoryAuthority(r["authority"]),
                    status=CandidateLifecycleStatus(r["status"]),
                    payload=json.loads(r["payload_json"]),
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    def accept_candidate(
        self,
        candidate_id: str,
        reviewer: str = "user",
        override_maturity: Optional[MemoryMaturity] = None,
        actor_type: str = "user",
        approval_ticket: Optional[str] = None,
    ) -> GraphNode:
        """
        Promote candidate to the Memory Plane as a canonical GraphNode.
        Atomic transaction:
        1. Insert canonical GraphNode.
        2. Insert DERIVED_FROM edge to source note/event.
        3. Mark candidate as ACCEPTED.
        4. Emit outbox event.
        """
        from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
        import hashlib

        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Read after acquiring the write lock: concurrent retries serialize.
            row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
            if not row:
                raise LookupError(f"Candidate {candidate_id} not found")
            from backend.auth.policy import current_access
            from backend.auth.approvals import ApprovalTickets, hash_payload
            context = current_access.get()
            approvals, receipt = None, None
            if context is not None:
                if context.scope != row['scope'] or not context.write:
                    raise PermissionError('Candidate acceptance outside authorized scope')
                if row['authority'] != 'INFERRED' or override_maturity is not None:
                    raise PermissionError('Agent cannot approve elevated authority or maturity')
                if not approval_ticket:
                    raise PermissionError('A local-owner approval ticket is required for formal memory acceptance')
                approvals = ApprovalTickets(self.storage, initialize=False)
                receipt = approvals.consume(conn, approval_ticket, principal=context.principal,
                    scope=context.scope, action='accept_candidate', target_id=candidate_id,
                    target_revision=row['source_revision'],
                    payload_hash=hash_payload(self.approval_payload(self.get_candidate(candidate_id))),
                    operation_id='accept:' + candidate_id)
                reviewer, actor_type = context.principal, 'agent'
            if row['status'] == 'ACCEPTED':
                event = conn.execute(
                    "SELECT payload_json FROM transactional_outbox WHERE aggregate_id = ? AND event_type = 'CANDIDATE_ACCEPTED' ORDER BY created_at LIMIT 1",
                    (candidate_id,),
                ).fetchone()
                if event:
                    promoted_id = json.loads(event['payload_json'])['promoted_node_id']
                    existing = conn.execute('SELECT data_json FROM nodes WHERE id = ?', (promoted_id,)).fetchone()
                    if existing:
                        recovered = GraphNode.model_validate_json(existing['data_json'])
                        if recovered.scope != row['scope'] or not recovered.is_active:
                            raise LookupError('Accepted memory is no longer available in this scope')
                        if approvals and not receipt['replayed']:
                            approvals.complete(conn, approval_ticket, operation_id='accept:' + candidate_id,
                                result=recovered.model_dump(mode='json'))
                        conn.execute('COMMIT')
                        return recovered
                raise ValueError('Accepted candidate has no recoverable promotion record; review required')
            if row['status'] not in ('VALIDATED', 'UNDER_REVIEW', 'PENDING'):
                raise ValueError(f"Cannot accept candidate in {row['status']} state")
            from backend.conflicts.conflict_manager import has_open_candidate_conflict
            if has_open_candidate_conflict(conn, candidate_id, row['scope']):
                raise ValueError('Candidate has an unresolved conflict; review the conflict before acceptance')
            if override_maturity not in (None, MemoryMaturity.L2_STRUCTURED):
                raise ValueError('Acceptance is not evidence verification; maturity must remain L2')
            source = conn.execute(
                'SELECT * FROM notes WHERE id = ? AND revision = ?',
                (row['source_id'], row['source_revision']),
            ).fetchone()
            if not source:
                raise ValueError('Source revision is not available; import the source before accepting')
            if source['scope'] != row['scope']:
                raise ValueError('Candidate and source scopes do not match')
            content_hash = hashlib.sha256(source['content'].encode('utf-8')).hexdigest()
            if content_hash != source['content_hash']:
                raise ValueError('Source revision failed its integrity check')

            payload = json.loads(row['payload_json'])
            if payload.get('content_hash') and payload['content_hash'] != content_hash:
                raise ValueError('Candidate source hash does not match its referenced revision')
            now_str = utc_now().isoformat()
            provenance = Provenance(
                source_id=row['source_id'], source_revision=row['source_revision'],
                content_hash=content_hash, author_or_actor=reviewer,
                extractor_version=payload.get('extractor_version', 'manual-candidate-v1'),
            )
            # A Source node identifies an immutable Notes revision. It does not
            # replace the original text or certify any assertion it contains.
            source_node_id = 'src-' + hashlib.sha256(
                f"{row['source_id']}:{row['source_revision']}".encode()
            ).hexdigest()[:24]
            if not conn.execute('SELECT 1 FROM nodes WHERE id = ?', (source_node_id,)).fetchone():
                source_node = GraphNode(
                    id=source_node_id, node_type=NodeType.SOURCE,
                    scope=row['scope'], title=f"{source['title']} · v{source['revision']}",
                    content='', maturity=MemoryMaturity.L0_RAW,
                    depth=MemoryDepth.D4_EVIDENCE_RAW, authority=MemoryAuthority.OBSERVED,
                    provenance=provenance,
                    source_props={'note_id': row['source_id'], 'revision': row['source_revision']},
                )
                self.storage.upsert_node(source_node, conn=conn)
            node = GraphNode(
                id=f"mem-{uuid.uuid4().hex[:10]}", node_type=NodeType(row['suggested_node_type']),
                scope=row['scope'], title=payload.get('title', row['claim'][:50]),
                content=payload.get('content', row['claim']),
                maturity=MemoryMaturity.L2_STRUCTURED, depth=payload.get('depth', MemoryDepth.D3_FACT_RELATION),
                authority=MemoryAuthority(row['authority']), confidence=payload.get('confidence', 0.5),
                importance=payload.get('importance', 0.5),
                core_props=payload.get('core_props', {}), source_props=payload.get('source_props', {}),
                graph_props=payload.get('graph_props', {}), memory_props=payload.get('memory_props', {}),
                temporal_props=payload.get('temporal_props', {}),
                provenance=provenance, custom_props=payload.get('custom_props', {}),
                verification_props={'status': 'UNVERIFIED', 'reviewer': reviewer, 'candidate_id': candidate_id},
            )
            # Recheck the stored candidate under current domain rules as well as
            # the canonical store's shared invariants. Failure rolls back Source.
            node = self.schema_registry.require_node(node.model_dump(mode='python'))
            self.storage.upsert_node(node, conn=conn)
            self.storage.upsert_edge(GraphEdge(
                id=f"edge-{uuid.uuid4().hex[:10]}", source_id=node.id,
                target_id=source_node_id, relation=RelationshipType.DERIVED_FROM,
                scope=row['scope'], authority=MemoryAuthority.OBSERVED,
                properties={'source_revision': row['source_revision']},
            ), conn=conn)
            conn.execute('UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?',
                         ('ACCEPTED', now_str, candidate_id))
            self.storage._insert_outbox_event(
                conn, aggregate_type='CANDIDATE', aggregate_id=candidate_id,
                event_type='CANDIDATE_ACCEPTED',
                payload={'candidate_id': candidate_id, 'promoted_node_id': node.id},
            )
            MemoryLogsheet(self.storage).append_entry(
                operation_id=candidate_id, event_type=LogsheetEventType.MEMORY_CHANGED,
                actor={'type': actor_type, 'id': reviewer}, scope=row['scope'],
                target_refs=[{'id': node.id, 'after_revision': node.version}],
                reason='Candidate accepted by reviewer; evidence verification remains separate',
                result='ACCEPTED', evidence_refs=[provenance.model_dump(mode='json')],
                idempotency_key=f'candidate:{candidate_id}:accept', conn=conn,
            )
            if approvals and not receipt['replayed']:
                approvals.complete(conn, approval_ticket, operation_id='accept:' + candidate_id,
                    result=node.model_dump(mode='json'))
            conn.execute('COMMIT;')
            return node

    def reject_candidate(self, candidate_id: str, reason: str = "", reviewer: str = "user", actor_type: str = "user") -> None:
        """Record rejection without erasing its evidence or changing terminal acceptance."""
        from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE;')
            row = conn.execute('SELECT * FROM candidates WHERE id = ?', (candidate_id,)).fetchone()
            if not row:
                raise LookupError(f'Candidate {candidate_id} not found')
            if row['status'] == 'REJECTED':
                return
            if row['status'] == 'ACCEPTED':
                raise ValueError('Accepted memory requires a separate correction, not candidate rejection')
            conn.execute('UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?',
                         ('REJECTED', utc_now().isoformat(), candidate_id))
            MemoryLogsheet(self.storage).append_entry(
                operation_id=candidate_id, event_type=LogsheetEventType.NO_CHANGE,
                actor={'type': actor_type, 'id': reviewer}, scope=row['scope'],
                target_refs=[{'id': candidate_id, 'type': 'candidate'}],
                reason=reason or 'Candidate rejected by reviewer', result='REJECTED',
                evidence_refs=[{'source_id': row['source_id'], 'source_revision': row['source_revision']}],
                idempotency_key=f'candidate:{candidate_id}:reject', conn=conn,
            )
            self.storage._insert_outbox_event(
                conn, aggregate_type='CANDIDATE', aggregate_id=candidate_id,
                event_type='CANDIDATE_REJECTED', payload={'candidate_id': candidate_id, 'reason': reason},
            )
            conn.execute('COMMIT;')
