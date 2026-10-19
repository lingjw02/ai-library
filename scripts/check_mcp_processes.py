#!/usr/bin/env python3
"""检查本机 AI Library MCP 进程是否存在重复连接。

背景：多个 run_mcp.py / run_embedding_mcp.py 进程同时打开同一个 data 目录的
SQLite 时，写锁会互相争抢，服务内每个 BEGIN IMMEDIATE 都可能抛
"attempt to write a readonly database"，表现为全站请求变慢、关键词索引
worker 持续失败重试。

判定规则（重要）：
    启动器会派生子进程，父子两个 python.exe 其实是**同一个连接**。
    所以按 principal 分组后，先剔除「父进程也在同组内」的子进程，
    剩下的根进程数量才是真实连接数。只有根 > 1 才算争锁。

用法：
    python scripts/check_mcp_processes.py              # 只读检查
    python scripts/check_mcp_processes.py --fix        # 保留每个 principal 最早的根，终止其余
    python scripts/check_mcp_processes.py --json       # 机器可读
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

# 路径可能含空格，命令行里带引号；用正则而不是 split 才不会被空格切断
def _arg_value_for(cmd: str, name: str) -> str:
    m = re.search(r'--' + re.escape(name) + r'[=\s]+(?:"([^"]*)"|\'([^\']*)\'|(\S+))', cmd)
    if not m:
        return ''
    return next((g for g in m.groups() if g is not None), '')

# PowerShell 枚举：只取 python.exe 且命令行含 MCP 入口脚本的进程
_PS_QUERY = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match 'run_mcp\.py' -or $_.CommandLine -match 'run_embedding_mcp\.py') } |
  Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine |
  ConvertTo-Json -Compress
"""


def _run_ps() -> str:
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', _PS_QUERY],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f'PowerShell 查询失败: {result.stderr.strip()[:400]}')
    return result.stdout.strip()


def _principal_of(cmd: str, script: str) -> str:
    """从命令行解析 --principal；嵌入服务没有该参数，用脚本名区分。"""
    value = _arg_value_for(cmd, 'principal')
    if value:
        return value
    return 'embedding' if 'run_embedding_mcp' in script else 'default'


def _data_dir_of(cmd: str) -> str:
    return _arg_value_for(cmd, 'data-dir')


def collect() -> list[dict]:
    raw = _run_ps()
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, dict):
        payload = [payload]

    procs = []
    for item in payload:
        cmd = item.get('CommandLine') or ''
        is_embedding = 'run_embedding_mcp.py' in cmd
        script = 'run_embedding_mcp.py' if is_embedding else 'run_mcp.py'
        procs.append({
            'pid': int(item['ProcessId']),
            'ppid': int(item['ParentProcessId']),
            'started': str(item.get('CreationDate') or ''),
            'principal': _principal_of(cmd, script),
            'data_dir': _data_dir_of(cmd),
            'script': script,
            # 记忆库服务会写 SQLite（BEGIN IMMEDIATE）；嵌入服务是纯计算，
            # 工具契约明确「no database writes」。两者重复的后果不同。
            'writes_db': not is_embedding,
        })
    return procs


def group_roots(procs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """按 (principal, data_dir) 分组，返回每组里的**根进程**（父进程不在本组内的）。"""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in procs:
        buckets[(p['principal'], p['data_dir'].lower())].append(p)

    roots: dict[tuple[str, str], list[dict]] = {}
    for key, members in buckets.items():
        pids = {m['pid'] for m in members}
        # 父进程也在本组 → 它是启动器派生的子进程，与父同属一个连接
        roots[key] = sorted([m for m in members if m['ppid'] not in pids], key=lambda m: m['started'])
    return roots


def main() -> int:
    ap = argparse.ArgumentParser(description='检查 AI Library MCP 进程是否重复连接')
    ap.add_argument('--fix', action='store_true', help='保留每个 principal 最早的根进程，终止其余')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()

    try:
        procs = collect()
    except Exception as exc:
        print(f'检查失败：{exc}', file=sys.stderr)
        return 2

    if not procs:
        print('未发现 MCP 进程。')
        return 0

    roots = group_roots(procs)
    # 区分后果：写库连接重复会争 SQLite 写锁；嵌入服务是纯计算，重复只占内存。
    lock_conflicts = {k: v for k, v in roots.items() if len(v) > 1 and any(m['writes_db'] for m in v)}
    mem_conflicts = {k: v for k, v in roots.items() if len(v) > 1 and not any(m['writes_db'] for m in v)}

    if args.json:
        print(json.dumps({
            'checked_at': datetime.now().isoformat(timespec='seconds'),
            'total_processes': len(procs),
            'groups': [
                {'principal': k[0], 'data_dir': v[0]['data_dir'], 'roots': [r['pid'] for r in v],
                 'writes_db': any(m['writes_db'] for m in v), 'conflict': len(v) > 1}
                for k, v in roots.items()
            ],
        }, ensure_ascii=False, indent=1))
        return 1 if lock_conflicts else 0

    print(f'MCP 进程检查 · {datetime.now():%Y-%m-%d %H:%M:%S}')
    print(f'共 {len(procs)} 个进程，{len(roots)} 个逻辑连接\n')
    print(f'{"principal":<16}{"连接":<6}{"类型":<10}{"data-dir":<46}PID')
    print('-' * 100)
    for key, members in sorted(roots.items()):
        principal, _ = key
        kind = '写库' if any(m['writes_db'] for m in members) else '纯计算'
        flag = ' ⚠' if len(members) > 1 else ''
        print(f'{principal:<16}{len(members):<6}{kind:<10}{(members[0]["data_dir"] or "(默认)"):<46}'
              f'{", ".join(str(m["pid"]) for m in members)}{flag}')

    if not lock_conflicts and not mem_conflicts:
        print('\n✓ 每个 principal 只有一个连接，未发现争锁风险。')
        return 0

    if lock_conflicts:
        print(f'\n⚠ {len(lock_conflicts)} 个写库连接重复 —— 会争抢 SQLite 写锁，可能导致请求报 readonly：')
        for (principal, _), members in lock_conflicts.items():
            print(f'  · {principal}: {len(members)} 个根进程')

    if mem_conflicts:
        print(f'\n提示：{len(mem_conflicts)} 个嵌入服务重复（不写库，只占内存与端口）：')
        for (principal, _), members in mem_conflicts.items():
            print(f'  · {principal}: {len(members)} 个根进程')

    if not args.fix:
        print('\n只读模式未做任何改动。加 --fix 可保留每个写库 principal 最早的根进程并终止其余。')
        return 1 if lock_conflicts else 0

    if not lock_conflicts:
        print('\n没有需要清理的写库重复连接（嵌入服务重复未自动清理，避免影响当前会话的嵌入能力）。')
        return 0

    print('\n开始清理（保留最早启动的根进程）：')
    for (principal, _), members in lock_conflicts.items():
        keep, drop = members[0], members[1:]
        print(f'  · {principal}: 保留 {keep["pid"]}，终止 {", ".join(str(m["pid"]) for m in drop)}')
        for m in drop:
            r = subprocess.run(['taskkill', '/PID', str(m['pid']), '/F'],
                               capture_output=True, text=True, errors='replace')
            print(f'      pid {m["pid"]}: {"OK" if r.returncode == 0 else r.stderr.strip()[:80]}')
    print('\n完成。建议复查：python scripts/check_mcp_processes.py')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
