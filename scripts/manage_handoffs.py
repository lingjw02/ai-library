"""Exchange work through the scoped tool boundary; never execute package content."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.adapters.mcp.stdio_server import build_service
from backend.handoffs.models import MAX_PACKAGE_BYTES


def read_json(path):
    with path.open('rb') as handle:
        data = handle.read(MAX_PACKAGE_BYTES + 1)
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError('Input exceeds the 512000-byte limit')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('Non-finite JSON number is not supported')

    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique_object, parse_constant=reject_constant)


def write_new(path, content):
    """Publish a complete UTF-8 file without replacing an existing destination."""
    if not path.parent.is_dir():
        raise ValueError('Output parent directory must already exist')
    staging = path.parent / ('.' + path.name + '.handoff-' + uuid.uuid4().hex + '.tmp')
    try:
        with staging.open('xb') as handle:
            handle.write(content.encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(staging, path)  # atomic publication; fails if destination exists
    finally:
        staging.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('AI_LIBRARY_DATA_DIR', ROOT / 'data')))
    parser.add_argument('--scope', default='default')
    parser.add_argument('--principal', required=True, help='Trusted local sender/receiver identity')
    parser.add_argument('--read-only', action='store_true')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('create', 'revise', 'import'):
        sub = actions.add_parser(action)
        sub.add_argument('--input', type=Path, required=True)
        sub.add_argument('--request-key', required=True)
        if action == 'revise':
            sub.add_argument('handoff_id')
            sub.add_argument('--expected-revision', type=int, required=True)
    listing = actions.add_parser('list')
    listing.add_argument('--limit', type=int, default=20)
    listing.add_argument('--offset', type=int, default=0)
    listing.add_argument('--target-system')
    for action in ('get', 'export'):
        sub = actions.add_parser(action)
        sub.add_argument('handoff_id')
        sub.add_argument('--revision', type=int)
        if action == 'get':
            sub.add_argument('--include-receipts', action='store_true')
            sub.add_argument('--receipt-limit', type=int, default=20)
            sub.add_argument('--receipt-offset', type=int, default=0)
        else:
            sub.add_argument('--format', choices=['json', 'markdown'], required=True)
            sub.add_argument('--output', type=Path, required=True)
    ack = actions.add_parser('ack')
    ack.add_argument('handoff_id')
    ack.add_argument('--revision', type=int, required=True)
    ack.add_argument('--content-hash', required=True)
    args = parser.parse_args()
    try:
        if not args.scope.strip() or not args.principal.strip():
            raise ValueError('scope and principal must not be blank')
        data = args.data_dir.resolve()
        if args.action not in ('create', 'import') and not (data / 'knowledge_os.db').is_file():
            raise ValueError('Existing knowledge_os.db is required for this action')
        values = {}
        if args.action in ('create', 'revise', 'import'):
            values = {'request_key': args.request_key,
                      'package' if args.action == 'import' else 'payload': read_json(args.input)}
        if args.action in ('get', 'export', 'ack', 'revise'):
            values['handoff_id'] = args.handoff_id
        if args.action in ('get', 'export', 'ack') and args.revision is not None:
            values['revision'] = args.revision
        if args.action == 'revise':
            values['expected_revision'] = args.expected_revision
        if args.action == 'list':
            values = {'limit': args.limit, 'offset': args.offset}
            if args.target_system is not None:
                values['target_system'] = args.target_system
        if args.action == 'get':
            values.update(include_receipts=args.include_receipts, receipt_limit=args.receipt_limit, receipt_offset=args.receipt_offset)
        if args.action == 'export':
            if args.output.exists() or args.output.is_symlink():
                raise ValueError('Output already exists; choose a new filename')
            values['format'] = args.format
        if args.action == 'ack':
            values['content_hash'] = args.content_hash
        names = {'create': 'create_work_handoff', 'revise': 'revise_work_handoff', 'get': 'get_work_handoff',
                 'list': 'list_work_handoffs', 'export': 'export_work_handoff',
                 'import': 'import_work_handoff', 'ack': 'acknowledge_work_handoff'}
        service = build_service(data, args.scope, args.read_only or args.action in ('list', 'get', 'export'), args.principal)
        response = service.execute_tool(names[args.action], values)
        result = json.loads(response['content'][0]['text'])
        if response.get('isError'):
            raise ValueError(result['message'])
        if args.action == 'export':
            write_new(args.output, result.pop('content'))
            result['output'] = str(args.output.absolute())
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
