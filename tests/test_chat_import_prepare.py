import json
from pathlib import Path

from scripts.chat_import.prepare import extract_file, merge_messages, split_text, category_from_cwd


def test_extract_only_visible_dialogue_preserves_source_and_attachment_reference(tmp_path):
    rows = [
        {'type': 'response_item', 'payload': {'type': 'reasoning', 'text': 'private'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'system', 'content': [{'type': 'text', 'text': 'system'}]}},
        {'type': 'response_item', 'timestamp': '2026-01-01', 'payload': {'type': 'message', 'role': 'user', 'id': 'u1', 'content': [
            {'type': 'input_text', 'text': '<environment_context>machine</environment_context>'},
            {'type': 'input_text', 'text': '  保留原话\n'}, {'type': 'input_image', 'image_url': 'data:image/png;base64,secretbytes'}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'channel': 'analysis', 'content': [{'type': 'text', 'text': 'private'}]}},
        {'type': 'response_item', 'timestamp': '2026-01-02', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'id': 'a1', 'content': [{'type': 'output_text', 'text': '仅是建议'}]}},
    ]
    file = tmp_path / 'log.jsonl'
    file.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf8')
    messages, audit = extract_file(file)
    assert len(messages) == 2
    assert messages[0]['text'].startswith('  保留原话\n')
    assert 'secretbytes' not in str(messages) and 'private' not in str(messages)
    assert messages[0]['attachments'] == 1
    assert messages[1]['phase'] == 'final_answer'
    assert messages[0]['source_line'] == 3
    assert audit['parse_errors'] == []


def test_cross_file_dedup_does_not_drop_repeated_user_turns_or_edits():
    base = {'id': 'u', 'role': 'user', 'phase': None, 'text': '继续', 'timestamp': '1', 'source_file': 'a', 'source_line': 1}
    output, audit = merge_messages([[base], [{**base, 'source_file': 'b'},
        {**base, 'id': 'u2', 'timestamp': '2'}, {**base, 'text': '改成别的', 'timestamp': '3'}]])
    assert len(output) == 3
    assert audit['duplicates'] == 1 and audit['conflicting_message_ids'] == ['u']


def test_chunking_is_lossless_and_bounded():
    text = ('long line\n段落内容 ' * 1000) + '\n tail '
    chunks = split_text(text, 777)
    assert ''.join(chunks) == text
    assert max(map(len, chunks)) <= 777


def test_cwd_dates_are_not_a_project_classification():
    assert category_from_cwd(r'D:\02_Development_Tools\AI library') == 'AI library'
    assert category_from_cwd(r'D:\02_Development_Tools\wallpaper\codex wallpaper') == 'wallpaper / codex wallpaper'
    assert category_from_cwd(r'C:\Users\ling5\Documents\Codex\2026-10-03\z') is None
