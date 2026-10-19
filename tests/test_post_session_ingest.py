import pytest
from pathlib import Path
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from scripts.hooks.post_session_ingest import run_ingest


def test_post_session_ingest_respects_preference_disabled(tmp_path: Path):
    db_path = tmp_path / "canonical.db"
    storage = CanonicalStorage(str(db_path))

    # Without preferences.json or autoRecordToLibrary, should skip
    res = run_ingest(
        data_dir=tmp_path,
        principal="test-agent",
        force=False,
    )
    assert res["status"] == "skipped"
    assert "autoRecordToLibrary is disabled" in res["reason"]


def test_post_session_ingest_force_creates_note(tmp_path: Path):
    db_path = tmp_path / "canonical.db"
    storage = CanonicalStorage(str(db_path))

    res = run_ingest(
        data_dir=tmp_path,
        principal="antigravity",
        title="Test Ingestion Insight",
        content="Bug in cache prefix layout resolved with canonical sorting.",
        force=True,
    )
    assert res["status"] == "recorded"
    assert res["note_id"].startswith("note-")
    assert res["author"] == "mcp-agent:antigravity"
    assert res["title"] == "Test Ingestion Insight"

    # Verify directly from database
    with storage.session() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (res["note_id"],)).fetchone()
        assert row is not None
        assert row["author"] == "mcp-agent:antigravity"
        assert "canonical sorting" in row["content"]
