"""
System Memory Backlog Implementation.
Ref: AI Knowledge OS Backend Specification §§33, 59, 60, 63, 78, 89.
Dedicated specifically to System Memory maintenance: verification, clarification,
conflict resolution, stale refresh, entity resolution, and memory consolidation.
"""

from __future__ import annotations
import uuid
import json
import hashlib
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.schema.models import utc_now
from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
from backend.notes.notes_manager import RevisionConflict


class BacklogType(str, Enum):
    """The 7 official Backlog item types from §89.3."""
    VERIFY_CLAIM = "VERIFY_CLAIM"
    CLARIFY_USER_MEMORY = "CLARIFY_USER_MEMORY"
    RESOLVE_CONFLICT = "RESOLVE_CONFLICT"
    REFRESH_STALE = "REFRESH_STALE"
    RESOLVE_ENTITY = "RESOLVE_ENTITY"
    CONSOLIDATE_MEMORY = "CONSOLIDATE_MEMORY"
    DISTILL_EXPERIENCE = "DISTILL_EXPERIENCE"


class BacklogStatus(str, Enum):
    """Backlog lifecycle states from §89.5."""
    OPEN = "OPEN"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    BLOCKED = "BLOCKED"
    DEFERRED = "DEFERRED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(str, Enum):
    """Independent approval dimension from §89.5."""
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BacklogPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class BacklogItem:
    def __init__(
        self,
        id: str,
        type: BacklogType,
        status: BacklogStatus,
        scope: str,
        targets: List[Dict[str, Any]],
        reason: str,
        source_refs: List[Dict[str, Any]],
        created_by: str,
        priority: BacklogPriority,
        priority_reason: str,
        approval_status: ApprovalStatus,
        blocker: Optional[str] = None,
        dedup_key: str = "",
        dependencies: Optional[List[str]] = None,
        job_refs: Optional[List[str]] = None,
        resolution: Optional[Dict[str, Any]] = None,
        revision: int = 1,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        self.id = id
        self.type = type
        self.status = status
        self.scope = scope
        self.targets = targets
        self.reason = reason
        self.source_refs = source_refs
        self.created_by = created_by
        self.priority = priority
        self.priority_reason = priority_reason
        self.approval_status = approval_status
        self.blocker = blocker
        self.dedup_key = dedup_key
        self.dependencies = dependencies or []
        self.job_refs = job_refs or []
        self.resolution = resolution
        self.revision = revision
        self.created_at = created_at or utc_now().isoformat()
        self.updated_at = updated_at or utc_now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "status": self.status.value,
            "scope": self.scope,
            "targets": self.targets,
            "reason": self.reason,
            "source_refs": self.source_refs,
            "created_by": self.created_by,
            "priority": self.priority.value,
            "priority_reason": self.priority_reason,
            "approval_status": self.approval_status.value,
            "blocker": self.blocker,
            "dedup_key": self.dedup_key,
            "dependencies": self.dependencies,
            "job_refs": self.job_refs,
            "resolution": self.resolution,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MemoryBacklog:
    """
    Manages the System Memory Backlog.
    Guarantees:
    - Only System Memory maintenance issues enter this backlog (§89.1).
    - Capturing does not alter canonical memory or elevate authority (§89.4).
    - Repeated signals are deduplicated; no duplicate open items (§89.6).
    """

    def __init__(self, storage: CanonicalStorage):
        self.storage = storage

    def compute_dedup_key(self, scope: str, item_type: BacklogType, targets: List[Dict[str, Any]]) -> str:
        """Compute unique deterministic deduplication key for scope + type + targets."""
        sorted_targets = sorted([f"{t.get('id') or t.get('node_id') or str(t)}" for t in targets])
        key_raw = f"{scope}:{item_type.value}:{','.join(sorted_targets)}"
        return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:24]

    def capture_item(
        self,
        item_type: BacklogType,
        scope: str,
        targets: List[Dict[str, Any]],
        reason: str,
        created_by: str = "system",
        priority: BacklogPriority = BacklogPriority.NORMAL,
        priority_reason: str = "Standard maintenance triage",
        source_refs: Optional[List[Dict[str, Any]]] = None,
        approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED,
        blocker: Optional[str] = None,
    ) -> BacklogItem:
        """
        Capture or retrieve existing open backlog item.
        Deduplicates by (scope, item_type, targets) so duplicate open items are not created (§89.4, §89.6).
        """
        dedup_key = self.compute_dedup_key(scope, item_type, targets)
        now_str = utc_now().isoformat()

        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Check if an active (non-terminal) item with this dedup key already exists
            existing_row = conn.execute(
                """
                SELECT * FROM system_memory_backlog
                WHERE (dedup_key = ? OR dedup_key LIKE ?) AND status NOT IN ('RESOLVED', 'CANCELLED');
                """,
                (dedup_key, dedup_key + ":%"),
            ).fetchone()

            if existing_row:
                # Return existing item without creating duplicate
                return self._row_to_item(existing_row)

            if conn.execute('SELECT 1 FROM system_memory_backlog WHERE dedup_key = ?', (dedup_key,)).fetchone():
                dedup_key += ':' + uuid.uuid4().hex[:10]
            # Insert new backlog item
            item_id = f"MB-{uuid.uuid4().hex[:8].upper()}"
            status = BacklogStatus.BLOCKED if blocker else BacklogStatus.OPEN

            conn.execute(
                """
                INSERT INTO system_memory_backlog (
                    id, type, status, scope, targets_json, reason, source_refs_json,
                    created_by, priority, priority_reason, approval_status, blocker,
                    dedup_key, dependencies_json, job_refs_json, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', 1, ?, ?);
                """,
                (
                    item_id,
                    item_type.value,
                    status.value,
                    scope,
                    json.dumps(targets),
                    reason,
                    json.dumps(source_refs or []),
                    created_by,
                    priority.value,
                    priority_reason,
                    approval_status.value,
                    blocker,
                    dedup_key,
                    now_str,
                    now_str,
                ),
            )
            # Emit outbox event
            self.storage._insert_outbox_event(
                conn,
                aggregate_type="BACKLOG_ITEM",
                aggregate_id=item_id,
                event_type="BACKLOG_ITEM_CREATED",
                payload={"backlog_id": item_id, "type": item_type.value, "scope": scope},
            )
            MemoryLogsheet(self.storage).append_entry(
                operation_id=f'{item_id}:1', event_type=LogsheetEventType.ITEM_CREATED,
                actor={'id': created_by}, scope=scope, target_refs=targets,
                reason=reason, result=status.value, evidence_refs=source_refs or [],
                backlog_id=item_id, idempotency_key=f'backlog:{item_id}:1', conn=conn,
            )
            conn.execute("COMMIT;")

        return BacklogItem(
            id=item_id,
            type=item_type,
            status=status,
            scope=scope,
            targets=targets,
            reason=reason,
            source_refs=source_refs or [],
            created_by=created_by,
            priority=priority,
            priority_reason=priority_reason,
            approval_status=approval_status,
            blocker=blocker,
            dedup_key=dedup_key,
            created_at=now_str,
            updated_at=now_str,
        )

    def get_item(self, item_id: str) -> Optional[BacklogItem]:
        """Fetch backlog item by ID."""
        with self.storage.session() as conn:
            row = conn.execute(
                "SELECT * FROM system_memory_backlog WHERE id = ?;",
                (item_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    def list_items(
        self,
        scope: Optional[str] = None,
        status: Optional[BacklogStatus] = None,
        item_type: Optional[BacklogType] = None,
        limit: int = 50,
        order_by: str = "priority DESC",
    ) -> List[BacklogItem]:
        """List backlog items with flexible filtering."""
        query = "SELECT * FROM system_memory_backlog WHERE 1=1"
        params: List[Any] = []
        if scope:
            query += " AND scope = ?"
            params.append(scope)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if item_type:
            query += " AND type = ?"
            params.append(item_type.value)

        allowed_orders = {
            "priority DESC": "CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, created_at ASC",
            "created_at ASC": "created_at ASC, id ASC",
            "updated_at DESC": "updated_at DESC, id ASC",
        }
        order_clause = allowed_orders.get(order_by, "created_at ASC, id ASC")
        query += f" ORDER BY {order_clause} LIMIT ?;"
        params.append(limit)

        with self.storage.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_item(r) for r in rows]

    def transition_status(
        self,
        item_id: str,
        target_status: BacklogStatus,
        reason: str = "",
        blocker: Optional[str] = None,
        resolution: Optional[Dict[str, Any]] = None,
        expected_revision: Optional[int] = None,
        actor: str = "user",
    ) -> BacklogItem:
        """
        Transition backlog item lifecycle (§89.5).
        Enforces:
        - RESOLVED requires resolution data.
        - BLOCKED records blocker reason.
        - Increments revision count for tracking.
        """
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE;')
            row = conn.execute('SELECT * FROM system_memory_backlog WHERE id = ?', (item_id,)).fetchone()
            if not row:
                raise ValueError(f'Backlog item {item_id} not found')
            item = self._row_to_item(row)
            if expected_revision is not None and expected_revision != item.revision:
                raise RevisionConflict(f'Backlog changed to revision {item.revision}; refresh before editing')
            if target_status == BacklogStatus.RESOLVED and not resolution:
                raise ValueError('Resolution requires a description of the actual verification performed')
            if target_status == BacklogStatus.BLOCKED and not blocker:
                raise ValueError('Blocked state requires a blocker reason')
            now_str = utc_now().isoformat()
            new_rev = item.revision + 1
            conn.execute(
                """
                UPDATE system_memory_backlog SET
                    status = ?,
                    blocker = ?,
                    resolution_json = ?,
                    revision = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    target_status.value,
                    blocker if target_status == BacklogStatus.BLOCKED else None,
                    json.dumps(resolution) if resolution else None,
                    new_rev,
                    now_str,
                    item_id,
                ),
            )
            self.storage._insert_outbox_event(
                conn,
                aggregate_type="BACKLOG_ITEM",
                aggregate_id=item_id,
                event_type="BACKLOG_STATE_CHANGED",
                payload={"backlog_id": item_id, "status": target_status.value, "reason": reason},
            )
            event_type = {
                BacklogStatus.RESOLVED: LogsheetEventType.ITEM_RESOLVED,
                BacklogStatus.CANCELLED: LogsheetEventType.ITEM_CANCELLED,
                BacklogStatus.DEFERRED: LogsheetEventType.ITEM_DEFERRED,
            }.get(target_status, LogsheetEventType.STATE_CHANGED)
            MemoryLogsheet(self.storage).append_entry(
                operation_id=f'{item_id}:{new_rev}', event_type=event_type,
                actor={'id': actor}, scope=item.scope,
                target_refs=[*item.targets, {'id': item_id, 'type': 'backlog', 'before_revision': item.revision, 'after_revision': new_rev}],
                reason=reason or f'{item.status.value} -> {target_status.value}',
                result=target_status.value, evidence_refs=[resolution] if resolution else [],
                backlog_id=item_id, idempotency_key=f'backlog:{item_id}:{new_rev}', conn=conn,
            )
            conn.execute("COMMIT;")

        item.status = target_status
        item.blocker = blocker
        item.resolution = resolution
        item.revision = new_rev
        item.updated_at = now_str
        return item

    def _row_to_item(self, row: Any) -> BacklogItem:
        return BacklogItem(
            id=row["id"],
            type=BacklogType(row["type"]),
            status=BacklogStatus(row["status"]),
            scope=row["scope"],
            targets=json.loads(row["targets_json"]),
            reason=row["reason"],
            source_refs=json.loads(row["source_refs_json"]),
            created_by=row["created_by"],
            priority=BacklogPriority(row["priority"]),
            priority_reason=row["priority_reason"],
            approval_status=ApprovalStatus(row["approval_status"]),
            blocker=row["blocker"],
            dedup_key=row["dedup_key"],
            dependencies=json.loads(row["dependencies_json"]),
            job_refs=json.loads(row["job_refs_json"]),
            resolution=json.loads(row["resolution_json"]) if row["resolution_json"] else None,
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
