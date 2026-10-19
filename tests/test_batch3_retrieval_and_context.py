"""
Tests for Batch 3: Routing, BM25, RRF, Bounded Traversal, Context Compiler, and Caching.
Ref: Acceptance Criteria §§21, 23, 70, 83, 84, 85.
"""

import tempfile
import pytest
from pathlib import Path
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.routing.router import SourceRouter, QueryRouter, SearchTargetPlane, QueryIntent
from backend.core.retrieval.bm25 import BM25Scorer
from backend.core.retrieval.rrf import ReciprocalRankFusion
from backend.graph.traversal.bounded_traversal import BoundedGraphTraversal
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.context.context_compiler import ContextCompiler
from backend.core.evidence.evidence_packet import EvidencePacketBuilder
from backend.cache.cache_manager import CacheManager
from backend.graph.nodes.typed_nodes import NodeFactory
from backend.schema.models import GraphEdge, RelationshipType


@pytest.fixture
def retrieval_env():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_file = Path(tmpdir) / "test_os.db"
        raw_dir = Path(tmpdir) / "raw_sources"
        storage = CanonicalStorage(db_path=str(db_file))
        raw_store = RawSourceStore(base_dir=str(raw_dir))
        notes_mgr = NotesManager(storage=storage, raw_store=raw_store)
        cache_mgr = CacheManager()
        coordinator = RetrievalCoordinator(storage=storage, notes_mgr=notes_mgr, cache_mgr=cache_mgr)
        yield {
            "storage": storage,
            "raw_store": raw_store,
            "notes": notes_mgr,
            "cache": cache_mgr,
            "coordinator": coordinator,
        }
        storage.close()


def test_source_and_query_routers():
    """Verify routing decisions according to query intent and source indications (§83.1, §83.2)."""
    # Notes router
    s_route1 = SourceRouter.route("What did I write in my notes yesterday?")
    assert SearchTargetPlane.NOTES_PLANE in s_route1.target_planes

    # Memory router
    s_route2 = SourceRouter.route("What is our approved architecture decision?")
    assert SearchTargetPlane.MEMORY_PLANE in s_route2.target_planes

    # Query router entity fast path
    q_route1 = QueryRouter.route("Check status of node:dec-1002")
    assert q_route1.intent == QueryIntent.ENTITY_FAST_PATH

    # Query router relationship traversal
    q_route2 = QueryRouter.route("What depends on or is connected to SQLite?")
    assert q_route2.intent == QueryIntent.RELATION_MULTI_HOP
    assert q_route2.use_graph_expansion is True


def test_bm25_and_rrf_fusion():
    """Verify BM25 scoring and deterministic RRF ranking fusion (§83.4)."""
    corpus = {
        "doc1": "Python is used for backend development with FastAPI",
        "doc2": "React is used for building modern frontend web interfaces",
        "doc3": "SQLite provides lightweight ACID transactions for local data",
    }
    bm25 = BM25Scorer()
    bm25.fit(corpus)
    scores = bm25.score("Python backend")
    assert len(scores) > 0
    assert scores[0][0] == "doc1"

    # RRF fusion
    rrf = ReciprocalRankFusion(k=60)
    list1 = ["doc1", "doc3", "doc2"]
    list2 = ["doc3", "doc1", "doc2"]
    fused = rrf.fuse([list1, list2])
    # Both doc1 and doc3 have top ranks (rank 1 and 2), doc2 is 3rd in both
    fused_ids = [doc_id for doc_id, _ in fused]
    assert "doc2" == fused_ids[-1]


def test_bounded_graph_traversal_hard_limits(retrieval_env):
    """
    Acceptance Gate §21, §70, §83.5:
    Graph traversal has hard limits (depth <= 3, nodes <= 20) and avoids infinite loops.
    """
    storage = retrieval_env["storage"]

    # Create a cyclic chain of 5 nodes: n1 -> n2 -> n3 -> n4 -> n5 -> n1
    nodes = [
        NodeFactory.create_entity(title=f"Entity {i}", content=f"Entity content {i}", node_id=f"node-{i}")
        for i in range(1, 6)
    ]
    for n in nodes:
        storage.upsert_node(n)

    for i in range(1, 5):
        storage.upsert_edge(
            GraphEdge(
                id=f"e-{i}",
                source_id=f"node-{i}",
                target_id=f"node-{i+1}",
                relation=RelationshipType.RELATED_TO,
            )
        )
    # Loop back
    storage.upsert_edge(
        GraphEdge(
            id="e-loop",
            source_id="node-5",
            target_id="node-1",
            relation=RelationshipType.RELATED_TO,
        )
    )

    traversal = BoundedGraphTraversal(storage=storage, hard_max_depth=3, hard_max_nodes=10)
    result = traversal.traverse(seed_node_ids=["node-1"])

    assert len(result.nodes) <= 10
    assert result.depth_reached <= 3
    # Did not hang or explode on cyclic loop
    assert result.stop_reason in ("sufficient", "no_new_evidence", "budget_exhausted")


def test_context_compiler_deterministic_hash_and_token_cap():
    """
    Acceptance Gate §23, §70, §85:
    Context compiler produces deterministic serialization, stable prefix, and enforces hard token cap.
    """
    n1 = NodeFactory.create_decision(
        title="Dec 1",
        content="Must use WAL mode in SQLite for concurrency",
        rationale="ACID safety",
        node_id="dec-wal",
    )
    n2 = NodeFactory.create_fact(
        title="Fact 1",
        content="Antigravity provides intelligent AI pair programming",
        node_id="fact-agy",
    )

    packet = EvidencePacketBuilder.build("How should storage be configured?", [n1, n2])

    # 1. Deterministic output
    art1 = ContextCompiler.compile(packet, token_budget=4096)
    art2 = ContextCompiler.compile(packet, token_budget=4096)
    assert art1.context_hash == art2.context_hash
    assert art1.combined_prompt == art2.combined_prompt

    # 2. Strict token cap enforcement
    small_budget_art = ContextCompiler.compile(packet, token_budget=60)
    assert small_budget_art.is_truncated is True
    assert "[CONTEXT_TRUNCATED" in small_budget_art.combined_prompt


def test_cache_scope_invalidation(retrieval_env):
    """
    Acceptance Gate §84, §86:
    Cache invalidation removes target scope entries without disturbing other scopes.
    """
    cache = retrieval_env["cache"]

    cache.put_retrieval("q1", ["item-1"], scope="scope:A")
    cache.put_retrieval("q2", ["item-2"], scope="scope:B")

    assert cache.get_retrieval("q1") == ["item-1"]
    assert cache.get_retrieval("q2") == ["item-2"]

    # Invalidate scope A
    cache.invalidate_scope("scope:A")

    assert cache.get_retrieval("q1") is None
    assert cache.get_retrieval("q2") == ["item-2"]
