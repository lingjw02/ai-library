#!/usr/bin/env python3
"""Installer script for AI Library Git hooks."""
import sys
import shutil
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def install():
    root = Path(__file__).resolve().parents[1]
    git_dir = root / '.git'
    if not git_dir.exists() or not git_dir.is_dir():
        print(f"[Error] {git_dir} does not exist or is not a git repository.")
        return False

    hooks_dir = git_dir / 'hooks'
    hooks_dir.mkdir(parents=True, exist_ok=True)
    source_dir = root / 'scripts' / 'git-hooks'

    installed = []
    for hook_name in ['pre-commit', 'post-commit']:
        src = source_dir / hook_name
        dest = hooks_dir / hook_name
        if src.exists():
            shutil.copyfile(src, dest)
            installed.append(hook_name)

    print(f"✅ Successfully installed Git hooks into {hooks_dir}: {', '.join(installed)}")
    return True

if __name__ == '__main__':
    ok = install()
    sys.exit(0 if ok else 1)
