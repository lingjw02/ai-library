"""Tests for Multimedia storage/streaming, Chat History session stitching, and Reader upgrades."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app


PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
)

MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"trailer<< /Root 1 0 R /Size 4 >>\n"
    b"%%EOF\n"
)


def _client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "test_multimedia.db"
    raw_dir = tmp_path / "raw_sources"
    artifacts_dir = tmp_path / "artifacts"
    app = create_app(str(db_path), str(raw_dir), str(artifacts_dir))
    return TestClient(app)


def test_media_upload_and_range_streaming(tmp_path: Path):
    client = _client(tmp_path)

    # 1. Upload an image without creating a note
    res = client.post(
        "/api/media/upload",
        params={"filename": "sample.png", "create_note": "false"},
        content=PNG_1X1_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert res.status_code == 201, res.text
    payload = res.json()
    assert payload["note"] is None
    media = payload["media"]
    assert media["media_kind"] == "image"
    assert media["mime_type"] == "image/png"
    assert media["byte_length"] == len(PNG_1X1_BYTES)
    media_id = media["id"]

    # 2. Fetch full media stream
    full_res = client.get(f"/api/media/{media_id}")
    assert full_res.status_code == 200
    assert full_res.headers["accept-ranges"] == "bytes"
    assert full_res.content == PNG_1X1_BYTES

    # 3. Fetch partial content with HTTP Range header (used by video/audio/PDF viewers)
    range_res = client.get(f"/api/media/{media_id}", headers={"Range": "bytes=0-15"})
    assert range_res.status_code == 206
    assert range_res.headers["content-range"] == f"bytes 0-15/{len(PNG_1X1_BYTES)}"
    assert range_res.content == PNG_1X1_BYTES[:16]


def test_media_upload_creates_multimedia_note(tmp_path: Path):
    client = _client(tmp_path)

    res = client.post(
        "/api/media/upload",
        params={
            "filename": "paper.pdf",
            "create_note": "true",
            "title": "Architecture Whitepaper",
            "caption": "Core system diagrams and specs",
        },
        content=MINIMAL_PDF_BYTES,
        headers={"Content-Type": "application/pdf"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["media"]["media_kind"] == "pdf"
    assert data["note"] is not None
    note = data["note"]
    assert note["title"] == "Architecture Whitepaper"
    assert data["media"]["url"] in note["content"]


def test_chat_session_stitching_and_multimedia_resolution(tmp_path: Path):
    client = _client(tmp_path)

    # Create a real local image file referenced inside a chat history note
    local_img = tmp_path / "screenshot_bug.png"
    local_img.write_bytes(PNG_1X1_BYTES)

    # Create a raw JSONL chat log containing a base64 data URI attachment
    jsonl_dir = tmp_path / "sessions" / "2026" / "04" / "18"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    session_id = "019d9999-8888-7777-6666-555544443333"
    jsonl_file = jsonl_dir / f"rollout-2026-04-18T12-00-00-{session_id}.jsonl"
    data_uri = "data:image/png;base64," + base64.b64encode(PNG_1X1_BYTES).decode("ascii")
    jsonl_lines = [
        json.dumps({"type": "session_meta", "payload": {"id": session_id, "cwd": str(tmp_path)}}),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Here is the UI screenshot and inline image:"},
                        {"type": "input_image", "image_url": data_uri},
                    ],
                },
            }
        ),
    ]
    jsonl_file.write_text("\n".join(jsonl_lines), encoding="utf-8")

    fake_sha = "a" * 64
    # Create two multi-part chat notes (1/2 and 2/2) with a split turn across the 5000-char boundary
    note1_content = (
        f"会话 {session_id}；类别 AI_library；分段 1/2\n"
        f"来源性质：本地会话记录\n"
        f"正文为可见文字导入副本（分段 1/2）\n"
        f"副本全文 SHA256：{fake_sha}\n\n"
        f"### 消息 1｜user｜2026-04-18T12:00:00Z\n"
        f"源文件：{jsonl_file}\n"
        f"Look at {local_img} and `<image name=[Image #1]>`\n"
        f"[附件引用 input_image x1]\n\n"
        f"### 消息 2｜assistant｜2026-04-18T12:01:00Z\n"
        f"源文件：{jsonl_file}\n"
        f"First half of a long assistant explanation "
    )
    note2_content = (
        f"会话 {session_id}；类别 AI_library；分段 2/2\n"
        f"来源性质：本地会话记录\n"
        f"正文为可见文字导入副本（分段 2/2）\n"
        f"副本全文 SHA256：{fake_sha}\n\n"
        f"that continues seamlessly in part 2 without breaking the turn!\n"
    )

    r1 = client.post(
        "/api/notes",
        json={
            "title": f"聊天原文｜AI_library｜{session_id[:8]}｜1/2",
            "content": note1_content,
        },
    )
    assert r1.status_code == 201, r1.text
    note1_id = r1.json()["id"]

    r2 = client.post(
        "/api/notes",
        json={
            "title": f"聊天原文｜AI_library｜{session_id[:8]}｜2/2",
            "content": note2_content,
        },
    )
    assert r2.status_code == 201, r2.text
    note2_id = r2.json()["id"]

    # Fetch stitched chat session bundle from note 1
    chat_res = client.get(f"/api/notes/{note1_id}/chat-session")
    assert chat_res.status_code == 200, chat_res.text
    bundle = chat_res.json()

    assert bundle["is_chat_session"] is True
    assert bundle["session_id"] == session_id
    assert bundle["total_parts"] == 2
    assert [c["id"] for c in bundle["chunks"]] == [note1_id, note2_id]

    # Verify the split assistant turn across (1/2) -> (2/2) was healed into a single turn
    messages = bundle["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert "First half of a long assistant explanation" in messages[1]["text"]
    assert "continues seamlessly in part 2" in messages[1]["text"]

    # Verify the local file image referenced in the chat turn was resolved into a media asset
    assert len(bundle["session_media"]) >= 1
    assert len(messages[0]["media"]) >= 1
    for m in messages[0]["media"]:
        stream_check = client.get(m["url"])
        assert stream_check.status_code == 200
        assert stream_check.content == PNG_1X1_BYTES


def test_frontend_serves_content_renderer_and_updated_html(tmp_path: Path):
    client = _client(tmp_path)

    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.text
    assert "note-view-mode-group" in html
    assert "note-rendered-view" in html
    assert "reader-resizer" in html

    js_res = client.get("/js/content-renderer.js")
    assert js_res.status_code == 200
    assert "renderChatSessionHtml" in js_res.text
    assert "openMediaLightbox" in js_res.text
