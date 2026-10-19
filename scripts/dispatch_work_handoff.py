"""Save/read a scoped handoff and open a visible NEW Agent conversation in its project."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.adapters.mcp.stdio_server import build_service
from backend.capabilities.connections.desktop_handoff import builtin_profile, read_profile, agent_argv, launch_window
from scripts.manage_handoffs import read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('AI_LIBRARY_DATA_DIR', ROOT/'data')))
    parser.add_argument('--scope', default='default')
    parser.add_argument('--principal', required=True)
    parser.add_argument('--agent', required=True)
    parser.add_argument('--project-dir', type=Path, default=ROOT)
    parser.add_argument('--instruction-file', type=Path, required=True,
                        help='Direct user-authorized task; package content alone never authorizes execution')
    parser.add_argument('--profile-file', type=Path, help='Trusted local executable/argv adapter; never read from a handoff')
    parser.add_argument('--launch-key', default='default', help='Idempotent per handoff/revision/agent; change explicitly for an intentional retry')
    parser.add_argument('--observe-log', type=Path, help='Open a new read-only progress conversation for a task already executing')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--handoff-id')
    source.add_argument('--input', type=Path, help='New v1 payload; requires request-key')
    parser.add_argument('--request-key')
    args = parser.parse_args()
    saved_id = None
    try:
        if not args.scope.strip() or not args.principal.strip():
            raise ValueError('scope and principal must not be blank')
        if not args.project_dir.is_dir():
            raise ValueError('Project directory must exist')
        if args.instruction_file.stat().st_size > 200000:
            raise ValueError('Direct instruction exceeds 200KB')
        instruction = args.instruction_file.read_text(encoding='utf-8-sig')
        if not instruction.strip():
            raise ValueError('Direct user instruction must not be empty')
        profile = read_profile(args.profile_file) if args.profile_file else builtin_profile(args.agent)
        if profile.get('agent') != args.agent:
            raise ValueError('Profile agent does not match --agent')
        # Validate trusted host configuration before any new handoff is saved.
        agent_argv(profile, instruction, args.instruction_file, cwd=args.project_dir)
        data = args.data_dir.resolve()
        if args.handoff_id and not (data/'knowledge_os.db').is_file():
            raise ValueError('Existing knowledge_os.db required')
        service = build_service(data, args.scope, bool(args.handoff_id), args.principal)
        def call(name, values):
            response = service.execute_tool(name, values)
            result = json.loads(response['content'][0]['text'])
            if response.get('isError'):
                raise ValueError(result['message'])
            return result
        if args.input:
            if not args.request_key:
                raise ValueError('--request-key is required with --input')
            payload = read_json(args.input)
            if payload.get('target_system') not in (None, args.agent):
                raise ValueError('Payload target does not match --agent')
            record = call('create_work_handoff', {'payload': payload, 'request_key': args.request_key})
        else:
            record = call('get_work_handoff', {'handoff_id': args.handoff_id})
        saved_id = record['id']
        result = launch_window(directory=data/'agent_windows', record=record, profile=profile,
            instruction=instruction, cwd=args.project_dir, runner=ROOT/'scripts/agent_window.py',
            launch_key=args.launch_key, observe_log=args.observe_log)
        failed = result['state'] in ('WINDOW_FAILED', 'AGENT_FAILED', 'DISPATCH_UNCERTAIN')
        print(json.dumps(result, ensure_ascii=True), file=sys.stderr if failed else sys.stdout)
        return 1 if failed else 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'state': 'DISPATCH_FAILED', 'handoff_id': saved_id, 'received': False,
                          'message': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
