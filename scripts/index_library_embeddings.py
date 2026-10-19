"""Explicit Agent-side backfill through two MCPs; never changes source content.

Reruns skip revisions already embedded in the pinned space. Concurrent source
changes stop safely; the backend rechecks scope/revision/hash at submission.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.embedding.service import SPACE


async def index_missing(call):
    cursor = None
    submitted = skipped = pages = 0
    while True:
        args = {'space': SPACE, 'plane': 'BOTH', 'limit': 16}
        if cursor is not None:
            args['cursor'] = cursor
        page = await call('memory', 'list_embedding_sources', args)
        pages += 1
        for source in page['items']:
            note = source['plane'] == 'NOTES'
            item = await call('memory', 'get_user_note' if note else 'get_memory_node',
                {'note_id' if note else 'node_id': source['id'], 'revision': source['revision']})
            content = item['content']
            if hashlib.sha256(content.encode('utf-8')).hexdigest() != source['content_hash']:
                raise ValueError('Source content changed or hash is invalid; refresh sources and retry')
            if not content.strip():
                skipped += 1
                continue
            encoded = await call('embedding', 'embed_texts', {'texts': [content], 'role': 'document'})
            vector = encoded['items'][0]
            if encoded['space'] != SPACE or vector['content_hash'] != source['content_hash']:
                raise ValueError('Embedding contract does not match source or pinned model')
            await call('memory', 'submit_embedding', {'space': SPACE, 'plane': source['plane'],
                'object_id': source['id'], 'revision': source['revision'],
                'content_hash': source['content_hash'], 'vector': vector['vector']})
            submitted += 1
        cursor = page['next_cursor']
        if cursor is None:
            return {'submitted': submitted, 'skipped_blank': skipped, 'pages': pages}


async def main():
    from mcp import Client, StdioServerParameters
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--principal', default='local-embedding-backfill')
    args = parser.parse_args()
    if not (args.data_dir / 'knowledge_os.db').is_file():
        parser.error('Data directory must contain an existing knowledge_os.db')
    memory = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'scripts/run_mcp.py'),
        '--data-dir', str(args.data_dir.resolve()), '--scope', args.scope, '--principal', args.principal])
    embedding = StdioServerParameters(command=str(ROOT / '.agent-tmp/embedding-runtime/Scripts/python.exe'),
        args=[str(ROOT / 'scripts/run_embedding_mcp.py')])
    async with Client(memory, read_timeout_seconds=60) as mem, Client(embedding, read_timeout_seconds=600) as emb:
        async def call(server, name, arguments):
            result = await (mem if server == 'memory' else emb).call_tool(name, arguments)
            if result.is_error:
                raise ValueError(str(result.content))
            return json.loads(result.content[0].text)
        print(json.dumps(await index_missing(call)))


if __name__ == '__main__':
    asyncio.run(main())
