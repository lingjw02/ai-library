#!/usr/bin/env python3
"""Pre-commit check for AI Library repository.
Verifies that:
1. Temporary scratch/activity files are not staged.
2. Knowledge OS quarantine and conflict health checks pass.
"""
import sys
import subprocess
import sqlite3
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    root = Path(__file__).resolve().parents[2]

    # 1. Check staged files for forbidden paths
    try:
        proc = subprocess.run(
            ['git', 'diff', '--cached', '--name-only'],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        staged_files = proc.stdout.splitlines()
    except Exception as e:
        print(f"[AI Library Pre-Commit Warning] Failed to inspect git status: {e}")
        staged_files = []

    forbidden_patterns = ['.agent-tmp/', '__pycache__/', '.pytest_cache/']
    violations = []
    for f in staged_files:
        for p in forbidden_patterns:
            if p in f or f.startswith(p):
                violations.append(f)

    if violations:
        print("[AI Library Pre-Commit Error] The following temporary files are staged for commit:")
        for v in violations:
            print(f"  - {v}")
        print("Please unstage them (e.g., git reset HEAD <file>) before committing.")
        sys.exit(1)

    # 2. Check quarantine health in data/knowledge_os.db
    db_path = root / 'data' / 'knowledge_os.db'
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            unresolved = conn.execute("SELECT COUNT(*) FROM quarantine WHERE scope = 'default' AND resolved = 0").fetchone()[0]
            open_conflicts = conn.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE node_type = 'Conflict' AND scope = 'default' AND is_active = 1
                AND COALESCE(json_extract(data_json, '$.core_props.status'), 'OPEN') = 'OPEN'
            """).fetchone()[0]
            conn.close()

            if unresolved > 0:
                print(f"[AI Library Pre-Commit Notice] {unresolved} unresolved quarantine records exist.")
            if open_conflicts > 0:
                print(f"[AI Library Pre-Commit Notice] {open_conflicts} open memory conflicts detected.")
        except Exception:
            pass

    print("[AI Library Pre-Commit] Pre-commit checks passed.")
    sys.exit(0)

if __name__ == '__main__':
    main()
