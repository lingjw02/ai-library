"""FastAPI REST Router for Multimedia Upload, HTTP Range Streaming, and Chat Session Media."""
from __future__ import annotations
from pathlib import Path
import re
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.media.manager import MediaManager


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def create_media_router(storage, notes_mgr, raw_store) -> APIRouter:
    manager = MediaManager(storage, notes_mgr, raw_store)
    router = APIRouter()
    router.media_manager = manager

    @router.post("/api/media/upload", status_code=201)
    async def upload_media(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        scope: str = Query(default="default", min_length=1, max_length=200),
        title: str = Query(default="", max_length=500),
        caption: str = Query(default="", max_length=10000),
        note_id: Optional[str] = Query(default=None, max_length=200),
        create_note: bool = Query(default=False),
    ):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > manager.MAX_MEDIA_BYTES:
                raise HTTPException(status_code=413, detail="Uploaded media exceeds the 100 MB limit")

        if not data:
            raise HTTPException(status_code=422, detail="Uploaded media file is empty")

        raw_bytes = bytes(data)
        try:
            if create_note:
                return await run_in_threadpool(
                    lambda: manager.create_multimedia_note(
                        raw_bytes,
                        filename,
                        title=title,
                        caption=caption,
                        scope=scope,
                    )
                )
            asset = await run_in_threadpool(
                lambda: manager.store_media_bytes(
                    raw_bytes,
                    filename,
                    scope=scope,
                    source_note_id=note_id,
                    caption=caption,
                )
            )
            return {"media": asset, "note": None}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/media")
    def list_media(
        scope: Optional[str] = Query(default=None),
        note_id: Optional[str] = Query(default=None),
        session_id: Optional[str] = Query(default=None),
        media_kind: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return manager.list_media(
            scope=scope,
            note_id=note_id,
            session_id=session_id,
            media_kind=media_kind,
            limit=limit,
        )

    @router.get("/api/media/{media_id}/info")
    def get_media_info(media_id: str):
        asset = manager.get_media(media_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Media asset not found")
        return asset

    @router.api_route("/api/media/{media_id}", methods=["GET", "HEAD"])
    def stream_media(media_id: str, request: Request, download: bool = Query(default=False)):
        asset = manager.get_media(media_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Media asset not found")

        file_path = Path(asset["file_path"])
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Media blob missing on disk")

        file_size = file_path.stat().st_size
        mime_type = asset["mime_type"] or "application/octet-stream"
        disp_type = "attachment" if download else "inline"
        encoded_name = quote(asset["filename"])
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f"{disp_type}; filename*=UTF-8''{encoded_name}",
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        }

        if request.method == "HEAD":
            headers["Content-Length"] = str(file_size)
            return Response(status_code=200, media_type=mime_type, headers=headers)

        range_header = request.headers.get("range") or request.headers.get("Range")
        if range_header:
            m = _RANGE_RE.match(range_header.strip())
            if m:
                start_s, end_s = m.group(1), m.group(2)
                if start_s == "" and end_s != "":
                    suffix_len = int(end_s)
                    start = max(0, file_size - suffix_len)
                    end = file_size - 1
                else:
                    start = int(start_s) if start_s else 0
                    end = int(end_s) if end_s else file_size - 1
                if start >= file_size or start > end:
                    headers["Content-Range"] = f"bytes */{file_size}"
                    return Response(status_code=416, headers=headers)
                end = min(end, file_size - 1)
                chunk_len = end - start + 1
                headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                headers["Content-Length"] = str(chunk_len)

                def iter_range():
                    with file_path.open("rb") as fh:
                        fh.seek(start)
                        remaining = chunk_len
                        while remaining > 0:
                            buf = fh.read(min(65536, remaining))
                            if not buf:
                                break
                            remaining -= len(buf)
                            yield buf

                return StreamingResponse(
                    iter_range(),
                    status_code=206,
                    media_type=mime_type,
                    headers=headers,
                )

        headers["Content-Length"] = str(file_size)

        def iter_full():
            with file_path.open("rb") as fh:
                while True:
                    buf = fh.read(65536)
                    if not buf:
                        break
                    yield buf

        return StreamingResponse(
            iter_full(),
            status_code=200,
            media_type=mime_type,
            headers=headers,
        )

    @router.get("/api/notes/{note_id}/chat-session")
    def get_note_chat_session(
        note_id: str,
        scope: str = Query(default="default", min_length=1, max_length=200),
        full_session: bool = Query(default=True),
    ):
        try:
            return manager.get_chat_session_bundle(
                note_id,
                scope=scope,
                include_full_session=full_session,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
