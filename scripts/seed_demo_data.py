"""
Seed realistic demo data into AI Knowledge OS for the Web UI.
Populates Notes Plane, Memory Plane (nodes & edges), Backlog, and Logsheet.
"""

import os
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.candidates.candidate_bridge import CandidateBridge
from backend.maintenance.backlog import MemoryBacklog, BacklogType, BacklogStatus, BacklogPriority, ApprovalStatus
from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
from backend.schema.models import (
    GraphNode,
    GraphEdge,
    NodeType,
    RelationshipType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
)


def seed():
    os.makedirs("data/raw_sources", exist_ok=True)
    storage = CanonicalStorage(db_path="data/knowledge_os.db")
    raw_store = RawSourceStore(base_dir="data/raw_sources")
    notes_mgr = NotesManager(storage=storage, raw_store=raw_store)
    bridge = CandidateBridge(storage=storage)
    backlog = MemoryBacklog(storage=storage)
    logsheet = MemoryLogsheet(storage=storage)

    print("Seeding Notes Plane...")
    n1 = notes_mgr.create_note(
        title="Local-First AI Knowledge Engine Architecture",
        content="""# Local-First Architecture
The AI Knowledge OS adopts a local-first philosophy. All canonical state is held in a transactional SQLite database configured with Write-Ahead Logging (WAL) and strict ACID transactional guarantees.

## Key Principles:
1. Canonical single source of truth in SQLite.
2. Raw input files stored immutably with SHA-256 content hashes.
3. Linear token accounting to prevent run-away inference costs.
4. Model-agnostic design with zero vendor lock-in.""",
        scope="default",
        author="operator",
    )

    n2 = notes_mgr.create_note(
        title="Bounded BFS Traversal & Anti-Loop Safeguards",
        content="""# Graph Traversal Strategy
When assembling context for agent queries, graph expansion must be strictly bounded to prevent infinite recursion and context window overflow.

## Enforced Limits:
- Maximum traversal depth: 3 hops.
- Maximum expanded nodes: 20 nodes.
- Visited set tracking for deterministic cycle prevention.""",
        scope="default",
        author="operator",
    )

    n3 = notes_mgr.create_note(
        title="Candidate Bridge & Cognitive Quarantine",
        content="""# Cognitive Quarantine
LLM-generated inferences and external claims cannot be written directly into the authoritative Memory Plane.
Instead, they pass into the Candidate Bridge for validation. Malformed or schema-conflicting claims are routed safely to Quarantine without crashing the engine.""",
        scope="default",
        author="operator",
    )

    print("Seeding Memory Plane...")
    nodes = [
        GraphNode(
            id="mem_local_first",
            node_type=NodeType.CONCEPT,
            title="Local-First Architecture",
            content="Local-first data ownership with transactional SQLite WAL canonical storage.",
            scope="default",
            maturity=MemoryMaturity.L5_CANONICAL,
            depth=MemoryDepth.D1_TOPIC,
            authority=MemoryAuthority.EXPLICIT,
            confidence=0.99,
        ),
        GraphNode(
            id="mem_immutable_raw",
            node_type=NodeType.FACT,
            title="Immutable Raw Store",
            content="Raw documents and user revisions are stored immutably with SHA-256 manifests.",
            scope="default",
            maturity=MemoryMaturity.L4_CONSOLIDATED,
            depth=MemoryDepth.D3_FACT_RELATION,
            authority=MemoryAuthority.EXPLICIT,
            confidence=0.98,
        ),
        GraphNode(
            id="mem_bounded_bfs",
            node_type=NodeType.STRATEGY,
            title="Bounded BFS Traversal",
            content="Context expansion constrained to depth <= 3 and max nodes <= 20 with cycle detection.",
            scope="default",
            maturity=MemoryMaturity.L3_VERIFIED,
            depth=MemoryDepth.D2_ENTITY_CONCEPT,
            authority=MemoryAuthority.EXPLICIT,
            confidence=0.95,
        ),
        GraphNode(
            id="mem_candidate_bridge",
            node_type=NodeType.CONCEPT,
            title="Candidate Bridge Intake",
            content="Buffer plane mediating between raw notes and memory plane with human-in-the-loop review.",
            scope="default",
            maturity=MemoryMaturity.L3_VERIFIED,
            depth=MemoryDepth.D2_ENTITY_CONCEPT,
            authority=MemoryAuthority.OBSERVED,
            confidence=0.92,
        ),
        GraphNode(
            id="mem_quarantine",
            node_type=NodeType.CONFLICT,
            title="Cognitive Quarantine",
            content="Isolated dead-letter and quarantine storage for unverified or conflicting claims.",
            scope="default",
            maturity=MemoryMaturity.L2_STRUCTURED,
            depth=MemoryDepth.D3_FACT_RELATION,
            authority=MemoryAuthority.OBSERVED,
            confidence=0.88,
        ),
        GraphNode(
            id="mem_token_accountant",
            node_type=NodeType.DECISION,
            title="Linear Token Accounting",
            content="Strict daily token budgets with category isolation and quota tripwires.",
            scope="default",
            maturity=MemoryMaturity.L4_CONSOLIDATED,
            depth=MemoryDepth.D2_ENTITY_CONCEPT,
            authority=MemoryAuthority.EXPLICIT,
            confidence=0.97,
        ),
    ]

    for n in nodes:
        storage.upsert_node(n)

    edges = [
        GraphEdge(
            id="edge_1",
            source_id="mem_local_first",
            target_id="mem_immutable_raw",
            relation=RelationshipType.PART_OF,
            scope="default",
        ),
        GraphEdge(
            id="edge_2",
            source_id="mem_local_first",
            target_id="mem_bounded_bfs",
            relation=RelationshipType.RELATED_TO,
            scope="default",
        ),
        GraphEdge(
            id="edge_3",
            source_id="mem_candidate_bridge",
            target_id="mem_local_first",
            relation=RelationshipType.RELATED_TO,
            scope="default",
        ),
        GraphEdge(
            id="edge_4",
            source_id="mem_candidate_bridge",
            target_id="mem_quarantine",
            relation=RelationshipType.CONTRADICTS,
            scope="default",
        ),
        GraphEdge(
            id="edge_5",
            source_id="mem_token_accountant",
            target_id="mem_local_first",
            relation=RelationshipType.APPLIES_TO,
            scope="default",
        ),
    ]

    for e in edges:
        storage.upsert_edge(e)

    print("Seeding Candidate Bridge...")
    bridge.submit_candidate(
        source_id=n1.id,
        source_revision=1,
        claim="SQLite WAL mode provides ACID durability without remote database dependencies.",
        suggested_node_type=NodeType.FACT,
        payload={"topic": "SQLite", "mode": "WAL"},
        scope="default",
    )

    print("Seeding System Memory Backlog...")
    b1 = backlog.capture_item(
        item_type=BacklogType.REFRESH_STALE,
        scope="default",
        targets=[{"node_id": "mem_immutable_raw"}],
        reason="Verify SHA-256 manifests across raw files against database records",
        priority=BacklogPriority.HIGH,
        priority_reason="Data integrity check",
    )
    b2 = backlog.capture_item(
        item_type=BacklogType.RESOLVE_ENTITY,
        scope="default",
        targets=[{"node_id": "mem_quarantine"}],
        reason="Reconcile quarantine contradiction with verified axiom",
        priority=BacklogPriority.NORMAL,
        priority_reason="Graph consistency",
    )
    b3 = backlog.capture_item(
        item_type=BacklogType.RESOLVE_CONFLICT,
        scope="default",
        targets=[{"node_id": "mem_token_accountant"}],
        reason="Resolve token quota policy override for external agent harness",
        priority=BacklogPriority.CRITICAL,
        priority_reason="Agent execution unblocker",
        blocker="Awaiting human confirmation of daily token cap",
    )
    # Transition b2 to RESOLVED
    backlog.transition_status(
        item_id=b2.id,
        target_status=BacklogStatus.RESOLVED,
        reason="Resolved by operator",
        resolution={"note": "Linked conflict node to quarantine registry", "resolved_by": "operator"},
    )
    # Transition b3 to BLOCKED
    backlog.transition_status(
        item_id=b3.id,
        target_status=BacklogStatus.BLOCKED,
        reason="Needs human approval",
        blocker="Awaiting human confirmation of daily token cap",
    )

    print("Seeding System Memory Logsheet...")
    logsheet.append_entry(
        operation_id="op_init_bootstrap",
        event_type=LogsheetEventType.MEMORY_CHANGED,
        actor={"role": "system", "name": "EngineBootstrap"},
        scope="default",
        target_refs=[{"scope": "default"}],
        reason="Initialized canonical SQLite database and verified schema tables",
        result="SUCCESS",
    )
    logsheet.append_entry(
        operation_id="op_candidate_accept",
        event_type=LogsheetEventType.ITEM_RESOLVED,
        actor={"role": "operator", "name": "LeadArchitect"},
        scope="default",
        target_refs=[{"node_id": "mem_local_first"}],
        reason="Promoted Local-First Architecture to L5 Canonical Maturity",
        result="SUCCESS",
        backlog_id=b2.id,
    )

    print("Demo data seeded successfully!")


if __name__ == "__main__":
    seed()
