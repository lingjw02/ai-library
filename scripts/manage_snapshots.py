"""Create, verify or restore local-owner snapshots; never overwrite a directory."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.storage.recovery import create_snapshot, restore_snapshot, verify_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout-seconds', type=float, default=120)
    actions = parser.add_subparsers(dest='action', required=True)
    for name in ['create', 'verify', 'restore']:
        action = actions.add_parser(name)
        action.add_argument('source', type=Path)
        if name != 'verify':
            action.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'create':
            result = create_snapshot(args.source, args.destination, timeout_seconds=args.timeout_seconds)
            output = {'state': 'SNAPSHOT_CREATED', 'destination': str(args.destination), 'files': len(result['files'])}
        elif args.action == 'verify':
            result = verify_snapshot(args.source, timeout_seconds=args.timeout_seconds)
            output = {'state': 'INTEGRITY_PASSED', 'files': len(result['files']), 'format_version': result['format_version']}
        else:
            output = restore_snapshot(args.source, args.destination, timeout_seconds=args.timeout_seconds)
            output['state'] = 'RESTORED_TO_NEW_DIRECTORY'
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
