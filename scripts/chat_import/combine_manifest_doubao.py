#!/usr/bin/env python3
"""Combine per-session prepared outputs into the manifest expected by import_batch,
and validate each session's summary against the pipeline contract.

Usage: combine_manifest.py --prepared <dir> --output <manifest.json>
Runs in the same validation spirit as import_batch.prepare_batch, so a failure
here is a failure before import.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.chat_import.redact import redact  # noqa: E402

ALLOWED_NODE_TYPES = {'Session', 'Decision', 'ProjectContext', 'Experience'}
REQUIRED_SUMMARY = ('session_id', 'title', 'category', 'classification',
                    'classification_reason', 'summary_markdown',
                    'source_message_numbers', 'coverage', 'memory_claims')


def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    entries, errors = [], []
    for folder in sorted(p for p in args.prepared.iterdir() if p.is_dir()):
        sid = folder.name
        meta_path = folder / 'metadata.json'
        summary_path = folder / 'summary.json'
        messages_path = folder / 'messages.json'
        if not meta_path.exists() or not summary_path.exists() or not messages_path.exists():
            if not (folder / 'access-error.txt').exists():
                errors.append(f'{sid}: missing metadata.json/summary.json/messages.json')
            continue
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        messages = json.loads(messages_path.read_text(encoding='utf-8'))

        if summary.get('session_id') != sid:
            errors.append(f'{sid}: summary.session_id mismatch')
        for field in REQUIRED_SUMMARY:
            if field not in summary:
                errors.append(f'{sid}: summary missing {field}')
        if meta.get('category') is not None and meta.get('category') != summary.get('category'):
            errors.append(f'{sid}: metadata.category != summary.category')
        node_types = {c.get('node_type') for c in summary.get('memory_claims', [])}
        bad = node_types - ALLOWED_NODE_TYPES
        if bad:
            errors.append(f'{sid}: node_type not allowed: {sorted(bad)}')
        n = len(messages)
        for ref_field, refs in (('source_message_numbers', summary.get('source_message_numbers') or []),
                                *[(f'claim[{i}].evidence', c.get('evidence') or []) for i, c in enumerate(summary.get('memory_claims', []))]):
            for r in refs:
                if not (isinstance(r, int) and 1 <= r <= n):
                    errors.append(f'{sid}: {ref_field} out of range: {r} (messages={n})')
        if len(summary.get('memory_claims', [])) < 3 or len(summary.get('memory_claims', [])) > 8:
            errors.append(f'{sid}: memory_claims count {len(summary.get("memory_claims", []))} outside 3..8')
        red, counts = redact(json.dumps(summary, ensure_ascii=False))
        if counts:
            errors.append(f'{sid}: summary contains credential patterns: {counts}')
        if not summary.get('summary_markdown') or len(summary['summary_markdown']) < 300:
            errors.append(f'{sid}: summary_markdown too short ({len(summary.get("summary_markdown", ""))} chars)')
        for i, m in enumerate(messages, 1):
            for field in ('id', 'role', 'text', 'timestamp', 'source_file', 'source_line', 'attachments'):
                if field not in m:
                    errors.append(f'{sid}: messages[{i}] missing {field}')
            if m.get('source_line') != i:
                errors.append(f'{sid}: messages[{i}].source_line != {i}')
        meta['category'] = summary.get('category')
        meta['classification'] = summary.get('classification')
        meta['title'] = summary.get('title', meta.get('title', ''))
        entries.append(meta)
    manifest = json.dumps(entries, ensure_ascii=False, indent=2)
    (args.output).write_text(manifest, encoding='utf-8')
    print(json.dumps({'sessions': len(entries), 'errors': errors}, ensure_ascii=False, indent=2))
    if errors:
        sys.exit(1)


if __name__ == '__main__':
    main()
