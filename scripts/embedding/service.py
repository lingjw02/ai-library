"""Agent-side, text-only embedding adapter. Never imported by the backend.

Weights are provisioned separately. Inference is local-only and cannot silently
download or substitute a model. Exact content is hashed before prompt formatting.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import threading

MODEL_ID = 'google/embeddinggemma-2'
REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
SPACE = {'model_id': MODEL_ID, 'model_version': REVISION + ':text-v1:literal:mean-inclprompt:fp32',
         'dimension': 768, 'metric': 'cosine', 'normalization': 'l2'}
PROMPTS = {'query': 'task: search result | query: ', 'document': 'title: none | text: '}
MAX_TOKENS = 8192
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT / '.agent-tmp/models/embeddinggemma-2' / REVISION


def prepare_texts(texts, role):
    if not isinstance(role, str) or role not in PROMPTS:
        raise ValueError('role must be query or document')
    if not isinstance(texts, list) or not 1 <= len(texts) <= 16:
        raise ValueError('texts must contain 1 to 16 strings')
    if any(not isinstance(t, str) or not t.strip() or len(t) > 65536 for t in texts):
        raise ValueError('Each text must be nonblank and at most 65536 characters')
    if sum(map(len, texts)) > 131072:
        raise ValueError('Total request exceeds 131072 characters')
    return [PROMPTS[role] + text for text in texts]


def finish_embeddings(texts, role, rows):
    prepare_texts(texts, role)
    if len(rows) != len(texts):
        raise ValueError('Model returned the wrong row count')
    items = []
    for text, row in zip(texts, rows):
        if len(row) != 768 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in row):
            raise ValueError('Model returned an invalid 768-dimensional vector')
        norm = math.hypot(*row)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('Model returned an invalid norm')
        items.append({'content_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                      'vector': [v / norm for v in row]})
    return {'space': dict(SPACE), 'role': role, 'source_contract': 'sha256_utf8_content_only', 'items': items}


class EmbeddingService:
    def __init__(self, model_dir=DEFAULT_MODEL_DIR):
        self.model_dir = Path(model_dir).resolve()
        self.model = None
        self.tokenizer = None
        self._lock = threading.Lock()

    def status(self):
        return {'space': dict(SPACE), 'loaded': self.model is not None, 'device': 'cpu',
                'dtype': 'float32', 'modalities': ['text'], 'max_tokens': MAX_TOKENS,
                'max_batch': 16, 'overlength_policy': 'reject_without_truncation',
                'network_during_inference': False,
                'source_contract': 'sha256_utf8_content_only'}

    def _load(self):
        manifest_path = self.model_dir / 'deployment.json'
        if not manifest_path.is_file():
            raise ValueError('Missing verified model deployment; run scripts/embedding/setup.py')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('model_id') != MODEL_ID or manifest.get('revision') != REVISION:
            raise ValueError('Model deployment identity does not match the pinned revision')
        files = manifest.get('files', {})
        if not {'config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors'} <= files.keys():
            raise ValueError('Model deployment manifest is incomplete')
        for name, expected in files.items():
            path = (self.model_dir / name).resolve()
            if not path.is_relative_to(self.model_dir) or not path.is_file():
                raise ValueError('Model deployment file is missing or outside its directory')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Model deployment file failed integrity verification: ' + name)
        # These variables disable network lookups even if library defaults change.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        import torch
        from transformers import AutoConfig, AutoModel, AutoTokenizer
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        config = AutoConfig.from_pretrained(self.model_dir, local_files_only=True, trust_remote_code=False)
        config.vision_config = None
        config.audio_config = None
        tokenizer = AutoTokenizer.from_pretrained(self.model_dir, local_files_only=True, trust_remote_code=False)
        model = AutoModel.from_pretrained(self.model_dir, config=config, dtype=torch.float32,
            local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
        self.tokenizer, self.model = tokenizer, model

    def embed(self, texts, role):
        prompted = prepare_texts(texts, role)
        # One inference at a time; bound batch memory even under concurrent MCP calls.
        with self._lock:
            if self.model is None:
                self._load()
            import torch
            # User documents may discuss <|image|> etc. Encode them literally;
            # otherwise the text-only model replaces modality IDs with PAD.
            # The tokenizer still inserts its actual BOS/EOS wrapper.
            tokens = self.tokenizer(prompted, padding=False, truncation=False, split_special_tokens=True)
            lengths = [len(ids) for ids in tokens['input_ids']]
            if any(n > MAX_TOKENS for n in lengths):
                raise ValueError(f'Input exceeds {MAX_TOKENS} tokens including prompt; split upstream. No text was truncated.')
            # Sequential rows prevent padding a large batch to the longest input.
            rows = []
            with torch.inference_mode():
                for ids, mask in zip(tokens['input_ids'], tokens['attention_mask']):
                    inputs = {'input_ids': torch.tensor([ids]), 'attention_mask': torch.tensor([mask])}
                    hidden = self.model(**inputs).last_hidden_state.float()
                    weights = inputs['attention_mask'].unsqueeze(-1).float()
                    pooled = (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)
                    rows.append(pooled[0].tolist())
            result = finish_embeddings(texts, role, rows)
            result['token_counts'] = lengths
            return result
