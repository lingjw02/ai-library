"""
Unit & Integration tests for Prompt Cache Hit optimization and Token reduction.
Ref: Schemes 2 & 3.
- Scheme 2: ContextArtifact Lean Mode (removes triple-duplicated context strings, saving ~50-60% tokens).
- Scheme 3: Frozen Prefix & Deterministic Cache Layout (evidence before query, byte-deterministic output).
"""
import json
import pytest
import tempfile
from pathlib import Path

from backend.context.context_compiler import ContextCompiler, ContextArtifact
from backend.core.evidence.evidence_packet import EvidencePacketBuilder
from backend.graph.nodes.typed_nodes import NodeFactory
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.adapters.mcp.stdio_server import build_service


def test_context_artifact_lean_mode_reduces_payload_tokens():
    """
    Scheme 2 Verification:
    Verify to_lean_dict() removes triple-duplicated strings and volatile timestamps,
    achieving >= 45% reduction in character/token payload.
    """
    n1 = NodeFactory.create_decision(
        title="SQLite WAL Mode",
        content="Must use WAL mode in SQLite for concurrency and ACID transactions across threads.",
        rationale="Prevents SQLite write contention under multithreaded readers.",
        node_id="dec-wal-1",
    )
    n2 = NodeFactory.create_fact(
        title="Vector Dimension",
        content="Default vector dimension is 1536 normalized with cosine similarity metric.",
        node_id="fact-vec-1",
    )
    n3 = NodeFactory.create_entity(
        title="Canonical Architecture",
        content="SQLite stores all canonical graph nodes, edges, logsheets, and audit records.",
        node_id="ent-arch-1",
    )

    packet = EvidencePacketBuilder.build("How is storage and concurrency configured?", [n1, n2, n3])

    # 1. Non-lean artifact
    full_art = ContextCompiler.compile(packet, token_budget=4096, lean=False)
    full_dict = full_art.model_dump(mode="json")

    # Verify non-lean contains all duplicated fields
    assert "stable_context" in full_dict and len(full_dict["stable_context"]) > 0
    assert "dynamic_context" in full_dict and len(full_dict["dynamic_context"]) > 0
    assert "combined_prompt" in full_dict and len(full_dict["combined_prompt"]) > 0
    assert "created_at" in full_dict and full_dict["created_at"] is not None

    # 2. Lean artifact
    lean_art = ContextCompiler.compile(packet, token_budget=4096, lean=True)
    lean_dict = lean_art.to_lean_dict()

    # Verify lean strips duplicated context and volatile timestamps
    assert "stable_context" not in lean_dict
    assert "dynamic_context" not in lean_dict
    assert "created_at" not in lean_dict
    assert lean_dict.get("lean") is True
    assert "combined_prompt" in lean_dict

    full_json = json.dumps(full_dict, ensure_ascii=False)
    lean_json = json.dumps(lean_dict, ensure_ascii=False)

    savings_ratio = 1.0 - (len(lean_json) / len(full_json))
    # Must save at least 45% in JSON payload size
    assert savings_ratio >= 0.45, f"Expected >= 45% savings, got {savings_ratio:.2%}"
    assert "SQLite WAL Mode" in lean_dict["combined_prompt"]
    assert "Vector Dimension" in lean_dict["combined_prompt"]


def test_frozen_prefix_layout_produces_a_deterministic_shared_prefix():
    """
    Determinism check only; this does not claim an external provider cache hit.
    """
    n1 = NodeFactory.create_decision(
        title="Primary Storage Engine",
        content="SQLite WAL mode is the exclusive canonical storage engine.",
        rationale="Embedded, zero network latency, durable ACID.",
        node_id="dec-db-engine",
    )
    n2 = NodeFactory.create_fact(
        title="Schema Migration",
        content="All schema migrations must execute within atomic transactions.",
        node_id="fact-migration",
    )

    packet_q1 = EvidencePacketBuilder.build("What database engine do we use?", [n1, n2])
    packet_q2 = EvidencePacketBuilder.build("How are transactions and migrations run?", [n1, n2])

    # 1. Cache-friendly layout (Default)
    art1_cached = ContextCompiler.compile(packet_q1, token_budget=4096, cache_prefix_layout=True)
    art2_cached = ContextCompiler.compile(packet_q2, token_budget=4096, cache_prefix_layout=True)

    # Find common prefix length
    p1 = art1_cached.combined_prompt
    p2 = art2_cached.combined_prompt

    common_len = 0
    for c1, c2 in zip(p1, p2):
        if c1 == c2:
            common_len += 1
        else:
            break

    # The entire System Directive AND the Evidence section must be identical!
    common_prefix = p1[:common_len]
    assert "SYSTEM DIRECTIVE:" in common_prefix
    assert "Primary Storage Engine" in common_prefix
    # The common prefix extends right up to the query label!
    assert common_prefix.endswith("QUERY: ")
    # The specific query text is strictly after the shared cache prefix
    assert "What database engine" not in common_prefix
    assert "How are transactions" not in common_prefix

    # Suffix contains the specific query
    assert p1.endswith("QUERY: What database engine do we use?")
    assert p2.endswith("QUERY: How are transactions and migrations run?")

    # Common prefix must be > 80% of the total prompt length!
    prefix_ratio = common_len / len(p1)
    assert prefix_ratio >= 0.80, f"Expected >= 80% prefix cache sharing, got {prefix_ratio:.2%}"

    # 2. Compare with Legacy layout (cache_prefix_layout=False)
    art1_legacy = ContextCompiler.compile(packet_q1, token_budget=4096, cache_prefix_layout=False)
    art2_legacy = ContextCompiler.compile(packet_q2, token_budget=4096, cache_prefix_layout=False)

    legacy_common_len = 0
    for c1, c2 in zip(art1_legacy.combined_prompt, art2_legacy.combined_prompt):
        if c1 == c2:
            legacy_common_len += 1
        else:
            break

    # In legacy layout, divergence happens immediately at QUERY (before evidence is seen)
    legacy_divergence_text = art1_legacy.combined_prompt[:legacy_common_len]
    assert "Primary Storage Engine" not in legacy_divergence_text, "Legacy layout unexpectedly cached evidence"
    assert legacy_common_len < common_len


def test_deterministic_mcp_output_and_no_fabricated_provider_metrics():
    """
    Stable serialization is measured locally; a local cache hit is not provider telemetry.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp_path = Path(tmpdir)
        service = build_service(tmp_path, 'default', False, 'cache-test-agent')

        # Add a note
        service.notes_mgr.create_note("Concurrency Architecture", "WAL mode handles multi-process concurrency cleanly.")

        # Call search_knowledge_context twice with lean=True
        res1 = service.execute_tool('search_knowledge_context', {'query': 'Concurrency', 'lean': True})
        res2 = service.execute_tool('search_knowledge_context', {'query': 'Concurrency', 'lean': True})

        assert not res1.get('isError')
        assert not res2.get('isError')

        json_str1 = res1['content'][0]['text']
        json_str2 = res2['content'][0]['text']

        # Exact byte-for-byte match: guaranteed prompt cache stability!
        assert json_str1 == json_str2

        data = json.loads(json_str1)
        assert data.get('lean') is True
        assert 'Concurrency Architecture' in data['combined_prompt']
        assert 'stable_context' not in data
        assert 'created_at' not in data
        assert 'as_of' not in data.get('retrieval', {})
        metrics=service.token_accountant.get_cache_metrics()
        assert metrics['cache_hit_queries']==0
        assert metrics['cache_prefix_queries']==0
        assert metrics['avg_latency_saved_ms']==0


def test_token_budget_truncation_under_both_layouts():
    """
    Verify strict token budgeting and [CONTEXT_TRUNCATED] marker under both layouts.
    """
    n1 = NodeFactory.create_decision(
        title="Long Decision Title",
        content="Detailed content " * 40,
        rationale="Detailed rationale for test decision",
        node_id="dec-long-1",
    )
    packet = EvidencePacketBuilder.build("Budget test", [n1])

    # 1. Cache prefix layout with budget that truncates evidence but fits query + marker
    art_cached = ContextCompiler.compile(packet, token_budget=120, cache_prefix_layout=True)
    assert art_cached.is_truncated is True
    assert "[CONTEXT_TRUNCATED" in art_cached.combined_prompt
    assert "QUERY: Budget test" in art_cached.combined_prompt

    # 2. Extreme low budget (fallback to marker alone)
    art_tight = ContextCompiler.compile(packet, token_budget=60, cache_prefix_layout=True)
    assert art_tight.is_truncated is True
    assert art_tight.combined_prompt == "[CONTEXT_TRUNCATED]"

    # 3. Legacy layout under moderate budget
    art_legacy = ContextCompiler.compile(packet, token_budget=120, cache_prefix_layout=False)
    assert art_legacy.is_truncated is True
    assert "[CONTEXT_TRUNCATED" in art_legacy.combined_prompt
    assert "QUERY: Budget test" in art_legacy.combined_prompt
