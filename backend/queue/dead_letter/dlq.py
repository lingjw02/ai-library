"""
Dead Letter Queue (DLQ) implementation.
Ref: AI Knowledge OS Backend Specification §§17, 70, 89.2.
Guarantees failed background tasks and exhausted retries are safely preserved
without infinite loops or unbounded LLM invocations.
"""

from __future__ import annotations
import uuid
import json
from typing import Any, Dict, List, Optional
from datetime import datetime
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.schema.models import utc_now


class DeadLetterQueue:
    """
    DLQ for capturing tasks that exceeded retry thresholds or encountered unrecoverable errors.
    """

    def __init__(self, storage: CanonicalStorage):
        self.storage = storage

    def push(
        self,
        job_type: str,
        payload: Dict[str, Any],
        error_message: str,
        retry_count: int = 3,
    ) -> str:
        """Enqueue an item into the Dead Letter Queue."""
        dlq_id = f"dlq-{uuid.uuid4().hex[:12]}"
        now_str = utc_now().isoformat()
        with self.storage.session() as conn:
            conn.execute(
                """
                INSERT INTO dead_letter_queue (
                    id, job_type, payload_json, error_message, retry_count, resolved, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?);
                """,
                (
                    dlq_id,
                    job_type,
                    json.dumps(payload),
                    error_message,
                    retry_count,
                    now_str,
                ),
            )
        return dlq_id

    def list_unresolved(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List unresolved dead letter entries."""
        with self.storage.session() as conn:
            rows = conn.execute(
                "SELECT * FROM dead_letter_queue WHERE resolved = 0 ORDER BY created_at DESC LIMIT ?;",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "job_type": r["job_type"],
                    "payload": json.loads(r["payload_json"]),
                    "error_message": r["error_message"],
                    "retry_count": r["retry_count"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def mark_resolved(self, dlq_id: str) -> None:
        """Mark an item in DLQ as resolved."""
        with self.storage.session() as conn:
            conn.execute(
                "UPDATE dead_letter_queue SET resolved = 1 WHERE id = ?;",
                (dlq_id,),
            )
