"""Tests for AI Library Hook Integration:
1. Antigravity Lifecycle Hooks (PreInvocation, PostToolUse, Stop).
2. Git Lifecycle Hooks (pre-commit, post-commit).
"""
import sys
import json
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_antigravity_pre_invocation_hook():
    script = ROOT / '.agents' / 'run_hook.py'
    assert script.exists()
    proc = subprocess.run(
        [sys.executable, str(script), 'pre_invocation'],
        cwd=str(ROOT / '.agents'),
        input=json.dumps({"conversationId": "test-conv-123"}),
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "injectSteps" in data
    assert len(data["injectSteps"]) >= 1
    assert "AI Library Context Sync" in data["injectSteps"][0]["ephemeralMessage"]


def test_antigravity_post_tool_use_hook(tmp_path):
    script = ROOT / '.agents' / 'run_hook.py'
    payload = {
        "toolCall": {
            "name": "write_to_file",
            "args": {"TargetFile": str(tmp_path / "test_file.py")}
        },
        "stepIdx": 42
    }
    proc = subprocess.run(
        [sys.executable, str(script), 'post_tool_use'],
        cwd=str(ROOT / '.agents'),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_antigravity_stop_hook():
    script = ROOT / '.agents' / 'run_hook.py'
    proc = subprocess.run(
        [sys.executable, str(script), 'stop'],
        cwd=str(ROOT / '.agents'),
        input=json.dumps({"executionNum": 1, "fullyIdle": True}),
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_git_pre_commit_script():
    pre_commit = ROOT / 'scripts' / 'git-hooks' / 'pre_commit.py'
    assert pre_commit.exists()
    proc = subprocess.run(
        [sys.executable, str(pre_commit)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    assert proc.returncode == 0
    assert "Pre-commit checks passed" in proc.stdout
