"""
Tests for Batch 1: Schema Registry, Quarantine, Canonical Storage, Raw Source Store, and Outbox.
Ref: Acceptance Criteria §70.
"""

import tempfile
import pytest
from pathlib import Path
from backend.schema.models import (
    NodeType,
    RelationshipType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
    ConflictType,
    GraphNode,
    GraphEdge,
)
from backend.schema.registry import SchemaRegistry
from backend.graph.nodes.typed_nodes import NodeFactory
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.queue.dead_letter.dlq import DeadLetterQueue


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_file = Path(tmpdir) / "test_os.db"
        storage = CanonicalStorage(db_path=str(db_file))
        yield storage
        storage.close()


@pytest.fixture
def temp_raw_store():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        yield RawSourceStore(base_dir=tmpdir)


def test_schema_valid_node_creation():
    """Verify strictly typed node factory and schema model."""
    node = NodeFactory.create_decision(
        title="Use SQLite for MVP",
        content="Project uses SQLite for the canonical store",
        rationale="Transactional ACID, zero extra daemons, WAL mode support",
        scope="project:ai-knowledge-os",
    )
    assert node.node_type == NodeType.DECISION
    assert node.maturity == MemoryMaturity.L5_CANONICAL
    assert node.authority == MemoryAuthority.EXPLICIT
    assert node.memory_props["rationale"] == "Transactional ACID, zero extra daemons, WAL mode support"


def test_schema_conflict_does_not_crash_and_routes_to_quarantine():
    """
    Acceptance Gate §70: Schema conflict does not crash system; bad data enters Quarantine.
    """
    registry = SchemaRegistry()
    # Bad payload missing required field or wrong types
    bad_payload = {
        "id": "bad-node-1",
        "node_type": "InvalidTypeThatDoesNotExist",
        "confidence": "not-a-number",
    }
    result = registry.validate_node_data(bad_payload, source_id="test-source")
    assert not result.is_valid
    assert result.quarantine_record is not None
    assert result.quarantine_record.conflict_type == ConflictType.SCHEMA_CONFLICT
    assert "bad-node-1" in result.quarantine_record.payload["id"]


def test_canonical_storage_node_crud_and_ordering(temp_storage: CanonicalStorage):
    """
    Verify SQLite canonical storage node CRUD, version increment, and independent ASC/DESC ordering (§10.3).
    """
    n1 = NodeFactory.create_fact(
        title="Fact 1",
        content="First fact",
        maturity=MemoryMaturity.L1_INDEXED,
        depth=MemoryDepth.D3_FACT_RELATION,
        node_id="fact-001",
    )
    n2 = NodeFactory.create_fact(
        title="Fact 2",
        content="Second fact",
        maturity=MemoryMaturity.L5_CANONICAL,
        depth=MemoryDepth.D1_TOPIC,
        node_id="fact-002",
    )

    temp_storage.upsert_node(n1)
    temp_storage.upsert_node(n2)

    # Fetch n1
    fetched1 = temp_storage.get_node("fact-001")
    assert fetched1 is not None
    assert fetched1.title == "Fact 1"

    # Update n1 and check version
    n1_updated = fetched1.model_copy()
    n1_updated.content = "First fact updated"
    temp_storage.upsert_node(n1_updated)

    refetched1 = temp_storage.get_node("fact-001")
    assert refetched1.version == 2
    assert refetched1.content == "First fact updated"

    # Test ASC vs DESC orderings (§10.3)
    asc_maturity = temp_storage.list_nodes(order_by="maturity ASC")
    assert asc_maturity[0].maturity == MemoryMaturity.L1_INDEXED

    desc_maturity = temp_storage.list_nodes(order_by="maturity DESC")
    assert desc_maturity[0].maturity == MemoryMaturity.L5_CANONICAL


def test_canonical_storage_edge_operations(temp_storage: CanonicalStorage):
    """Verify edge persistence and adjacent traversal querying (§7, §11)."""
    n1 = NodeFactory.create_entity(title="AI Library", content="The main repo", node_id="ent-1")
    n2 = NodeFactory.create_decision(
        title="Decision 1",
        content="Use Python",
        rationale="Ecosystem support",
        node_id="dec-1",
    )
    temp_storage.upsert_node(n1)
    temp_storage.upsert_node(n2)

    edge = GraphEdge(
        id="edge-1",
        source_id="dec-1",
        target_id="ent-1",
        relation=RelationshipType.APPLIES_TO,
    )
    temp_storage.upsert_edge(edge)

    edges_out = temp_storage.get_edges_for_node("dec-1", direction="out")
    assert len(edges_out) == 1
    assert edges_out[0].relation == RelationshipType.APPLIES_TO
    assert edges_out[0].target_id == "ent-1"


def test_raw_source_store_immutability_and_hash(temp_raw_store: RawSourceStore):
    """
    Acceptance Gate §11, §69: Raw Source Store preserves immutable revisions and verifies hashes.
    """
    manifest1 = temp_raw_store.store_source("doc-01", "# Revision 1\nInitial content.")
    assert manifest1["revision"] == 1
    assert manifest1["content_hash"] is not None

    manifest2 = temp_raw_store.store_source("doc-01", "# Revision 2\nUpdated content.")
    assert manifest2["revision"] == 2

    # Both revisions are retrievable and unchanged
    rev1 = temp_raw_store.get_source("doc-01", revision=1)
    rev2 = temp_raw_store.get_source("doc-01", revision=2)

    assert "Initial content" in rev1["content"]
    assert "Updated content" in rev2["content"]


def test_transactional_outbox_flow(temp_storage: CanonicalStorage):
    """Verify outbox captures events atomically with storage changes (§18, §86)."""
    node = NodeFactory.create_entity(title="Outbox Test", content="Content", node_id="ent-outbox")
    temp_storage.upsert_node(node, emit_outbox=True)

    pending = temp_storage.fetch_pending_outbox_events()
    assert len(pending) >= 1
    assert pending[0]["aggregate_id"] == "ent-outbox"

    temp_storage.mark_outbox_dispatched([pending[0]["id"]])
    assert len(temp_storage.fetch_pending_outbox_events()) == 0


def test_dead_letter_queue_push_and_resolution(temp_storage: CanonicalStorage):
    """Verify DLQ captures errors without crashing (§17)."""
    dlq = DeadLetterQueue(temp_storage)
    dlq_id = dlq.push(
        job_type="EVOLUTION_CONSOLIDATION",
        payload={"task_id": "task-99"},
        error_message="External token limit exhausted",
        retry_count=3,
    )
    unresolved = dlq.list_unresolved()
    assert len(unresolved) == 1
    assert unresolved[0]["id"] == dlq_id

    dlq.mark_resolved(dlq_id)
    assert len(dlq.list_unresolved()) == 0
