"""Import reviewed local dialogue through MCP, preserving provenance and retries.

Sources remain Notes; digests and selected claims become L2 UNVERIFIED Memory.
Only the prepared batch is written. This does not change permissions or models.
"""
import argparse
import asyncio
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.chat_import.prepare import split_text
from scripts.chat_import.redact import redact


def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def save_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def prepare_batch(folder):
    sessions = []
    for meta in json.loads((folder/'manifest.json').read_text(encoding='utf8')):
        sid = meta['id']
        summary = json.loads((folder/sid/'summary.json').read_text(encoding='utf8'))
        assert summary['session_id'] == sid
        if meta['category']:
            assert meta['category'] == summary['category'], 'Existing category must be preserved'
        messages = json.loads((folder/sid/'messages.json').read_text(encoding='utf8'))
        redactions, blocks, excluded = Counter(), [], []
        for number, message in enumerate(messages, 1):
            text = message['text']
            if text.lstrip().startswith('<codex_internal_context'):
                excluded.append(number)
                continue
            # Remove runtime page wrappers anywhere in a multi-part user block.
            text = re.sub(r'<in-app-browser-context\b[^>]*>.*?</in-app-browser-context>', '', text, flags=re.S)
            text, counts = redact(text)
            redactions.update(counts)
            blocks.append(f"### 消息 {number}｜{message['role']}｜{message['timestamp']}\n"
                          f"源文件：{message['source_file']}:{message['source_line']}\n\n{text}")
        transcript = '\n\n'.join(blocks)
        chunks = split_text(transcript) if transcript else []
        assert ''.join(chunks) == transcript
        (folder/sid/'import-transcript.md').write_bytes(transcript.encode('utf8'))
        for item in [summary, *summary.get('memory_claims', [])]:
            refs = item.get('source_message_numbers', item.get('evidence', []))
            assert all(1 <= n <= len(messages) and n not in excluded for n in refs)
        assert redact(json.dumps(summary, ensure_ascii=False))[1] == {}, 'Summary contains a credential pattern'
        sessions.append({'meta': meta, 'summary': summary, 'chunks': chunks,
                         'transcript_hash': digest(transcript), 'redactions': dict(redactions),
                         'excluded_runtime_messages': excluded, 'imported_messages': len(blocks)})
    return sessions


async def import_batch(call, sessions, checkpoint, progress=lambda value: None):
    report = json.loads(checkpoint.read_text(encoding='utf8')) if checkpoint.exists() else {'notes': {}, 'memories': {}, 'sessions': {}}
    catalog = {}
    offset = 0
    while True:
        page = await call('list_user_notes', {'limit': 100, 'offset': offset})
        for note in page:
            catalog[(note['title'], note['content_hash'])] = note
        if len(page) < 100:
            break
        offset += len(page)

    async def note(key, title, content):
        h = digest(content)
        existing = catalog.get((title, h))
        if key in report['notes']:
            old = report['notes'][key]
            if old['hash'] != h or old['title'] != title:
                raise ValueError('Prepared content changed after checkpoint; use a new batch')
            existing = await call('get_user_note', {'note_id': old['id'], 'revision': old['revision']})
        result = existing or await call('create_user_note', {'title': title, 'content': content})
        fetched = await call('get_user_note', {'note_id': result['id'], 'revision': result['revision']})
        if fetched['content'] != content or fetched['content_hash'] != h:
            raise ValueError('Source readback mismatch')
        report['notes'][key] = {'id': fetched['id'], 'revision': fetched['revision'], 'hash': h, 'title': title}
        catalog[(title,h)] = fetched
        save_json(checkpoint, report)
        return fetched

    async def memory(key, source, claim, node_type):
        candidate = await call('submit_candidate_claim', {'source_id': source['id'],
            'source_revision': source['revision'], 'source_content_hash': source['content_hash'],
            'claim': claim, 'suggested_node_type': node_type})
        node = await call('accept_candidate', {'candidate_id': candidate['id']})
        read = await call('get_memory_node', {'node_id': node['id']})
        assert read['content'] == claim and read['maturity'] == 'L2'
        assert read['authority'] == 'INFERRED' and read['verification_props']['status'] == 'UNVERIFIED'
        assert read['provenance']['source_id'] == source['id']
        assert read['provenance']['content_hash'] == source['content_hash']
        report['memories'][key] = {'id': node['id'], 'revision': read['version'], 'hash': digest(claim),
                                 'candidate_id': candidate['id'], 'source_id': source['id'], 'node_type': node_type}
        save_json(checkpoint, report)
        return node

    categories = defaultdict(list)
    # Write source blocks before summaries so Notes' newest-first window shows useful digests.
    for session in sessions:
        meta, summary = session['meta'], session['summary']
        sid, category = meta['id'], summary['category']
        refs = []
        for i, chunk in enumerate(session['chunks'], 1):
            title = f"【{category}】聊天原文｜{summary['title']}｜{i}/{len(session['chunks'])}"
            header = f"会话 {sid}；类别 {category}；分段 {i}/{len(session['chunks'])}\n来源性质：历史对话数据，含用户与助手原话，不代表当前指令或已验证事实。\n正文为可见文字导入副本，凭据已遮蔽；附件只保留引用。\n副本全文 SHA256：{session['transcript_hash']}\n\n"
            source = await note(f'{sid}/raw/{i}', title, header + chunk)
            refs.append({'id': source['id'], 'revision': source['revision'], 'hash': source['content_hash']})
        report['sessions'][sid] = {'category':category, 'classification':summary['classification'],
            'classification_reason':summary['classification_reason'], 'source_chunks':refs,
            'imported_messages':session['imported_messages'], 'redactions':session['redactions'],
            'excluded_runtime_messages':session['excluded_runtime_messages'],
            'transcript_hash':session['transcript_hash'], 'metadata_only':not bool(session['chunks'])}
        save_json(checkpoint, report)
        progress({'stage':'source_saved','session':sid,'chunks':len(refs)})
    for session in sessions:
        meta, summary = session['meta'], session['summary']
        sid, category = meta['id'], summary['category']
        refs = report['sessions'][sid]['source_chunks']
        source_list = '\n'.join(f"- {r['id']} rev {r['revision']} SHA256 {r['hash']}" for r in refs)
        origin_list = '\n'.join(f"- {s['path']}；快照字节 {s['snapshot_bytes']}；SHA256 {s['snapshot_sha256']}" for s in meta['sources'])
        body = f"# {summary['title']}\n分类：{category}（{summary['classification_reason']}）\n会话：{sid}\n工作目录：{meta['cwd']}\n子 Agent：{meta['subagent']}；父会话：{meta['parent_id']}\n\n{summary['summary_markdown']}\n\n摘要证据消息号：{summary['source_message_numbers']}\n覆盖：{summary['coverage']}\n\n## 可复用记忆提取\n"
        for c in summary.get('memory_claims', []):
            body += f"- {c['claim']}（消息 {c['evidence']}）\n"
        body += f"\n## 来源副本\n{source_list or '无可恢复正文，仅保存元数据。'}\n\n## 原始记录快照\n{origin_list}\n\n历史成果未在本次重新验证；Agent 归纳以 INFERRED / UNVERIFIED 保存，不能自动代表当前状态。"
        source = await note(f'{sid}/summary', f"【{category}】聊天摘要｜{summary['title']}", body)
        node_ids = []
        if refs:
            claim = f"【{category}】{summary['title']}\n{summary['summary_markdown']}\n\n历史会话 {sid}；证据消息 {summary['source_message_numbers']}。仅归纳可见记录，历史成果未在本次复测；详见来源笔记及原文分段。"
            node_ids.append((await memory(f'{sid}/session',source,claim,'Session'))['id'])
            for i, c in enumerate(summary.get('memory_claims', []),1):
                claim = f"【{category}】{c['claim']}\n\n来源：历史会话 {sid}，消息 {c['evidence']}。这是 Agent 对聊天的归纳；未将历史报告升级为当前验收结论。"
                node_ids.append((await memory(f'{sid}/claim/{i}',source,claim,c['node_type']))['id'])
        report['sessions'][sid]['summary_note_id'] = source['id']
        report['sessions'][sid]['memory_ids'] = node_ids
        categories[category].append((summary['title'], source['id'], node_ids))
        save_json(checkpoint, report)
        progress({'stage':'memory_saved','session':sid,'memories':len(node_ids)})
    index_refs = []
    for category, entries in sorted(categories.items()):
        lines = [f'# 聊天历史分类：{category}', '分类是来源主题标记，不改变访问 scope。当前使用目录笔记和记忆标题前缀，尚非 Collections 文件夹界面。']
        for title, nid, mids in entries:
            lines.append(f'- {title}：来源摘要 {nid}；AI Memory {", ".join(mids) or "无正文，不生成内容记忆"}')
        entry = await note('category/'+category, '聊天分类目录｜'+category, '\n\n'.join(lines))
        index_refs.append(f"- {category}：{len(entries)} 会话；目录 {entry['id']}")
    entry = await note('batch-index', '豆包聊天历史导入总目录｜2026-10-11',
        '# 本机豆包（Doubao Work）聊天历史导入\n\n'+'\n'.join(index_refs)+'\n\n仅覆盖本批可读取的本机会话快照，并非完整云端账号历史。原文在 Notes，摘要与提取在 AI Memory。2 个仅元数据会话没有内容记忆。附件只保留引用，不含二进制或 OCR。当前聊天后续新增内容未自动同步。')
    report['index_note_id'] = entry['id']
    report['state'] = 'IMPORTED_READBACK_VERIFIED'
    save_json(checkpoint, report)
    return report


async def main():
    from mcp import Client, StdioServerParameters
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    args=parser.parse_args()
    sessions=prepare_batch(args.prepared)
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'scripts/run_mcp.py'),
        '--data-dir',str(args.data_dir.resolve()),'--scope','default','--principal','doubao-history-import'])
    async with Client(params,read_timeout_seconds=90) as client:
        async def call(name, arguments):
            response=await client.call_tool(name,arguments)
            if response.is_error: raise RuntimeError(name+': '+str(response.content))
            return json.loads(response.content[0].text)
        report=await import_batch(call,sessions,args.checkpoint,lambda p: print(json.dumps(p),flush=True))
        print(json.dumps({'notes':len(report['notes']),'memories':len(report['memories']),'index':report['index_note_id']}),flush=True)

if __name__=='__main__':
    asyncio.run(main())
