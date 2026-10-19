"""
Multimedia & Chat History Media Manager.
Preserves content-addressed SHA-256 media blobs in RawSourceStore,
supports PDF text extraction, image/video/audio streaming, and
resolves multimedia + multi-chunk transcripts for Chat History notes.
"""

from __future__ import annotations
import base64
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Dict, List, Optional, Tuple

from backend.schema.models import utc_now


MEDIA_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    media_kind TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'default',
    owner TEXT NOT NULL DEFAULT 'local-owner',
    source_note_id TEXT,
    session_id TEXT,
    turn_number INTEGER,
    caption TEXT NOT NULL DEFAULT '',
    extracted_text TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_hash_scope ON media_assets(content_hash, scope);
CREATE INDEX IF NOT EXISTS idx_media_note ON media_assets(source_note_id);
CREATE INDEX IF NOT EXISTS idx_media_session ON media_assets(session_id);
"""

MIME_BY_EXT = {
    ".pdf": ("application/pdf", "pdf"),
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".jfif": ("image/jpeg", "image"),
    ".gif": ("image/gif", "image"),
    ".webp": ("image/webp", "image"),
    ".svg": ("image/svg+xml", "image"),
    ".bmp": ("image/bmp", "image"),
    ".avif": ("image/avif", "image"),
    ".mp4": ("video/mp4", "video"),
    ".webm": ("video/webm", "video"),
    ".mov": ("video/quicktime", "video"),
    ".m4v": ("video/mp4", "video"),
    ".ogv": ("video/ogg", "video"),
    ".mp3": ("audio/mpeg", "audio"),
    ".wav": ("audio/wav", "audio"),
    ".ogg": ("audio/ogg", "audio"),
    ".m4a": ("audio/mp4", "audio"),
    ".flac": ("audio/flac", "audio"),
    ".aac": ("audio/aac", "audio"),
}


def detect_media_type(filename: str, mime_hint: str = "") -> Tuple[str, str]:
    ext = PurePath(filename or "").suffix.lower()
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    if mime_hint:
        low = mime_hint.lower().split(";")[0].strip()
        if low == "application/pdf":
            return low, "pdf"
        if low.startswith("image/"):
            return low, "image"
        if low.startswith("video/"):
            return low, "video"
        if low.startswith("audio/"):
            return low, "audio"
    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        if guessed == "application/pdf":
            return guessed, "pdf"
        if guessed.startswith("image/"):
            return guessed, "image"
        if guessed.startswith("video/"):
            return guessed, "video"
        if guessed.startswith("audio/"):
            return guessed, "audio"
    return "application/octet-stream", "other"


def extract_pdf_text(data: bytes, max_chars: int = 120_000) -> str:
    """
    Extract readable text from PDF bytes using pypdf/fitz if available,
    or a safe pure-Python PDF stream parser fallback.
    """
    # Try pypdf / PyMuPDF first if present in environment
    for mod_name in ("pypdf", "PyPDF2"):
        try:
            import io
            mod = __import__(mod_name)
            reader = mod.PdfReader(io.BytesIO(data))
            pages = []
            for idx, page in enumerate(reader.pages, 1):
                txt = (page.extract_text() or "").strip()
                if txt:
                    pages.append(f"[第 {idx} 页]\n{txt}")
            if pages:
                return "\n\n".join(pages)[:max_chars]
        except Exception:
            pass

    # Pure-Python fallback: decompress FlateDecode streams and extract literal strings
    chunks: List[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw_stream = match.group(1)
        decoded = raw_stream
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decoded = zlib.decompress(raw_stream, wbits)
                break
            except Exception:
                continue
        if b"BT" not in decoded and b"Tj" not in decoded and b"TJ" not in decoded:
            continue
        text_parts: List[str] = []
        for lit in re.finditer(rb"\((?:\\.|[^\\()])*\)", decoded):
            inner = lit.group(0)[1:-1]
            inner = (
                inner.replace(b"\\n", b"\n")
                .replace(b"\\r", b"\r")
                .replace(b"\\t", b"\t")
                .replace(b"\\(", b"(")
                .replace(b"\\)", b")")
                .replace(b"\\\\", b"\\")
            )
            try:
                s = inner.decode("utf-8", errors="ignore").strip()
                if s and any(c.isalnum() for c in s):
                    text_parts.append(s)
            except Exception:
                pass
        if text_parts:
            chunks.append(" ".join(text_parts))
        if sum(len(c) for c in chunks) >= max_chars:
            break
    result = "\n\n".join(chunks).strip()
    return result[:max_chars]


class MediaManager:
    MAX_MEDIA_BYTES = 100 * 1024 * 1024  # 100 MB

    def __init__(self, storage, notes_mgr, raw_store):
        self.storage = storage
        self.notes_mgr = notes_mgr
        self.raw_store = raw_store
        with self.storage.session() as conn:
            conn.executescript(MEDIA_SCHEMA)

    def store_media_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        mime_type: str = "",
        scope: str = "default",
        owner: str = "local-owner",
        source_note_id: Optional[str] = None,
        session_id: Optional[str] = None,
        turn_number: Optional[int] = None,
        caption: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not data:
            raise ValueError("Media content cannot be empty")
        if len(data) > self.MAX_MEDIA_BYTES:
            raise ValueError(f"Media file exceeds limit ({self.MAX_MEDIA_BYTES // (1024 * 1024)} MB)")

        clean_name = PurePath(filename or "media.bin").name or "media.bin"
        resolved_mime, media_kind = detect_media_type(clean_name, mime_type)
        if media_kind == "other":
            raise ValueError(f"Unsupported media file type for '{clean_name}'")

        content_hash = hashlib.sha256(data).hexdigest()
        blob = self.raw_store.base_dir / f"{content_hash}.raw"
        if not blob.exists():
            staging = blob.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                with staging.open("xb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(staging, blob)
            finally:
                staging.unlink(missing_ok=True)

        extracted_text = ""
        if media_kind == "pdf":
            extracted_text = extract_pdf_text(data)

        now_str = utc_now().isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self.storage.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            existing = conn.execute(
                "SELECT * FROM media_assets WHERE content_hash = ? AND scope = ? LIMIT 1;",
                (content_hash, scope),
            ).fetchone()
            if existing:
                media_id = existing["id"]
                if source_note_id and not existing["source_note_id"]:
                    conn.execute(
                        "UPDATE media_assets SET source_note_id = ? WHERE id = ?;",
                        (source_note_id, media_id),
                    )
                if session_id and not existing["session_id"]:
                    conn.execute(
                        "UPDATE media_assets SET session_id = ?, turn_number = COALESCE(turn_number, ?) WHERE id = ?;",
                        (session_id, turn_number, media_id),
                    )
                conn.execute("COMMIT;")
                row = conn.execute("SELECT * FROM media_assets WHERE id = ?;", (media_id,)).fetchone()
                return self._row_to_dict(row)

            media_id = f"media-{content_hash[:16]}"
            if conn.execute("SELECT 1 FROM media_assets WHERE id = ?;", (media_id,)).fetchone():
                media_id = f"media-{uuid.uuid4().hex[:14]}"

            conn.execute(
                """
                INSERT INTO media_assets (
                    id, content_hash, filename, mime_type, media_kind, byte_length,
                    file_path, scope, owner, source_note_id, session_id, turn_number,
                    caption, extracted_text, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    media_id,
                    content_hash,
                    clean_name,
                    resolved_mime,
                    media_kind,
                    len(data),
                    str(blob.resolve()),
                    scope,
                    owner,
                    source_note_id,
                    session_id,
                    turn_number,
                    caption or "",
                    extracted_text,
                    meta_json,
                    now_str,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO raw_manifests VALUES (?, ?, ?, ?, ?, ?, ?);",
                (media_id, 1, str(blob.resolve()), content_hash, len(data), resolved_mime, now_str),
            )
            conn.execute("COMMIT;")
            row = conn.execute("SELECT * FROM media_assets WHERE id = ?;", (media_id,)).fetchone()
            return self._row_to_dict(row)

    def create_multimedia_note(
        self,
        data: bytes,
        filename: str,
        *,
        title: str = "",
        caption: str = "",
        scope: str = "default",
        author: str = "user",
    ) -> Dict[str, Any]:
        """Store media asset and create a rich Note linking to it."""
        asset = self.store_media_bytes(
            data,
            filename,
            scope=scope,
            owner=author,
            caption=caption,
        )
        media_id = asset["id"]
        media_url = f"/api/media/{media_id}"
        kind = asset["media_kind"]
        note_title = (title or "").strip() or asset["filename"]
        size_kb = max(1, round(asset["byte_length"] / 1024, 1))

        kind_badges = {
            "pdf": "📄 PDF 文档",
            "image": "🖼️ 图片资料",
            "video": "🎬 视频资料",
            "audio": "🎵 音频资料",
        }
        badge = kind_badges.get(kind, "📎 多媒体资料")

        lines = [
            f"<!-- media-asset: {media_id} | kind: {kind} | mime: {asset['mime_type']} | bytes: {asset['byte_length']} -->",
            f"### {badge}：{asset['filename']}",
            "",
        ]
        if kind == "image":
            lines.append(f"![{asset['filename']}]({media_url})")
        elif kind == "pdf":
            lines.append(f"![PDF 预览：{asset['filename']}]({media_url})")
        elif kind == "video":
            lines.append(f"![视频预览：{asset['filename']}]({media_url})")
        elif kind == "audio":
            lines.append(f"![音频预览：{asset['filename']}]({media_url})")
        else:
            lines.append(f"[{asset['filename']}]({media_url})")

        lines.append("")
        lines.append(f"- **文件名称**：`{asset['filename']}`")
        lines.append(f"- **媒体类型**：`{asset['mime_type']}`（{size_kb} KB）")
        lines.append(f"- **内容哈希 (SHA-256)**：`{asset['content_hash']}`")

        if caption and caption.strip():
            lines.extend(["", "### 备注说明", "", caption.strip()])

        if kind == "pdf" and asset.get("extracted_text"):
            lines.extend([
                "",
                "### PDF 提取正文（用于全文检索与候选提取）",
                "",
                asset["extracted_text"],
            ])

        note_rev = self.notes_mgr.create_note(
            title=note_title,
            content="\n".join(lines),
            scope=scope,
            author=author,
        )
        with self.storage.session() as conn:
            conn.execute(
                "UPDATE media_assets SET source_note_id = ? WHERE id = ?;",
                (note_rev.id, media_id),
            )
            conn.commit()
        asset["source_note_id"] = note_rev.id
        return {
            "media": asset,
            "note": note_rev.to_dict(),
        }

    def get_media(self, media_id: str) -> Optional[Dict[str, Any]]:
        with self.storage.session() as conn:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE id = ? OR content_hash = ? LIMIT 1;",
                (media_id, media_id),
            ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def list_media(
        self,
        *,
        scope: Optional[str] = None,
        note_id: Optional[str] = None,
        session_id: Optional[str] = None,
        media_kind: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if note_id:
            clauses.append("source_note_id = ?")
            params.append(note_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if media_kind:
            clauses.append("media_kind = ?")
            params.append(media_kind)
        params.append(limit)
        sql = f"SELECT * FROM media_assets WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?;"
        with self.storage.session() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json", "{}") or "{}")
        except Exception:
            d["metadata"] = {}
        d["url"] = f"/api/media/{d['id']}"
        return d

    # ------------------------------------------------------------------
    # Chat History Session Stitching & Multimedia Resolution
    # ------------------------------------------------------------------
    _SESSION_HEADER_RE = re.compile(
        r"会话\s+([0-9a-fA-F\-]{8,64})；类别\s+([^；\r\n]+)；分段\s+(\d+)/(\d+)"
    )
    _SUMMARY_SESSION_RE = re.compile(r"^会话：([0-9a-fA-F\-]{8,64})\s*$", re.M)
    _TURN_SPLIT_RE = re.compile(
        r"^###\s+(?:消息\s+(\d+)｜(user|assistant|system)｜([^\r\n]*)|(\d+)\.\s+(user|assistant|system)\s*\|\s*([^\r\n|]*)(?:\|\s*([^\r\n]*))?)\r?\n(?:(?:源文件|来源)：([^\r\n]+)\r?\n)?",
        re.M,
    )
    _LOCAL_MEDIA_PATH_RE = re.compile(
        r'([A-Za-z]:[\\/][^\r\n"\'<>|?*`]+?\.(?:pdf|png|jpe?g|jfif|gif|webp|svg|mp4|webm|mov|mp3|wav))',
        re.I,
    )
    _IMAGE_TAG_PATH_RE = re.compile(r'<image\b[^>]*\bpath="([^"]+)"[^>]*>', re.I)

    def get_chat_session_bundle(
        self,
        note_id: str,
        *,
        scope: str = "default",
        include_full_session: bool = True,
    ) -> Dict[str, Any]:
        """
        Inspect a note; if it belongs to a Chat History session (either a raw chunk
        `聊天原文｜...｜k/N` or a summary `聊天摘要｜...`), stitch all chunks in order,
        parse structured dialogue turns, and resolve embedded/referenced multimedia!
        """
        note = self.notes_mgr.get_note(note_id)
        if not note:
            raise LookupError(f"Note '{note_id}' not found")

        content = note.content or ""
        title = note.title or ""
        header_match = self._SESSION_HEADER_RE.search(content[:600])
        summary_match = self._SUMMARY_SESSION_RE.search(content[:1000])

        session_id: Optional[str] = None
        category: Optional[str] = None
        current_part = 1
        total_parts = 1
        is_summary = "聊天摘要｜" in title or bool(summary_match and not header_match)

        if header_match:
            session_id = header_match.group(1).strip()
            category = header_match.group(2).strip()
            current_part = int(header_match.group(3))
            total_parts = int(header_match.group(4))
        elif summary_match:
            session_id = summary_match.group(1).strip()
            cat_m = re.search(r"^分类：([^\r\n（(]+)", content[:600], re.M)
            if cat_m:
                category = cat_m.group(1).strip()

        # Check if note has generic turns even without a session_id
        has_turns = bool(self._TURN_SPLIT_RE.search(content))
        if not session_id and not has_turns:
            # Still resolve any local media paths or media assets attached to this note
            note_media = self.resolve_note_media(note.id, content, scope=note.scope)
            return {
                "is_chat_session": False,
                "note_id": note.id,
                "media": note_media,
            }

        chunks_meta: List[Dict[str, Any]] = []
        summary_note: Optional[Dict[str, Any]] = None
        chunk_rows: List[Tuple[int, Dict[str, Any]]] = []

        if session_id:
            with self.storage.session() as conn:
                like_pat = f"%{session_id}%"
                rows = conn.execute(
                    "SELECT id, revision, title, content, content_hash, scope, author, created_at "
                    "FROM notes WHERE is_current = 1 AND scope = ? AND content LIKE ?;",
                    (note.scope, like_pat),
                ).fetchall()
                for r in rows:
                    r_dict = dict(r)
                    hm = self._SESSION_HEADER_RE.search((r_dict["content"] or "")[:600])
                    if hm and hm.group(1).strip() == session_id:
                        part_num = int(hm.group(3))
                        total_parts = max(total_parts, int(hm.group(4)))
                        if not category:
                            category = hm.group(2).strip()
                        chunk_rows.append((part_num, r_dict))
                    elif "聊天摘要" in (r_dict["title"] or "") or self._SUMMARY_SESSION_RE.search((r_dict["content"] or "")[:1000]):
                        summary_note = {
                            "id": r_dict["id"],
                            "revision": r_dict["revision"],
                            "title": r_dict["title"],
                        }

            chunk_rows.sort(key=lambda x: x[0])
            chunks_meta = [
                {
                    "part": part_num,
                    "id": r_dict["id"],
                    "revision": r_dict["revision"],
                    "title": r_dict["title"],
                    "is_active": r_dict["id"] == note.id,
                }
                for part_num, r_dict in chunk_rows
            ]

        # Stitch body across chunks if include_full_session is True (or parse single chunk)
        if include_full_session and chunk_rows:
            body_segments = [self._strip_chunk_header(r_dict["content"]) for _, r_dict in chunk_rows]
            stitched_text = "".join(body_segments)
        else:
            stitched_text = self._strip_chunk_header(content)

        messages = self._parse_turns(stitched_text)

        # Resolve multimedia for the chat turns & session
        session_media = self._resolve_chat_session_media(
            session_id=session_id,
            note_id=note.id,
            scope=note.scope,
            messages=messages,
        )

        return {
            "is_chat_session": True,
            "is_summary": is_summary,
            "note_id": note.id,
            "session_id": session_id,
            "category": category or "未分类",
            "current_part": current_part,
            "total_parts": total_parts,
            "chunks": chunks_meta,
            "summary_note": summary_note,
            "message_count": len(messages),
            "messages": messages,
            "session_media": session_media,
        }

    def _strip_chunk_header(self, text: str) -> str:
        """
        Strip the 4-line provenance preamble added by import_batch.py to each 5000-char chunk
        so adjacent chunks concatenate cleanly back into the original transcript!
        """
        if not text:
            return ""
        m = re.match(
            r"^会话\s+[0-9a-fA-F\-]+；类别\s+[^\r\n]+\r?\n"
            r"来源性质：[^\r\n]+\r?\n"
            r"正文为可见文字导入副本[^\r\n]+\r?\n"
            r"副本全文 SHA256：[0-9a-fA-F]{64}\r?\n\r?\n",
            text,
        )
        if m:
            return text[m.end():]
        return text

    def _parse_turns(self, text: str) -> List[Dict[str, Any]]:
        matches = list(self._TURN_SPLIT_RE.finditer(text))
        if not matches:
            return []
        turns: List[Dict[str, Any]] = []
        for idx, m in enumerate(matches):
            num_str = m.group(1) or m.group(4) or str(idx + 1)
            role = (m.group(2) or m.group(5) or "user").lower()
            timestamp = (m.group(3) or m.group(6) or "").strip()
            phase = (m.group(7) or "").strip()
            source_ref = (m.group(8) or "").strip()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[start:end].strip()

            has_attachment_marker = "附件引用" in body
            turns.append({
                "number": int(num_str) if num_str.isdigit() else idx + 1,
                "role": role,
                "timestamp": timestamp,
                "phase": phase,
                "source_ref": source_ref,
                "text": body,
                "has_attachment_marker": has_attachment_marker,
                "media": [],
            })
        return turns

    def resolve_note_media(self, note_id: str, content: str, *, scope: str = "default") -> List[Dict[str, Any]]:
        """Resolve any media assets linked to or referenced by path in an ordinary note."""
        found: Dict[str, Dict[str, Any]] = {}
        for item in self.list_media(scope=scope, note_id=note_id):
            found[item["id"]] = item

        # Also check `<!-- media-asset: media-xxxx ... -->` or `/api/media/media-xxxx`
        for m in re.finditer(r"/api/media/(media-[a-zA-Z0-9_-]+)", content or ""):
            asset = self.get_media(m.group(1))
            if asset:
                found[asset["id"]] = asset

        # Also check local file paths in note content
        for m in self._LOCAL_MEDIA_PATH_RE.finditer(content or ""):
            raw_path = m.group(1).strip()
            asset = self._ingest_local_file_if_exists(raw_path, scope=scope, source_note_id=note_id)
            if asset:
                found[asset["id"]] = asset

        return list(found.values())

    def _ingest_local_file_if_exists(
        self,
        raw_path: str,
        *,
        scope: str = "default",
        source_note_id: Optional[str] = None,
        session_id: Optional[str] = None,
        turn_number: Optional[int] = None,
        caption: str = "",
    ) -> Optional[Dict[str, Any]]:
        try:
            p = Path(raw_path.strip().strip('"').strip("'"))
            if not p.exists() or not p.is_file():
                return None
            size = p.stat().st_size
            if size <= 0 or size > self.MAX_MEDIA_BYTES:
                return None
            data = p.read_bytes()
            return self.store_media_bytes(
                data,
                p.name,
                scope=scope,
                source_note_id=source_note_id,
                session_id=session_id,
                turn_number=turn_number,
                caption=caption or f"本地路径：{p}",
                metadata={"original_path": str(p)},
            )
        except Exception:
            return None

    def _resolve_chat_session_media(
        self,
        *,
        session_id: Optional[str],
        note_id: str,
        scope: str,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Resolve multimedia across a chat session's turns:
        1. `<image path="...">` and local file paths (`.png`, `.jpg`, `.pdf`, `.mp4`, etc.) inside turns
        2. `.codex/sessions` and `.codex/archived_sessions` JSONL line base64 `input_image` payloads
        3. `.codex/thread_history_1.sqlite` `userMessage` base64 images, `imageGeneration`, and `imageView` items
        4. `.codex/generated_images/<session_id>/*.png` generated artwork
        """
        all_session_media: Dict[str, Dict[str, Any]] = {}
        codex_root = Path.home() / ".codex"

        # 1. Scan each message turn for explicit `<image path="...">` and local media file paths
        for turn in messages:
            turn_media: Dict[str, Dict[str, Any]] = {}
            t_text = turn["text"] or ""

            for m in self._IMAGE_TAG_PATH_RE.finditer(t_text):
                asset = self._ingest_local_file_if_exists(
                    m.group(1),
                    scope=scope,
                    source_note_id=note_id,
                    session_id=session_id,
                    turn_number=turn["number"],
                )
                if asset:
                    turn_media[asset["id"]] = asset
                    all_session_media[asset["id"]] = asset

            for m in self._LOCAL_MEDIA_PATH_RE.finditer(t_text):
                asset = self._ingest_local_file_if_exists(
                    m.group(1),
                    scope=scope,
                    source_note_id=note_id,
                    session_id=session_id,
                    turn_number=turn["number"],
                )
                if asset:
                    turn_media[asset["id"]] = asset
                    all_session_media[asset["id"]] = asset

            # 2. If the turn has `[附件引用 ...]` and still needs media, check JSONL source line
            if turn["has_attachment_marker"] and not turn_media and turn.get("source_ref"):
                for asset in self._extract_from_jsonl_source_ref(
                    turn["source_ref"],
                    codex_root=codex_root,
                    scope=scope,
                    source_note_id=note_id,
                    session_id=session_id,
                    turn_number=turn["number"],
                ):
                    turn_media[asset["id"]] = asset
                    all_session_media[asset["id"]] = asset

            turn["media"] = list(turn_media.values())

        # 3. Check `.codex/thread_history_1.sqlite` and `.codex/generated_images/<session_id>` if session_id is known
        if session_id:
            extra_assets = self._extract_from_codex_session_stores(
                session_id=session_id,
                codex_root=codex_root,
                scope=scope,
                source_note_id=note_id,
                messages=messages,
            )
            for asset in extra_assets:
                all_session_media[asset["id"]] = asset

        return list(all_session_media.values())

    def _extract_from_jsonl_source_ref(
        self,
        source_ref: str,
        *,
        codex_root: Path,
        scope: str,
        source_note_id: str,
        session_id: Optional[str],
        turn_number: int,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        m = re.match(r"^(.+):(\d+)$", source_ref.strip())
        if not m:
            return results
        raw_file, line_no = Path(m.group(1).strip()), int(m.group(2))
        candidates = [
            raw_file,
            codex_root / "archived_sessions" / raw_file.name,
            codex_root / "sessions" / raw_file.name,
        ]
        target = next((c for c in candidates if c.exists() and c.is_file()), None)
        if not target:
            return results
        try:
            with target.open("r", encoding="utf-8", errors="replace") as fh:
                for idx, line in enumerate(fh, 1):
                    if idx == line_no:
                        obj = json.loads(line)
                        parts = obj.get("payload", {}).get("content", [])
                        for p_idx, pt in enumerate(parts, 1):
                            url_val = pt.get("image_url") or pt.get("url") or ""
                            if isinstance(url_val, dict):
                                url_val = url_val.get("url") or ""
                            asset = self._ingest_data_uri_or_path(
                                str(url_val),
                                fallback_name=f"chat_turn_{turn_number}_{p_idx}.png",
                                scope=scope,
                                source_note_id=source_note_id,
                                session_id=session_id,
                                turn_number=turn_number,
                            )
                            if asset:
                                results.append(asset)
                        break
        except Exception:
            pass
        return results

    def _ingest_data_uri_or_path(
        self,
        val: str,
        *,
        fallback_name: str,
        scope: str,
        source_note_id: Optional[str],
        session_id: Optional[str],
        turn_number: Optional[int],
        caption: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not val:
            return None
        if val.startswith("data:"):
            try:
                header, b64_data = val.split(",", 1)
                mime = header[5:].split(";")[0].strip() or "image/png"
                ext = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/jpg": ".jpg",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                    "application/pdf": ".pdf",
                }.get(mime.lower(), ".png")
                raw_bytes = base64.b64decode(b64_data)
                fname = PurePath(fallback_name).with_suffix(ext).name
                return self.store_media_bytes(
                    raw_bytes,
                    fname,
                    mime_type=mime,
                    scope=scope,
                    source_note_id=source_note_id,
                    session_id=session_id,
                    turn_number=turn_number,
                    caption=caption or "对话内嵌多媒体附件",
                )
            except Exception:
                return None
        return self._ingest_local_file_if_exists(
            val,
            scope=scope,
            source_note_id=source_note_id,
            session_id=session_id,
            turn_number=turn_number,
            caption=caption,
        )

    def _extract_from_codex_session_stores(
        self,
        *,
        session_id: str,
        codex_root: Path,
        scope: str,
        source_note_id: str,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        # Check already-ingested media for this session_id first
        existing_session_media = self.list_media(scope=scope, session_id=session_id, limit=50)
        if existing_session_media:
            results.extend(existing_session_media)
            # Attach turn-matched media to messages
            by_turn: Dict[int, List[Dict[str, Any]]] = {}
            for m_item in existing_session_media:
                t_num = m_item.get("turn_number")
                if t_num:
                    by_turn.setdefault(t_num, []).append(m_item)
            for turn in messages:
                if turn["number"] in by_turn:
                    existing_ids = {x["id"] for x in turn["media"]}
                    for item in by_turn[turn["number"]]:
                        if item["id"] not in existing_ids:
                            turn["media"].append(item)
            return results

        # A. Check `.codex/generated_images/<session_id>/*.png`
        gen_dir = codex_root / "generated_images" / session_id
        gen_prompts: Dict[str, str] = {}

        # B. Query `.codex/thread_history_1.sqlite` for imageGeneration prompts, imageView paths, and userMessage images
        th_db = codex_root / "thread_history_1.sqlite"
        if th_db.exists():
            try:
                tc = sqlite3.connect(f"file:{th_db}?mode=ro", uri=True)
                rows = tc.execute(
                    "SELECT item_id, item_type, created_at_ms, item_json FROM thread_items "
                    "WHERE thread_id = ? AND item_type IN ('imageGeneration', 'imageView', 'userMessage') "
                    "ORDER BY rollout_ordinal ASC;",
                    (session_id,),
                ).fetchall()
                tc.close()

                attachment_turns = [t for t in messages if t.get("has_attachment_marker") and not t.get("media")]
                att_idx = 0

                for item_id, item_type, created_ms, item_json_str in rows:
                    try:
                        obj = json.loads(item_json_str)
                    except Exception:
                        continue

                    if item_type == "imageGeneration":
                        prompt = (obj.get("revisedPrompt") or "").strip()
                        if prompt:
                            gen_prompts[item_id] = prompt
                    elif item_type == "imageView":
                        img_path = obj.get("path")
                        if img_path:
                            asset = self._ingest_local_file_if_exists(
                                img_path,
                                scope=scope,
                                source_note_id=source_note_id,
                                session_id=session_id,
                                caption=f"会话检视图片：{PurePath(img_path).name}",
                            )
                            if asset:
                                results.append(asset)
                    elif item_type == "userMessage":
                        for pt in obj.get("content", []):
                            ptype = pt.get("type", "")
                            if ptype in ("image", "input_image", "localImage"):
                                url_or_path = pt.get("imageUrl") or pt.get("image_url") or pt.get("url") or pt.get("path") or ""
                                target_turn = attachment_turns[att_idx] if att_idx < len(attachment_turns) else None
                                t_num = target_turn["number"] if target_turn else None
                                asset = self._ingest_data_uri_or_path(
                                    str(url_or_path),
                                    fallback_name=f"user_image_{item_id[:8]}.png",
                                    scope=scope,
                                    source_note_id=source_note_id,
                                    session_id=session_id,
                                    turn_number=t_num,
                                    caption="用户上传对话图片",
                                )
                                if asset:
                                    results.append(asset)
                                    if target_turn is not None:
                                        target_turn["media"].append(asset)
                                        att_idx += 1
            except Exception:
                pass

        if gen_dir.exists() and gen_dir.is_dir():
            for img_file in sorted(gen_dir.glob("*.*")):
                if img_file.suffix.lower() in MIME_BY_EXT:
                    stem = img_file.stem
                    prompt_caption = gen_prompts.get(stem, "AI 会话生成图片")
                    asset = self._ingest_local_file_if_exists(
                        str(img_file),
                        scope=scope,
                        source_note_id=source_note_id,
                        session_id=session_id,
                        caption=prompt_caption[:240],
                    )
                    if asset:
                        results.append(asset)

        return results
