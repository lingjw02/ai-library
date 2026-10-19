"""Safety and actual artifact/version integrity, independent of client installation."""
from pathlib import Path

import pytest

from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.capabilities.registry.capability_registry import (
    CapabilityRegistry, SkillBundle, McpServerDefinition, McpServerType,
)
from backend.capabilities.connections.agent_connections import AgentConnectionManager


@pytest.fixture
def env(tmp_path):
    storage = CanonicalStorage(str(tmp_path / "db.sqlite"))
    registry = CapabilityRegistry(storage, str(tmp_path / "artifacts"))
    yield registry, AgentConnectionManager(storage, registry), tmp_path
    storage.close()


def bundle(**changes):
    values = dict(capability_id="skill-a", name="a", description="test", skill_md_content="# A")
    values.update(changes)
    return SkillBundle(**values)


@pytest.mark.parametrize("changes", [
    {"capability_id": "../escape"}, {"version": "../escape"},
    {"capability_id": "ABSOLUTE_TEMP"}, {"version": "CON"},
    {"scripts": {"../../escape.txt": "bad"}},
    {"references": {"ABSOLUTE_TEMP": "bad"}},
    {"scripts": {"safe.py:stream": "bad"}},
    {"scripts": {"good.py": "ok", "../bad.py": "bad"}},
])
def test_unsafe_paths_fail_before_writes(env, changes):
    registry, _, root = env
    changes = dict(changes)
    if changes.get("capability_id") == "ABSOLUTE_TEMP":
        changes["capability_id"] = str(root / "escaped_absolute")
    if "references" in changes:
        changes["references"] = {str(root / "escaped_reference.txt"): "bad"}
    with pytest.raises(ValueError):
        registry.register_skill(bundle(**changes))
    assert registry.list_capabilities() == []
    assert list((root / "artifacts").iterdir()) == []


def test_nested_complete_bundle_and_content_identity(env):
    registry, _, root = env
    cap = registry.register_skill(bundle(scripts={"nested/run.py": "print('hi')"},
                                         references={"nested/guide.md": "guide"},
                                         files={"assets/icon.bin": b"\x00\xff"}))
    path = Path(cap.manifest["artifact_path"])
    assert path.is_absolute()
    assert (path / "references/nested/guide.md").read_text() == "guide"
    assert (path / "assets/icon.bin").read_bytes() == b"\x00\xff"
    assert (path / "scripts/nested/run.py").exists()
    assert bundle(scripts={"same": "body"}).compute_hash() != bundle(references={"same": "body"}).compute_hash()


def test_same_version_is_idempotent_and_immutable(env):
    registry, _, _ = env
    first = registry.register_skill(bundle())
    again = registry.register_skill(bundle())
    assert first.model_dump() == again.model_dump()
    assert len(registry.get_audit_trail(first.id)) == 1
    for change in ({"skill_md_content": "changed"}, {"origin": "other"}, {"name": "different"}):
        with pytest.raises(ValueError):
            registry.register_skill(bundle(**change))
    assert Path(first.manifest["artifact_path"], "SKILL.md").read_text() == "# A"


def test_versions_survive_upgrade_and_restart(env):
    registry, manager, _ = env
    first = registry.register_skill(bundle())
    profile = manager.create_profile("unverified")
    manager.bind_skill(profile.id, first.id, "1.0.0")
    registry.register_skill(bundle(version="2.0.0", skill_md_content="# B"))
    reopened = CapabilityRegistry(registry.storage, str(registry.artifacts_dir))
    assert reopened.get_capability(first.id, "1.0.0").content_hash == first.content_hash
    assert reopened.get_capability(first.id).version == "2.0.0"
    assert manager.get_profile(profile.id).skill_bindings[0].pinned_version == "1.0.0"
    assert reopened.register_skill(bundle()).version == "1.0.0"
    assert reopened.get_capability(first.id).version == "2.0.0"


def test_missing_version_wrong_kind_and_tampered_bundle_rejected(env):
    registry, manager, _ = env
    cap = registry.register_skill(bundle())
    profile = manager.create_profile("unknown")
    with pytest.raises(ValueError):
        manager.bind_skill(profile.id, cap.id, "missing")
    with pytest.raises(ValueError):
        manager.bind_mcp(profile.id, cap.id, "anything")
    Path(cap.manifest["artifact_path"], "SKILL.md").write_text("tampered")
    with pytest.raises(ValueError):
        manager.bind_skill(profile.id, cap.id)
    with pytest.raises(ValueError):
        registry.register_skill(bundle())
    assert manager.get_profile(profile.id).skill_bindings == []


@pytest.mark.parametrize("definition", [
    {"server_type": "REMOTE", "endpoint": "https://example.invalid/mcp", "credential_ref": "raw-secret-value"},
    {"server_type": "LOCAL", "command": "node", "env_refs": {"TOKEN": "sk-secret-value"}},
    {"server_type": "REMOTE", "endpoint": "https://user:password@example.invalid/mcp"},
    {"server_type": "REMOTE", "endpoint": "https://example.invalid/mcp?token=rawsecret"},
    {"server_type": "LOCAL", "command": "node", "args": ["--api-key", "rawsecret"]},
    {"server_type": "LOCAL"}, {"server_type": "REMOTE"},
])
def test_invalid_or_secret_bearing_mcp_definitions_rejected(env, definition):
    registry, _, _ = env
    with pytest.raises(ValueError):
        registry.register_mcp_server("test", "test", McpServerDefinition(**definition))
    assert registry.list_capabilities() == []


def test_mcp_pins_kind_and_duplicate_export_names(env):
    registry, manager, _ = env
    def register(cid, command="old", version="1.0.0"):
        return registry.register_mcp_server("same", "test", McpServerDefinition(
            server_type=McpServerType.LOCAL, command=command, cwd="/work",
            env_refs={"API_KEY": "ENV_API_KEY"}), cap_id=cid, version=version)
    first = register("mcp-a")
    profile = manager.create_profile("unknown")
    with pytest.raises(ValueError):
        manager.bind_skill(profile.id, first.id)
    manager.bind_mcp(profile.id, first.id, "/work")
    register("mcp-a", "new", "2.0.0")
    exported = manager.export_mcp_config(profile.id)["mcpServers"]["same"]
    assert exported["command"] == "old"
    assert exported["cwd"] == "/work"
    register("mcp-b")
    manager.bind_mcp(profile.id, "mcp-b", "/work")
    with pytest.raises(ValueError, match="name"):
        manager.export_mcp_config(profile.id)


def test_profile_raw_credential_rejected_without_state_change(env):
    registry, manager, _ = env
    cap = registry.register_mcp_server("remote", "test", McpServerDefinition(
        server_type=McpServerType.REMOTE, endpoint="https://example.invalid/mcp",
        credential_ref="vault://tokens/example"))
    profile = manager.create_profile("unknown")
    with pytest.raises(ValueError):
        manager.bind_mcp(profile.id, cap.id, "https://example.invalid/mcp", "raw-secret-value")
    assert manager.get_profile(profile.id).mcp_bindings == []


def test_symlink_parent_rejected(env):
    registry, _, root = env
    outside = root / "outside"
    outside.mkdir()
    try:
        (registry.artifacts_dir / "skill-a").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(ValueError):
        registry.register_skill(bundle())
    assert list(outside.iterdir()) == []


def test_kind_cannot_change_for_existing_id(env):
    registry, _, _ = env
    registry.register_skill(bundle())
    with pytest.raises(ValueError):
        registry.register_mcp_server("a", "test", McpServerDefinition(server_type="LOCAL", command="node"),
                                     cap_id="skill-a", version="2.0.0")
    assert registry.get_capability("skill-a").kind.value == "SKILL"
