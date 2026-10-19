"""Tests for caller-supplied accounting and truthful local cache telemetry."""
import pytest
from fastapi.testclient import TestClient

from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.notes.notes_manager import NotesManager
from backend.storage.raw.raw_store import RawSourceStore
from backend.candidates.candidate_bridge import CandidateBridge
from backend.maintenance.backlog import MemoryBacklog
from backend.maintenance.logsheet import MemoryLogsheet
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.adapters.mcp.knowledge_os_mcp import KnowledgeOsMcpServer
from backend.telemetry.token_accounting import TokenAccountant
from backend.adapters.api.app import create_app


def test_token_accountant_memory_metrics():
    accountant = TokenAccountant(daily_quota=50_000)
    metrics_initial = accountant.get_cache_metrics()
    assert metrics_initial["total_mcp_queries"] == 0
    assert metrics_initial["tokens_saved_estimate"] == 0
    assert metrics_initial["cache_hit_rate"] == 0.0

    accountant.record_cache_optimization(
        operation="search_knowledge_context",
        raw_tokens=1000,
        lean_tokens=450,
        is_lean=True,
        has_cache_prefix=True,
        is_cache_hit=True,
        latency_saved_ms=320.0,
    )
    metrics = accountant.get_cache_metrics()
    assert metrics["total_mcp_queries"] == 1
    assert metrics["lean_queries"] == 1
    assert metrics["cache_prefix_queries"] == 1  # Explicit values supplied to the accounting API.
    assert metrics["cache_hit_queries"] == 1
    assert metrics["cache_hit_rate"] == 100.0
    # tokens saved: raw - lean (550) + lean on cache hit (450) = 1000
    assert metrics["tokens_saved_estimate"] == 1000
    assert metrics["avg_latency_saved_ms"] == 320.0


def test_token_accountant_sqlite_persistence(tmp_path):
    storage = CanonicalStorage(str(tmp_path / "knowledge_os.db"))
    accountant = TokenAccountant(daily_quota=100_000, storage=storage)

    accountant.record_cache_optimization(
        operation="search_knowledge_context",
        raw_tokens=2000,
        lean_tokens=1000,
        is_lean=True,
        has_cache_prefix=True,
        is_cache_hit=False,
        latency_saved_ms=50.0,
    )
    accountant.record_cache_optimization(
        operation="search_knowledge_context",
        raw_tokens=2000,
        lean_tokens=1000,
        is_lean=True,
        has_cache_prefix=True,
        is_cache_hit=True,
        latency_saved_ms=320.0,
    )

    # Re-instantiate another accountant pointing to same database
    accountant2 = TokenAccountant(daily_quota=100_000, storage=storage)
    metrics = accountant2.get_cache_metrics()
    assert metrics["total_mcp_queries"] == 2
    assert metrics["lean_queries"] == 2
    assert metrics["cache_hit_queries"] == 1
    assert metrics["cache_hit_rate"] == 50.0
    assert metrics["tokens_saved_estimate"] == (1000) + (1000 + 1000)  # 3000
    assert metrics["avg_latency_saved_ms"] == round((50.0 + 320.0) / 2, 1)


def test_mcp_search_records_telemetry(tmp_path):
    db_path = str(tmp_path / "knowledge_os.db")
    storage = CanonicalStorage(db_path)
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / "raw_sources")))
    bridge = CandidateBridge(storage)
    backlog = MemoryBacklog(storage)
    logsheet = MemoryLogsheet(storage)
    coordinator = RetrievalCoordinator(storage, notes)
    accountant = TokenAccountant(storage=storage)

    mcp = KnowledgeOsMcpServer(
        storage, notes, bridge, backlog, logsheet, coordinator,
        token_accountant=accountant, scope="default"
    )

    # Execute search
    res = mcp.execute_tool("search_knowledge_context", {
        "query": "architecture",
        "lean": True,
        "cache_prefix_layout": True,
    })
    assert not res.get("isError")

    metrics = accountant.get_cache_metrics()
    assert metrics["total_mcp_queries"] == 1
    assert metrics["lean_queries"] == 1
    assert metrics["cache_prefix_queries"] == 0  # Layout is not provider cache telemetry.
    assert metrics["cache_hit_queries"] == 0
    assert metrics["avg_latency_saved_ms"] == 0
    assert metrics["tokens_saved_estimate"] >= 0


def test_api_cache_metrics_endpoint(tmp_path):
    app = create_app(
        db_path=str(tmp_path / "knowledge_os.db"),
        raw_dir=str(tmp_path / "raw_sources"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    client = TestClient(app)

    # Query initial metrics
    resp = client.get("/api/telemetry/cache-metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == {"reports": 0, "cached_tokens": None}
    assert "lookups" in data["retrieval"]
    assert "lookups" in data["context"]

    # Perform a search via API
    search_resp = client.post("/api/retrieval/search", json={
        "query": "system overview",
        "scope": "default",
        "token_budget": 2048,
    })
    assert search_resp.status_code == 200

    # Query metrics again
    resp2 = client.get("/api/telemetry/cache-metrics")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["retrieval"]["lookups"] >= 1
    assert data2["provider"] == {"reports": 0, "cached_tokens": None}
