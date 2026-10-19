"""Import Antigravity sessions into AI Knowledge OS via MCP stdio.

Idempotent, resumable batch importer with persistent checkpoints.
"""
import argparse
import asyncio
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.chat_import.prepare import split_text


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def save_json(path: Path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def load_prepared(folder: Path):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    sessions = []
    for meta in manifest:
        sid = meta['id']
        sdir = folder / sid
        summary = json.loads((sdir / 'summary.json').read_text(encoding='utf-8'))
        transcript = (sdir / 'import-transcript.md').read_text(encoding='utf-8')
        chunks = split_text(transcript) if transcript else []
        sessions.append({
            'meta': meta,
            'summary': summary,
            'transcript': transcript,
            'chunks': chunks,
            'transcript_hash': digest(transcript)
        })
    return sessions


async def import_antigravity_batch(call, sessions, checkpoint_path: Path, progress=lambda p: None):
    report = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {
        'notes': {},
        'memories': {},
        'sessions': {}
    }

    # Fetch existing notes to avoid duplicates
    catalog = {}
    offset = 0
    while True:
        page = await call('list_user_notes', {'limit': 100, 'offset': offset})
        for note in page:
            catalog[(note['title'], note['content_hash'])] = note
        if len(page) < 100:
            break
        offset += len(page)

    async def write_note(key, title, content):
        h = digest(content)
        existing = catalog.get((title, h))
        if key in report['notes']:
            old = report['notes'][key]
            if old['hash'] == h and old['title'] == title:
                try:
                    existing = await call('get_user_note', {'note_id': old['id'], 'revision': old['revision']})
                except Exception:
                    pass
        result = existing or await call('create_user_note', {'title': title, 'content': content})
        fetched = await call('get_user_note', {'note_id': result['id'], 'revision': result['revision']})
        report['notes'][key] = {'id': fetched['id'], 'revision': fetched['revision'], 'hash': h, 'title': title}
        catalog[(title, h)] = fetched
        save_json(checkpoint_path, report)
        return fetched

    async def write_memory(key, source, claim, node_type):
        if key in report['memories']:
            old = report['memories'][key]
            if old['hash'] == digest(claim):
                try:
                    read = await call('get_memory_node', {'node_id': old['id']})
                    return read
                except Exception:
                    pass

        candidate = await call('submit_candidate_claim', {
            'source_id': source['id'],
            'source_revision': source['revision'],
            'source_content_hash': source['content_hash'],
            'claim': claim,
            'suggested_node_type': node_type
        })
        node = await call('accept_candidate', {'candidate_id': candidate['id']})
        read = await call('get_memory_node', {'node_id': node['id']})
        report['memories'][key] = {
            'id': node['id'],
            'revision': read['version'],
            'hash': digest(claim),
            'candidate_id': candidate['id'],
            'source_id': source['id'],
            'node_type': node_type
        }
        save_json(checkpoint_path, report)
        return node

    categories = defaultdict(list)

    # 1. Write source transcripts first
    print(f"Ingesting raw source transcripts for {len(sessions)} sessions...")
    for idx, session in enumerate(sessions, 1):
        meta, summary = session['meta'], session['summary']
        sid, cat = meta['id'], summary['category']
        refs = []
        for i, chunk in enumerate(session['chunks'], 1):
            title = f"【{cat}】Antigravity 聊天原文｜{summary['title']}｜{i}/{len(session['chunks'])}"
            header = (
                f"会话 ID：{sid}；类别：{cat}；分段：{i}/{len(session['chunks'])}\n"
                f"来源性质：Antigravity 历史工作与对话记录，凭据已遮蔽。\n"
                f"原文本 SHA256：{session['transcript_hash']}\n\n"
            )
            src_note = await write_note(f'{sid}/raw/{i}', title, header + chunk)
            refs.append({'id': src_note['id'], 'revision': src_note['revision'], 'hash': src_note['content_hash']})

        report['sessions'][sid] = {
            'category': cat,
            'source_chunks': refs,
            'message_count': meta['message_count'],
            'artifacts_count': meta['artifacts_count']
        }
        save_json(checkpoint_path, report)
        progress({'stage': 'source_saved', 'index': idx, 'total': len(sessions), 'session': sid})

    # 2. Write summaries and memory nodes
    print(f"Ingesting summaries and memory claims...")
    for idx, session in enumerate(sessions, 1):
        meta, summary = session['meta'], session['summary']
        sid, cat = meta['id'], summary['category']
        refs = report['sessions'][sid]['source_chunks']
        source_list = '\n'.join(f"- 笔记 {r['id']} (版本 {r['revision']})" for r in refs)

        body = (
            f"# {summary['title']}\n"
            f"项目分类：{cat}\n"
            f"会话 ID：{sid}\n"
            f"时间戳：{meta.get('created_at', '')}\n\n"
            f"{summary['summary_markdown']}\n\n"
            f"## 关联原始来源\n"
            f"{source_list or '无正文记录'}\n\n"
            f"来源性质：由 Antigravity 历史工作过程自动归纳，存入为 L2 / INFERRED / UNVERIFIED 记忆。"
        )
        summary_note = await write_note(f'{sid}/summary', f"【{cat}】Antigravity 成果摘要｜{summary['title']}", body)

        node_ids = []
        if refs:
            # Main Session memory
            session_claim = f"【{cat}】{summary['title']}\n{summary['summary_markdown']}\n\n来源：Antigravity 会话 {sid}。"
            mem_node = await write_memory(f'{sid}/session', summary_note, session_claim, 'Session')
            node_ids.append(mem_node['id'])

            # Granular memory claims
            for i, c in enumerate(summary.get('memory_claims', []), 1):
                mem_claim = f"{c['claim']}\n\n来源：Antigravity 会话 {sid}。"
                ntype = c.get('node_type', 'Document')
                if ntype not in ('Decision', 'Document', 'Fact', 'Concept', 'Task', 'Experience'):
                    ntype = 'Document'
                claim_node = await write_memory(f'{sid}/claim/{i}', summary_note, mem_claim, ntype)
                node_ids.append(claim_node['id'])

        report['sessions'][sid]['summary_note_id'] = summary_note['id']
        report['sessions'][sid]['memory_ids'] = node_ids
        categories[cat].append((summary['title'], summary_note['id'], node_ids))
        save_json(checkpoint_path, report)
        progress({'stage': 'memory_saved', 'index': idx, 'total': len(sessions), 'session': sid})

    # 3. Write category index notes
    print("Writing category and master index notes...")
    index_refs = []
    for cat, entries in sorted(categories.items()):
        lines = [
            f"# Antigravity 历史专案归档：{cat}",
            f"总计包含 {len(entries)} 个历史会话与交付物。本归档记录保留在 default scope 中。",
            ""
        ]
        for title, nid, mids in entries:
            lines.append(f"- **{title}**：来源摘要笔记 {nid}；AI Memory 节点：{', '.join(mids) or '无'}")
        cat_note = await write_note(f'category/{cat}', f"Antigravity 专案目录｜{cat}", '\n'.join(lines))
        index_refs.append(f"- **{cat}**：{len(entries)} 个会话；目录索引笔记：{cat_note['id']}")

    # 4. Master index note
    master_body = (
        "# Antigravity 历史工作与专案成果导入总目录｜2026-10-11\n\n"
        "本目录记录了从 Antigravity CLI 本地运行环境归档导入的全部对话、代码构建与设计文档。\n\n"
        "## 各项目专案分类索引\n"
        + '\n'.join(index_refs) +
        "\n\n---\n"
        "数据结构说明：所有原文和摘要保存在 My Notes（可追溯）；核心方案与决策提取为 AI Memory（L2 / UNVERIFIED 状态，支持混合向量与关键词检索）。"
    )
    master_note = await write_note('batch-master-index', 'Antigravity 历史导入总目录｜2026-10-11', master_body)
    report['master_index_note_id'] = master_note['id']
    report['state'] = 'IMPORTED_VERIFIED'
    save_json(checkpoint_path, report)
    print(f"Batch import finished successfully! Master index note: {master_note['id']}")
    return report


async def main():
    from mcp import Client, StdioServerParameters
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / '.agent-tmp' / 'antigravity-import-20261011')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--checkpoint', type=Path, default=ROOT / '.agent-tmp' / 'antigravity-import-20261011' / 'checkpoint.json')
    args = parser.parse_args()

    sessions = load_prepared(args.prepared)
    print(f"Loaded {len(sessions)} prepared sessions.")

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(ROOT / 'scripts/run_mcp.py'),
            '--data-dir', str(args.data_dir.resolve()),
            '--scope', 'default',
            '--principal', 'antigravity-history-import'
        ]
    )

    async with Client(params, read_timeout_seconds=120) as client:
        async def call(name, arguments):
            resp = await client.call_tool(name, arguments)
            if resp.is_error:
                raise RuntimeError(f"{name} error: {resp.content}")
            return json.loads(resp.content[0].text)

        report = await import_antigravity_batch(
            call,
            sessions,
            args.checkpoint,
            lambda p: print(f"[{p['stage']}] {p.get('index', '')}/{p.get('total', '')} ({p.get('session', '')[:8]})", flush=True)
        )
        print("Import completed:")
        print(f"  Notes created/verified: {len(report['notes'])}")
        print(f"  Memories created/verified: {len(report['memories'])}")
        print(f"  Master Index Note ID: {report['master_index_note_id']}")


if __name__ == '__main__':
    asyncio.run(main())
