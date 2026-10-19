#!/usr/bin/env python3
"""Post-Session Ingestion Hook for AI Library.

Zero-friction background memory accumulator.
Runs at session termination (e.g., Antigravity stop hook, post-commit, or CI).
Inspects session changes, and when autoRecordToLibrary is True or --force is supplied,
automatically persists a structured learning note into AI Library.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.settings.api import _read_preferences


def get_git_diff_summary(repo_root: Path) -> str:
    """Get a concise summary of recent modified files and diff stat."""
    try:
        res = subprocess.run(
            ['git', 'diff', '--stat', 'HEAD~1'],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    try:
        res = subprocess.run(
            ['git', 'status', '--short'],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def run_ingest(
    data_dir: Path,
    scope: str = "default",
    principal: str = "antigravity",
    title: str | None = None,
    content: str | None = None,
    force: bool = False,
) -> dict:
    """Execute post-session ingestion."""
    prefs = _read_preferences(data_dir)
    auto_record = prefs.get('automation', {}).get('autoRecordToLibrary', False)

    if not auto_record and not force:
        return {
            "status": "skipped",
            "reason": "autoRecordToLibrary is disabled in preferences.json (use --force to override)",
        }

    # Resolve db path
    db_path = data_dir / "canonical.db"
    if not db_path.exists():
        # Fallback to local default database
        alt_db = PROJECT_ROOT / "data" / "canonical.db"
        if alt_db.exists():
            db_path = alt_db
        else:
            return {"status": "error", "error": f"Database not found at {db_path}"}

    # Gather content if not provided
    if not content:
        diff_stat = get_git_diff_summary(PROJECT_ROOT)
        activity_file = PROJECT_ROOT / ".agent-tmp" / "session_activity.json"
        activity_summary = ""
        if activity_file.exists():
            try:
                data = json.loads(activity_file.read_text(encoding="utf-8"))
                activity_summary = f"Recorded activities: {len(data)} actions"
            except Exception:
                pass

        if not diff_stat and not activity_summary:
            return {
                "status": "skipped",
                "reason": "No detectable session changes or git modifications.",
            }

        title = title or f"Session Insight · {principal} ({Path.cwd().name})"
        content = (
            f"# {title}\n\n"
            f"- **Agent**: `mcp-agent:{principal}`\n"
            f"- **Scope**: `{scope}`\n"
            f"- **Tags**: `#session-insight #auto-ingest`\n\n"
            f"## 变更摘要 (Changes Summary)\n\n"
            f"```text\n{diff_stat or activity_summary}\n```\n"
        )
    else:
        title = title or f"Session Record · {principal}"

    # Insert note into storage
    storage = CanonicalStorage(str(db_path))
    raw_store = RawSourceStore(str(data_dir / "raw_sources"), canonical_storage=storage)
    notes_mgr = NotesManager(storage, raw_store)
    author = f"mcp-agent:{principal}"
    note = notes_mgr.create_note(
        title=title,
        content=content,
        scope=scope,
        author=author,
    )

    return {
        "status": "recorded",
        "note_id": note.id,
        "revision": note.revision,
        "title": note.title,
        "author": note.author,
        "scope": note.scope,
    }


def main():
    parser = argparse.ArgumentParser(description="Post-session automated knowledge ingestion hook")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"), help="Path to data directory")
    parser.add_argument("--scope", default="default", help="Scope for ingested note")
    parser.add_argument("--principal", default="antigravity", help="Agent principal name")
    parser.add_argument("--title", default=None, help="Note title")
    parser.add_argument("--content", default=None, help="Explicit note content")
    parser.add_argument("--force", action="store_true", help="Force ingestion regardless of autoRecordToLibrary")
    args = parser.parse_args()

    result = run_ingest(
        data_dir=Path(args.data_dir),
        scope=args.scope,
        principal=args.principal,
        title=args.title,
        content=args.content,
        force=args.force,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
