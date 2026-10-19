"""Import prepared Pangu Narrative & Game Systems into AI Knowledge OS via MCP stdio.

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


def load_prepared_pangu(folder: Path):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    items = []
    for meta in manifest:
        key = meta['key']
        pdir = folder / key
        summary = json.loads((pdir / 'summary.json').read_text(encoding='utf-8'))
        doc_text = (pdir / 'document.md').read_text(encoding='utf-8')
        chunks = split_text(doc_text) if doc_text else []
        items.append({
            'meta': meta,
            'summary': summary,
            'document': doc_text,
            'chunks': chunks,
            'doc_hash': digest(doc_text)
        })
    return items


async def import_pangu_batch(call, items, checkpoint_path: Path, progress=lambda p: None):
    report = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {
        'notes': {},
        'memories': {},
        'items': {}
    }

    # Fetch existing notes to avoid duplicate insertion
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

    # 1. Write source documents
    print(f"Ingesting raw milestone specifications for {len(items)} systems...")
    for idx, item in enumerate(items, 1):
        meta, summary = item['meta'], item['summary']
        pkey, cat = meta['key'], summary['category']
        refs = []
        for i, chunk in enumerate(item['chunks'], 1):
            title = f"【{cat}】设计规格原文｜{summary['title']}｜{i}/{len(item['chunks'])}"
            header = (
                f"模块标识：{pkey}；分类：{cat}；节点类型：{summary['node_type']}；分段：{i}/{len(item['chunks'])}\n"
                f"源文件路径：{summary['source_path']}\n"
                f"内容 SHA256：{item['doc_hash']}\n\n"
            )
            src_note = await write_note(f'{pkey}/raw/{i}', title, header + chunk)
            refs.append({'id': src_note['id'], 'revision': src_note['revision'], 'hash': src_note['content_hash']})

        report['items'][pkey] = {
            'category': cat,
            'source_chunks': refs
        }
        save_json(checkpoint_path, report)
        progress({'stage': 'source_saved', 'index': idx, 'total': len(items), 'item': pkey})

    # 2. Write architecture summaries and memory claims
    print("Ingesting system specifications and AI memory claims...")
    for idx, item in enumerate(items, 1):
        meta, summary = item['meta'], item['summary']
        pkey, cat = meta['key'], summary['category']
        refs = report['items'][pkey]['source_chunks']
        source_list = '\n'.join(f"- 规格分段笔记：`{r['id']}` (版本 {r['revision']})" for r in refs)

        body = (
            f"# {summary['title']}\n\n"
            f"- **系统模块**：`{pkey}`\n"
            f"- **所属分类**：`{cat}`\n"
            f"- **源设计文档**：`{summary['source_path']}`\n\n"
            f"## 系统设计定位与机制概述\n"
            f"{summary['summary']}\n\n"
            f"## 关键机制与契约规范\n"
            + '\n'.join(f"- {c}" for c in summary['claims']) +
            f"\n\n## 溯源关联\n"
            f"{source_list}\n\n"
            f"来源性质：由盘古游戏设计规范体系提取，存入为 L2 / INFERRED / UNVERIFIED 记忆节点。"
        )
        summary_note = await write_note(f'{pkey}/summary', f"【{cat}】系统设计规范｜{summary['title']}", body)

        node_ids = []
        if refs:
            # Main System Memory Node
            main_claim = (
                f"【{cat}】{summary['title']}\n"
                f"{summary['summary']}\n\n"
                f"来源：盘古设计文档 `{summary['source_path']}`。"
            )
            main_mem = await write_memory(f'{pkey}/main', summary_note, main_claim, summary['node_type'])
            node_ids.append(main_mem['id'])

            # Granular claim nodes
            for i, claim_text in enumerate(summary['claims'], 1):
                mem_claim = f"{claim_text}\n\n来源：系统规范 {pkey}。"
                claim_node = await write_memory(f'{pkey}/claim/{i}', summary_note, mem_claim, 'Fact')
                node_ids.append(claim_node['id'])

        report['items'][pkey]['summary_note_id'] = summary_note['id']
        report['items'][pkey]['memory_ids'] = node_ids
        categories[cat].append((summary['title'], summary_note['id'], node_ids))
        save_json(checkpoint_path, report)
        progress({'stage': 'memory_saved', 'index': idx, 'total': len(items), 'item': pkey})

    # 3. Write category index notes
    print("Writing category index notes...")
    index_refs = []
    for cat, entries in sorted(categories.items()):
        lines = [
            f"# 游戏与叙事子系统索引：{cat}",
            f"收录盘古体系下 {len(entries)} 个核心子系统设计规范。本索引保存在 default scope 中。",
            ""
        ]
        for title, nid, mids in entries:
            lines.append(f"- **{title}**：规范画像笔记 `{nid}`；AI Memory 节点：{', '.join(mids) or '无'}")
        cat_note = await write_note(f'pangu_category/{cat}', f"盘古子系统索引｜{cat}", '\n'.join(lines))
        index_refs.append(f"- **{cat}**：{len(entries)} 个系统；分类索引笔记：`{cat_note['id']}`")

    # 4. Master index note
    master_body = (
        "# 盘古AI全套叙事引擎、游戏机制与设计总目录｜2026-10-11\n\n"
        "本目录完整收录了盘古AI（`Pangu-novel`）的核心体系架构，涵盖从 M1 到 P11 的 11 个核心里程碑设计、"
        "16 项开发者接口契约参考，以及 LetterSea 跨角色互动信件系统。\n\n"
        "## 各分类设计体系索引\n"
        + '\n'.join(index_refs) +
        "\n\n---\n"
        "系统架构特色：\n"
        "1. **可玩内核与对抗公式**：主循环轮转、确定性骰子、六维属性与伤害减免算法；\n"
        "2. **分层记忆与卡牌网络**：卡牌生命周期触发、SQLite FTS5 全文搜索、Obsidian 双向屏幕联动；\n"
        "3. **自治NPC与过程生成**：好感度矩阵、情绪演化、地牢瓦片拓扑与大世界迷雾探索。"
    )
    master_note = await write_note('pangu-master-index', '盘古AI叙事引擎与游戏系统总目录｜2026-10-11', master_body)
    report['master_index_note_id'] = master_note['id']
    report['state'] = 'IMPORTED_VERIFIED'
    save_json(checkpoint_path, report)
    print(f"\nBatch import finished successfully! Master index note: {master_note['id']}")
    return report


async def main():
    from mcp import Client, StdioServerParameters
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / '.agent-tmp' / 'prepared_pangu')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--checkpoint', type=Path, default=ROOT / '.agent-tmp' / 'prepared_pangu' / 'checkpoint.json')
    args = parser.parse_args()

    items = load_prepared_pangu(args.prepared)
    print(f"Loaded {len(items)} prepared Pangu systems.")

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(ROOT / 'scripts/run_mcp.py'),
            '--data-dir', str(args.data_dir.resolve()),
            '--scope', 'default',
            '--principal', 'pangu-narrative-import'
        ]
    )

    async with Client(params, read_timeout_seconds=120) as client:
        async def call(name, arguments):
            resp = await client.call_tool(name, arguments)
            if resp.is_error:
                raise RuntimeError(f"{name} error: {resp.content}")
            return json.loads(resp.content[0].text)

        report = await import_pangu_batch(
            call,
            items,
            args.checkpoint,
            lambda p: print(f"[{p['stage']}] {p.get('index', '')}/{p.get('total', '')} ({p.get('item', '')})", flush=True)
        )
        print("Import completed:")
        print(f"  Notes created/verified: {len(report['notes'])}")
        print(f"  Memories created/verified: {len(report['memories'])}")
        print(f"  Master Index Note ID: {report['master_index_note_id']}")


if __name__ == '__main__':
    asyncio.run(main())
