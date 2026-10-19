import hashlib

import pytest

from scripts.index_library_embeddings import index_missing
from scripts.embedding.service import SPACE


@pytest.mark.asyncio
async def test_backfill_follows_empty_pages_and_only_submits_matching_sources():
    calls = []
    text = '  原文\n'
    digest = hashlib.sha256(text.encode()).hexdigest()
    async def call(server, name, args):
        calls.append((server, name, args))
        if name == 'list_embedding_sources':
            if 'cursor' not in args:
                return {'items': [], 'next_cursor': ['MEMORY', 'a']}
            return {'items': [{'plane': 'NOTES', 'id': 'n', 'revision': 2, 'content_hash': digest}], 'next_cursor': None}
        if name == 'get_user_note':
            return {'content': text}
        if name == 'embed_texts':
            assert args == {'texts': [text], 'role': 'document'}
            return {'space': SPACE, 'items': [{'content_hash': digest, 'vector': [1.] + [0.] * 767}]}
        if name == 'submit_embedding':
            assert args['revision'] == 2 and args['content_hash'] == digest
            return {'idempotent': False}
        raise AssertionError(name)
    result = await index_missing(call)
    assert result == {'submitted': 1, 'skipped_blank': 0, 'pages': 2}
    assert [name for _, name, _ in calls].count('submit_embedding') == 1


@pytest.mark.asyncio
async def test_backfill_never_submits_changed_source():
    async def call(server, name, args):
        if name == 'list_embedding_sources':
            return {'items': [{'plane': 'MEMORY', 'id': 'm', 'revision': 1, 'content_hash': 'a' * 64}], 'next_cursor': None}
        if name == 'get_memory_node':
            return {'content': 'changed'}
        raise AssertionError('Changed source must stop before model call or submission')
    with pytest.raises(ValueError, match='changed'):
        await index_missing(call)
