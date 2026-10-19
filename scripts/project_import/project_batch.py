"""Import prepared Project Profiles and Architecture Claims into AI Knowledge OS via MCP stdio.

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

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def save_json(path: Path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def load_prepared_projects(folder: Path):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    projects = []
    for meta in manifest:
        key = meta['key']
        pdir = folder / key
        summary = json.loads((pdir / 'summary.json').read_text(encoding='utf-8'))
        profile = (pdir / 'profile.md').read_text(encoding='utf-8')
        chunks = split_text(profile) if profile else []
        projects.append({
            'meta': meta,
            'summary': summary,
            'profile': profile,
            'chunks': chunks,
            'profile_hash': digest(profile)
        })
    return projects


async def import_projects_batch(call, projects, checkpoint_path: Path, progress=lambda p: None):
    report = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {
        'notes': {},
        'memories': {},
        'projects': {}
    }

    # Fetch catalog of existing notes to avoid duplicate insertion
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

    # 1. Write source profile notes
    print(f"Ingesting raw project profiles for {len(projects)} projects...")
    for idx, p in enumerate(projects, 1):
        meta, summary = p['meta'], p['summary']
        pkey, cat = meta['key'], summary['category']
        refs = []
        for i, chunk in enumerate(p['chunks'], 1):
            title = f"【{cat}】工程档案原文｜{summary['title']}｜{i}/{len(p['chunks'])}"
            header = (
                f"工程标识：{pkey}；类别：{cat}；领域：{summary['domain']}；分段：{i}/{len(p['chunks'])}\n"
                f"代码根目录：{summary['path']}（文件规模：约 {summary['file_count']} 个文件）\n"
                f"文本 SHA256：{p['profile_hash']}\n\n"
            )
            src_note = await write_note(f'{pkey}/raw/{i}', title, header + chunk)
            refs.append({'id': src_note['id'], 'revision': src_note['revision'], 'hash': src_note['content_hash']})

        report['projects'][pkey] = {
            'category': cat,
            'source_chunks': refs,
            'file_count': summary['file_count']
        }
        save_json(checkpoint_path, report)
        progress({'stage': 'source_saved', 'index': idx, 'total': len(projects), 'project': pkey})

    # 2. Write summary profiles and memory claims
    print("Ingesting project architecture summaries and memory claims...")
    for idx, p in enumerate(projects, 1):
        meta, summary = p['meta'], p['summary']
        pkey, cat = meta['key'], summary['category']
        refs = report['projects'][pkey]['source_chunks']
        source_list = '\n'.join(f"- 档案分段笔记：`{r['id']}` (版本 {r['revision']})" for r in refs)

        body = (
            f"# {summary['title']}\n\n"
            f"- **项目标识**：`{pkey}`\n"
            f"- **所属类别**：`{cat}`\n"
            f"- **领域标签**：`{summary['domain']}`\n"
            f"- **本地代码路径**：`{summary['path']}`\n"
            f"- **文件规模**：约 {summary['file_count']} 个源码与配置文件\n\n"
            f"## 核心特征与设计概述\n"
            + '\n'.join(f"- {c}" for c in summary['claims']) +
            f"\n\n## 溯源档案关联\n"
            f"{source_list}\n\n"
            f"来源性质：由本地代码库及项目架构自动提取，存入为 L2 / INFERRED / UNVERIFIED 记忆体系。"
        )
        summary_note = await write_note(f'{pkey}/summary', f"【{cat}】核心架构与工程画像｜{summary['title']}", body)

        node_ids = []
        if refs:
            # Main Project Memory Node
            main_claim = (
                f"【{cat}】{summary['title']}\n"
                f"位于 `{summary['path']}`，包含约 {summary['file_count']} 个文件。\n"
                f"主领域：{summary['domain']}。"
            )
            main_mem = await write_memory(f'{pkey}/main', summary_note, main_claim, 'Document')
            node_ids.append(main_mem['id'])

            # Granular claim nodes
            for i, claim_text in enumerate(summary['claims'], 1):
                mem_claim = f"{claim_text}\n\n来源：项目档案 {pkey} ({summary['path']})。"
                claim_node = await write_memory(f'{pkey}/claim/{i}', summary_note, mem_claim, 'Fact')
                node_ids.append(claim_node['id'])

        report['projects'][pkey]['summary_note_id'] = summary_note['id']
        report['projects'][pkey]['memory_ids'] = node_ids
        categories[cat].append((summary['title'], summary_note['id'], node_ids))
        save_json(checkpoint_path, report)
        progress({'stage': 'memory_saved', 'index': idx, 'total': len(projects), 'project': pkey})

    # 3. Write category index notes
    print("Writing category index notes...")
    index_refs = []
    for cat, entries in sorted(categories.items()):
        lines = [
            f"# 核心工程分类索引：{cat}",
            f"总计收录 {len(entries)} 个工程项目架构画像。本索引保存在 default scope 中。",
            ""
        ]
        for title, nid, mids in entries:
            lines.append(f"- **{title}**：架构画像笔记 `{nid}`；AI Memory 节点：{', '.join(mids) or '无'}")
        cat_note = await write_note(f'category/{cat}', f"工程分类索引｜{cat}", '\n'.join(lines))
        index_refs.append(f"- **{cat}**：{len(entries)} 个项目；分类索引笔记：`{cat_note['id']}`")

    # 4. Master index note
    master_body = (
        "# 本地全景工程与学术资产总索引｜2026-10-11\n\n"
        "本目录完整收录了开发者在本地计算机（`D:\\02_Development_Tools` 及 `D:\\03_Study_Projects`）"
        "构建的全部核心软件工程、工业算法系统、桌面AI调度中枢与人工智能专业学术成果。\n\n"
        "## 各分类工程体系索引\n"
        + '\n'.join(index_refs) +
        "\n\n---\n"
        "数据结构与查询指引：\n"
        "1. 所有项目详细架构与配置文件均已分段保留在 Notes 中，具备完整 SHA256 溯源；\n"
        "2. 所有核心定位、技术栈和入口已作为 Fact / Document 注册至 AI Memory（L2 / UNVERIFIED 状态）；\n"
        "3. 全系统所有 Agent（Antigravity、WorkBuddy、豆包）均可通过 MCP 标准工具进行图谱关联检索与上下文注入。"
    )
    master_note = await write_note('project-master-index', '本地全景工程资产总索引｜2026-10-11', master_body)
    report['master_index_note_id'] = master_note['id']
    report['state'] = 'IMPORTED_VERIFIED'
    save_json(checkpoint_path, report)
    print(f"\nBatch import finished successfully! Master index note: {master_note['id']}")
    return report


async def main():
    from mcp import Client, StdioServerParameters
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / '.agent-tmp' / 'prepared_projects')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--checkpoint', type=Path, default=ROOT / '.agent-tmp' / 'prepared_projects' / 'checkpoint.json')
    args = parser.parse_args()

    projects = load_prepared_projects(args.prepared)
    print(f"Loaded {len(projects)} prepared project profiles.")

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(ROOT / 'scripts/run_mcp.py'),
            '--data-dir', str(args.data_dir.resolve()),
            '--scope', 'default',
            '--principal', 'project-profile-import'
        ]
    )

    async with Client(params, read_timeout_seconds=120) as client:
        async def call(name, arguments):
            resp = await client.call_tool(name, arguments)
            if resp.is_error:
                raise RuntimeError(f"{name} error: {resp.content}")
            return json.loads(resp.content[0].text)

        report = await import_projects_batch(
            call,
            projects,
            args.checkpoint,
            lambda p: print(f"[{p['stage']}] {p.get('index', '')}/{p.get('total', '')} ({p.get('project', '')})", flush=True)
        )
        print("Import completed:")
        print(f"  Notes created/verified: {len(report['notes'])}")
        print(f"  Memories created/verified: {len(report['memories'])}")
        print(f"  Master Index Note ID: {report['master_index_note_id']}")


if __name__ == '__main__':
    asyncio.run(main())
