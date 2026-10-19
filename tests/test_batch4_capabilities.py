"""
Tests for Batch 4: Capability Registry (Skills, MCPs, Agent Connections, and Audit).
Ref: Acceptance Criteria §§91, 92.
"""

import tempfile
import pytest
from pathlib import Path
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.capabilities.registry.capability_registry import (
    CapabilityRegistry,
    SkillBundle,
    McpServerDefinition,
    McpServerType,
    CapabilityKind,
)
from backend.capabilities.connections.agent_connections import AgentConnectionManager


@pytest.fixture
def cap_env():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_file = Path(tmpdir) / "test_os.db"
        artifacts_dir = Path(tmpdir) / "artifacts"
        storage = CanonicalStorage(db_path=str(db_file))
        registry = CapabilityRegistry(storage=storage, artifacts_dir=str(artifacts_dir))
        conn_mgr = AgentConnectionManager(storage=storage, registry=registry)
        yield {
            "storage": storage,
            "registry": registry,
            "conn_mgr": conn_mgr,
            "artifacts_dir": artifacts_dir,
        }
        storage.close()


def test_skill_registration_and_artifact_preservation(cap_env):
    """
    Acceptance Gate §91.3, §92.2:
    Complete skill bundle is preserved, versioned, hashed, and stored on disk.
    """
    registry = cap_env["registry"]
    artifacts_dir = cap_env["artifacts_dir"]

    bundle = SkillBundle(
        capability_id="skill-test-1",
        name="test-skill",
        description="A test skill for automation",
        skill_md_content="# Test Skill\nInstructions here.",
        scripts={"run.py": "print('hello from skill')"},
        version="1.0.0",
    )

    cap = registry.register_skill(bundle)
    assert cap.id == "skill-test-1"
    assert cap.kind == CapabilityKind.SKILL
    assert cap.content_hash is not None

    # Check file exists on disk
    expected_md = artifacts_dir / "skill-test-1" / "1.0.0" / "SKILL.md"
    expected_script = artifacts_dir / "skill-test-1" / "1.0.0" / "scripts" / "run.py"
    assert expected_md.exists()
    assert expected_script.exists()
    assert "hello from skill" in expected_script.read_text(encoding="utf-8")


def test_mcp_registration_credential_safety(cap_env):
    """
    Acceptance Gate §91.4, §91.7, §92.2:
    Credential values are not stored in manifest or plain text; only credential_ref is kept.
    """
    registry = cap_env["registry"]

    mcp_def = McpServerDefinition(
        server_type=McpServerType.REMOTE,
        endpoint="https://api.example.com/mcp",
        transport="sse",
        credential_ref="vault://tokens/example_key",
    )

    cap = registry.register_mcp_server(
        name="example-service",
        description="Remote example service",
        server_def=mcp_def,
    )
    assert cap.kind == CapabilityKind.MCP
    assert cap.manifest["credential_ref"] == "vault://tokens/example_key"
    assert "token" not in cap.manifest.get("env_refs", {})


def test_agent_connections_binding_and_export(cap_env):
    """
    Acceptance Gate §91.5, §92.2:
    Profile binding plans and client configuration export without secret leak.
    """
    registry = cap_env["registry"]
    conn_mgr = cap_env["conn_mgr"]

    # 1. Register a local MCP server
    local_mcp = McpServerDefinition(
        server_type=McpServerType.LOCAL,
        command="node",
        args=["./server.js"],
        env_refs={"API_KEY": "ENV_API_KEY"},
    )
    mcp_cap = registry.register_mcp_server(
        name="local-tools",
        description="Local tool server",
        server_def=local_mcp,
        cap_id="mcp-local-1",
    )

    # 2. Create agent profile and bind
    profile = conn_mgr.create_profile(client_kind="antigravity", client_version="2.0")
    conn_mgr.bind_mcp(profile.id, "mcp-local-1", endpoint_or_install_ref="./server.js")

    # 3. Export configuration
    exported = conn_mgr.export_mcp_config(profile.id)
    assert "mcpServers" in exported
    assert "local-tools" in exported["mcpServers"]
    server_conf = exported["mcpServers"]["local-tools"]
    assert server_conf["command"] == "node"
    assert server_conf["env"]["API_KEY"] == "${ENV_API_KEY}"


def test_capability_audit_isolation(cap_env):
    """
    Acceptance Gate §91.8, §92.2:
    Capability audit log is recorded and maintained separately from System Memory Logsheet.
    """
    registry = cap_env["registry"]

    bundle = SkillBundle(
        capability_id="skill-aud-1",
        name="audit-skill",
        description="Audit test",
        skill_md_content="# Audit",
    )
    registry.register_skill(bundle)

    trail = registry.get_audit_trail("skill-aud-1")
    assert len(trail) >= 1
    assert trail[0]["event_type"] == "SKILL_REGISTERED"
