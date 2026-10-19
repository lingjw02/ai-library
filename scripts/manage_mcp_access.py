"""Local owner's persistent MCP grant control; not exposed as an Agent tool."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.auth.policy import AccessPolicy
from backend.storage.canonical.sqlite_storage import CanonicalStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--principal', required=True)
    parser.add_argument('--scope', default='default')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('show')
    setter = sub.add_parser('set')
    setter.add_argument('--access', choices=['full', 'read-only', 'revoked'], required=True)
    setter.add_argument('--expected-revision', type=int, required=True)
    setter.add_argument('--expires-at', help='ISO timestamp with timezone; "none" explicitly removes expiration. Omitted preserves it.')
    args = parser.parse_args()
    database = args.data_dir.resolve()/'knowledge_os.db'
    if not database.is_file():
        parser.error('Existing knowledge_os.db is required; refusing to create a new data directory')
    policy = AccessPolicy(CanonicalStorage(str(database)))
    grant = policy.get_grant(args.principal, args.scope)
    if grant is None:
        parser.error('No grant for this principal/scope; inspect the approved MCP launch configuration')
    if args.action == 'set':
        extra = {}
        if args.expires_at is not None:
            extra['expires_at'] = None if args.expires_at == 'none' else args.expires_at
        try:
            grant = policy.update_grant(args.principal, args.scope,
                read=args.access != 'revoked', write=args.access == 'full',
                expected_revision=args.expected_revision, actor='local-owner', **extra)
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(grant, ensure_ascii=True))


if __name__ == '__main__':
    main()
