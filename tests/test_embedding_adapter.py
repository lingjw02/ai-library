"""External adapter contract tests; no model download in the backend suite."""
import hashlib
import math
import json
from pathlib import Path
import sys

import pytest

from scripts.embedding.service import EmbeddingService, prepare_texts, finish_embeddings, SPACE


def test_prompts_preserve_exact_content_and_roles():
    text = '  中文来源\n第二行\n'
    assert prepare_texts([text], 'document') == ['title: none | text: ' + text]
    assert prepare_texts([text], 'query') == ['task: search result | query: ' + text]
    result = finish_embeddings([text], 'document', [[2.] + [0.] * 767])
    assert result['space'] == SPACE
    assert result['items'][0]['content_hash'] == hashlib.sha256(text.encode()).hexdigest()
    assert result['items'][0]['vector'][0] == 1.
    assert len(result['items'][0]['vector']) == 768


@pytest.mark.parametrize('texts,role', [([], 'query'), (['x'] * 17, 'document'),
    (['x' * 65537], 'document'), (['x'], 'image'), ([1], 'query'),
    (['  '], 'document'), ('text', 'query')])
def test_rejects_unsupported_or_unbounded_input(texts, role):
    with pytest.raises(ValueError):
        prepare_texts(texts, role)


@pytest.mark.parametrize('rows', [[], [[1.]], [[0.] * 768],
    [[math.nan] * 768], [[math.inf] * 768], [[True] * 768]])
def test_bad_model_output_never_becomes_a_submittable_vector(rows):
    with pytest.raises(ValueError):
        finish_embeddings(['text'], 'document', rows)


def test_status_does_not_download_or_load_model(tmp_path):
    service = EmbeddingService(tmp_path)
    status = service.status()
    assert status['loaded'] is False
    assert status['space']['dimension'] == 768
    assert status['network_during_inference'] is False
    with pytest.raises(ValueError, match='deployment'):
        service.embed(['a document'], 'document')


def test_pipeline_identity_includes_revision_and_input_contract():
    assert '914f7f89142e33e77833254d9c9b90c3cef7303b' in SPACE['model_version']
    assert 'text-v1' in SPACE['model_version']
    assert SPACE['normalization'] == 'l2'


@pytest.mark.asyncio
async def test_embedding_mcp_starts_without_model_and_rejects_bad_calls(tmp_path):
    from mcp import Client, StdioServerParameters
    script = Path(__file__).resolve().parents[1] / 'scripts/run_embedding_mcp.py'
    async with Client(StdioServerParameters(command=sys.executable,
            args=[str(script), '--model-dir', str(tmp_path)], cwd=str(tmp_path)), read_timeout_seconds=30) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {'embed_texts', 'embedding_status'}
        status = json.loads((await client.call_tool('embedding_status', {})).content[0].text)
        assert not status['loaded']
        for arguments in [{'texts': [], 'role': 'query'},
                          {'texts': ['text'], 'role': 'image'},
                          {'texts': ['text'], 'role': 'query', 'scope': 'private'}]:
            assert (await client.call_tool('embed_texts', arguments)).is_error
        missing = await client.call_tool('embed_texts', {'texts': ['text'], 'role': 'query'})
        assert missing.is_error and 'deployment' in str(missing.content)


def test_tampered_deployment_refused_before_importing_model(tmp_path):
    from scripts.embedding.service import MODEL_ID, REVISION
    names = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors']
    files = {}
    for name in names:
        (tmp_path / name).write_bytes(b'original')
        files[name] = hashlib.sha256(b'original').hexdigest()
    (tmp_path / 'deployment.json').write_text(json.dumps({'model_id': MODEL_ID, 'revision': REVISION, 'files': files}))
    (tmp_path / 'model.safetensors').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='integrity'):
        EmbeddingService(tmp_path).embed(['text'], 'document')
