"""
Tests for Batch 2: Notes Plane, Candidate Bridge, System Memory Backlog, and Logsheet.
Ref: Acceptance Criteria §§5, 70, 89, 90.
"""

import tempfile
import pytest
from pathlib import Path
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.candidates.candidate_bridge import CandidateBridge
from backend.maintenance.backlog import (
    MemoryBacklog,
    BacklogType,
    BacklogStatus,
    BacklogPriority,
    ApprovalStatus,
)
from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
from backend.schema.models import (
    NodeType,
    RelationshipType,
    MemoryAuthority,
    MemoryMaturity,
)


@pytest.fixture
def test_env():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_file = Path(tmpdir) / "test_os.db"
        raw_dir = Path(tmpdir) / "raw_sources"
        storage = CanonicalStorage(db_path=str(db_file))
        raw_store = RawSourceStore(base_dir=str(raw_dir))
        notes_mgr = NotesManager(storage=storage, raw_store=raw_store)
        bridge = CandidateBridge(storage=storage)
        backlog = MemoryBacklog(storage=storage)
        logsheet = MemoryLogsheet(storage=storage)
        yield {
            "storage": storage,
            "raw_store": raw_store,
            "notes": notes_mgr,
            "bridge": bridge,
            "backlog": backlog,
            "logsheet": logsheet,
        }
        storage.close()


def test_notes_plane_immutable_revisions(test_env):
    """
    Acceptance Gate §5, §11: Notes Plane revisions are immutable, historical revisions remain untouched.
    """
    notes = test_env["notes"]

    rev1 = notes.create_note(title="Arch Plan", content="Use microservices.", scope="project:alpha")
    assert rev1.revision == 1
    assert rev1.is_current is True

    rev2 = notes.revise_note(note_id=rev1.id, title="Arch Plan Updated", content="Use modular monolith instead.")
    assert rev2.revision == 2
    assert rev2.is_current is True

    # Check that revision 1 still exists exactly as it was
    historical = notes.get_note(rev1.id, revision=1)
    assert historical is not None
    assert historical.revision == 1
    assert historical.content == "Use microservices."
    assert historical.is_current is False

    current = notes.get_note(rev1.id)
    assert current is not None
    assert current.revision == 2
    assert current.content == "Use modular monolith instead."


def test_candidate_bridge_validation_and_quarantine(test_env):
    """
    Acceptance Gate §5, §16: Bad schema candidate goes to quarantine, valid candidate becomes VALIDATED.
    """
    bridge = test_env["bridge"]
    storage = test_env["storage"]

    # 1. Invalid payload
    bad_payload = {"confidence": "invalid_type_str", "title": "Bad"}
    ok, quar_id, err = bridge.submit_candidate(
        source_id="note-1",
        source_revision=1,
        claim="Bad claim",
        suggested_node_type=NodeType.DECISION,
        payload=bad_payload,
    )
    assert ok is False
    assert quar_id.startswith("quar-")
    assert err is not None

    quarantine_items = storage.list_quarantine()
    assert len(quarantine_items) == 1
    assert quarantine_items[0].id == quar_id

    # 2. Valid payload
    valid_payload = {"title": "Valid Fact", "content": "Antigravity uses Python 3.12+"}
    ok2, cand_id, err2 = bridge.submit_candidate(
        source_id="note-1",
        source_revision=1,
        claim="Antigravity uses Python 3.12+",
        suggested_node_type=NodeType.FACT,
        payload=valid_payload,
        authority=MemoryAuthority.OBSERVED,
    )
    assert ok2 is True
    assert cand_id.startswith("cand-")
    assert err2 is None

    cand = bridge.get_candidate(cand_id)
    assert cand is not None
    assert cand.claim == "Antigravity uses Python 3.12+"


def test_candidate_acceptance_promotion_to_memory_plane(test_env):
    """
    Acceptance Gate §5, §11: Accepted candidate promotes to canonical GraphNode with DERIVED_FROM edge.
    """
    bridge = test_env["bridge"]
    storage = test_env["storage"]

    valid_payload = {"title": "SQLite Selected", "content": "Project uses SQLite for Canonical store."}
    test_env['notes'].create_note(note_id='note-99', title=valid_payload['title'], content=valid_payload['content'])
    ok, cand_id, _ = bridge.submit_candidate(
        source_id="note-99",
        source_revision=1,
        claim="Project uses SQLite for Canonical store",
        suggested_node_type=NodeType.DECISION,
        payload=valid_payload,
        authority=MemoryAuthority.EXPLICIT,
    )
    assert ok is True

    promoted_node = bridge.accept_candidate(candidate_id=cand_id, reviewer="lead-architect")
    assert promoted_node.id.startswith("mem-")
    assert promoted_node.maturity == MemoryMaturity.L2_STRUCTURED
    assert len(storage.get_edges_for_node(promoted_node.id)) == 1
    assert promoted_node.provenance.source_id == "note-99"

    # Verify node exists in canonical storage
    saved_node = storage.get_node(promoted_node.id)
    assert saved_node is not None
    assert saved_node.title == "SQLite Selected"


def test_system_memory_backlog_dedup_and_lifecycle(test_env):
    """
    Acceptance Gate §89:
    - Capture does not duplicate open items with identical scope/target/type.
    - Transitions follow lifecycle with mandatory resolution payload for RESOLVED.
    """
    backlog = test_env["backlog"]

    targets = [{"id": "mem-001", "expected_revision": 1}]
    item1 = backlog.capture_item(
        item_type=BacklogType.VERIFY_CLAIM,
        scope="project:core",
        targets=targets,
        reason="Claim lacks evidence",
        priority=BacklogPriority.HIGH,
    )
    assert item1.status == BacklogStatus.OPEN

    # Duplicate signal: capture same issue again
    item2 = backlog.capture_item(
        item_type=BacklogType.VERIFY_CLAIM,
        scope="project:core",
        targets=targets,
        reason="Repeated scan saw claim lacks evidence",
    )
    assert item1.id == item2.id, "Repeated signal must deduplicate and return existing open item"

    # Transition to IN_PROGRESS
    in_prog = backlog.transition_status(item1.id, BacklogStatus.IN_PROGRESS, reason="Assigned to worker")
    assert in_prog.status == BacklogStatus.IN_PROGRESS
    assert in_prog.revision == 2

    # Transition to RESOLVED without resolution must raise ValueError
    with pytest.raises(ValueError):
        backlog.transition_status(item1.id, BacklogStatus.RESOLVED, reason="Done")

    # Transition to RESOLVED with valid resolution
    resolution = {
        "disposition": "ACCEPTED",
        "evidence_id": "evid-123",
        "verified_at": "2026-10-05T00:00:00Z",
    }
    resolved = backlog.transition_status(
        item1.id,
        BacklogStatus.RESOLVED,
        reason="Verified against test logs",
        resolution=resolution,
    )
    assert resolved.status == BacklogStatus.RESOLVED
    assert resolved.resolution["disposition"] == "ACCEPTED"


def test_system_memory_logsheet_and_idempotency(test_env):
    """
    Acceptance Gate §90:
    - Logsheet captures immutable event trail.
    - Idempotency key prevents replay duplication.
    """
    logsheet = test_env["logsheet"]

    entry = logsheet.append_entry(
        operation_id="OP-TEST-1",
        event_type=LogsheetEventType.ATTEMPT_STARTED,
        actor={"kind": "worker", "id": "verifier-1"},
        scope="project:core",
        target_refs=[{"id": "mem-001", "before_revision": 1}],
        reason="Starting verification attempt",
        result="in_progress",
        idempotency_key="idemp-key-001",
    )
    assert entry.entry_id.startswith("ML-")

    # Replay with same idempotency key
    logsheet.append_entry(
        operation_id="OP-TEST-1",
        event_type=LogsheetEventType.ATTEMPT_STARTED,
        actor={"kind": "worker", "id": "verifier-1"},
        scope="project:core",
        target_refs=[{"id": "mem-001", "before_revision": 1}],
        reason="Starting verification attempt replay",
        result="in_progress",
        idempotency_key="idemp-key-001",
    )

    entries = logsheet.query_entries(operation_id="OP-TEST-1")
    assert len(entries) == 1, "Idempotency key must prevent duplicate log entries"
