"""
Token Accounting & Budgeting Telemetry.
Ref: AI Knowledge OS Backend Specification §§36, 37, 38, 45, 46, 48.
Strict Linear Token Model:
T_total = T_read + T_write + T_distill + T_overhead
Tracks budget consumption, prevents runaway LLM loops, and raises quota alerts.
"""

from __future__ import annotations
import math
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from datetime import date
from pydantic import BaseModel, Field, ConfigDict
from backend.schema.models import utc_now

if TYPE_CHECKING:
    from backend.storage.canonical.sqlite_storage import CanonicalStorage


class TokenRecord(BaseModel):
    operation: str
    tokens_consumed: int
    category: str = "read"  # "read", "write", "distill", "overhead"
    scope: str = "default"
    actor: str = "system"
    timestamp: str = Field(default_factory=lambda: utc_now().isoformat())

    model_config = ConfigDict(extra="ignore")


class DailyTokenBudget(BaseModel):
    daily_quota: int = 100_000  # Personal budget recommendation (§46)
    tokens_used: int = 0
    records: List[TokenRecord] = Field(default_factory=list)
    day: str = Field(default_factory=lambda: date.today().isoformat())

    model_config = ConfigDict(extra="ignore")


class CacheTelemetryRecord(BaseModel):
    operation: str
    raw_tokens: int
    lean_tokens: int
    tokens_saved: int
    is_lean: bool = True
    has_cache_prefix: bool = True
    is_cache_hit: bool = False
    latency_saved_ms: float = 0.0
    timestamp: str = Field(default_factory=lambda: utc_now().isoformat())

    model_config = ConfigDict(extra="ignore")


class TokenAccountant:
    """
    Enforces strict token limits across operations and tracks Prompt Cache / Lean savings.
    Guarantee (§48): No single background operation or query can consume unbounded tokens.
    """

    def __init__(self, daily_quota: int = 100_000, storage: Optional[CanonicalStorage] = None):
        self.daily_quota = daily_quota
        self.storage = storage
        self.budgets: Dict[str, DailyTokenBudget] = {}
        self._memory_cache_records: List[CacheTelemetryRecord] = []
        if self.storage is not None:
            self._ensure_cache_telemetry_table()

    @contextmanager
    def _system_session(self):
        """Context manager to ensure internal telemetry writes bypass agent connection guards."""
        if self.storage is None:
            yield None
            return
        token = None
        try:
            from backend.auth.policy import current_access
            token = current_access.set(None)
        except Exception:
            pass
        try:
            with self.storage.session() as conn:
                yield conn
        finally:
            if token is not None:
                try:
                    from backend.auth.policy import current_access
                    current_access.reset(token)
                except Exception:
                    pass

    def _ensure_cache_telemetry_table(self) -> None:
        if self.storage is None:
            return
        with self._system_session() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    raw_tokens INTEGER NOT NULL,
                    lean_tokens INTEGER NOT NULL,
                    tokens_saved INTEGER NOT NULL,
                    is_lean INTEGER NOT NULL DEFAULT 1,
                    has_cache_prefix INTEGER NOT NULL DEFAULT 1,
                    is_cache_hit INTEGER NOT NULL DEFAULT 0,
                    latency_saved_ms REAL NOT NULL DEFAULT 0.0,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_telemetry_time ON cache_telemetry(timestamp)")

    def _get_current_budget(self) -> DailyTokenBudget:
        today_str = date.today().isoformat()
        if today_str not in self.budgets:
            self.budgets[today_str] = DailyTokenBudget(daily_quota=self.daily_quota, day=today_str)
        return self.budgets[today_str]

    def record_usage(
        self,
        operation: str,
        tokens: int,
        category: str = "read",
        scope: str = "default",
        actor: str = "system",
    ) -> bool:
        """
        Record token consumption. Returns True if within quota, False if budget exceeded.
        """
        budget = self._get_current_budget()
        record = TokenRecord(
            operation=operation,
            tokens_consumed=tokens,
            category=category,
            scope=scope,
            actor=actor,
        )
        budget.records.append(record)
        budget.tokens_used += tokens
        return budget.tokens_used <= budget.daily_quota

    def record_cache_optimization(
        self,
        operation: str,
        raw_tokens: int,
        lean_tokens: int,
        is_lean: bool = True,
        has_cache_prefix: bool = True,
        is_cache_hit: bool = False,
        latency_saved_ms: float = 0.0,
    ) -> None:
        """
        Record Prompt Cache and Lean serialization savings.
        """
        tokens_saved = max(0, raw_tokens - lean_tokens) + (lean_tokens if is_cache_hit else 0)
        record = CacheTelemetryRecord(
            operation=operation,
            raw_tokens=raw_tokens,
            lean_tokens=lean_tokens,
            tokens_saved=tokens_saved,
            is_lean=is_lean,
            has_cache_prefix=has_cache_prefix,
            is_cache_hit=is_cache_hit,
            latency_saved_ms=latency_saved_ms,
        )
        if self.storage is not None:
            with self._system_session() as conn:
                conn.execute(
                    """
                    INSERT INTO cache_telemetry
                    (operation, raw_tokens, lean_tokens, tokens_saved, is_lean, has_cache_prefix, is_cache_hit, latency_saved_ms, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.operation,
                        record.raw_tokens,
                        record.lean_tokens,
                        record.tokens_saved,
                        1 if record.is_lean else 0,
                        1 if record.has_cache_prefix else 0,
                        1 if record.is_cache_hit else 0,
                        record.latency_saved_ms,
                        record.timestamp,
                    ),
                )
        else:
            self._memory_cache_records.append(record)

    def get_cache_metrics(self) -> Dict[str, Any]:
        """
        Aggregate real-time cache and token savings metrics.
        """
        if self.storage is not None:
            with self._system_session() as conn:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total_queries,
                        SUM(CASE WHEN is_lean = 1 THEN 1 ELSE 0 END) as lean_queries,
                        SUM(CASE WHEN has_cache_prefix = 1 THEN 1 ELSE 0 END) as cache_prefix_queries,
                        SUM(CASE WHEN is_cache_hit = 1 THEN 1 ELSE 0 END) as cache_hit_queries,
                        COALESCE(SUM(raw_tokens), 0) as total_raw_tokens,
                        COALESCE(SUM(lean_tokens), 0) as total_lean_tokens,
                        COALESCE(SUM(tokens_saved), 0) as tokens_saved_estimate,
                        COALESCE(SUM(latency_saved_ms), 0.0) as total_latency_saved_ms
                    FROM cache_telemetry
                """).fetchone()
                total = row["total_queries"] or 0
                lean_count = row["lean_queries"] or 0
                prefix_count = row["cache_prefix_queries"] or 0
                hits = row["cache_hit_queries"] or 0
                raw_tok = row["total_raw_tokens"] or 0
                lean_tok = row["total_lean_tokens"] or 0
                saved_tok = row["tokens_saved_estimate"] or 0
                lat_saved = row["total_latency_saved_ms"] or 0.0
        else:
            total = len(self._memory_cache_records)
            lean_count = sum(1 for r in self._memory_cache_records if r.is_lean)
            prefix_count = sum(1 for r in self._memory_cache_records if r.has_cache_prefix)
            hits = sum(1 for r in self._memory_cache_records if r.is_cache_hit)
            raw_tok = sum(r.raw_tokens for r in self._memory_cache_records)
            lean_tok = sum(r.lean_tokens for r in self._memory_cache_records)
            saved_tok = sum(r.tokens_saved for r in self._memory_cache_records)
            lat_saved = sum(r.latency_saved_ms for r in self._memory_cache_records)

        hit_rate = round((hits / total) * 100, 1) if total > 0 else 0.0
        # No measured tokens means no claimed reduction; never substitute a demo estimate.
        reduction_rate = round((saved_tok / raw_tok) * 100, 1) if raw_tok > 0 else 0.0
        avg_latency = round(lat_saved / total, 1) if total > 0 else 0.0

        return {
            "total_mcp_queries": total,
            "lean_queries": lean_count,
            "cache_prefix_queries": prefix_count,
            "cache_hit_queries": hits,
            "cache_hit_rate": hit_rate,
            "tokens_saved_estimate": saved_tok,
            "total_raw_tokens": raw_tok,
            "total_lean_tokens": lean_tok,
            "token_reduction_rate": reduction_rate,
            "avg_latency_saved_ms": avg_latency,
            "daily_budget_remaining": self.get_remaining_budget(),
        }

    def get_remaining_budget(self) -> int:
        budget = self._get_current_budget()
        return max(0, budget.daily_quota - budget.tokens_used)

    def is_quota_exceeded(self) -> bool:
        budget = self._get_current_budget()
        return budget.tokens_used >= budget.daily_quota
