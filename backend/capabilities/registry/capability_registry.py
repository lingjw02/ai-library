"""Inert capability registration and immutable, verified local resource versions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schema.models import utc_now
from backend.storage.canonical.sqlite_storage import CanonicalStorage


class CapabilityKind(str, Enum):
    SKILL = "SKILL"
    MCP = "MCP"


class McpServerType(str, Enum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class RegistrationStatus(str, Enum):
    REGISTERED = "REGISTERED"
    ARCHIVED = "ARCHIVED"


class ArtifactStatus(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CapabilityModel(BaseModel):
    id: str
    kind: CapabilityKind
    name: str
    description: str
    version: str = "1.0.0"
    origin: str
    content_hash: str
    status: RegistrationStatus = RegistrationStatus.REGISTERED
    artifact_status: ArtifactStatus = ArtifactStatus.PRESENT
    manifest: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    model_config = ConfigDict(extra="ignore")


def _relative_name(value: str, *, component: bool = False) -> str:
    """Reject Windows aliases and ADS even on non-Windows hosts."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid artifact path")
    parts = value.split("/")
    if component and len(parts) != 1:
        raise ValueError("Invalid artifact identifier")
    if PureWindowsPath(value).drive or value.startswith("/"):
        raise ValueError("Absolute artifact paths are forbidden")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"{prefix}{i}" for prefix in ("COM", "LPT") for i in "123456789¹²³")
    for part in parts:
        if (part in ("", ".", "..") or part[-1:] in (" ", ".")
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split(".")[0].upper() in reserved):
            raise ValueError("Invalid artifact path component")
    return "/".join(parts)


def _assert_no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Artifact paths cannot contain links or reparse points")


def validate_credential_ref(value: Optional[str], *, env_identifier: bool = False) -> Optional[str]:
    """Explicit secret-store references, or environment variable identifiers."""
    if value is None:
        return None
    if env_identifier and re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", value):
        return value
    if re.fullmatch(r"(?:vault|secret)://[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}", value):
        if all(p not in ("", ".", "..") for p in value.split("://", 1)[1].split("/")):
            return value
    if re.fullmatch(r"env://[A-Z_][A-Z0-9_]{0,127}", value):
        return value
    raise ValueError("Credential values are forbidden; use an explicit credential reference")


_SENSITIVE_KEY = re.compile(r"(?:token|secret|password|passwd|api[_-]?key|authorization|credential)", re.I)


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or any(ord(c) < 32 for c in value)
            or any(_SENSITIVE_KEY.search(k) for k, _ in parse_qsl(parsed.query))):
        raise ValueError("MCP endpoint must be HTTP(S) without embedded credentials")
    return value


class SkillBundle:
    """Complete inert files; arbitrary resources may be bytes in ``files``."""
    def __init__(self, capability_id: str, name: str, description: str, skill_md_content: str,
                 scripts: Optional[Dict[str, str]] = None,
                 references: Optional[Dict[str, str]] = None,
                 origin: str = "local", version: str = "1.0.0",
                 files: Optional[Dict[str, str | bytes]] = None):
        self.capability_id, self.name, self.description = capability_id, name, description
        self.skill_md_content = skill_md_content
        self.scripts, self.references = scripts or {}, references or {}
        self.origin, self.version, self.files = origin, version, files or {}

    def file_contents(self) -> Dict[str, bytes]:
        result = {"SKILL.md": self.skill_md_content.encode("utf-8")}
        supplied = [(f"{directory}/{_relative_name(name)}", content)
                    for directory, files in (("scripts", self.scripts), ("references", self.references))
                    for name, content in files.items()]
        supplied.extend(self.files.items())
        folded = {"skill.md"}
        for name, content in supplied:
            name = _relative_name(name)
            if name.casefold() in folded:
                raise ValueError("Duplicate artifact file path")
            folded.add(name.casefold())
            if not isinstance(content, (str, bytes)):
                raise ValueError("Artifact contents must be text or bytes")
            result[name] = content.encode("utf-8") if isinstance(content, str) else content
        for name in folded:
            if any("/".join(name.split("/")[:i]) in folded for i in range(1, len(name.split("/")))):
                raise ValueError("Artifact file conflicts with a directory")
        return result

    def compute_hash(self) -> str:
        return _file_hash(self.file_contents())


def _file_hash(files: Dict[str, bytes]) -> str:
    manifest = {name: {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for name, body in files.items()}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class McpServerDefinition(BaseModel):
    server_type: McpServerType
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    env_refs: Dict[str, str] = Field(default_factory=dict)
    endpoint: Optional[str] = None
    transport: str = "sse"
    credential_ref: Optional[str] = None
    tool_snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_definition(self):
        validate_credential_ref(self.credential_ref)
        for name, reference in self.env_refs.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Invalid environment variable name")
            validate_credential_ref(reference, env_identifier=True)
        if self.server_type == McpServerType.REMOTE:
            if not self.endpoint:
                raise ValueError("Remote MCP requires an endpoint")
            validate_endpoint(self.endpoint)
            if self.command or self.args or self.cwd or self.env_refs:
                raise ValueError("Remote MCP cannot include local execution settings")
        else:
            if not self.command or not self.command.strip() or self.endpoint:
                raise ValueError("Local MCP requires a command and no remote endpoint")
            for argument in [self.command, *self.args]:
                if _SENSITIVE_KEY.search(argument.split("=", 1)[0]) and argument.startswith("-"):
                    raise ValueError("Credential-bearing arguments are forbidden; use environment references")
                if argument.startswith(("http://", "https://")):
                    validate_endpoint(argument)
        if self.tool_snapshots:
            raise ValueError("Tool snapshots require an authorized discovery operation")
        return self


class CapabilityRegistry:
    def __init__(self, storage: CanonicalStorage, artifacts_dir: str = "data/capabilities/artifacts"):
        self.storage = storage
        root = Path(os.path.abspath(artifacts_dir))
        _assert_no_links(root)
        root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = root.resolve()
        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""CREATE TABLE IF NOT EXISTS capability_versions (
                    capability_id TEXT NOT NULL, version TEXT NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY (capability_id, version))""")
                for row in conn.execute("SELECT * FROM capabilities").fetchall():
                    cap = self._from_row(row)
                    conn.execute("INSERT OR IGNORE INTO capability_versions VALUES (?, ?, ?)",
                                 (cap.id, cap.version, cap.model_dump_json()))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _path(self, capability_id: str, version: str) -> Path:
        path = self.artifacts_dir / _relative_name(capability_id, component=True) / _relative_name(version, component=True)
        _assert_no_links(path)
        if not path.resolve().is_relative_to(self.artifacts_dir):
            raise ValueError("Artifact path escapes the artifact store")
        return path

    def register_skill(self, bundle: SkillBundle) -> CapabilityModel:
        cap_id = bundle.capability_id or f"skill-{uuid.uuid4().hex[:10]}"
        path = self._path(cap_id, bundle.version)
        files = bundle.file_contents()
        hashes = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
        cap = CapabilityModel(id=cap_id, kind=CapabilityKind.SKILL, name=bundle.name,
                              description=bundle.description, version=bundle.version, origin=bundle.origin,
                              content_hash=_file_hash(files), manifest={
                                  "entrypoint": "SKILL.md", "scripts": sorted(bundle.scripts),
                                  "references": sorted(bundle.references), "files": hashes,
                                  "hash_format": "paths-sha256-v1", "artifact_path": str(path)})
        return self._register(cap, files)

    def register_mcp_server(self, name: str, description: str, server_def: McpServerDefinition,
                            origin: str = "local", version: str = "1.0.0",
                            cap_id: Optional[str] = None) -> CapabilityModel:
        server_def = McpServerDefinition.model_validate(server_def.model_dump())
        manifest = server_def.model_dump(mode="json")
        cap = CapabilityModel(id=cap_id or f"mcp-{uuid.uuid4().hex[:10]}", kind=CapabilityKind.MCP,
                              name=name, description=description, version=version, origin=origin,
                              content_hash=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                              artifact_status=ArtifactStatus.NOT_APPLICABLE, manifest=manifest)
        _relative_name(cap.id, component=True)
        _relative_name(cap.version, component=True)
        return self._register(cap)

    def _register(self, cap: CapabilityModel, files: Optional[Dict[str, bytes]] = None) -> CapabilityModel:
        staging = published = None
        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                latest = conn.execute("SELECT * FROM capabilities WHERE id = ?", (cap.id,)).fetchone()
                if latest and latest["kind"] != cap.kind.value:
                    raise ValueError("An existing capability cannot change kind")
                row = conn.execute("SELECT payload_json FROM capability_versions WHERE capability_id = ? AND version = ?",
                                   (cap.id, cap.version)).fetchone()
                if row:
                    previous = CapabilityModel.model_validate_json(row[0])
                    immutable = ("kind", "name", "description", "origin", "content_hash", "manifest")
                    if any(getattr(previous, k) != getattr(cap, k) for k in immutable):
                        raise ValueError("An existing capability version is immutable; import a new version")
                    self.require_usable(previous)
                    conn.commit()
                    return previous
                if files is not None:
                    target = self._path(cap.id, cap.version)
                    if target.exists():
                        raise ValueError("Unregistered artifact directory already exists; preserve it for review")
                    staging = self.artifacts_dir / f".staging-{uuid.uuid4().hex}"
                    staging.mkdir()
                    for name, body in files.items():
                        destination = staging / name
                        _assert_no_links(destination)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with destination.open("xb") as stream:
                            stream.write(body)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self._path(cap.id, cap.version)
                    staging.rename(target)
                    published, staging = target, None
                conn.execute("INSERT INTO capability_versions VALUES (?, ?, ?)", (cap.id, cap.version, cap.model_dump_json()))
                self._upsert_capability(conn, cap)
                self._record_audit(conn, cap.id, f"{cap.kind.value}_REGISTERED",
                                   {"version": cap.version, "hash": cap.content_hash})
                conn.commit()
                return cap
            except Exception:
                conn.rollback()
                for candidate in (staging, published):
                    if candidate is not None:
                        _assert_no_links(candidate)
                        if candidate.resolve().is_relative_to(self.artifacts_dir) and candidate.exists():
                            shutil.rmtree(candidate)
                raise

    def require_usable(self, cap: CapabilityModel) -> None:
        if cap.status != RegistrationStatus.REGISTERED:
            raise ValueError("Capability is not registered")
        if cap.kind == CapabilityKind.MCP:
            definition = McpServerDefinition.model_validate(cap.manifest)
            digest = hashlib.sha256(json.dumps(definition.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
            if digest != cap.content_hash:
                raise ValueError("MCP version manifest integrity check failed")
            return
        path = self._path(cap.id, cap.version)
        if cap.manifest.get("artifact_path") != str(path) or not cap.manifest.get("files"):
            raise ValueError("Skill version requires integrity review before reuse")
        actual = {}
        try:
            if not path.is_dir():
                raise ValueError("Skill artifact is missing")
            for item in path.rglob("*"):
                _assert_no_links(item)
                if item.is_file():
                    name = _relative_name(item.relative_to(path).as_posix())
                    actual[name] = item.read_bytes()
            hashes = {name: hashlib.sha256(body).hexdigest() for name, body in actual.items()}
            if hashes != cap.manifest["files"] or _file_hash(actual) != cap.content_hash:
                raise ValueError("Skill artifact integrity check failed")
        except OSError as exc:
            raise ValueError("Skill artifacts cannot be read") from exc

    @staticmethod
    def _from_row(row) -> CapabilityModel:
        values = dict(row)
        values.update(json.loads(values.pop("status_json")))
        values["manifest"] = json.loads(values.pop("manifest_json"))
        return CapabilityModel.model_validate(values)

    def get_capability(self, capability_id: str, version: Optional[str] = None) -> Optional[CapabilityModel]:
        with self.storage.session() as conn:
            if version is not None:
                row = conn.execute("SELECT payload_json FROM capability_versions WHERE capability_id = ? AND version = ?",
                                   (capability_id, version)).fetchone()
                cap = CapabilityModel.model_validate_json(row[0]) if row else None
            else:
                row = conn.execute("SELECT * FROM capabilities WHERE id = ?", (capability_id,)).fetchone()
                cap = self._from_row(row) if row else None
        if cap and cap.kind == CapabilityKind.SKILL:
            try:
                self.require_usable(cap)
            except ValueError:
                cap.artifact_status = ArtifactStatus.MISSING
        return cap

    def list_capabilities(self, kind: Optional[CapabilityKind] = None) -> List[CapabilityModel]:
        with self.storage.session() as conn:
            rows = conn.execute("SELECT id FROM capabilities" + (" WHERE kind = ?" if kind else "") + " ORDER BY name",
                                (kind.value,) if kind else ()).fetchall()
        return [self.get_capability(row[0]) for row in rows]

    @staticmethod
    def _upsert_capability(conn, cap: CapabilityModel) -> None:
        conn.execute("""INSERT INTO capabilities
            (id, kind, name, description, version, origin, content_hash, status_json, manifest_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
            version=excluded.version, origin=excluded.origin, content_hash=excluded.content_hash,
            status_json=excluded.status_json, manifest_json=excluded.manifest_json, updated_at=excluded.updated_at""",
            (cap.id, cap.kind.value, cap.name, cap.description, cap.version, cap.origin, cap.content_hash,
             json.dumps({"status": cap.status.value, "artifact_status": cap.artifact_status.value}),
             json.dumps(cap.manifest), cap.created_at, cap.updated_at))

    @staticmethod
    def _record_audit(conn, capability_id: str, event_type: str, details: Dict[str, Any], actor=None) -> None:
        conn.execute("""INSERT INTO capability_audit
            (id, capability_id, event_type, actor_json, details_json, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (f"aud-{uuid.uuid4().hex}", capability_id, event_type,
             json.dumps(actor or {"kind": "system", "id": "registry"}), json.dumps(details), utc_now().isoformat()))

    def get_audit_trail(self, capability_id: str) -> List[Dict[str, Any]]:
        with self.storage.session() as conn:
            rows = conn.execute("SELECT * FROM capability_audit WHERE capability_id = ? ORDER BY recorded_at", (capability_id,)).fetchall()
        return [{"id": row["id"], "capability_id": row["capability_id"], "event_type": row["event_type"],
                 "actor": json.loads(row["actor_json"]), "details": json.loads(row["details_json"]),
                 "recorded_at": row["recorded_at"]} for row in rows]

    def export_skill_bundle(self, capability_id: str, version: Optional[str] = None) -> tuple[CapabilityModel, bytes, Dict[str, Any]]:
        """Export a verified versioned Skill bundle as a deterministic ZIP archive and JSON manifest (§91.3, B10)."""
        import base64
        import io
        import zipfile

        cap = self.get_capability(capability_id, version=version)
        if not cap:
            raise LookupError(f"Capability '{capability_id}' not found")
        if cap.kind != CapabilityKind.SKILL:
            raise ValueError(f"Capability '{capability_id}' is not a SKILL bundle")
        self.require_usable(cap)

        path = self._path(cap.id, cap.version)
        files_bytes: Dict[str, bytes] = {}
        files_export: Dict[str, Dict[str, Any]] = {}
        for item in sorted(path.rglob("*")):
            _assert_no_links(item)
            if item.is_file():
                rel = _relative_name(item.relative_to(path).as_posix())
                raw = item.read_bytes()
                files_bytes[rel] = raw
                try:
                    files_export[rel] = {"encoding": "utf-8", "content": raw.decode("utf-8"), "size": len(raw)}
                except UnicodeDecodeError:
                    files_export[rel] = {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii"), "size": len(raw)}

        manifest_payload = {
            "capability_id": cap.id,
            "name": cap.name,
            "description": cap.description,
            "version": cap.version,
            "origin": cap.origin,
            "content_hash": cap.content_hash,
            "manifest": cap.manifest,
            "files": files_export,
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True))
            for rel_name in sorted(files_bytes):
                zf.writestr(rel_name, files_bytes[rel_name])

        return cap, buf.getvalue(), manifest_payload

