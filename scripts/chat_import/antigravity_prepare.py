"""Prepare and redact visible Antigravity sessions and artifacts for AI library import.

Reads transcripts from C:/Users/ling5/.gemini/antigravity-cli/brain, extracts visible
dialogue turns, bundles session artifacts (.md), masks sensitive credentials,
and generates structured summaries ready for MCP ingestion.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.chat_import.prepare import split_text
from scripts.chat_import.redact import redact

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CURRENT_SESSION_ID = 'cb580f55-9e82-43eb-8938-6a4e5747fc6a'
BRAIN_DIR = Path(r'C:\Users\ling5\.gemini\antigravity-cli\brain')


def extract_antigravity_session(session_dir: Path):
    cid = session_dir.name
    tpath = session_dir / '.system_generated' / 'logs' / 'transcript.jsonl'
    if not tpath.is_file():
        return None

    dialogues = []
    created_at = None
    cwds = []
    line_no = 0

    with open(tpath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line_no += 1
            try:
                row = json.loads(line)
                if not created_at and row.get('created_at'):
                    created_at = row['created_at']

                # Extract tool call paths
                for call in row.get('tool_calls', []):
                    args = call.get('args', {})
                    for k in ('Cwd', 'TargetFile', 'AbsolutePath'):
                        v = args.get(k)
                        if isinstance(v, str):
                            try:
                                if v.startswith('"') and v.endswith('"'):
                                    v = json.loads(v)
                            except Exception:
                                pass
                            v = v.replace('\\\\', '\\').replace('/', '\\')
                            if any(x in v for x in ('02_Development_Tools', '03_Study_Projects', 'Desktop', 'Documents')):
                                cwds.append(v)

                t = row.get('type')
                s = row.get('source')
                c = row.get('content', '')

                if t == 'USER_INPUT' or (s == 'USER_EXPLICIT' and c):
                    m = re.search(r'<USER_REQUEST>(.*?)</USER_REQUEST>', c, re.DOTALL)
                    user_text = m.group(1).strip() if m else c.strip()
                    if user_text and not user_text.startswith('<SYSTEM_MESSAGE>') and not user_text.startswith('Task id'):
                        dialogues.append({
                            'id': f'{cid}-msg-{len(dialogues)+1}',
                            'role': 'user',
                            'timestamp': row.get('created_at', created_at or ''),
                            'text': user_text,
                            'source_file': str(tpath),
                            'source_line': line_no
                        })
                elif t == 'PLANNER_RESPONSE' and s == 'MODEL' and c.strip():
                    dialogues.append({
                        'id': f'{cid}-msg-{len(dialogues)+1}',
                        'role': 'assistant',
                        'timestamp': row.get('created_at', created_at or ''),
                        'text': c.strip(),
                        'source_file': str(tpath),
                        'source_line': line_no
                    })
            except Exception:
                continue

    # Artifact .md files in the session brain folder
    artifacts = []
    for md in session_dir.glob('*.md'):
        try:
            content = md.read_text(encoding='utf-8', errors='replace')
            artifacts.append({
                'name': md.name,
                'path': str(md),
                'size': md.stat().st_size,
                'content': content
            })
        except Exception:
            pass

    if not dialogues and not artifacts:
        return None

    # Determine project category
    category = 'General / Antigravity'
    for v in cwds:
        m = re.search(r'(02_Development_Tools\\[^\\]+|03_Study_Projects\\[^\\]+|Desktop\\[^\\]+)', v, re.IGNORECASE)
        if m:
            category = m.group(1).split('\\')[-1]
            break

    # If first user text mentions specific known projects
    first_user_text = next((d['text'] for d in dialogues if d['role'] == 'user'), '')
    if 'AutoCAD' in first_user_text or 'autocad' in first_user_text.lower():
        category = 'AutoCAD 2D to 3D'
    elif 'live2d' in first_user_text.lower():
        category = 'live2d-maker'
    elif 'wallpaper' in first_user_text.lower():
        category = 'wallpaper'
    elif 'social' in first_user_text.lower() and category == 'General / Antigravity':
        category = 'social-app'

    return {
        'id': cid,
        'created_at': created_at or '',
        'category': category,
        'dialogues': dialogues,
        'artifacts': artifacts,
        'cwds': cwds,
        'first_prompt': first_user_text
    }


def make_session_summary(session_data):
    cid = session_data['id']
    cat = session_data['category']
    dialogues = session_data['dialogues']
    artifacts = session_data['artifacts']
    first_prompt = session_data['first_prompt']

    # Generate title
    if first_prompt:
        clean_prompt = re.sub(r'[\r\n\t]+', ' ', first_prompt).strip()
        # Truncate to reasonable title
        title = clean_prompt[:40] if len(clean_prompt) <= 40 else clean_prompt[:37] + '...'
    elif artifacts:
        title = f"生成文档：{artifacts[0]['name']}"
    else:
        title = f"Antigravity 会话 {cid[:8]}"

    # Bullet points of key user requests and assistant deliverables
    bullets = []
    user_requests = [d['text'] for d in dialogues if d['role'] == 'user']
    asst_replies = [d['text'] for d in dialogues if d['role'] == 'assistant']

    for i, req in enumerate(user_requests[:4], 1):
        clean_req = req.replace('\r\n', ' ').replace('\n', ' ')[:100]
        bullets.append(f"- 用户目标 {i}：{clean_req}")

    if artifacts:
        bullets.append(f"- 产出专案文档：{', '.join(a['name'] for a in artifacts)}")

    if asst_replies:
        last_reply = asst_replies[-1].replace('\r\n', ' ').replace('\n', ' ')[:120]
        bullets.append(f"- 最终完成状态：{last_reply}")

    summary_md = f"本会话为 Antigravity 对话与实施记录（项目归属：{cat}）。\n\n" + '\n'.join(bullets)

    # Derive high-value claims for AI Memory
    claims = []
    if first_prompt:
        clean_p = first_prompt.replace('\r\n', ' ').replace('\n', ' ')[:140]
        claims.append({
            'node_type': 'Decision',
            'claim': f"【{cat}】用户需求与目标：{clean_p}",
            'evidence': [1] if dialogues else []
        })

    for a in artifacts:
        claims.append({
            'node_type': 'Document',
            'claim': f"【{cat}】专案交付文档《{a['name']}》（大小 {a['size']} 字节），记录了项目技术方案与验收标准。",
            'evidence': []
        })

    return {
        'session_id': cid,
        'title': title,
        'category': cat,
        'classification': 'antigravity_workspace_transcripts',
        'classification_reason': f"基于项目路径及会话内容归类为 {cat}",
        'summary_markdown': summary_md,
        'source_message_numbers': list(range(1, min(len(dialogues) + 1, 6))),
        'related_categories': [],
        'coverage': "覆盖用户请求、最终答复与产出文档；敏感凭据已脱敏。",
        'memory_claims': claims
    }


def prepare_all(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    conv_dirs = [d for d in BRAIN_DIR.iterdir() if d.is_dir()]
    print(f"Scanning {len(conv_dirs)} directories in brain...")

    for d in conv_dirs:
        if d.name == CURRENT_SESSION_ID:
            continue
        data = extract_antigravity_session(d)
        if not data:
            continue

        cid = data['id']
        session_folder = output_dir / cid
        session_folder.mkdir(exist_ok=True)

        summary = make_session_summary(data)

        # Build redacted messages and import transcript
        messages = []
        redactions = {}
        for d_msg in data['dialogues']:
            clean_text, counts = redact(d_msg['text'])
            for k, v in counts.items():
                redactions[k] = redactions.get(k, 0) + v
            messages.append({
                'id': d_msg['id'],
                'role': d_msg['role'],
                'timestamp': d_msg['timestamp'],
                'text': clean_text,
                'source_file': d_msg['source_file'],
                'source_line': d_msg['source_line']
            })

        # Append artifacts to transcript if any
        transcript_lines = []
        for i, m in enumerate(messages, 1):
            transcript_lines.append(f"### 消息 {i}｜{m['role']}｜{m['timestamp']}\n源文件：{m['source_file']}:{m['source_line']}\n\n{m['text']}")

        if data['artifacts']:
            transcript_lines.append("\n## 本会话生成的专案交付文件 (Artifacts)\n")
            for a in data['artifacts']:
                clean_art, c_art = redact(a['content'])
                for k, v in c_art.items():
                    redactions[k] = redactions.get(k, 0) + v
                transcript_lines.append(f"### 交付文档：{a['name']} ({a['size']} 字节)\n路径：{a['path']}\n\n{clean_art}")

        transcript_str = '\n\n'.join(transcript_lines)
        (session_folder / 'messages.json').write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
        (session_folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (session_folder / 'import-transcript.md').write_text(transcript_str, encoding='utf-8')

        manifest.append({
            'id': cid,
            'title': summary['title'],
            'created_at': data['created_at'],
            'category': data['category'],
            'classification': 'antigravity_workspace_transcripts',
            'subagent': False,
            'parent_id': None,
            'archived': False,
            'message_count': len(messages),
            'characters': len(transcript_str),
            'artifacts_count': len(data['artifacts']),
            'redactions': redactions,
            'sources': [{
                'path': str(d / '.system_generated' / 'logs' / 'transcript.jsonl'),
                'visible_messages': len(messages)
            }]
        })

    manifest.sort(key=lambda x: x['created_at'])
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Preparation complete! {len(manifest)} sessions packaged into {output_dir}")
    return manifest


if __name__ == '__main__':
    target = ROOT / '.agent-tmp' / 'antigravity-import-20261011'
    prepare_all(target)
