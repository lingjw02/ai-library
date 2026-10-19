"""Visible native Agent session. Called by the trusted desktop dispatcher only."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.capabilities.connections.desktop_handoff import write_json


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('launch_file', type=Path)
    parser.add_argument('--no-pause', action='store_true', help='Used by automated runner tests')
    args = parser.parse_args()
    launch = json.loads(args.launch_file.read_text(encoding='utf-8'))
    status = Path(launch['status_file'])
    state = {'state': 'WINDOW_VISIBLE', 'received': False, 'new_conversation': True,
             'runner_pid': os.getpid(), 'agent': launch['agent'], 'cwd': launch['cwd']}
    try:
        if os.name == 'nt':
            ctypes.windll.kernel32.SetConsoleTitleW(f'AI Library | {launch["agent"]} | new conversation')
            ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p
            state['console_hwnd'] = ctypes.windll.kernel32.GetConsoleWindow()
        write_json(status, state)
        print(f'AI Library -> {launch["agent"]}: NEW CONVERSATION', flush=True)
        print(f'Project: {launch["cwd"]}\nHandoff: {launch["handoff"]["id"]}', flush=True)
        print('用户交接任务：\n' + launch['instruction'] + '\n', flush=True)
        if launch.get('observe_log'):
            print('已有后台执行。本新对话仅查看真实进度，不重复执行原任务。', flush=True)
        child = subprocess.Popen(launch['argv'], cwd=launch['cwd'], shell=False)
        state.update(state='AGENT_RUNNING', agent_pid=child.pid)
        write_json(status, state)
        code = child.wait()
        state.update(state='AGENT_EXITED' if code == 0 else 'AGENT_FAILED', exit_code=code)
        write_json(status, state)
        print(f'\nAgent exited: {code}. 接收与完成状态以 Gateway 实际记录为准。', flush=True)
    except (OSError, ValueError) as exc:
        code = 1
        state.update(state='AGENT_FAILED', error=str(exc))
        write_json(status, state)
        print(f'无法启动 Agent：{exc}', file=sys.stderr, flush=True)
    if not args.no_pause and sys.stdin.isatty():
        try:
            input('按 Enter 关闭此会话窗口……')
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
