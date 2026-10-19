"""End-to-end verification of AAS Core v19.8.0 integration, deduplication, fusion, and 16-domain taxonomy."""
import io
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.capabilities.aas_engine import (
    AAS_PHILOSOPHY_MOTTO,
    DOMAIN_TAXONOMY,
    compose_aas_stack,
    deduplicate_and_fuse_skills,
    enrich_with_aas_philosophy,
)
from backend.capabilities.local_skills import (
    aas_package_root,
    discover_mcp_servers,
    discover_skills,
    load_aas_catalog_map,
)


def test_aas_core_package_and_workspace_manifests_installed():
    """Verify agentic-awesome-skills@19.8.0 is installed and workspace manifests exist."""
    pkg = aas_package_root()
    assert pkg.is_dir(), "AAS package directory must exist"
    assert (pkg / "package.json").is_file()
    assert (pkg / "skills_index.json").is_file()
    assert (pkg / "data" / "catalog.json").is_file()
    assert (pkg / "tools" / "bin" / "aas-mcp.js").is_file()

    aas_map = load_aas_catalog_map()
    assert len(aas_map) >= 2600

    ws_root = pkg.parents[2]
    stack_file = ws_root / "aas-stack.json"
    evidence_file = ws_root / "aas-selection-evidence.json"
    assert stack_file.is_file()
    assert evidence_file.is_file()

    stack_data = json.loads(stack_file.read_text(encoding="utf-8"))
    assert stack_data["schemaVersion"] == 2
    assert stack_data["aasCoreVersion"] == "19.8.0"
    assert stack_data["philosophy"] == AAS_PHILOSOPHY_MOTTO
    assert 1 <= len(stack_data["skills"]) <= 128


def test_exact_deduplication_and_similarity_fusion():
    """Verify identical skills are skipped and highly similar skills are fused."""
    sample_skills = [
        {
            "id": "s1",
            "kind": "skill",
            "title": "test-driven-development",
            "description": "Write failing unit tests first and follow red-green-refactor workflow.",
            "locations": [{"source": "当前项目", "path": "/workspace/.agents/skills/test-driven-development/SKILL.md"}],
        },
        {
            "id": "s2",
            "kind": "skill",
            "title": "test-driven-development",
            "description": "Write failing unit tests first.",
            "locations": [{"source": "Codex", "path": "/home/.codex/skills/test-driven-development/SKILL.md"}],
        },
        {
            "id": "s3",
            "kind": "skill",
            "title": "tdd",
            "description": "Enforce strict TDD test-first cycles before implementation code.",
            "locations": [{"source": "Gemini", "path": "/home/.gemini/skills/tdd/SKILL.md"}],
        },
        {
            "id": "s4",
            "kind": "skill",
            "title": "accessibility-patterns",
            "description": "WCAG 2.2 accessibility design patterns and ARIA keyboard navigation guidelines.",
            "locations": [{"source": "当前项目", "path": "/workspace/.agents/skills/accessibility-patterns/SKILL.md"}],
        },
        {
            "id": "s5",
            "kind": "skill",
            "title": "accessibility-testing",
            "description": "WCAG 2.2 accessibility audit and ARIA keyboard screen-reader testing guidelines.",
            "locations": [{"source": "Codex", "path": "/home/.codex/skills/accessibility-testing/SKILL.md"}],
        },
    ]

    deduped, stats = deduplicate_and_fuse_skills(sample_skills, {})
    assert stats["skipped_duplicates"] == 1
    assert stats["fused_skills"] >= 2
    assert len(deduped) == 2

    tdd_skill = next(s for s in deduped if s["canonical_id"] == "test-driven-development")
    assert "tdd" in tdd_skill["fused_from"]
    assert len(tdd_skill["locations"]) == 3
    assert "【融合互补能力 · tdd】" in tdd_skill["description"]
    assert tdd_skill["trust_boundary"].startswith("AAS Core 技能契约：")


def test_mcp_and_local_skills_aas_philosophy_and_16_domains():
    """Verify all discovered Skills and MCPs carry AAS Core philosophy and accurate 16-domain classification."""
    assert len(DOMAIN_TAXONOMY) == 16
    mcps = discover_mcp_servers()
    assert len(mcps) >= 7

    by_name = {m["name"]: m for m in mcps}
    assert "aas-core" in by_name
    assert by_name["aas-core"]["domainId"] == "dx"
    if "ai-memory-library" in by_name:
        assert by_name["ai-memory-library"]["domainId"] == "data"
    if "chrome-devtools" in by_name:
        assert by_name["chrome-devtools"]["domainId"] == "qa"
    if "supabase" in by_name:
        assert by_name["supabase"]["domainId"] == "backend"
    if "brevo" in by_name:
        assert by_name["brevo"]["domainId"] == "business"
    if "window-mcp" in by_name:
        assert by_name["window-mcp"]["domainId"] == "devops"

    for m in mcps:
        assert m.get("canonical_id")
        assert m.get("domain")
        assert m.get("domainId")
        assert m.get("risk") in {"none", "safe", "low", "medium", "high"}
        assert m.get("capability_dimension")
        assert AAS_PHILOSOPHY_MOTTO in m.get("trust_boundary", "")


def test_local_skills_api_and_compose_stack_endpoint(tmp_path: Path):
    """Verify GET /api/local-skills, POST /api/local-skills/compose-stack, and ZIP download."""
    app = create_app(
        db_path=str(tmp_path / "test.db"),
        raw_dir=str(tmp_path / "raw"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    client = TestClient(app)

    res = client.get("/api/local-skills")
    assert res.status_code == 200
    payload = res.json()
    assert payload["aas_core"]["version"] == "19.8.0"
    assert payload["aas_core"]["catalog_installed"] is True
    assert payload["stats"]["skipped_duplicates"] >= 50
    assert payload["stats"]["fused_skills"] >= 20
    assert len(payload["items"]) >= 2800

    # Test compose-stack endpoint
    stack_res = client.post(
        "/api/local-skills/compose-stack",
        json={
            "skill_ids": ["brainstorming", "dispatching-parallel-agents", "mcp-aas-core", "test-driven-development"],
            "project_name": "test-aas-stack",
        },
    )
    assert stack_res.status_code == 200
    manifest = stack_res.json()
    assert manifest["schemaVersion"] == 2
    assert manifest["project"]["name"] == "test-aas-stack"
    assert len(manifest["skills"]) == 4
    assert "covered_dimensions" in manifest["coverage"]

    # Test downloading a fused skill bundle
    dl_res = client.get("/api/local-skills/test-driven-development/download")
    assert dl_res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(dl_res.content), "r") as zf:
        names = zf.namelist()
        assert "SKILL.md" in names
        assert "manifest.json" in names
        manifest_json = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest_json["canonical_id"] == "test-driven-development"
        assert AAS_PHILOSOPHY_MOTTO in manifest_json["trust_boundary"]
