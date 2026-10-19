"""
Notes Plane Manager.
Ref: AI Knowledge OS Backend Specification §§5, 11, 56.
Maintains original user notes, immutable revisions, and separate provenance.
Guarantees: Notes Plane content is never silently overwritten or mutated.
"""

from __future__ import annotations
import hashlib
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.schema.models import utc_now


class RevisionConflict(ValueError):
    """The caller edited an older revision; its draft must be preserved."""


class NoteRevision:
    def __init__(
        self,
        id: str,
        revision: int,
        title: str,
        content: str,
        content_hash: str,
        scope: str,
        author: str,
        is_current: bool,
        created_at: str,
    ):
        self.id = id
        self.revision = revision
        self.title = title
        self.content = content
        self.content_hash = content_hash
        self.scope = scope
        self.author = author
        self.is_current = is_current
        self.created_at = created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "title": self.title,
            "content": self.content,
            "content_hash": self.content_hash,
            "scope": self.scope,
            "author": self.author,
            "is_current": self.is_current,
            "created_at": self.created_at,
        }


class NotesManager:
    """
    Manages the Notes Plane.
    Preserves raw source texts alongside versioned metadata in the database.
    """

    def __init__(self, storage: CanonicalStorage, raw_store: RawSourceStore):
        self.storage = storage
        self.raw_store = raw_store
        self.raw_store.canonical_storage = storage

    def _store_raw(self, conn, note_id, revision, content, content_hash, now_str):
        # Immutable content-addressed files; the manifest commits with the note.
        # An interrupted transaction can leave an unreferenced blob, never a
        # manifest pointing to a partial file or a shifted revision number.
        import os
        blob = self.raw_store.base_dir / f"{content_hash}.raw"
        if blob.exists():
            if hashlib.sha256(blob.read_bytes()).hexdigest() != content_hash:
                raise ValueError('Raw source integrity check failed')
        else:
            staging = blob.with_suffix(f'.{uuid.uuid4().hex}.tmp')
            with staging.open('xb') as handle:
                handle.write(content.encode('utf-8'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, blob)
        conn.execute(
            'INSERT INTO raw_manifests VALUES (?, ?, ?, ?, ?, ?, ?)',
            (note_id, revision, str(blob.resolve()), content_hash,
             len(content.encode('utf-8')), 'text/markdown', now_str),
        )

    def create_note(
        self,
        title: str,
        content: str,
        scope: str = "default",
        author: str = "user",
        note_id: Optional[str] = None,
    ) -> NoteRevision:
        """Create a new note (revision 1)."""
        nid = note_id or f"note-{uuid.uuid4().hex[:10]}"
        content_bytes = content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        now_str = utc_now().isoformat()

        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            self._store_raw(conn, nid, 1, content, content_hash, now_str)
            conn.execute(
                """
                INSERT INTO notes (id, revision, title, content, content_hash, scope, author, is_current, created_at)
                VALUES (?, 1, ?, ?, ?, ?, ?, 1, ?);
                """,
                (nid, title, content, content_hash, scope, author, now_str),
            )
            # Emit outbox event
            self.storage._insert_outbox_event(
                conn,
                aggregate_type="NOTE",
                aggregate_id=nid,
                event_type="NOTE_CREATED",
                payload={"note_id": nid, "revision": 1, "scope": scope},
            )
            conn.execute("COMMIT;")

        return NoteRevision(
            id=nid,
            revision=1,
            title=title,
            content=content,
            content_hash=content_hash,
            scope=scope,
            author=author,
            is_current=True,
            created_at=now_str,
        )

    def revise_note(
        self,
        note_id: str,
        title: str,
        content: str,
        author: str = "user",
        expected_revision: Optional[int] = None,
    ) -> NoteRevision:
        """
        Append a new immutable revision to an existing note without altering historical text.
        """
        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute(
                "SELECT revision, scope FROM notes WHERE id = ? AND is_current = 1;",
                (note_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Note '{note_id}' does not exist.")

            old_rev = row["revision"]
            if expected_revision is not None and expected_revision != old_rev:
                raise RevisionConflict(f'Note changed to v{old_rev}. Your draft was not saved; reopen the current version before merging it.')
            scope = row["scope"]
            new_rev = old_rev + 1

            content_bytes = content.encode("utf-8")
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            now_str = utc_now().isoformat()

            self._store_raw(conn, note_id, new_rev, content, content_hash, now_str)
            conn.execute(
                "UPDATE notes SET is_current = 0 WHERE id = ?;",
                (note_id,),
            )
            conn.execute(
                """
                INSERT INTO notes (id, revision, title, content, content_hash, scope, author, is_current, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?);
                """,
                (note_id, new_rev, title, content, content_hash, scope, author, now_str),
            )
            self.storage._insert_outbox_event(
                conn,
                aggregate_type="NOTE",
                aggregate_id=note_id,
                event_type="NOTE_REVISED",
                payload={"note_id": note_id, "revision": new_rev, "scope": scope},
            )
            conn.execute("COMMIT;")

        return NoteRevision(
            id=note_id,
            revision=new_rev,
            title=title,
            content=content,
            content_hash=content_hash,
            scope=scope,
            author=author,
            is_current=True,
            created_at=now_str,
        )

    def get_note(self, note_id: str, revision: Optional[int] = None) -> Optional[NoteRevision]:
        """Fetch current or specific historical revision of a note."""
        with self.storage.session() as conn:
            if revision is None:
                row = conn.execute(
                    "SELECT * FROM notes WHERE id = ? AND is_current = 1;",
                    (note_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM notes WHERE id = ? AND revision = ?;",
                    (note_id, revision),
                ).fetchone()

            if not row:
                return None

            return NoteRevision(
                id=row["id"],
                revision=row["revision"],
                title=row["title"],
                content=row["content"],
                content_hash=row["content_hash"],
                scope=row["scope"],
                author=row["author"],
                is_current=bool(row["is_current"]),
                created_at=row["created_at"],
            )

    def list_notes(self, scope: Optional[str] = None, limit: int = 50) -> List[NoteRevision]:
        """List current notes within optional scope."""
        query = "SELECT * FROM notes WHERE is_current = 1"
        params: List[Any] = []
        if scope:
            query += " AND scope = ?"
            params.append(scope)
        query += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)

        with self.storage.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                NoteRevision(
                    id=r["id"],
                    revision=r["revision"],
                    title=r["title"],
                    content=r["content"],
                    content_hash=r["content_hash"],
                    scope=r["scope"],
                    author=r["author"],
                    is_current=bool(r["is_current"]),
                    created_at=r["created_at"],
                )
                for r in rows
            ]
