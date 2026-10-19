"""
Batch 6: Frontend Integration Tests
Validates:
- StaticFiles serving of frontend/index.html, CSS files, and JS modules
- Newly added memory edges API endpoints
- End-to-end integration between Frontend assets and Backend API
"""

import os
import pytest
from fastapi.testclient import TestClient
from backend.adapters.api.app import create_app
from backend.schema.models import (
    GraphNode,
    GraphEdge,
    NodeType,
    RelationshipType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
)


@pytest.fixture
def app_client(tmp_path):
    db_file = str(tmp_path / "test_frontend.db")
    raw_dir = str(tmp_path / "raw")
    art_dir = str(tmp_path / "art")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(art_dir, exist_ok=True)

    app = create_app(db_path=db_file, raw_dir=raw_dir, artifacts_dir=art_dir)
    return TestClient(app)


def test_frontend_index_and_static_assets_serving(app_client):
    # Test index.html served at root
    res = app_client.get("/")
    assert res.status_code == 200
    assert "AI Library" in res.text
    assert "My Notes" in res.text
    assert "AI Memory" in res.text
    assert "Logsheet + Backlog" in res.text

    # Test CSS assets
    for css in ["main.css", "notes.css", "memory.css", "maintenance.css"]:
        r = app_client.get(f"/css/{css}")
        assert r.status_code == 200, f"Failed to serve /css/{css}"
        assert len(r.text) > 100

    # Test JS assets
    for js in ["app.js", "api.js", "notes.js", "network.js", "maintenance.js"]:
        r = app_client.get(f"/js/{js}")
        assert r.status_code == 200, f"Failed to serve /js/{js}"
        assert len(r.text) > 100


def test_memory_edges_and_node_expansion_api(app_client):
    # Create two nodes in Memory Plane
    node1 = {
        "id": "mem_concept_a",
        "node_type": "Concept",
        "title": "Local First Architecture",
        "content": "Storage lives locally with transactional durability",
        "scope": "default",
        "maturity": "L2",
        "depth": "D2",
        "authority": "EXPLICIT",
        "confidence": 0.95,
        "is_active": True,
    }
    node2 = {
        "id": "mem_fact_b",
        "node_type": "Fact",
        "title": "Zero Data Loss Principle",
        "content": "Raw files are immutable with SHA-256 manifests",
        "scope": "default",
        "maturity": "L3",
        "depth": "D1",
        "authority": "EXPLICIT",
        "confidence": 0.99,
        "is_active": True,
    }
    r1 = app_client.post("/api/memory/nodes", json=node1)
    assert r1.status_code == 201
    r2 = app_client.post("/api/memory/nodes", json=node2)
    assert r2.status_code == 201

    # Check node retrieval
    rn = app_client.get("/api/memory/nodes")
    assert rn.status_code == 200
    nodes = rn.json()
    assert len(nodes) == 2

    # Check edges endpoint
    re = app_client.get("/api/memory/edges")
    assert re.status_code == 200
    assert isinstance(re.json(), list)

    # Check single node edge expansion endpoint
    rne = app_client.get("/api/memory/nodes/mem_concept_a/edges")
    assert rne.status_code == 200
    assert isinstance(rne.json(), list)
