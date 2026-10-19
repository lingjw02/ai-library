#!/usr/bin/env python3
"""Antigravity PreInvocation Hook for AI Library.
Runs before model turn starts. Inspects current AI Library rules, active handoffs,
pending candidate claims, and open conflicts to inject a clean context sync ephemeralMessage.
Contract: Stdin JSON -> Stdout JSON {"injectSteps": [{"ephemeralMessage": "..."}]}
"""
import sys
import json
import sqlite3
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

def main():
    try:
        raw_input = sys.stdin.read()
        context = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        context = {}

    root = Path(__file__).resolve().parents[2]
    db_path = root / 'data' / 'knowledge_os.db'

    rule_rev = 1
    rule_hash = "unknown"
    active_handoff = None
    pending_candidates = 0
    open_conflicts = 0

    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row

            # 1. Latest usage rules
            row = conn.execute("SELECT revision, content_hash FROM mcp_usage_rule_versions ORDER BY revision DESC LIMIT 1").fetchone()
            if row:
                rule_rev = row['revision']
                rule_hash = row['content_hash'][:8]

            # 2. Latest active handoff
            h_row = conn.execute("""
                SELECT h.id, json_extract(h.payload_json, '$.title') as title 
                FROM work_handoff_packages h 
                WHERE scope = 'default' 
                ORDER BY revision DESC LIMIT 1
            """).fetchone()
            if h_row:
                active_handoff = {
                    'id': h_row['id'],
                    'title': h_row['title'] or 'Active Work Handoff'
                }

            # 3. Pending candidates count
            cand_row = conn.execute("SELECT COUNT(*) FROM candidates WHERE scope = 'default' AND status = 'VALIDATED'").fetchone()
            if cand_row:
                pending_candidates = cand_row[0]

            # 4. Open conflicts count
            conf_row = conn.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE node_type = 'Conflict' AND scope = 'default' AND is_active = 1
                AND COALESCE(json_extract(data_json, '$.core_props.status'), 'OPEN') = 'OPEN'
            """).fetchone()
            if conf_row:
                open_conflicts = conf_row[0]

            conn.close()
        except Exception:
            pass

    handoff_desc = f"'{active_handoff['title']}' ({active_handoff['id']})" if active_handoff else "无未闭环交接"
    msg = (
        f"🧠 [AI Library Context Sync]: "
        f"Active Rules v{rule_rev} ({rule_hash}) | "
        f"活跃交接: {handoff_desc} | "
        f"待审候选: {pending_candidates} 条 | "
        f"未决冲突: {open_conflicts} 项。\n"
        f"🛡️ 执行准则: 查阅 get_usage_rules；代码/数据写操作须证据闭环并征得用户明确同意；勿将假设当作已核实验证。"
    )

    output = {
        "injectSteps": [
            {
                "ephemeralMessage": msg
            }
        ]
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    sys.stdout.flush()

if __name__ == '__main__':
    main()
