"""
System Memory Logsheet Implementation.
Ref: AI Knowledge OS Backend Specification §§90, 89.1, 89.2.
Append-only operational ledger for System Memory.
Captures what actually happened, why, and with what result.
"""

from __future__ import annotations
import uuid
import json
import sqlite3
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.schema.models import utc_now


class LogsheetEventType(str, Enum):
    """The official Logsheet events from §90.3."""
    ITEM_CREATED = "ITEM_CREATED"
    STATE_CHANGED = "STATE_CHANGED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    APPROVAL_RECORDED = "APPROVAL_RECORDED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    VERIFICATION_RECORDED = "VERIFICATION_RECORDED"
    MEMORY_CHANGED = "MEMORY_CHANGED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    ATTEMPT_INTERRUPTED = "ATTEMPT_INTERRUPTED"
    NO_CHANGE = "NO_CHANGE"
    ITEM_RESOLVED = "ITEM_RESOLVED"
    ITEM_DEFERRED = "ITEM_DEFERRED"
    ITEM_CANCELLED = "ITEM_CANCELLED"
    ITEM_REOPENED = "ITEM_REOPENED"


class LogsheetEntry:
    def __init__(
        self,
        entry_id: str,
        operation_id: str,
        event_type: LogsheetEventType,
        actor: Dict[str, Any],
        scope: str,
        target_refs: List[Dict[str, Any]],
        reason: str,
        result: str,
        evidence_refs: List[Dict[str, Any]],
        backlog_id: Optional[str] = None,
        policy_revision: Optional[str] = None,
        approval_ref: Optional[str] = None,
        job_ref: Optional[str] = None,
        error_code: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        occurred_at: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ):
        self.entry_id = entry_id
        self.operation_id = operation_id
        self.event_type = event_type
        self.actor = actor
        self.scope = scope
        self.target_refs = target_refs
        self.reason = reason
        self.result = result
        self.evidence_refs = evidence_refs
        self.backlog_id = backlog_id
        self.policy_revision = policy_revision
        self.approval_ref = approval_ref
        self.job_ref = job_ref
        self.error_code = error_code
        self.idempotency_key = idempotency_key
        self.occurred_at = occurred_at or utc_now().isoformat()
        self.recorded_at = recorded_at or utc_now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "operation_id": self.operation_id,
            "backlog_id": self.backlog_id,
            "event_type": self.event_type.value,
            "actor": self.actor,
            "scope": self.scope,
            "target_refs": self.target_refs,
            "reason": self.reason,
            "result": self.result,
            "evidence_refs": self.evidence_refs,
            "policy_revision": self.policy_revision,
            "approval_ref": self.approval_ref,
            "job_ref": self.job_ref,
            "error_code": self.error_code,
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
        }


class MemoryLogsheet:
    """
    Manages the System Memory Logsheet append-only ledger (§90).
    Guarantees:
    - Never silently rolls back or deletes legitimate audit entries.
    - Idempotency key prevents duplicate events on retry.
    - Supports atomic commits with canonical memory transactions.
    """

    def __init__(self, storage: CanonicalStorage):
        self.storage = storage

    def append_entry(
        self,
        operation_id: str,
        event_type: LogsheetEventType,
        actor: Dict[str, Any],
        scope: str,
        target_refs: List[Dict[str, Any]],
        reason: str,
        result: str,
        evidence_refs: Optional[List[Dict[str, Any]]] = None,
        backlog_id: Optional[str] = None,
        policy_revision: Optional[str] = None,
        approval_ref: Optional[str] = None,
        job_ref: Optional[str] = None,
        error_code: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        occurred_at: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> LogsheetEntry:
        """
        Append an immutable logsheet entry.
        If `conn` is provided, executes inside the caller's active transaction.
        """
        entry_id = f"ML-{uuid.uuid4().hex[:8].upper()}"
        occ_str = occurred_at or utc_now().isoformat()
        rec_str = utc_now().isoformat()

        entry = LogsheetEntry(
            entry_id=entry_id,
            operation_id=operation_id,
            event_type=event_type,
            actor=actor,
            scope=scope,
            target_refs=target_refs,
            reason=reason,
            result=result,
            evidence_refs=evidence_refs or [],
            backlog_id=backlog_id,
            policy_revision=policy_revision,
            approval_ref=approval_ref,
            job_ref=job_ref,
            error_code=error_code,
            idempotency_key=idempotency_key,
            occurred_at=occ_str,
            recorded_at=rec_str,
        )

        owns_conn = conn is None
        c = conn or self.storage.get_connection()
        try:
            if owns_conn:
                c.execute("BEGIN IMMEDIATE;")

            c.execute(
                """
                INSERT INTO system_memory_logsheet (
                    entry_id, operation_id, backlog_id, event_type, actor_json, scope,
                    target_refs_json, reason, result, evidence_refs_json, policy_revision,
                    approval_ref, job_ref, error_code, idempotency_key, occurred_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING;
                """,
                (
                    entry.entry_id,
                    entry.operation_id,
                    entry.backlog_id,
                    entry.event_type.value,
                    json.dumps(entry.actor),
                    entry.scope,
                    json.dumps(entry.target_refs),
                    entry.reason,
                    entry.result,
                    json.dumps(entry.evidence_refs),
                    entry.policy_revision,
                    entry.approval_ref,
                    entry.job_ref,
                    entry.error_code,
                    entry.idempotency_key,
                    entry.occurred_at,
                    entry.recorded_at,
                ),
            )

            if owns_conn:
                c.execute("COMMIT;")
        except Exception:
            if owns_conn:
                c.execute("ROLLBACK;")
            raise
        finally:
            if owns_conn:
                c.close()

        return entry

    def query_entries(
        self,
        operation_id: Optional[str] = None,
        backlog_id: Optional[str] = None,
        scope: Optional[str] = None,
        event_type: Optional[LogsheetEventType] = None,
        limit: int = 50,
        order_by: str = "occurred_at DESC",
    ) -> List[LogsheetEntry]:
        """Query logsheet entries with filtering and pagination (§90.5)."""
        query = "SELECT * FROM system_memory_logsheet WHERE 1=1"
        params: List[Any] = []
        if operation_id:
            query += " AND operation_id = ?"
            params.append(operation_id)
        if backlog_id:
            query += " AND backlog_id = ?"
            params.append(backlog_id)
        if scope:
            query += " AND scope = ?"
            params.append(scope)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)

        allowed_orders = {
            "occurred_at DESC": "occurred_at DESC, entry_id DESC",
            "occurred_at ASC": "occurred_at ASC, entry_id ASC",
        }
        order_clause = allowed_orders.get(order_by, "occurred_at DESC, entry_id DESC")
        query += f" ORDER BY {order_clause} LIMIT ?;"
        params.append(limit)

        with self.storage.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def _row_to_entry(self, row: Any) -> LogsheetEntry:
        return LogsheetEntry(
            entry_id=row["entry_id"],
            operation_id=row["operation_id"],
            backlog_id=row["backlog_id"],
            event_type=LogsheetEventType(row["event_type"]),
            actor=json.loads(row["actor_json"]),
            scope=row["scope"],
            target_refs=json.loads(row["target_refs_json"]),
            reason=row["reason"],
            result=row["result"],
            evidence_refs=json.loads(row["evidence_refs_json"]),
            policy_revision=row["policy_revision"],
            approval_ref=row["approval_ref"],
            job_ref=row["job_ref"],
            error_code=row["error_code"],
            idempotency_key=row["idempotency_key"],
            occurred_at=row["occurred_at"],
            recorded_at=row["recorded_at"],
        )
