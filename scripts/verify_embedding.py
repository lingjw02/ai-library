"""Opt-in real-model MCP acceptance using disposable, explicitly synthetic notes.

Run with the backend Python. Only the separate embedding subprocess imports ML
libraries. This small Chinese smoke corpus is not a held-out quality benchmark.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import Client, StdioServerParameters
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.core.retrieval.vector_index import VectorIndex
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.notes.notes_manager import NotesManager
from backend.storage.raw.raw_store import RawSourceStore
from scripts.embedding.service import SPACE

DOCUMENTS = [
    ('backup', '知识库备份流程：创建一致性快照，核对原文文件哈希，再恢复到全新的目录。不要覆盖现有数据。'),
    ('vectors', '知识库的语义检索使用外部 Agent 生成的嵌入向量，后端只保存和搜索向量，不加载模型。'),
    ('scope', '不同项目的资料按 scope 隔离；撤销 Agent 的访问授权后，它不能继续读取该范围的笔记。'),
    ('candidate', '新发现的主张先保存为候选，审核通过才进入记忆库。接受候选并不代表事实已经独立验证。'),
    ('notes', '修改笔记必须提供预期版本。另一个客户端先保存时，旧版本提交会被拒绝，用户草稿应保留。'),
    ('sources', '来源资料保留原文、版本和内容哈希。引用记忆时必须能追溯到具体来源。'),
    ('quarantine', '格式无效的输入进入隔离区。修复会创建新的有效候选，原始错误内容仍然保留。'),
    ('conflict', '相互矛盾的记忆需要记录冲突，保留双方证据，由用户审核解决，不能自动选择一个作为真相。'),
    ('plants', '阳台种植番茄需要充足阳光和排水良好的土壤，浇水避免长期积水。'),
    ('cooking', '番茄炒蛋先炒熟鸡蛋，再加入番茄翻炒，最后调味。'),
    ('travel', '乘坐火车旅行前检查车票日期、出发站和行李规定，预留到站时间。'),
    ('exercise', '跑步训练应循序渐进，先热身，结束后补水，避免突然增加运动量。'),
]
QUERIES = [
    ('backup', '电脑坏了以后怎样把资料救回来？'),
    ('vectors', '把文字变成数字表示的工作由谁来做？'),
    ('scope', '如何阻止被取消权限的助手查看项目资料？'),
    ('candidate', 'AI 提出来的说法能直接当成已经证实的事实吗？'),
    ('notes', '两个人同时编辑同一条笔记会怎样？'),
    ('sources', '我怎样知道这条记忆的结论来自哪份原文？'),
    ('quarantine', '提交的数据格式不正确，还能保留并修正吗？'),
    ('conflict', '资料里的两个结论互相否定时该怎么办？'),
]


def decoded(result):
    if result.is_error:
        raise AssertionError(str(result.content))
    return json.loads(result.content[0].text)


async def main():
    run = Path(tempfile.mkdtemp(prefix='embedding-acceptance-', dir=ROOT / '.agent-tmp'))
    print('EVIDENCE', run, flush=True)
    report = {'corpus': 'synthetic_chinese_smoke_v1', 'quality_limit': 'Not a held-out general quality benchmark',
              'space': SPACE, 'checks': {}, 'queries': []}
    embedding = StdioServerParameters(command=str(ROOT / '.agent-tmp/embedding-runtime/Scripts/python.exe'),
        args=[str(ROOT / 'scripts/run_embedding_mcp.py')], cwd=str(run))
    knowledge = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'scripts/run_mcp.py'),
        '--data-dir', str(run / 'data'), '--scope', 'embedding-qa', '--principal', 'embedding-acceptance'], cwd=str(run))
    # SDK initializes quickly before any model is loaded; first inference may be slow.
    async with Client(embedding, read_timeout_seconds=180) as embed, Client(knowledge, read_timeout_seconds=45) as memory:
        report['checks']['lazy_start'] = not decoded(await embed.call_tool('embedding_status', {}))['loaded']
        notes = []
        for key, content in DOCUMENTS:
            notes.append(decoded(await memory.call_tool('create_user_note', {'title': key, 'content': content})))
        start = time.perf_counter()
        vectors = decoded(await embed.call_tool('embed_texts', {'texts': [d[1] for d in DOCUMENTS], 'role': 'document'}))
        report['cold_load_plus_documents_seconds'] = time.perf_counter() - start
        report['document_token_counts'] = vectors['token_counts']
        for note, item in zip(notes, vectors['items']):
            assert item['content_hash'] == note['content_hash']
            request = {'space': vectors['space'], 'plane': 'NOTES', 'object_id': note['id'],
                'revision': note['revision'], 'content_hash': item['content_hash'], 'vector': item['vector']}
            decoded(await memory.call_tool('submit_embedding', request))
        report['checks']['idempotent_submit'] = decoded(await memory.call_tool('submit_embedding', request))['idempotent']
        sources = decoded(await memory.call_tool('list_embedding_sources', {'space': SPACE, 'plane': 'NOTES'}))
        report['checks']['all_sources_embedded'] = not sources['items']
        start = time.perf_counter()
        queries = decoded(await embed.call_tool('embed_texts', {'texts': [q[1] for q in QUERIES], 'role': 'query'}))
        report['warm_queries_seconds'] = time.perf_counter() - start
        store = CanonicalStorage(str(run / 'data/knowledge_os.db'))
        index = VectorIndex(store)
        coordinator = RetrievalCoordinator(store, NotesManager(store, RawSourceStore(str(run / 'data/raw_sources'))))
        for (expected, text), item in zip(QUERIES, queries['items']):
            results = index.search(item['vector'], space=SPACE, scope='embedding-qa', plane='NOTES', limit=3)
            keyword = coordinator.search(text, scope='embedding-qa', plane='NOTES', limit=3)
            hybrid = coordinator.search(text, scope='embedding-qa', plane='NOTES', limit=3,
                embedding_query={'space': SPACE, 'vector': item['vector']})
            ranks = [r['title'] for r in results['items']]
            report['queries'].append({'query': text, 'expected': expected, 'vector_top3': ranks,
                'keyword_top3': [r['title'] for r in keyword['items']],
                'hybrid_top3': [r['title'] for r in hybrid['items']],
                'top1_correct': bool(ranks and ranks[0] == expected),
                'top3_correct': expected in ranks})
        context = decoded(await memory.call_tool('search_knowledge_context', {'query': QUERIES[0][1], 'plane': 'NOTES',
            'embedding_query': {'space': SPACE, 'vector': queries['items'][0]['vector']}}))
        report['checks']['mcp_hybrid_context'] = context['retrieval']['vector_search'] and DOCUMENTS[0][1] in context['combined_prompt']
        decoded(await memory.call_tool('revise_user_note', {'note_id': notes[-1]['id'], 'title': 'changed',
            'content': '修订后应重新生成向量。', 'expected_revision': 1}))
        report['checks']['stale_submission_denied'] = (await memory.call_tool('submit_embedding', request)).is_error
        report['checks']['cross_scope_denied'] = (await memory.call_tool('list_embedding_sources', {'space': SPACE, 'scope': 'private'})).is_error
        rejected = await embed.call_tool('embed_texts', {'texts': ['hello ' * 9000], 'role': 'document'})
        report['checks']['overlong_rejected'] = rejected.is_error and 'No text was truncated' in str(rejected.content)
        markers = decoded(await embed.call_tool('embed_texts', {
            'texts': ['文档中的原文标记 ' + marker for marker in ['<|image|>', '<|audio|>', '<|video|>']],
            'role': 'document'}))
        report['checks']['literal_markers_preserved'] = len({tuple(i['vector']) for i in markers['items']}) == 3
        report['checks']['backend_did_not_load_model'] = not index.status()['model_loaded'] and 'torch' not in sys.modules
        report['adapter_status'] = decoded(await embed.call_tool('embedding_status', {}))
        # Reusable real vectors for installed-client probes; contains only this public synthetic fixture.
        (run / 'client-fixture.json').write_text(json.dumps({'space': SPACE, 'document': DOCUMENTS[0][1],
            'document_vector': vectors['items'][0], 'query': QUERIES[0][1], 'query_vector': queries['items'][0]}), encoding='utf-8')
    report['vector_top1'] = sum(q['top1_correct'] for q in report['queries']) / len(QUERIES)
    report['vector_top3'] = sum(q['top3_correct'] for q in report['queries']) / len(QUERIES)
    (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
    assert all(report['checks'].values()), report['checks']
    assert report['vector_top3'] == 1, 'Small-corpus retrieval regression; inspect rankings'


if __name__ == '__main__':
    asyncio.run(main())
