"""
Agent Connections & Compatibility Manager.
Ref: AI Knowledge OS Backend Specification §§91.5, 91.6, 92.
Binds approved Skills and MCP servers to specific Agent profiles,
generates binding plans, and exports client-compatible configuration templates.
"""

from __future__ import annotations
import uuid
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.capabilities.registry.capability_registry import (
    CapabilityRegistry, CapabilityKind, validate_credential_ref, validate_endpoint,
)
from backend.schema.models import utc_now
from backend.capabilities.connections.client_config import plan_local_mcp_config


class ProfileRevisionConflict(ValueError):
    """A caller's binding view is no longer current."""


class SkillBinding(BaseModel):
    capability_id: str
    pinned_version: str
    binding_method: str = "read_only_path"  # "read_only_path", "symlink", "copy"

    model_config = ConfigDict(extra="ignore")


class McpBinding(BaseModel):
    capability_id: str
    installation_or_endpoint_ref: str
    credential_ref: Optional[str] = None  # Reference only!
    pinned_version: Optional[str] = None  # Legacy unpinned bindings need explicit rebinding.

    @field_validator("credential_ref")
    @classmethod
    def reference_only(cls, value):
        return validate_credential_ref(value)

    model_config = ConfigDict(extra="ignore")


class AgentProfile(BaseModel):
    id: str
    client_kind: str
    client_version: str
    device_id: str
    skill_bindings: List[SkillBinding] = Field(default_factory=list)
    mcp_bindings: List[McpBinding] = Field(default_factory=list)
    compatibility_status: str = "UNVERIFIED"
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())

    model_config = ConfigDict(extra="ignore")


class AgentConnectionManager:
    """Manages Agent Profiles, Skill/MCP bindings, and client configuration exports."""

    def __init__(self, storage: CanonicalStorage, registry: CapabilityRegistry):
        self.storage = storage
        self.registry = registry
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(agent_profiles)')}
            if 'revision' not in columns:
                conn.execute('ALTER TABLE agent_profiles ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
            conn.execute('COMMIT')

    def create_profile(
        self,
        client_kind: str,
        client_version: str = "UNKNOWN",
        device_id: str = "local",
        profile_id: Optional[str] = None,
    ) -> AgentProfile:
        pid = profile_id or f"prof-{uuid.uuid4().hex[:10]}"
        now_str = utc_now().isoformat()
        profile = AgentProfile(
            id=pid,
            client_kind=client_kind,
            client_version=client_version,
            device_id=device_id,
            created_at=now_str,
            updated_at=now_str,
        )

        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(
                """
                INSERT INTO agent_profiles (
                    id, client_kind, client_version, device_id, skill_bindings_json,
                    mcp_bindings_json, compatibility_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '[]', '[]', 'UNVERIFIED', ?, ?);
                """,
                (pid, client_kind, client_version, device_id, now_str, now_str),
            )
            self.registry._record_audit(conn, 'profile:' + pid, 'PROFILE_CREATED',
                {'profile_id': pid, 'revision': 1, 'client_kind': client_kind, 'client_version': client_version},
                actor={'kind': 'user', 'id': 'local-owner'})
            self.storage._insert_outbox_event(conn, 'AGENT_PROFILE', pid, 'PROFILE_CREATED',
                {'profile_id': pid, 'revision': 1})
            conn.execute('COMMIT')
        return profile

    def get_profile(self, profile_id: str) -> Optional[AgentProfile]:
        with self.storage.session() as conn:
            row = conn.execute("SELECT * FROM agent_profiles WHERE id = ?;", (profile_id,)).fetchone()
            if not row:
                return None
            return AgentProfile(
                id=row["id"],
                client_kind=row["client_kind"],
                client_version=row["client_version"],
                device_id=row["device_id"],
                skill_bindings=[SkillBinding.model_validate(b) for b in json.loads(row["skill_bindings_json"])],
                mcp_bindings=[McpBinding.model_validate(b) for b in json.loads(row["mcp_bindings_json"])],
                compatibility_status=row["compatibility_status"],
                revision=row['revision'],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_profiles(self) -> List[AgentProfile]:
        with self.storage.session() as conn:
            rows = conn.execute("SELECT id FROM agent_profiles ORDER BY updated_at DESC, id ASC;").fetchall()
        return [p for r in rows if (p := self.get_profile(r["id"])) is not None]


    def bind_skill(
        self,
        profile_id: str,
        capability_id: str,
        pinned_version: str = "1.0.0",
        binding_method: str = "read_only_path",
        *, expected_revision: Optional[int] = None,
    ) -> AgentProfile:
        """Bind an approved skill to an agent profile (§91.5)."""
        cap = self.registry.get_capability(capability_id, pinned_version)
        if not cap:
            raise ValueError("Skill capability version not found in registry")
        if cap.kind != CapabilityKind.SKILL:
            raise ValueError("Capability is not a Skill")
        if binding_method not in {"read_only_path", "symlink", "copy"}:
            raise ValueError("Unknown Skill binding method")
        self.registry.require_usable(cap)

        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"AgentProfile {profile_id} not found.")

        # Update or add binding
        existing = [b for b in profile.skill_bindings if b.capability_id != capability_id]
        existing.append(
            SkillBinding(
                capability_id=capability_id,
                pinned_version=pinned_version,
                binding_method=binding_method,
            )
        )
        profile.skill_bindings = existing
        profile.updated_at = utc_now().isoformat()

        self._save_profile(profile, 'SKILL_BOUND', capability_id, expected_revision)
        return profile

    def bind_mcp(
        self,
        profile_id: str,
        capability_id: str,
        endpoint_or_install_ref: str,
        credential_ref: Optional[str] = None,
        pinned_version: Optional[str] = None,
        *, expected_revision: Optional[int] = None,
    ) -> AgentProfile:
        """Bind an approved MCP server to an agent profile."""
        validate_credential_ref(credential_ref)
        if endpoint_or_install_ref.startswith(("http://", "https://")):
            validate_endpoint(endpoint_or_install_ref)
        cap = self.registry.get_capability(capability_id, pinned_version)
        if not cap:
            raise ValueError(f"Capability {capability_id} not found in registry.")
        if cap.kind != CapabilityKind.MCP:
            raise ValueError("Capability is not an MCP server")
        self.registry.require_usable(cap)
        if cap.manifest["server_type"] == "REMOTE" and endpoint_or_install_ref != cap.manifest["endpoint"]:
            raise ValueError("Remote binding endpoint does not match its pinned definition")

        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"AgentProfile {profile_id} not found.")

        existing = [b for b in profile.mcp_bindings if b.capability_id != capability_id]
        existing.append(
            McpBinding(
                capability_id=capability_id,
                installation_or_endpoint_ref=endpoint_or_install_ref,
                credential_ref=credential_ref,
                pinned_version=cap.version,
            )
        )
        profile.mcp_bindings = existing
        profile.updated_at = utc_now().isoformat()

        self._save_profile(profile, 'MCP_BOUND', capability_id, expected_revision)
        return profile

    def export_mcp_config(self, profile_id: str) -> Dict[str, Any]:
        """
        Export an inert mcpServers template; client compatibility is UNVERIFIED.
        Credential references are never resolved into values.
        """
        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"Profile {profile_id} not found.")

        mcp_servers: Dict[str, Any] = {}
        for binding in profile.mcp_bindings:
            if not binding.pinned_version:
                raise ValueError("Legacy MCP binding requires an explicit version pin")
            cap = self.registry.get_capability(binding.capability_id, binding.pinned_version)
            if not cap:
                raise ValueError("Pinned MCP capability version is missing")
            if cap.kind != CapabilityKind.MCP:
                raise ValueError("Bound capability is not an MCP server")
            self.registry.require_usable(cap)
            if cap.name in mcp_servers:
                raise ValueError("Duplicate MCP export name; distinct server names are required")

            manifest = cap.manifest
            server_type = manifest.get("server_type")

            if server_type == "LOCAL":
                env = {}
                for name, reference in manifest.get("env_refs", {}).items():
                    if reference.startswith(("vault://", "secret://")):
                        raise ValueError("Secret-store environment references require a client-specific resolver")
                    env[name] = "${" + reference.removeprefix("env://") + "}"
                mcp_servers[cap.name] = {
                    "command": manifest.get("command", ""),
                    "args": manifest.get("args", []),
                    "env": env,
                }
                if manifest.get("cwd"):
                    mcp_servers[cap.name]["cwd"] = manifest["cwd"]
            else:
                mcp_servers[cap.name] = {
                    "url": manifest.get("endpoint", binding.installation_or_endpoint_ref),
                    "transport": manifest.get("transport", "sse"),
                    "credentialRef": binding.credential_ref or manifest.get("credential_ref"),
                }

        return {"mcpServers": mcp_servers}

    def plan_client_config(self, profile_id: str, before: bytes | None = None):
        """Plan only: pinned definitions, no process launch or client-file writes."""
        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError('Profile not found')
        exported = self.export_mcp_config(profile_id)
        return plan_local_mcp_config(profile.client_kind, before, exported['mcpServers'])

    def unbind_capability(self, profile_id: str, capability_id: str, kind: str, *, expected_revision: int):
        """Remove registry binding only; shared resources and client files remain."""
        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError('Profile not found')
        if kind == 'SKILL':
            profile.skill_bindings = [b for b in profile.skill_bindings if b.capability_id != capability_id]
        elif kind == 'MCP':
            profile.mcp_bindings = [b for b in profile.mcp_bindings if b.capability_id != capability_id]
        else:
            raise ValueError('kind must be SKILL or MCP')
        self._save_profile(profile, kind + '_UNBOUND', capability_id, expected_revision)
        return profile

    def get_profile_audit(self, profile_id: str, limit: int = 100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be 1–100')
        with self.storage.session() as conn:
            rows = conn.execute("SELECT * FROM capability_audit WHERE json_extract(details_json, '$.profile_id') = ? ORDER BY recorded_at DESC, id DESC LIMIT ?",
                (profile_id, limit)).fetchall()
        return [{'event_type': row['event_type'], 'capability_id': row['capability_id'],
                 'actor': json.loads(row['actor_json']), 'details': json.loads(row['details_json']),
                 'recorded_at': row['recorded_at']} for row in rows]

    def _save_profile(self, profile: AgentProfile, event_type: str, capability_id: str,
                      expected_revision: Optional[int] = None) -> None:
        expected = profile.revision if expected_revision is None else expected_revision
        if type(expected) is not int or expected < 1:
            raise ValueError('Expected profile revision must be a positive integer')
        if expected != profile.revision:
            raise ProfileRevisionConflict('Profile revision changed; reload before editing')
        skills = json.dumps([b.model_dump() for b in profile.skill_bindings])
        mcps = json.dumps([b.model_dump() for b in profile.mcp_bindings])
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT * FROM agent_profiles WHERE id = ?', (profile.id,)).fetchone()
            if not previous or previous['revision'] != expected:
                raise ProfileRevisionConflict('Profile revision changed; reload before editing')
            if previous['skill_bindings_json'] == skills and previous['mcp_bindings_json'] == mcps:
                profile.revision = previous['revision']
                profile.compatibility_status = previous['compatibility_status']
                profile.updated_at = previous['updated_at']
                conn.execute('COMMIT')
                return
            profile.revision = expected + 1
            profile.compatibility_status = 'UNVERIFIED'
            profile.updated_at = utc_now().isoformat()
            conn.execute(
                """
                UPDATE agent_profiles SET
                    skill_bindings_json = ?,
                    mcp_bindings_json = ?,
                    compatibility_status = ?,
                    updated_at = ?, revision = ?
                WHERE id = ?;
                """,
                (
                    skills,
                    mcps,
                    profile.compatibility_status,
                    profile.updated_at,
                    profile.revision,
                    profile.id,
                ),
            )
            self.registry._record_audit(conn, capability_id, event_type,
                {'profile_id': profile.id, 'revision': profile.revision,
                 'before': {'skills': json.loads(previous['skill_bindings_json']), 'mcp': json.loads(previous['mcp_bindings_json'])},
                 'after': {'skills': json.loads(skills), 'mcp': json.loads(mcps)},
                 'client_configuration_changed': False}, actor={'kind': 'user', 'id': 'local-owner'})
            self.storage._insert_outbox_event(conn, 'AGENT_PROFILE', profile.id, 'PROFILE_BINDING_CHANGED',
                {'profile_id': profile.id, 'revision': profile.revision, 'capability_id': capability_id})
            conn.execute('COMMIT')
