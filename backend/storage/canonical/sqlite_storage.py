"""
Canonical Structured Store implemented with transactional SQLite.
Ref: AI Knowledge OS Backend Specification §§11, 57, 58, 69, 86.
WAL mode, atomic transactions, zero data corruption across nodes, and outbox integration.
"""

from __future__ import annotations
import sqlite3
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from backend.schema.registry import global_schema_registry
from backend.schema.models import (
    GraphNode,
    GraphEdge,
    NodeType,
    RelationshipType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
    QuarantineRecord,
    ConflictType,
    utc_now,
)

logger = logging.getLogger(__name__)


class CanonicalRevisionConflict(ValueError):
    """The caller's base revision no longer matches canonical state."""


SCHEMA_INIT_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- Authoritative Canonical Nodes (§6, §11)
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    maturity TEXT NOT NULL,
    depth TEXT NOT NULL,
    authority TEXT NOT NULL,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_type_scope ON nodes(node_type, scope);
CREATE INDEX IF NOT EXISTS idx_nodes_maturity ON nodes(maturity);
CREATE INDEX IF NOT EXISTS idx_nodes_authority ON nodes(authority);
CREATE INDEX IF NOT EXISTS idx_nodes_depth ON nodes(depth);
CREATE INDEX IF NOT EXISTS idx_nodes_active ON nodes(is_active);

-- Authoritative Canonical Edges (§7, §11)
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    scope TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    authority TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);

-- Immutable snapshots. Migration captures only currently available state;
-- absent older versions must never be fabricated from that state.
CREATE TABLE IF NOT EXISTS node_revisions (
    id TEXT NOT NULL, version INTEGER NOT NULL, scope TEXT NOT NULL,
    data_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
    PRIMARY KEY(id, version)
);
CREATE TABLE IF NOT EXISTS edge_revisions (
    id TEXT NOT NULL, version INTEGER NOT NULL, scope TEXT NOT NULL,
    data_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
    PRIMARY KEY(id, version)
);
INSERT OR IGNORE INTO node_revisions
    SELECT id, version, scope, data_json, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM nodes;
INSERT OR IGNORE INTO edge_revisions
    SELECT id, COALESCE(json_extract(data_json, '$.version'), 1), scope,
        data_json, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM edges;

-- Candidate Bridge table (§5)
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL DEFAULT 1,
    claim TEXT NOT NULL,
    suggested_node_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    authority TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
CREATE INDEX IF NOT EXISTS idx_candidates_scope ON candidates(scope);

-- System Memory Backlog (§89)
CREATE TABLE IF NOT EXISTS system_memory_backlog (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    scope TEXT NOT NULL,
    targets_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    priority TEXT NOT NULL,
    priority_reason TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    blocker TEXT,
    dedup_key TEXT UNIQUE NOT NULL,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    job_refs_json TEXT NOT NULL DEFAULT '[]',
    resolution_json TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backlog_status ON system_memory_backlog(status);
CREATE INDEX IF NOT EXISTS idx_backlog_scope ON system_memory_backlog(scope);
CREATE INDEX IF NOT EXISTS idx_backlog_priority ON system_memory_backlog(priority);

-- System Memory Logsheet (§90)
CREATE TABLE IF NOT EXISTS system_memory_logsheet (
    entry_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    backlog_id TEXT,
    event_type TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    scope TEXT NOT NULL,
    target_refs_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    result TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    policy_revision TEXT,
    approval_ref TEXT,
    job_ref TEXT,
    error_code TEXT,
    idempotency_key TEXT UNIQUE,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logsheet_operation ON system_memory_logsheet(operation_id);
CREATE INDEX IF NOT EXISTS idx_logsheet_backlog ON system_memory_logsheet(backlog_id);
CREATE INDEX IF NOT EXISTS idx_logsheet_scope ON system_memory_logsheet(scope);
CREATE INDEX IF NOT EXISTS idx_logsheet_occurred ON system_memory_logsheet(occurred_at);

-- Quarantine System (§16)
CREATE TABLE IF NOT EXISTS quarantine (
    id TEXT PRIMARY KEY,
    conflict_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_id TEXT,
    payload_json TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolution_notes TEXT,
    quarantined_at TEXT NOT NULL
);

-- Transactional Outbox (§18, §57, §86)
CREATE TABLE IF NOT EXISTS transactional_outbox (
    id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    dispatched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_status ON transactional_outbox(status);

-- Durable ordering independent of timestamps and outbox delivery flags.
CREATE TABLE IF NOT EXISTS outbox_sequence (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE REFERENCES transactional_outbox(id)
);
CREATE TRIGGER IF NOT EXISTS sequence_outbox_insert
AFTER INSERT ON transactional_outbox BEGIN
    INSERT INTO outbox_sequence(event_id) VALUES (NEW.id);
END;
INSERT OR IGNORE INTO outbox_sequence(event_id)
    SELECT id FROM transactional_outbox o WHERE NOT EXISTS
        (SELECT 1 FROM outbox_sequence s WHERE s.event_id = o.id) ORDER BY o.rowid;

-- Dead Letter Queue (§17, §89)
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    error_message TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Notes Plane revisions (§5, §11)
CREATE TABLE IF NOT EXISTS notes (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    scope TEXT NOT NULL,
    author TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);

CREATE INDEX IF NOT EXISTS idx_notes_current ON notes(id, is_current);

-- universe() 的 CURRENT 视图按 (scope, is_current) 过滤 notes；
-- 原索引以 id 为前导列，对这条谓词无效，只能全表扫描。
CREATE INDEX IF NOT EXISTS idx_notes_scope_current ON notes(scope, is_current);

-- Raw Manifests (§11, §69)
CREATE TABLE IF NOT EXISTS raw_manifests (
    source_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, revision)
);

-- Capabilities (Skill and MCP registry §91)
CREATE TABLE IF NOT EXISTS capabilities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    version TEXT NOT NULL,
    origin TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status_json TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capabilities_kind ON capabilities(kind);

-- Agent Profiles (§91.5)
CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    client_kind TEXT NOT NULL,
    client_version TEXT NOT NULL,
    device_id TEXT NOT NULL,
    skill_bindings_json TEXT NOT NULL,
    mcp_bindings_json TEXT NOT NULL,
    compatibility_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Capability Audit (§91.8)
CREATE TABLE IF NOT EXISTS capability_audit (
    id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

-- Work exchange is independent of Notes, accepted Memory and maintenance tasks.
CREATE TABLE IF NOT EXISTS work_handoff_revisions (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    scope TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    origin_json TEXT,
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    PRIMARY KEY(id, revision),
    UNIQUE(scope, created_by, request_key)
);
CREATE INDEX IF NOT EXISTS idx_work_handoff_scope ON work_handoff_revisions(scope, created_at);
CREATE TABLE IF NOT EXISTS work_handoff_receipts (
    id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    scope TEXT NOT NULL,
    received_by TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE(handoff_id, revision, received_by),
    FOREIGN KEY(handoff_id, revision) REFERENCES work_handoff_revisions(id, revision)
);
"""


from contextlib import closing, contextmanager


class CanonicalStorage:
    """
    Transactional SQLite Canonical Storage.
    Provides ACID guarantees for the single source of structured truth.
    """

    def __init__(self, db_path: str = "data/knowledge_os.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Create a connection with foreign keys and WAL enabled."""
        from backend.auth.policy import GuardedConnection, current_access, check_connection
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode; we manage BEGIN/COMMIT explicitly
            factory=GuardedConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        if current_access.get() is not None:
            try:
                check_connection(conn, current_access.get())
            except Exception:
                conn.close()
                raise
        return conn

    @contextmanager
    def session(self):
        """Context manager guaranteeing connection closing even on exceptions."""
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize all schema tables and indices."""
        with closing(sqlite3.connect(str(self.db_path))) as conn, conn:
            conn.executescript(SCHEMA_INIT_SQL)
            # Preserve legacy payloads without inventing scope or source history.
            conn.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(quarantine)')}
            for name, declaration in {'scope': 'TEXT', 'source_revision': 'INTEGER',
                    'revision': 'INTEGER NOT NULL DEFAULT 1', 'submission_json': 'TEXT',
                    'result_candidate_id': 'TEXT'}.items():
                if name not in columns:
                    conn.execute(f'ALTER TABLE quarantine ADD COLUMN {name} {declaration}')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_quarantine_scope ON quarantine(scope, resolved, quarantined_at)')

    def close(self) -> None:
        """Close storage resources."""
        pass

    # ---------------------------------------------------------
    # Node Operations (§6, §10, §11, §57)
    # ---------------------------------------------------------

    def upsert_node(
        self,
        node: GraphNode,
        conn: Optional[sqlite3.Connection] = None,
        emit_outbox: bool = True,
        expected_revision: Optional[int] = None,
    ) -> None:
        """Upsert a canonical graph node. If conn is provided, executes within caller's transaction."""
        original_node = node
        node = global_schema_registry.require_node(node.model_dump(mode='python'))
        owns_conn = conn is None
        c = conn or self.get_connection()
        try:
            if owns_conn:
                c.execute("BEGIN IMMEDIATE;")

            # Check if node already exists to accurately increment version in data_json
            existing = c.execute("SELECT version, created_at FROM nodes WHERE id = ?;", (node.id,)).fetchone()
            actual = existing['version'] if existing else 0
            if expected_revision is not None and actual != expected_revision:
                raise CanonicalRevisionConflict(f'Node is at revision {actual}; refresh before writing')
            current_version = (existing["version"] + 1) if existing else node.version
            node.version = current_version
            if existing:
                node.created_at = datetime.fromisoformat(existing['created_at'])
            node.updated_at = utc_now()
            original_node.version = node.version
            original_node.created_at = node.created_at
            original_node.updated_at = node.updated_at

            data_json = node.model_dump_json()
            c.execute(
                """
                INSERT INTO nodes (
                    id, node_type, scope, title, content, maturity, depth,
                    authority, confidence, importance, version, is_active,
                    data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    node_type = excluded.node_type,
                    scope = excluded.scope,
                    title = excluded.title,
                    content = excluded.content,
                    maturity = excluded.maturity,
                    depth = excluded.depth,
                    authority = excluded.authority,
                    confidence = excluded.confidence,
                    importance = excluded.importance,
                    version = excluded.version,
                    is_active = excluded.is_active,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at;
                """,
                (
                    node.id,
                    node.node_type.value,
                    node.scope,
                    node.title,
                    node.content,
                    node.maturity.value,
                    node.depth.value,
                    node.authority.value,
                    node.confidence,
                    node.importance,
                    node.version,
                    1 if node.is_active else 0,
                    data_json,
                    node.created_at.isoformat(),
                    utc_now().isoformat(),
                ),
            )

            c.execute('INSERT INTO node_revisions(id, version, scope, data_json, recorded_at) VALUES (?, ?, ?, ?, ?)',
                      (node.id, node.version, node.scope, data_json, node.updated_at.isoformat()))

            if emit_outbox:
                self._insert_outbox_event(
                    c,
                    aggregate_type="NODE",
                    aggregate_id=node.id,
                    event_type="NODE_UPSERTED",
                    payload={"node_id": node.id, "node_type": node.node_type.value, "scope": node.scope},
                )

            if owns_conn:
                c.execute("COMMIT;")
        except Exception:
            if owns_conn and c.in_transaction:
                c.execute("ROLLBACK;")
            raise
        finally:
            if owns_conn:
                c.close()

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Fetch node by primary ID."""
        with self.session() as conn:
            row = conn.execute("SELECT data_json FROM nodes WHERE id = ?;", (node_id,)).fetchone()
            if row:
                return GraphNode.model_validate_json(row["data_json"])
            return None

    def get_node_revision(self, node_id: str, revision: int, *, scope: str) -> Optional[GraphNode]:
        with self.session() as conn:
            row = conn.execute('''SELECT r.data_json FROM node_revisions r
                JOIN nodes n ON n.id = r.id
                WHERE r.id = ? AND r.version = ? AND r.scope = ? AND n.scope = ? AND n.is_active = 1''',
                (node_id, revision, scope, scope)).fetchone()
            return GraphNode.model_validate_json(row['data_json']) if row else None

    def list_node_revisions(self, node_id: str, *, scope: str, limit: int = 100, offset: int = 0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError('Invalid revision page')
        with self.session() as conn:
            return [dict(row) for row in conn.execute('''SELECT r.version, r.recorded_at,
                json_extract(r.data_json, '$.title') AS title
                FROM node_revisions r JOIN nodes n ON n.id = r.id
                WHERE r.id = ? AND r.scope = ? AND n.scope = ? AND n.is_active = 1
                ORDER BY r.version DESC LIMIT ? OFFSET ?''', (node_id, scope, scope, limit, offset))]

    def get_edge_revision(self, edge_id: str, revision: int, *, scope: str) -> Optional[GraphEdge]:
        with self.session() as conn:
            conn.execute('BEGIN')
            row = conn.execute('''SELECT r.data_json FROM edge_revisions r JOIN edges e ON e.id = r.id
                WHERE r.id = ? AND r.version = ? AND r.scope = ? AND e.scope = ?''',
                (edge_id, revision, scope, scope)).fetchone()
            if not row:
                return None
            edge = GraphEdge.model_validate_json(row['data_json'])
            for node_id in (edge.source_id, edge.target_id):
                if not conn.execute('SELECT 1 FROM nodes WHERE id = ? AND scope = ? AND is_active = 1', (node_id, scope)).fetchone():
                    return None
            return edge

    def list_nodes(
        self,
        node_type: Optional[NodeType] = None,
        scope: Optional[str] = None,
        maturity: Optional[MemoryMaturity] = None,
        depth: Optional[MemoryDepth] = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "updated_at DESC",
    ) -> List[GraphNode]:
        """Query canonical nodes with flexible filtering and ASC/DESC ordering (§10.3)."""
        query = "SELECT data_json FROM nodes WHERE is_active = 1"
        params: List[Any] = []

        if node_type:
            query += " AND node_type = ?"
            params.append(node_type.value)
        if scope:
            query += " AND scope = ?"
            params.append(scope)
        if maturity:
            query += " AND maturity = ?"
            params.append(maturity.value)
        if depth:
            query += " AND depth = ?"
            params.append(depth.value)

        # Allow safe ordering keys
        allowed_orders = {
            "updated_at DESC": "updated_at DESC, id ASC",
            "updated_at ASC": "updated_at ASC, id ASC",
            "created_at ASC": "created_at ASC, id ASC",
            "maturity ASC": "maturity ASC, created_at ASC, id ASC",
            "maturity DESC": "maturity DESC, updated_at DESC, id ASC",
            "depth ASC": "depth ASC, id ASC",
            "depth DESC": "depth DESC, id ASC",
            "confidence DESC": "confidence DESC, id ASC",
        }
        order_clause = allowed_orders.get(order_by, "updated_at DESC, id ASC")
        query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [GraphNode.model_validate_json(r["data_json"]) for r in rows]

    # ---------------------------------------------------------
    # Edge Operations (§7, §11)
    # ---------------------------------------------------------

    def upsert_edge(
        self,
        edge: GraphEdge,
        conn: Optional[sqlite3.Connection] = None,
        emit_outbox: bool = True,
        expected_revision: Optional[int] = None,
    ) -> None:
        """Upsert a canonical graph edge."""
        original_edge = edge
        valid, edge, error = global_schema_registry.validate_edge(edge.model_dump(mode='python'))
        if not valid:
            raise ValueError(error)
        owns_conn = conn is None
        c = conn or self.get_connection()
        try:
            if owns_conn:
                c.execute("BEGIN IMMEDIATE;")

            existing = c.execute('SELECT data_json FROM edges WHERE id = ?', (edge.id,)).fetchone()
            actual = GraphEdge.model_validate_json(existing['data_json']).version if existing else 0
            if expected_revision is not None and expected_revision != actual:
                raise CanonicalRevisionConflict(f'Edge is at revision {actual}; refresh before writing')
            edge.version = actual + 1 if existing else edge.version
            original_edge.version = edge.version
            data_json = edge.model_dump_json()
            c.execute(
                """
                INSERT INTO edges (
                    id, source_id, target_id, relation, scope, weight,
                    confidence, authority, data_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_id = excluded.source_id,
                    target_id = excluded.target_id,
                    relation = excluded.relation,
                    scope = excluded.scope,
                    weight = excluded.weight,
                    confidence = excluded.confidence,
                    authority = excluded.authority,
                    data_json = excluded.data_json;
                """,
                (
                    edge.id,
                    edge.source_id,
                    edge.target_id,
                    edge.relation.value,
                    edge.scope,
                    edge.weight,
                    edge.confidence,
                    edge.authority.value,
                    data_json,
                    edge.created_at.isoformat(),
                ),
            )

            c.execute('INSERT INTO edge_revisions(id, version, scope, data_json, recorded_at) VALUES (?, ?, ?, ?, ?)',
                      (edge.id, edge.version, edge.scope, data_json, utc_now().isoformat()))

            if emit_outbox:
                self._insert_outbox_event(
                    c,
                    aggregate_type="EDGE",
                    aggregate_id=edge.id,
                    event_type="EDGE_UPSERTED",
                    payload={"edge_id": edge.id, "source": edge.source_id, "target": edge.target_id},
                )

            if owns_conn:
                c.execute("COMMIT;")
        except Exception:
            if owns_conn and c.in_transaction:
                c.execute("ROLLBACK;")
            raise
        finally:
            if owns_conn:
                c.close()

    def get_edges_for_node(
        self,
        node_id: str,
        direction: str = "both",
        relations: Optional[List[RelationshipType]] = None,
    ) -> List[GraphEdge]:
        """Fetch adjacent edges for a node within optional relationship type filters."""
        query_parts = []
        params: List[Any] = []

        if direction in ("out", "both"):
            query_parts.append("source_id = ?")
            params.append(node_id)
        if direction in ("in", "both"):
            query_parts.append("target_id = ?")
            params.append(node_id)

        where_clause = f"({' OR '.join(query_parts)})"

        if relations:
            rel_placeholders = ",".join(["?"] * len(relations))
            where_clause += f" AND relation IN ({rel_placeholders})"
            params.extend([r.value for r in relations])

        query = f"SELECT data_json FROM edges WHERE {where_clause};"
        with self.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [GraphEdge.model_validate_json(r["data_json"]) for r in rows]

    # ---------------------------------------------------------
    # Quarantine System Operations (§16)
    # ---------------------------------------------------------

    def save_quarantine(self, record: QuarantineRecord, conn: Optional[sqlite3.Connection] = None) -> None:
        """Persist a quarantined malformed or invalid item."""
        owns_conn = conn is None
        c = conn or self.get_connection()
        try:
            if owns_conn:
                c.execute("BEGIN IMMEDIATE;")
            c.execute(
                """
                INSERT OR IGNORE INTO quarantine (
                    id, conflict_type, reason, source_id, payload_json, resolved, resolution_notes, quarantined_at,
                    scope, source_revision, revision, submission_json, result_candidate_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.id,
                    record.conflict_type.value,
                    record.reason,
                    record.source_id,
                    json.dumps(record.payload),
                    1 if record.resolved else 0,
                    record.resolution_notes,
                    record.quarantined_at.isoformat(),
                    record.scope, record.source_revision, record.revision,
                    json.dumps(record.submission) if record.submission is not None else None,
                    record.result_candidate_id,
                ),
            )
            saved = c.execute('SELECT * FROM quarantine WHERE id = ?', (record.id,)).fetchone()
            if (json.loads(saved['payload_json']) != record.payload or saved['scope'] != record.scope or
                    saved['source_id'] != record.source_id or saved['source_revision'] != record.source_revision or
                    (json.loads(saved['submission_json']) if saved['submission_json'] else None) != record.submission):
                raise ValueError('Quarantine identity already belongs to different original data')
            if owns_conn:
                c.execute("COMMIT;")
        except Exception:
            if owns_conn:
                c.execute("ROLLBACK;")
            raise
        finally:
            if owns_conn:
                c.close()

    def list_quarantine(self, limit: int = 100) -> List[QuarantineRecord]:
        """Fetch unresolved quarantined entries."""
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM quarantine WHERE resolved = 0 ORDER BY quarantined_at DESC LIMIT ?;",
                (limit,),
            ).fetchall()
            results = []
            for r in rows:
                results.append(
                    self.quarantine_from_row(r)
                )
            return results

    @staticmethod
    def quarantine_from_row(row):
        data = dict(row)
        data['payload'] = json.loads(data.pop('payload_json'))
        submission = data.pop('submission_json')
        data['submission'] = json.loads(submission) if submission else None
        return QuarantineRecord.model_validate(data)

    # ---------------------------------------------------------
    # Transactional Outbox Helper (§18, §57, §86)
    # ---------------------------------------------------------

    def _insert_outbox_event(
        self,
        conn: sqlite3.Connection,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Internal insertion of outbox event within the active transaction."""
        import uuid
        event_id = f"out-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO transactional_outbox (
                id, aggregate_type, aggregate_id, event_type, payload_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?);
            """,
            (
                event_id,
                aggregate_type,
                aggregate_id,
                event_type,
                json.dumps(payload),
                utc_now().isoformat(),
            ),
        )

    def fetch_pending_outbox_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch pending outbox events for asynchronous projection workers."""
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM transactional_outbox WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT ?;",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_outbox_dispatched(self, event_ids: List[str]) -> None:
        """Mark outbox events as dispatched."""
        if not event_ids:
            return
        placeholders = ",".join(["?"] * len(event_ids))
        now_str = utc_now().isoformat()
        with self.session() as conn:
            conn.execute(
                f"UPDATE transactional_outbox SET status = 'DISPATCHED', dispatched_at = ? WHERE id IN ({placeholders});",
                [now_str] + event_ids,
            )
