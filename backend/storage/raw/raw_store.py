"""
Raw Source Store implementation.
Ref: AI Knowledge OS Backend Specification §§11, 56, 69.
Preserves immutable source revisions with SHA-256 manifests.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from backend.schema.models import utc_now


class RawSourceStore:
    """
    Manages raw immutable document revisions and manifest records.
    Guarantee: User notes and raw sources are never silently rewritten.
    """

    def __init__(self, base_dir: str = "data/raw_sources", canonical_storage=None):
        self.canonical_storage = canonical_storage
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.base_dir / "manifests.json"
        self._manifests: Dict[str, Dict[str, Any]] = self._load_manifests()

    def _load_manifests(self) -> Dict[str, Dict[str, Any]]:
        if self.manifest_file.exists():
            try:
                return json.loads(self.manifest_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_manifests(self) -> None:
        self.manifest_file.write_text(json.dumps(self._manifests, indent=2), encoding="utf-8")

    def store_source(
        self,
        source_id: str,
        content: str | bytes,
        mime_type: str = "text/markdown",
        scope: str = "default",
    ) -> Dict[str, Any]:
        """
        Store a new or updated source as an immutable revision.
        Computes SHA-256 content hash and records metadata in the manifest.
        """
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        content_hash = hashlib.sha256(content_bytes).hexdigest()

        # Check existing revision count
        existing_revs = [k for k in self._manifests.keys() if k.startswith(f"{source_id}:rev:")]
        revision = len(existing_revs) + 1

        rel_path = f"{source_id}_rev_{revision}.raw"
        file_path = self.base_dir / rel_path
        file_path.write_bytes(content_bytes)

        key = f"{source_id}:rev:{revision}"
        manifest_entry = {
            "source_id": source_id,
            "revision": revision,
            "file_path": str(file_path),
            "content_hash": content_hash,
            "byte_length": len(content_bytes),
            "mime_type": mime_type,
            "scope": scope,
            "created_at": utc_now().isoformat(),
        }
        self._manifests[key] = manifest_entry
        self._save_manifests()
        return manifest_entry

    def get_source(self, source_id: str, revision: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieve raw source by source_id and revision (or latest revision).
        Verifies content hash integrity upon reading.
        """
        manifest = None
        if self.canonical_storage is not None:
            query = ('SELECT m.*, n.scope FROM raw_manifests m LEFT JOIN notes n '
                     'ON n.id = m.source_id AND n.revision = m.revision WHERE m.source_id = ?')
            params = [source_id]
            if revision is not None:
                query += ' AND m.revision = ?'
                params.append(revision)
            query += ' ORDER BY m.revision DESC LIMIT 1'
            with self.canonical_storage.session() as conn:
                row = conn.execute(query, params).fetchone()
                if row:
                    manifest = dict(row)
        # Historical standalone sources remain readable without migration.
        self._manifests = self._load_manifests()
        if manifest is not None:
            pass
        elif revision is None:
            # find latest
            revs = [v for k, v in self._manifests.items() if v["source_id"] == source_id]
            if not revs:
                return None
            revs.sort(key=lambda x: x["revision"], reverse=True)
            manifest = revs[0]
        else:
            key = f"{source_id}:rev:{revision}"
            manifest = self._manifests.get(key)
            if not manifest:
                return None

        file_path = Path(manifest["file_path"])
        if not file_path.exists():
            return None

        data = file_path.read_bytes()
        # Verify hash
        read_hash = hashlib.sha256(data).hexdigest()
        if read_hash != manifest["content_hash"]:
            raise ValueError(f"Content hash mismatch for {source_id} rev {manifest['revision']}: corrupt file")

        return {
            **manifest,
            "content": data.decode("utf-8", errors="replace"),
        }
