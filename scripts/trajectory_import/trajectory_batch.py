"""Import prepared Antigravity Trajectory Profiles into AI Knowledge OS via MCP stdio.

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


def load_prepared_trajectories(folder: Path):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    clusters = []
    for meta in manifest:
        key = meta['key']
        cdir = folder / key
        summary = json.loads((cdir / 'summary.json').read_text(encoding='utf-8'))
        profile = (cdir / 'profile.md').read_text(encoding='utf-8')
        chunks = split_text(profile) if profile else []
        clusters.append({
            'meta': meta,
            'summary': summary,
            'profile': profile,
            'chunks': chunks,
            'profile_hash': digest(profile)
        })
    return clusters


async def import_trajectories_batch(call, clusters, checkpoint_path: Path, progress=lambda p: None):
    report = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {
        'notes': {},
        'memories': {},
        'clusters': {}
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

    # 1. Write source profile notes
    print(f"Ingesting raw trajectory profiles for {len(clusters)} clusters...")
    for idx, c in enumerate(clusters, 1):
        meta, summary = c['meta'], c['summary']
        ckey, cat = meta['key'], summary['category']
        refs = []
        for i, chunk in enumerate(c['chunks'], 1):
            title = f"【{cat}】研发履历原文｜{summary['title']}｜{i}/{len(c['chunks'])}"
            header = (
                f"轨迹标识：{ckey}；领域分类：{cat}；会话数：{summary['sessions_count']}；研发步骤：{summary['total_steps']} Steps；分段：{i}/{len(c['chunks'])}\n"
                f"文本 SHA256：{c['profile_hash']}\n\n"
            )
            src_note = await write_note(f'{ckey}/raw/{i}', title, header + chunk)
            refs.append({'id': src_note['id'], 'revision': src_note['revision'], 'hash': src_note['content_hash']})

        report['clusters'][ckey] = {
            'category': cat,
            'source_chunks': refs,
            'total_steps': summary['total_steps'],
            'sessions_count': summary['sessions_count']
        }
        save_json(checkpoint_path, report)
        progress({'stage': 'source_saved', 'index': idx, 'total': len(clusters), 'cluster': ckey})

    # 2. Write summary profiles and memory claims
    print("Ingesting trajectory summaries and experience memory claims...")
    for idx, c in enumerate(clusters, 1):
        meta, summary = c['meta'], c['summary']
        ckey, cat = meta['key'], summary['category']
        refs = report['clusters'][ckey]['source_chunks']
        source_list = '\n'.join(f"- 履历分段笔记：`{r['id']}` (版本 {r['revision']})" for r in refs)

        body = (
            f"# {summary['title']}\n\n"
            f"- **聚合标识**：`{ckey}`\n"
            f"- **专业分类**：`{cat}`\n"
            f"- **深度会话**：{summary['sessions_count']} 组完整会话库\n"
            f"- **研发步骤**：{summary['total_steps']} 个详细执行步骤\n\n"
            f"## 核心攻关成就与总结\n"
            f"{summary['summary']}\n\n"
            f"## 经验要点与实战决策\n"
            + '\n'.join(f"- {cl}" for cl in summary['claims']) +
            f"\n\n## 溯源关联\n"
            f"{source_list}\n\n"
            f"来源性质：由 Antigravity 76 组 SQLite 全量对话轨迹（26,945 Steps）聚类提炼，存入为 L2 / INFERRED / UNVERIFIED 记忆节点。"
        )
        summary_note = await write_note(f'{ckey}/summary', f"【{cat}】研发实战履历与关键决策｜{summary['title']}", body)

        node_ids = []
        if refs:
            # Main Experience Memory Node
            main_claim = (
                f"【{cat}】{summary['title']}\n"
                f"包含 {summary['sessions_count']} 组会话共 {summary['total_steps']} 个研发步骤。\n"
                f"{summary['summary']}"
            )
            main_mem = await write_memory(f'{ckey}/main', summary_note, main_claim, 'Experience')
            node_ids.append(main_mem['id'])

            # Granular claim nodes
            for i, claim_text in enumerate(summary['claims'], 1):
                mem_claim = f"{claim_text}\n\n来源：实战轨迹 {ckey}。"
                claim_node = await write_memory(f'{ckey}/claim/{i}', summary_note, mem_claim, 'Fact')
                node_ids.append(claim_node['id'])

        report['clusters'][ckey]['summary_note_id'] = summary_note['id']
        report['clusters'][ckey]['memory_ids'] = node_ids
        categories[cat].append((summary['title'], summary_note['id'], node_ids))
        save_json(checkpoint_path, report)
        progress({'stage': 'memory_saved', 'index': idx, 'total': len(clusters), 'cluster': ckey})

    # 3. Write category index notes
    print("Writing category index notes...")
    index_refs = []
    for cat, entries in sorted(categories.items()):
        lines = [
            f"# 实战研发分类索引：{cat}",
            f"总计包含 {len(entries)} 个工程研发轨迹聚类。本索引保存在 default scope 中。",
            ""
        ]
        for title, nid, mids in entries:
            lines.append(f"- **{title}**：履历画像笔记 `{nid}`；AI Memory 节点：{', '.join(mids) or '无'}")
        cat_note = await write_note(f'traj_category/{cat}', f"研发分类索引｜{cat}", '\n'.join(lines))
        index_refs.append(f"- **{cat}**：{len(entries)} 个领域轨迹；分类索引笔记：`{cat_note['id']}`")

    # 4. Master index note
    master_body = (
        "# 全生命周期 26,945 步骤实战研发履历总目录｜2026-10-11\n\n"
        "本目录完整收录了开发者在 Antigravity 平台中执行的全部 76 组 SQLite 会话数据库，"
        "累计 26,945 个研发执行步骤（Steps），覆盖从计算机视觉学术研究、工业CAD三维建模、"
        "游戏过程式生成到桌面AI中枢的完整演进全过程。\n\n"
        "## 各研发领域体系索引\n"
        + '\n'.join(index_refs) +
        "\n\n---\n"
        "数据价值：\n"
        "1. **深度覆盖**：不仅包含最终代码，更记录了每一次报错调试、架构重构与方案选型的真实思考与排错路径；\n"
        "2. **全Agent透传**：任何 AI 助手（Antigravity、WorkBuddy、豆包）在遇到相似技术问题时，均可检索该履历库，复用历史踩坑经验。"
    )
    master_note = await write_note('traj-master-index', '全生命周期26,945步实战研发履历总目录｜2026-10-11', master_body)
    report['master_index_note_id'] = master_note['id']
    report['state'] = 'IMPORTED_VERIFIED'
    save_json(checkpoint_path, report)
    print(f"\nBatch import finished successfully! Master index note: {master_note['id']}")
    return report


async def main():
    from mcp import Client, StdioServerParameters
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / '.agent-tmp' / 'prepared_trajectories')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--checkpoint', type=Path, default=ROOT / '.agent-tmp' / 'prepared_trajectories' / 'checkpoint.json')
    args = parser.parse_args()

    clusters = load_prepared_trajectories(args.prepared)
    print(f"Loaded {len(clusters)} prepared trajectory clusters.")

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(ROOT / 'scripts/run_mcp.py'),
            '--data-dir', str(args.data_dir.resolve()),
            '--scope', 'default',
            '--principal', 'trajectory-profile-import'
        ]
    )

    async with Client(params, read_timeout_seconds=120) as client:
        async def call(name, arguments):
            resp = await client.call_tool(name, arguments)
            if resp.is_error:
                raise RuntimeError(f"{name} error: {resp.content}")
            return json.loads(resp.content[0].text)

        report = await import_trajectories_batch(
            call,
            clusters,
            args.checkpoint,
            lambda p: print(f"[{p['stage']}] {p.get('index', '')}/{p.get('total', '')} ({p.get('cluster', '')})", flush=True)
        )
        print("Import completed:")
        print(f"  Notes created/verified: {len(report['notes'])}")
        print(f"  Memories created/verified: {len(report['memories'])}")
        print(f"  Master Index Note ID: {report['master_index_note_id']}")


if __name__ == '__main__':
    asyncio.run(main())
