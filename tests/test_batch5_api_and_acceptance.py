"""
Tests for Batch 5: FastAPI REST API, MCP Tool Adapter, Token Accounting,
and End-to-End Acceptance Gates.
Ref: Acceptance Criteria §§60, 70, 87, 89, 90, 92.
"""

import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.adapters.api.app import create_app
from backend.adapters.mcp.knowledge_os_mcp import KnowledgeOsMcpServer
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.candidates.candidate_bridge import CandidateBridge
from backend.maintenance.backlog import MemoryBacklog
from backend.maintenance.logsheet import MemoryLogsheet
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.telemetry.token_accounting import TokenAccountant


@pytest.fixture
def api_client():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_file = Path(tmpdir) / "test_api.db"
        raw_dir = Path(tmpdir) / "raw_sources"
        art_dir = Path(tmpdir) / "artifacts"
        app = create_app(db_path=str(db_file), raw_dir=str(raw_dir), artifacts_dir=str(art_dir))
        client = TestClient(app)
        yield client


@pytest.fixture
def mcp_server():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_file = Path(tmpdir) / "test_mcp.db"
        raw_dir = Path(tmpdir) / "raw_sources"
        storage = CanonicalStorage(db_path=str(db_file))
        raw_store = RawSourceStore(base_dir=str(raw_dir))
        notes_mgr = NotesManager(storage=storage, raw_store=raw_store)
        bridge = CandidateBridge(storage=storage)
        backlog = MemoryBacklog(storage=storage)
        logsheet = MemoryLogsheet(storage=storage)
        coordinator = RetrievalCoordinator(storage=storage, notes_mgr=notes_mgr)
        server = KnowledgeOsMcpServer(
            storage=storage,
            notes_mgr=notes_mgr,
            bridge=bridge,
            backlog=backlog,
            logsheet=logsheet,
            coordinator=coordinator,
        )
        yield server
        storage.close()


def test_api_health_check(api_client):
    res = api_client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["version"] == "1.4.0"


def test_api_notes_lifecycle(api_client):
    # 1. Create note
    res1 = api_client.post(
        "/api/notes",
        json={"title": "Design Spec", "content": "Initial draft of spec.", "scope": "default"},
    )
    assert res1.status_code == 201
    note1 = res1.json()
    note_id = note1["id"]
    assert note1["revision"] == 1

    # 2. Revise note
    res2 = api_client.post(
        f"/api/notes/{note_id}/revisions",
        json={"title": "Design Spec v2", "content": "Revised draft of spec.", "expected_revision": 1},
    )
    assert res2.status_code == 200
    note2 = res2.json()
    assert note2["revision"] == 2

    # 3. Read current vs historical
    curr_res = api_client.get(f"/api/notes/{note_id}")
    assert curr_res.status_code == 200
    assert curr_res.json()["content"] == "Revised draft of spec."

    hist_res = api_client.get(f"/api/notes/{note_id}?revision=1")
    assert hist_res.status_code == 200
    assert hist_res.json()["content"] == "Initial draft of spec."


def test_api_candidate_and_memory_flow(api_client):
    note = api_client.post('/api/notes', json={'title': 'FastAPI', 'content': 'Project uses FastAPI'}).json()
    # 1. Submit valid candidate
    res1 = api_client.post(
        "/api/candidates",
        json={
            "source_id": note['id'],
            "source_revision": 1,
            "claim": "Project uses FastAPI",
            "suggested_node_type": "Fact",
            "payload": {"title": "FastAPI Used", "content": "Project uses FastAPI"},
            "authority": "EXPLICIT",
        },
    )
    assert res1.status_code == 200
    cand_data = res1.json()
    assert cand_data["success"] is True
    cand_id = cand_data["id"]

    # 2. Accept candidate
    res2 = api_client.post(f"/api/candidates/{cand_id}/accept")
    assert res2.status_code == 200
    promoted = res2.json()["promoted_node"]
    node_id = promoted["id"]

    # 3. Fetch canonical node
    res3 = api_client.get(f"/api/memory/nodes/{node_id}")
    assert res3.status_code == 200
    assert res3.json()["title"] == "FastAPI Used"


def test_api_backlog_and_logsheet_flow(api_client):
    # 1. Capture backlog item
    res1 = api_client.post(
        "/api/maintenance/backlog",
        json={
            "type": "VERIFY_CLAIM",
            "targets": [{"id": "mem-1", "expected_revision": 1}],
            "reason": "Verify compatibility with Python 3.12",
            "priority": "high",
        },
    )
    assert res1.status_code == 200
    item = res1.json()
    item_id = item["id"]
    assert item["status"] == "OPEN"

    # 2. Transition item with resolution
    res2 = api_client.post(
        f"/api/maintenance/backlog/{item_id}/transition",
        json={
            "status": "RESOLVED",
            "expected_revision": 1,
            "reason": "Tests passed on Python 3.12.10",
            "resolution": {"status": "PASSED", "verified_by": "pytest"},
        },
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "RESOLVED"

    # 3. Append and query logsheet
    res3 = api_client.post(
        "/api/maintenance/logsheet",
        json={
            "operation_id": "OP-100",
            "event_type": "ITEM_RESOLVED",
            "actor": {"kind": "system", "id": "test_runner"},
            "scope": "default",
            "target_refs": [{"id": "mem-1"}],
            "reason": "Verified successfully",
            "result": "success",
            "backlog_id": item_id,
        },
    )
    assert res3.status_code == 200

    query_res = api_client.get(f"/api/maintenance/logsheet?operation_id=OP-100")
    assert query_res.status_code == 200
    assert len(query_res.json()) == 1


def test_api_search_and_context_compiler(api_client):
    # Create a note
    api_client.post(
        "/api/notes",
        json={"title": "Architecture Overview", "content": "Knowledge OS uses local-first architecture.", "scope": "default"},
    )
    # Search
    search_res = api_client.post(
        "/api/retrieval/search",
        json={"query": "What architecture is used?", "scope": "default", "token_budget": 1024},
    )
    assert search_res.status_code == 200
    artifact = search_res.json()
    assert "combined_prompt" in artifact
    assert "local-first" in artifact["combined_prompt"]


def test_mcp_server_tool_definitions_and_execution(mcp_server):
    tools = mcp_server.get_tool_definitions()
    tool_names = [t["name"] for t in tools]
    assert "search_knowledge_context" in tool_names
    assert "create_user_note" in tool_names
    assert "inspect_system_memory_backlog" in tool_names

    # Test creating user note tool
    create_res = mcp_server.execute_tool(
        "create_user_note",
        {"title": "Note from MCP", "content": "Created via standard MCP interface.", "scope": "default"},
    )
    assert "isError" not in create_res
    assert len(create_res["content"]) > 0


def test_token_accounting_budget_enforcement():
    accountant = TokenAccountant(daily_quota=500)
    # Consume 300 tokens
    ok1 = accountant.record_usage("query", tokens=300)
    assert ok1 is True
    assert accountant.get_remaining_budget() == 200

    # Consume 250 tokens -> exceeds 500
    ok2 = accountant.record_usage("distill", tokens=250)
    assert ok2 is False
    assert accountant.is_quota_exceeded() is True
    assert accountant.get_remaining_budget() == 0
