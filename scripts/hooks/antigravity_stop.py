#!/usr/bin/env python3
"""Antigravity Stop Hook for AI Library.
Runs when the agent execution turn terminates.
Inspects session activity and ensures critical changes have been tracked or audited.
Contract: Stdin JSON -> Stdout JSON {} or {"decision": "continue", "reason": "..."}
"""
import sys
import json
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

def main():
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    root = Path(__file__).resolve().parents[2]
    activity_file = root / '.agent-tmp' / 'session_activity.json'

    # Read activity if present
    file_changes = []
    if activity_file.exists():
        try:
            with open(activity_file, 'r', encoding='utf-8') as f:
                activity = json.load(f)
            file_changes = [a.get('target_file') for a in activity if a.get('target_file')]
        except Exception:
            file_changes = []

    # Clean up or allow normal termination
    # If substantial changes occurred and autoRecordToLibrary is enabled, auto-ingest
    try:
        from scripts.hooks.post_session_ingest import run_ingest
        data_dir = root / 'data'
        run_ingest(data_dir=data_dir, principal='antigravity')
    except Exception:
        pass

    sys.stdout.write(json.dumps({}))
    sys.stdout.flush()

if __name__ == '__main__':
    main()
