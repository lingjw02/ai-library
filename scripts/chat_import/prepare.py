"""Snapshot and deduplicate visible Codex dialogue; never execute source text.

Runtime instructions, analysis and tool payloads are not conversation content.
Attachments are recorded as references only, not silently decoded or OCR'd.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re


def split_text(text, maximum=5000):
    if maximum < 1:
        raise ValueError('maximum must be positive')
    return [text[i:i + maximum] for i in range(0, len(text), maximum)] or ['']


def category_from_cwd(cwd):
    parts = PureWindowsPath(cwd or '').parts
    for i, part in enumerate(parts):
        if part.casefold() == '02_development_tools' and i + 1 < len(parts):
            return ' / '.join(parts[i+1:])
    return None


def _visible_text(text):
    # Only known injected wrappers at the beginning of a content block are
    # removed. A quoted example occurring within the user's request is retained.
    for tag in ('environment_context', 'recommended_plugins', 'in-app-browser-context', 'external_codex_apps_open_page'):
        while re.match(r'^\s*<' + tag + r'[\s>]', text):
            cleaned, count = re.subn(r'^\s*<' + tag + r'\b[^>]*>.*?</' + tag + r'>\s*', '', text, count=1, flags=re.S)
            if not count:
                break
            text = cleaned
    if text.startswith('# AGENTS.md instructions'):
        # This is a runtime context block, not a newly asserted user fact.
        return ''
    if text.strip().startswith('## My request:'):
        text = text[text.index('## My request:') + len('## My request:'):].lstrip('\r\n')
    return text


def extract_file(path, media_dir=None):
    import base64
    path = Path(path)
    byte_limit = path.stat().st_size
    audit = {'path': str(path), 'snapshot_bytes': byte_limit, 'parse_errors': [], 'excluded_context_blocks': 0}
    rows = []
    digest = hashlib.sha256()
    if media_dir is not None:
        media_dir = Path(media_dir)
        media_dir.mkdir(parents=True, exist_ok=True)
    with path.open('rb') as stream:
        remaining = byte_limit
        line_no = 0
        while remaining > 0:
            raw = stream.readline(remaining)
            remaining -= len(raw)
            digest.update(raw)
            line_no += 1
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError):
                audit['parse_errors'].append(line_no)
                continue
            p = row.get('payload', {})
            if row.get('type') != 'response_item' or p.get('type') != 'message':
                continue
            role = p.get('role')
            phase = p.get('channel') or p.get('phase')
            if role not in ('user', 'assistant') or phase in ('analysis', 'summary'):
                continue
            texts, attachments, media_refs = [], 0, []
            for part in p.get('content', []):
                if part.get('type') in ('input_text', 'output_text', 'text'):
                    original = part.get('text', '')
                    text = _visible_text(original) if role == 'user' else original
                    if text != original:
                        audit['excluded_context_blocks'] += 1
                    if text.strip():
                        texts.append(text)
                elif part.get('type') in ('input_image', 'image', 'input_audio', 'audio', 'video'):
                    attachments += 1
                    if media_dir is not None:
                        raw_url = part.get('image_url') or part.get('url') or ''
                        if isinstance(raw_url, dict):
                            raw_url = raw_url.get('url') or ''
                        if isinstance(raw_url, str) and raw_url.startswith('data:') and ',' in raw_url:
                            try:
                                header, b64 = raw_url.split(',', 1)
                                mime = header[5:].split(';')[0].strip() or 'image/png'
                                ext = {
                                    'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                                    'image/gif': '.gif', 'video/mp4': '.mp4', 'audio/mpeg': '.mp3',
                                    'audio/wav': '.wav', 'application/pdf': '.pdf'
                                }.get(mime.lower(), '.bin')
                                data_bytes = base64.b64decode(b64)
                                h = hashlib.sha256(data_bytes).hexdigest()
                                out_file = media_dir / f'{h}{ext}'
                                if not out_file.exists():
                                    out_file.write_bytes(data_bytes)
                                media_refs.append({'sha256': h, 'mime_type': mime, 'file': out_file.name})
                            except Exception:
                                pass
            if not texts and not attachments:
                continue
            text = '\n\n'.join(texts)
            if attachments:
                if media_refs:
                    for mr in media_refs:
                        text += f"\n![对话附件](/api/media/media-{mr['sha256'][:16]})"
                else:
                    text += f'\n[附件引用 {attachments} 项：本次仅保留引用存在性；未导入二进制、未做 OCR。]'
            entry = {'id': p.get('id'), 'role': role, 'phase': phase, 'text': text,
                'timestamp': row.get('timestamp', ''), 'source_file': str(path),
                'source_line': line_no, 'attachments': attachments}
            if media_refs:
                entry['media_refs'] = media_refs
            rows.append(entry)
    audit['snapshot_sha256'] = digest.hexdigest()
    audit['visible_messages'] = len(rows)
    return rows, audit


def merge_messages(groups):
    seen, by_id, output = {}, defaultdict(set), []
    duplicates = 0
    for group in groups:
        for row in group:
            body = hashlib.sha256((row['role'] + '\0' + row['text']).encode()).hexdigest()
            identity = row.get('id') or (row.get('timestamp'), body)
            key = (str(identity), body)
            if key in seen:
                duplicates += 1
                seen[key].setdefault('also_seen_at', []).append({'path': row['source_file'], 'line': row['source_line']})
                continue
            copied = dict(row)
            seen[key] = copied
            output.append(copied)
            if row.get('id'):
                by_id[row['id']].add(body)
    output.sort(key=lambda r: r.get('timestamp') or '')
    return output, {'duplicates': duplicates,
        'conflicting_message_ids': sorted(k for k, v in by_id.items() if len(v) > 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--session-index', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    titles = {}
    for line in args.session_index.read_text(encoding='utf-8').splitlines():
        item = json.loads(line)
        titles[item['id']] = item.get('thread_name', '')
    inventory = json.loads(args.inventory.read_text(encoding='utf-8'))
    sessions = defaultdict(list)
    for item in inventory['sessions']:
        sessions[item['id']].append(item)
    result = []
    for sid, files in sessions.items():
        groups, audits = [], []
        for file in files:
            messages, audit = extract_file(file['path'])
            groups.append(messages)
            audits.append(audit)
        messages, merge_audit = merge_messages(groups)
        first = files[0]
        subagent = isinstance(first.get('source'), dict) and 'subagent' in first['source']
        parent = first['source']['subagent'].get('thread_spawn', {}).get('parent_thread_id') if subagent else None
        title = titles.get(sid) or next((m['text'].strip()[:90] for m in messages if m['role']=='user'), '无可读取正文')
        category = category_from_cwd(first.get('cwd'))
        meta = {'id': sid, 'title': title, 'cwd': first.get('cwd'), 'category': category,
            'classification': 'source_project_path' if category else 'needs_content_classification',
            'subagent': subagent, 'parent_id': parent, 'archived': any(f['archived'] for f in files),
            'message_count': len(messages), 'characters': sum(len(m['text']) for m in messages),
            'attachment_references': sum(m['attachments'] for m in messages),
            'sources': audits, **merge_audit}
        folder = args.output / sid
        folder.mkdir()
        (folder/'messages.json').write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
        transcript = '\n\n'.join(f"### {i+1}. {m['role']} | {m['timestamp']} | {m.get('phase') or 'message'}\n来源：{Path(m['source_file']).name}:{m['source_line']}\n\n{m['text']}" for i, m in enumerate(messages))
        (folder/'transcript.md').write_text(transcript, encoding='utf-8')
        # A compact reading map; full dialogue stays available for the summarizer.
        digest = '\n\n'.join(f"[{i+1}] {m['role']} {m.get('phase')}\n{m['text'][:1600]}" for i,m in enumerate(messages)
            if m['role']=='user' or m.get('phase') in ('final','final_answer'))
        (folder/'reading-map.md').write_text(digest, encoding='utf-8')
        (folder/'metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        result.append(meta)
    (args.output/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'id':s['id'],'title':s['title'],'category':s['category'],'child':s['subagent'],
        'messages':s['message_count'],'characters':s['characters'],'duplicates':s['duplicates']} for s in result],ensure_ascii=True))


if __name__ == '__main__':
    main()
