from backend.capabilities.local_skills import discover_skills, skill_roots


def write_skill(root, name, content):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(content, encoding='utf-8')
    return directory


def test_names_deduplication_and_no_execution(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    text = '---\nname: Actual name\ndescription: Useful skill\n---\nNever execute this body.'
    directory = write_skill(a, 'folder-name', text)
    write_skill(b, 'other-folder', text)
    write_skill(directory / 'references', 'example', '---\nname: Not installed\n---\n')
    result = discover_skills([(a, 'Codex'), (b, 'Gemini')])
    assert not result['partial']
    assert len(result['items']) == 1
    assert result['items'][0]['title'] == 'Actual name'
    assert len(result['items'][0]['locations']) == 2
    assert 'Never execute' not in str(result)


def test_system_bundles_invalid_header_and_large_file(tmp_path):
    write_skill(tmp_path / '.system', 'built-in', '---\nname: Internal\n---\n')
    write_skill(tmp_path / 'bundle/skills', 'nested', '---\nname: Nested\n---\n')
    write_skill(tmp_path, 'broken', '---\nname: [invalid\n---\n')
    write_skill(tmp_path, 'large', 'x' * 1048577)
    result = discover_skills([(tmp_path, 'Test')])
    assert {i['title'] for i in result['items']} == {'Internal', 'Nested', 'broken'}
    assert result['partial'] and len(result['warnings']) == 2


def test_local_plugin_versions_do_not_repeat_history(tmp_path):
    root = tmp_path / '.codex/plugins/cache/vendor/package'
    write_skill(root / '1.9/skills', 'old', 'old')
    write_skill(root / '1.10/skills', 'new', 'new')
    roots = skill_roots(tmp_path, tmp_path / 'workspace')
    assert root / '1.10/skills' in [p for p, _ in roots]
    assert root / '1.9/skills' not in [p for p, _ in roots]
