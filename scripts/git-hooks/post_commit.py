#!/usr/bin/env python3
"""Post-commit hook for AI Library repository.
Automatically records git commits as provenanced development notes in AI Library.
"""
import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    root = Path(__file__).resolve().parents[2]

    # 1. Extract commit metadata
    try:
        proc = subprocess.run(
            ['git', 'log', '-1', '--format=%H|%h|%an|%ae|%s'],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return
        parts = proc.stdout.strip().split('|')
        full_hash, short_hash, author, email, subject = parts[0], parts[1], parts[2], parts[3], parts[4]

        # Get list of changed files
        diff_proc = subprocess.run(
            ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        changed_files = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]
    except Exception as e:
        print(f"[AI Library Post-Commit Warning] Failed to inspect git commit: {e}")
        return

    # 2. Record note in AI Library
    db_path = root / 'data' / 'knowledge_os.db'
    raw_dir = root / 'data' / 'raw_sources'
    if not db_path.exists():
        return

    try:
        sys.path.insert(0, str(root))
        from backend.storage.canonical.sqlite_storage import CanonicalStorage
        from backend.storage.raw.raw_store import RawSourceStore
        from backend.notes.notes_manager import NotesManager

        storage = CanonicalStorage(str(db_path))
        raw_store = RawSourceStore(str(raw_dir))
        notes_mgr = NotesManager(storage, raw_store)

        file_list_md = "\n".join(f"- `{f}`" for f in changed_files[:30])
        if len(changed_files) > 30:
            file_list_md += f"\n- ... and {len(changed_files) - 30} more files"

        content = (
            f"### Git Commit Record\n\n"
            f"- **Commit Hash**: `{full_hash}`\n"
            f"- **Author**: {author} <{email}>\n"
            f"- **Message**: {subject}\n"
            f"- **Changed Files** ({len(changed_files)}):\n"
            f"{file_list_md}\n"
        )

        title = f"[Git Commit] {short_hash}: {subject[:80]}"
        note = notes_mgr.create_note(
            title=title,
            content=content,
            scope='default',
            author=f"git-hook:{author}"
        )
        print(f"🔗 [AI Library] Commit {short_hash} recorded as knowledge note: {note.id}")
    except Exception as e:
        print(f"[AI Library Post-Commit Notice] Note recording skipped: {e}")

if __name__ == '__main__':
    main()
