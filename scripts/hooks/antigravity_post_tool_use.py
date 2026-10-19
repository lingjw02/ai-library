#!/usr/bin/env python3
"""Antigravity PostToolUse Hook for AI Library.
Runs after tools (write_to_file, replace_file_content, run_command) complete.
Audits tool actions and records session changes to .agent-tmp/session_activity.json.
Contract: Stdin JSON -> Stdout JSON {}
"""
import sys
import json
import time
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
    tmp_dir = root / '.agent-tmp'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    activity_file = tmp_dir / 'session_activity.json'

    # Load existing session activity
    activity = []
    if activity_file.exists():
        try:
            with open(activity_file, 'r', encoding='utf-8') as f:
                activity = json.load(f)
        except Exception:
            activity = []

    tool_call = payload.get('toolCall', {})
    name = tool_call.get('name', '')
    args = tool_call.get('args', {})
    step_idx = payload.get('stepIdx')

    record = {
        'timestamp': time.time(),
        'step_idx': step_idx,
        'tool': name,
        'has_error': bool(payload.get('error')),
    }

    if name in ('write_to_file', 'replace_file_content'):
        record['target_file'] = args.get('TargetFile', '')
    elif name == 'run_command':
        record['command_line'] = args.get('CommandLine', '')

    activity.append(record)
    # Keep last 100 entries
    if len(activity) > 100:
        activity = activity[-100:]

    try:
        with open(activity_file, 'w', encoding='utf-8') as f:
            json.dump(activity, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    sys.stdout.write(json.dumps({}))
    sys.stdout.flush()

if __name__ == '__main__':
    main()
