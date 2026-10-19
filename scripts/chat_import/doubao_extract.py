#!/usr/bin/env python3
"""Extract visible messages from raw Doubao (Doubao Work) conversation dumps.

Input: a session directory containing page-*.json files. Each file is the raw
return of im_get_conversation_messages for one page: either the parsed object
{conversation, page, turns, ...} directly, or a wrapper whose content[].text
holds that object as a JSON string (the tool's standard text payload).

Output (same directory):
  messages.json   - list of visible messages {id, role, phase, text, timestamp,
                    source_file, source_line, attachments}
  transcript.md   - full readable transcript (for reference / spot checks)
  reading-map.md  - compact map: user messages in full, assistant truncated
                    (what a summarizer needs without loading everything)
  metadata.json   - session meta incl. source snapshot digest (for manifest)

Rules follow the 2026-10-10 history import: only visible user/assistant
message text is content; tool_call items and runtime/system blocks are not
conversation content; attachments (image/file entity JSON) are recorded as
references only, never decoded or OCR'd; the raw dump is never modified.
"""
import argparse
import hashlib
import json
from pathlib import Path

ATTACHMENT_PLACEHOLDER = '[附件引用 {n} 项：仅保留引用存在性；未导入二进制、未做 OCR。]'
SOURCE_TAG = 'doubao-conversation'


def unwrap(obj):
    """Return the dict that has 'turns', unwrapping a tool text payload if needed."""
    if isinstance(obj, dict):
        if 'turns' in obj:
            return obj
        content = obj.get('content')
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'text':
                    try:
                        inner = json.loads(part.get('text', ''))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(inner, dict) and 'turns' in inner:
                        return inner
    return None


def load_pages(session_dir):
    pages = []
    for path in sorted(session_dir.glob('page-*.json')):
        raw = json.loads(path.read_text(encoding='utf-8'))
        obj = unwrap(raw)
        if obj is None:
            raise ValueError(f'{path.name}: no turns object found')
        pages.append((path, obj))
    if not pages:
        raise ValueError(f'{session_dir}: no page-*.json files')
    return pages


def is_attachment_json(text):
    """A message whose whole text is an entity envelope (image/file/audio)."""
    t = text.strip()
    if not t.startswith('{'):
        return False
    try:
        obj = json.loads(t)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and isinstance(obj.get('entities'), list)


def extract_visible(items):
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'message':
            continue  # tool_call / status items are not conversation content
        role = item.get('role')
        if role not in ('user', 'assistant'):
            continue
        parts = item.get('content') or []
        texts, attachments = [], 0
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'text':
                texts.append(part.get('text', ''))
        text = '\n\n'.join(texts)
        if is_attachment_json(text):
            try:
                attachments = len(json.loads(text.strip()).get('entities', []))
            except (ValueError, TypeError):
                attachments = 1
            text = ATTACHMENT_PLACEHOLDER.format(n=attachments)
        if not text.strip() and not attachments:
            continue
        rows.append({'id': item.get('item_id', ''), 'role': role, 'phase': 'message',
                     'text': text, 'timestamp': item.get('created_at', ''),
                     'attachments': attachments})
    return rows


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', type=Path, required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--title', required=True)
    args = parser.parse_args()
    args.session_dir.mkdir(parents=True, exist_ok=True)

    pages = load_pages(args.session_dir)
    turns = {}
    for path, obj in pages:
        for turn in obj.get('turns', []):
            if isinstance(turn, dict) and turn.get('turn_id'):
                turns[turn['turn_id']] = turn
    ordered = sorted(turns.values(), key=lambda t: t.get('sequence') or 0)

    rows = []
    for turn in ordered:
        rows.extend(extract_visible(turn.get('items') or []))
    # De-duplicate by message id, preserving order.
    seen, unique = set(), []
    for row in rows:
        if row['id'] and row['id'] in seen:
            continue
        if row['id']:
            seen.add(row['id'])
        unique.append(row)
    messages = unique
    for i, m in enumerate(messages, 1):
        m['source_file'] = f'{SOURCE_TAG}-{args.sid}'
        m['source_line'] = i

    transcript = '\n\n'.join(
        f"### {i}. {m['role']} | {m['timestamp']} | {m['phase']}\n"
        f"来源：{m['source_file']}:{m['source_line']}\n\n{m['text']}"
        for i, m in enumerate(messages, 1))
    reading_map = '\n\n'.join(
        f"[{i}] {m['role']}\n{m['text'] if m['role'] == 'user' else m['text'][:1500]}"
        for i, m in enumerate(messages, 1))

    sources = []
    for path, obj in pages:
        data = path.read_bytes()
        sources.append({'path': str(path.resolve()), 'snapshot_bytes': len(data),
                        'snapshot_sha256': digest_bytes(data), 'turn_count': len(obj.get('turns') or [])})
    metadata = {
        'id': args.sid, 'title': args.title, 'category': None, 'cwd': None,
        'classification': 'needs_content_classification', 'subagent': False,
        'parent_id': None, 'archived': False,
        'message_count': len(messages), 'characters': sum(len(m['text']) for m in messages),
        'attachment_references': sum(m['attachments'] for m in messages),
        'sources': sources,
    }
    (args.session_dir / 'messages.json').write_text(
        json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.session_dir / 'transcript.md').write_text(transcript, encoding='utf-8')
    (args.session_dir / 'reading-map.md').write_text(reading_map, encoding='utf-8')
    (args.session_dir / 'metadata.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'sid': args.sid, 'messages': len(messages),
                      'characters': metadata['characters'],
                      'attachments': metadata['attachment_references'],
                      'pages': len(pages), 'turns': len(ordered)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
