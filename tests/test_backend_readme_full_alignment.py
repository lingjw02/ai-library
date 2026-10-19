"""
Comprehensive verification suite for the 11 Backend README alignment items:
- [一.1] EvolutionManager wired into RetrievalCoordinator (app.py + stdio_server.py) & Maintenance UI
- [一.2] Saved Views wired into Frontend UI
- [一.3] Knowledge Health, Idle Plan, Graph Projection, and Token Budget wired into Frontend UI
- [一.4] Two-Step Source Intake Preview + Excerpt Selection + Topic Binding + User Comments
- [一.5] Conversation Lineage & Source Provenance wired into Note Reader
- [一.6] ViewState Persistence wired into 3D Universe
- [一.7] Single-Use Write Approval Tickets wired into Settings UI
- [一.8] Skill Complete Bundle Download (.zip) & Agent Profile Client-Plan UI
- [二.1] Project/Topic-Filtered Vector Hybrid Search (allowed_objects + embedding_query)
- [二.2] Historical `historical_as_of` Bounded Graph Traversal
- [二.3] Read-Only Multimedia & Chat Session MCP Tools (`list_media_assets`, `get_note_chat_session`)
"""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.adapters.mcp.stdio_server import build_service
from backend.capabilities.registry.capability_registry import CapabilityRegistry, SkillBundle
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.evolution.manager import EvolutionManager
from backend.notes.notes_manager import NotesManager
from backend.schema.models import GraphEdge, GraphNode, MemoryAuthority, MemoryDepth, MemoryMaturity, NodeType, Provenance, RelationshipType
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def _build_client(tmp_path: Path):
    db_path = tmp_path / "alignment.db"
    raw_dir = tmp_path / "raw_sources"
    artifacts_dir = tmp_path / "artifacts"
    app = create_app(db_path=str(db_path), raw_dir=str(raw_dir), artifacts_dir=str(artifacts_dir))
    return TestClient(app), app


def test_1_1_evolution_manager_wired_into_retrieval_and_stdio(tmp_path: Path):
    """[一.1] Verify RetrievalCoordinator in both FastAPI app and MCP stdio_server has EvolutionManager wired."""
    client, _ = _build_client(tmp_path)
    # Check active strategies endpoint works
    res = client.get("/api/evolution/active/retrieval_ranking?scope=default")
    assert res.status_code == 200
    data = res.json()
    assert data["strategy_key"] == "retrieval_ranking"

    # Verify stdio_server build_service wires strategy_manager into RetrievalCoordinator
    service = build_service(tmp_path / "stdio_data", scope="default", read_only=True)
    assert service.coordinator.strategy_manager is not None
    assert isinstance(service.coordinator.strategy_manager, EvolutionManager)


def test_1_8_skill_bundle_download_and_agent_profiles_api(tmp_path: Path):
    """[一.8] Verify Skill Complete Bundle (.zip) export and Agent Profiles listing/plan endpoints."""
    client, _ = _build_client(tmp_path)

    # 1. CapabilityRegistry skill bundle download
    storage = CanonicalStorage(db_path=str(tmp_path / "cap_test.db"))
    reg = CapabilityRegistry(storage, artifacts_dir=str(tmp_path / "bundles"))
    bundle = SkillBundle(
        capability_id="cap-sample-skill",
        name="Sample Skill",
        description="A sample skill bundle with scripts and references",
        skill_md_content="---\nname: sample-skill\ndescription: test\n---\n# Body",
        scripts={"helper.py": "print('ok')\n"},
        references={"guide.md": "# Guide\n"},
    )
    registered = reg.register_skill(bundle)
    cap, zip_bytes, manifest = reg.export_skill_bundle("cap-sample-skill")
    assert cap.id == registered.id
    assert "SKILL.md" in manifest["files"]
    assert "scripts/helper.py" in manifest["files"]
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "SKILL.md" in names
        assert "scripts/helper.py" in names
        assert "references/guide.md" in names

    # 2. Local skill .zip download via API
    cat_res = client.get("/api/local-skills")
    assert cat_res.status_code == 200
    items = cat_res.json().get("items", [])
    if items:
        first_id = items[0]["id"]
        dl_res = client.get(f"/api/local-skills/{first_id}/download")
        assert dl_res.status_code == 200
        assert dl_res.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(dl_res.content), "r") as zf:
            assert any(name.endswith("SKILL.md") for name in zf.namelist())

    # 3. Agent profiles list + create + bind MCP + client-plan
    mcp_reg = client.post("/api/capabilities/mcp", json={
        "cap_id": "mcp-ai-lib-local",
        "name": "ai_library_local",
        "description": "Local AI Library stdio MCP server",
        "version": "1.0.0",
        "server_def": {
            "server_type": "LOCAL",
            "command": "python",
            "args": ["-m", "backend.adapters.mcp.stdio_server"],
        },
    })
    assert mcp_reg.status_code == 200

    prof_create = client.post("/api/agent-profiles", json={
        "client_kind": "gemini-cli",
        "client_version": "1.0.0",
        "device_id": "local-workstation",
    })
    assert prof_create.status_code == 200
    prof_id = prof_create.json()["id"]

    bind_res = client.put(
        f"/api/agent-profiles/{prof_id}/bindings/mcp/mcp-ai-lib-local",
        json={
            "installation_or_endpoint_ref": "stdio://local",
            "pinned_version": "1.0.0",
            "expected_revision": 1,
        },
    )
    assert bind_res.status_code == 200

    prof_list = client.get("/api/agent-profiles")
    assert prof_list.status_code == 200
    prof_items = prof_list.json() if isinstance(prof_list.json(), list) else prof_list.json().get("items", [])
    assert any(p["id"] == prof_id for p in prof_items)

    plan_res = client.post(f"/api/agent-profiles/{prof_id}/client-plan", json={})
    assert plan_res.status_code == 200
    plan_json = plan_res.json()
    assert "proposed" in plan_json
    assert "diff" in plan_json


def test_2_1_project_topic_filtered_vector_hybrid_search(tmp_path: Path):
    """[二.1] Verify project/topic-filtered vector & hybrid search works without raising ValueError."""
    storage = CanonicalStorage(db_path=str(tmp_path / "vec_filter.db"))
    notes_mgr = NotesManager(storage, RawSourceStore(str(tmp_path / "raw")))
    coord = RetrievalCoordinator(storage, notes_mgr)

    n1 = notes_mgr.create_note("Alpha Topic Note", "Quantum entanglement simulation details", scope="default")
    n2 = notes_mgr.create_note("Beta Topic Note", "Quantum entanglement unrelated note", scope="default")

    coord.keyword_index.rebuild()
    space = {"model_id": "test-model", "model_version": "1.0", "dimension": 4}
    # Submit embeddings for both notes
    coord.vector_index.submit_vector(
        space=space,
        plane="NOTES",
        object_id=n1.id,
        revision=n1.revision,
        scope="default",
        content_hash=n1.content_hash,
        vector=[1.0, 0.0, 0.0, 0.0],
    )
    coord.vector_index.submit_vector(
        space=space,
        plane="NOTES",
        object_id=n2.id,
        revision=n2.revision,
        scope="default",
        content_hash=n2.content_hash,
        vector=[1.0, 0.0, 0.0, 0.0],
    )
    coord.vector_index.rebuild()

    # Search with allowed_objects filter + embedding_query
    allowed = {("NOTES", n1.id)}
    res = coord.search(
        "Quantum",
        scope="default",
        plane="NOTES",
        allowed_objects=allowed,
        embedding_query={
            "space": space,
            "vector": [1.0, 0.0, 0.0, 0.0],
        },
    )
    assert res["mode"] == "hybrid_rrf"
    returned_ids = {item["id"] for item in res["items"]}
    assert n1.id in returned_ids
    assert n2.id not in returned_ids


def test_2_2_historical_as_of_bounded_graph_traversal(tmp_path: Path):
    """[二.2] Verify historical `as_of` bounded graph traversal reconstructs graph from retained revisions."""
    storage = CanonicalStorage(db_path=str(tmp_path / "hist_traversal.db"))
    notes_mgr = NotesManager(storage, RawSourceStore(str(tmp_path / "raw_hist")))
    coord = RetrievalCoordinator(storage, notes_mgr)

    note = notes_mgr.create_note("History Seed", "Architecture history seed note", scope="default")
    prov = Provenance(source_id=note.id, source_revision=note.revision, content_hash=note.content_hash)
    node_a = GraphNode(
        id="mem_hist_a",
        node_type=NodeType.CONCEPT,
        scope="default",
        title="Historical Architecture Seed",
        content="Initial architecture concept before revision",
        maturity=MemoryMaturity.L2_STRUCTURED,
        depth=MemoryDepth.D2_ENTITY_CONCEPT,
        authority=MemoryAuthority.EXPLICIT,
        provenance=prov,
    )
    node_b = GraphNode(
        id="mem_hist_b",
        node_type=NodeType.DECISION,
        scope="default",
        title="Connected Historical Decision",
        content="Decision linked via RELATED_TO edge",
        maturity=MemoryMaturity.L2_STRUCTURED,
        depth=MemoryDepth.D2_ENTITY_CONCEPT,
        authority=MemoryAuthority.EXPLICIT,
        provenance=prov,
    )
    storage.upsert_node(node_a)
    storage.upsert_node(node_b)
    edge = GraphEdge(
        id="edge_hist_ab",
        source_id=node_a.id,
        target_id=node_b.id,
        relation=RelationshipType.RELATED_TO,
        scope="default",
        authority=MemoryAuthority.EXPLICIT,
    )
    storage.upsert_edge(edge)

    snapshot_time = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()

    # Traverse historically at snapshot_time
    expansion = coord.traversal.traverse(
        [node_a.id],
        max_depth=1,
        max_nodes=10,
        scope="default",
        as_of=snapshot_time,
    )
    expanded_ids = {n.id for n in expansion.nodes}
    assert node_a.id in expanded_ids
    assert node_b.id in expanded_ids

    # Also verify full retrieve() with read_mode='historical_as_of'
    artifact = coord.retrieve(
        "how does Historical Architecture Seed work",
        scope="default",
        read_mode="historical_as_of",
        as_of=snapshot_time,
    )
    assert artifact.retrieval["consistency"]["mode"] == "historical_as_of"
    assert artifact.retrieval["consistency"]["historical_snapshot"] is True


def test_2_3_mcp_multimedia_and_chat_session_tools(tmp_path: Path):
    """[二.3] Verify MCP service exposes read-only `list_media_assets` and `get_note_chat_session`."""
    data_dir = tmp_path / "mcp_media_dir"
    data_dir.mkdir(parents=True, exist_ok=True)
    mcp = build_service(data_dir, scope="default", read_only=True)

    chat_note = mcp.notes_mgr.create_note(
        "聊天原文｜测试对话 (1/1)",
        "会话 12345678-1234-1234-1234-1234567890ab；类别 技术；分段 1/1\n\n### 消息 1｜user｜2026-10-12T10:00:00Z\n你好，请展示 PDF 预览。\n\n### 消息 2｜assistant｜2026-10-12T10:01:00Z\n好的，这是解析结果。",
        scope="default",
    )

    tool_names = {t["name"] for t in mcp.get_tool_definitions()}
    assert "list_media_assets" in tool_names
    assert "get_note_chat_session" in tool_names

    media_call = mcp.execute_tool("list_media_assets", {"limit": 20})
    assert media_call.get("isError", False) is False
    media_payload = json.loads(media_call["content"][0]["text"])
    assert "items" in media_payload
    assert media_payload["count"] == 0

    chat_call = mcp.execute_tool("get_note_chat_session", {"note_id": chat_note.id, "full_session": True})
    assert chat_call.get("isError", False) is False
    chat_payload = json.loads(chat_call["content"][0]["text"])
    assert chat_payload["note_id"] == chat_note.id
    assert chat_payload["is_chat_session"] is True
    assert len(chat_payload["messages"]) == 2


def test_frontend_wiring_for_all_8_ui_audit_items():
    """Verify frontend HTML & JS modules wire all 8 UI audit items ([一.1]–[一.8])."""
    root = Path(__file__).resolve().parent.parent
    index_html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    api_js = (root / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
    maint_js = (root / "frontend" / "js" / "maintenance.js").read_text(encoding="utf-8")
    settings_js = (root / "frontend" / "js" / "settings.js").read_text(encoding="utf-8")
    universe_js = (root / "frontend" / "js" / "universe.js").read_text(encoding="utf-8")
    universe_app_js = (root / "frontend" / "js" / "universe-app.js").read_text(encoding="utf-8")
    notes_js = (root / "frontend" / "js" / "notes.js").read_text(encoding="utf-8")

    # [一.1] Evolution panel in Maintenance
    assert "sub-btn-evolution" in index_html
    assert "renderEvolution" in maint_js
    assert "fetchEvolutionRecords" in api_js

    # [一.2] Saved Views in Universe directory
    assert "universe-saved-views-bar" in index_html
    assert "loadSavedViews" in universe_js
    assert "saveCurrentSearchAsView" in universe_js

    # [一.3] Knowledge Health, Idle Plan, Graph Projection, Token Budget
    assert "sub-btn-health" in index_html
    assert "renderHealth" in maint_js
    assert "settings-projection-status" in index_html
    assert "settings-token-budget-status" in index_html
    assert "loadProjectionAndBudget" in settings_js

    # [一.4] Two-Step Source Intake Preview + Excerpt Selection + Topic Binding + User Comments
    assert "material-preview-card" in index_html
    assert "openPreviewCard" in universe_app_js
    assert "note-intake-section" in index_html
    assert "addIntakeComment" in notes_js

    # [一.5] Conversation Lineage in Note Reader
    assert "note-lineage-section" in index_html
    assert "fetchLineageSource" in notes_js

    # [一.6] ViewState Persistence in 3D Universe
    assert "universe-save-layout" in index_html
    assert "saveCurrentViewState" in universe_js
    assert "restoreViewState" in universe_js

    # [一.7] Write Approval Tickets in Settings
    assert "settings-approvals-list" in index_html
    assert "loadWriteApprovals" in settings_js
    assert "issueWriteApproval" in settings_js

    # [一.8] Skill Bundle Download & Agent Profile Client-Plan UI
    assert "universe-skill-download" in index_html
    assert "/api/local-skills/" in universe_js
    assert "settings-profiles-list" in index_html
    assert "loadAgentProfiles" in settings_js
