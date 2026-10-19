import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from backend.capabilities.connections import desktop_handoff as desktop


@pytest.fixture(autouse=True)
def simulated_window_handshake(monkeypatch):
    # Unit tests cannot open UI. Native-window acceptance runs separately.
    real = desktop.await_runner
    def visible(path, child, **kwargs):
        value = {'state': 'WINDOW_VISIBLE', 'received': False}
        desktop.write_json(path, value)
        return value
    monkeypatch.setattr(desktop, 'await_runner', visible)
    return real


def record():
    from backend.handoffs.models import WorkHandoffPayload, validate, content_hash
    payload = validate(WorkHandoffPayload, {'schema_version': '1.0', 'title': '窗口交接',
        'goal': '新对话接收任务', 'approved_scope': '测试交接', 'target_system': 'fixture',
        'next_steps': ['先读取资料']})
    return {'id': 'handoff-test', 'revision': 1, 'scope': 'default',
            'created_by': 'codex', 'created_at': '2026-10-12T04:00:00+00:00',
            'content_hash': content_hash(payload), 'payload': payload, 'reported_origin': None}


def profile():
    return {'agent': 'fixture', 'command': sys.executable,
            'new_args': ['agent.py', '--prompt={prompt}']}


def test_native_argv_preserves_chinese_quotes_shell_metacharacters_in_new_chat(tmp_path):
    instruction = '修复卡顿 "保持模型"; $(不要执行) `literal`\n第二行'
    argv = desktop.agent_argv(profile(), instruction, tmp_path/'交接.json')
    assert argv == [str(Path(sys.executable).resolve()), 'agent.py', '--prompt=' + instruction]


@pytest.mark.parametrize('change', [
    {'command': 'cmd.exe'},
    {'command': 'missing-not-an-executable.exe'},
    {'new_args': ['{unknown}']},
    {'new_args': ['--dangerously-skip-permissions', '{prompt}']},
    {'new_args': ['--approval-mode=yolo', '{prompt}']},
    {'new_args': ['plain', 'no-prompt']},
    {'new_args': ['{conversation_id}', '{prompt}']},
    {'new_args': ['--conversation=old-id', '{prompt}']},
    {'new_args': ['resume', 'old-id', '{prompt}']},
    {'new_args': ['--continue', '{prompt}']},
    {'new_args': ['--resume=latest', '{prompt}']},
    {'new_args': ['--dangerously-bypass-approvals-and-sandbox', '{prompt}']},
    {'new_args': ['-y', '{prompt}']},
    {'new_args': ['--dangerously-bypass-hook-trust', '{prompt}']},
    {'new_args': ['fork', '--last', '{prompt}']},
    {'new_args': ['--cd=D:/another-project', '{prompt}']},
    {'new_args': ['--sandbox=danger-full-access', '--ask-for-approval=never', '{prompt}']},
    {'new_args': ['--config', 'approval_policy=never', '{prompt}']},
])
def test_invalid_or_unsafe_profile_rejected_before_spawning(change, tmp_path):
    with pytest.raises(ValueError):
        desktop.agent_argv({**profile(), **change}, 'task', tmp_path/'handoff.json')


def test_file_prompt_adapter_and_windows_command_line_limit(tmp_path):
    p = {**profile(), 'new_args': ['--task', '{prompt_file}']}
    assert desktop.agent_argv(p, 'x' * 40000, tmp_path/'task.txt')[-1] == str(tmp_path/'task.txt')
    with pytest.raises(ValueError, match='command line'):
        desktop.agent_argv(profile(), 'x' * 40000, tmp_path/'task.txt')


def test_visible_launch_is_idempotent_and_does_not_claim_receipt(tmp_path, monkeypatch):
    calls = []
    class Process:
        pid = 12345
    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return Process()
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    monkeypatch.setattr(desktop, 'process_token', lambda pid: 'fixture-time')
    runner = tmp_path/'runner.py'; runner.write_text('')
    args = dict(directory=tmp_path/'windows', record=record(), profile=profile(),
                instruction='用户直接要求优化', cwd=tmp_path, runner=runner, popen=spawn)
    first = desktop.launch_window(**args)
    second = desktop.launch_window(**args)
    assert len(calls) == 1
    assert first['state'] == 'WINDOW_LAUNCHED'
    assert second['state'] == 'ALREADY_DISPATCHED'
    assert first['received'] is False
    argv, kwargs = calls[0]
    assert kwargs['creationflags'] == 0x10
    assert kwargs['shell'] is False
    launch = json.loads(Path(argv[-1]).read_text(encoding='utf-8'))
    assert '用户直接要求优化' in launch['prompt']
    assert launch['handoff']['id'] == 'handoff-test'
    assert launch['argv'][0] == str(Path(sys.executable).resolve())
    assert Path(launch['prompt_file']).is_file()
    assert Path(launch['package_file']).is_file()


def test_same_launch_key_with_changed_instruction_is_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    monkeypatch.setattr(desktop, 'process_token', lambda pid: 'fixture-time')
    class Process:
        pid = 12345
    runner = tmp_path/'runner.py'; runner.write_text('')
    args = dict(directory=tmp_path/'windows', record=record(), profile=profile(),
                instruction='first', cwd=tmp_path, runner=runner, popen=lambda *a, **k: Process())
    desktop.launch_window(**args)
    with pytest.raises(ValueError, match='different'):
        desktop.launch_window(**{**args, 'instruction': 'changed'})


def test_spawn_failure_preserves_package_and_records_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    def fail(*a, **k):
        raise OSError('fixture failure')
    runner = tmp_path/'runner.py'; runner.write_text('')
    with pytest.raises(OSError, match='fixture failure'):
        desktop.launch_window(directory=tmp_path/'windows', record=record(), profile=profile(),
                              instruction='task', cwd=tmp_path, runner=runner, popen=fail)
    folder = next((tmp_path/'windows').iterdir())
    assert json.loads((folder/'status.json').read_text())['state'] == 'WINDOW_FAILED'
    assert (folder/'handoff.json').is_file()
    repeated = desktop.launch_window(directory=tmp_path/'windows', record=record(), profile=profile(),
                                    instruction='task', cwd=tmp_path, runner=runner,
                                    popen=lambda *a, **k: pytest.fail('failed launch must not retry silently'))
    assert repeated['state'] == 'WINDOW_FAILED'


def test_unknown_agent_is_not_silently_opened_as_antigravity():
    with pytest.raises(ValueError, match='profile'):
        desktop.builtin_profile('unknown-agent')


def test_terminal_always_opens_new_window_in_project_and_keeps_prompt_out_of_terminal_parser(tmp_path):
    argv = desktop.window_argv('python.exe', tmp_path/'runner.py', tmp_path/'launch.json', tmp_path,
                               'antigravity', terminal='wt.exe')
    assert argv[:4] == ['wt.exe', '--window', 'new', 'new-tab']
    assert '--suppressApplicationTitle' in argv
    assert argv[argv.index('--startingDirectory') + 1] == str(tmp_path)
    assert argv[-1] == str(tmp_path/'launch.json')
    assert '{prompt}' not in ' '.join(argv)


def test_builtin_antigravity_uses_interactive_prompt_and_no_permission_override(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.shutil, 'which', lambda name: sys.executable)
    p = desktop.builtin_profile('antigravity')
    argv = desktop.agent_argv(p, '任务', tmp_path/'task.txt')
    assert '--prompt-interactive=任务' in argv
    assert '--conversation' not in argv and '--continue' not in argv
    assert '--print' not in argv and '--dangerously-skip-permissions' not in argv


def test_target_mismatch_and_empty_authorization_never_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    runner = tmp_path/'runner.py'; runner.write_text('')
    for instruction, agent in [('', 'fixture'), ('task', 'other')]:
        with pytest.raises(ValueError):
            desktop.launch_window(directory=tmp_path/'windows', record=record(),
                                  profile={**profile(), 'agent': agent}, instruction=instruction,
                                  cwd=tmp_path, runner=runner,
                                  popen=lambda *a, **k: pytest.fail('must not spawn'))


def test_current_background_task_opens_new_chat_for_observation_without_duplicate_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    monkeypatch.setattr(desktop, 'process_token', lambda pid: 'fixture-time')
    class Process:
        pid = 12345
    runner = tmp_path/'runner.py'; runner.write_text('')
    log = tmp_path/'existing.ndjson'; log.write_text('{"event":"init"}\n', encoding='utf-8')
    result = desktop.launch_window(directory=tmp_path/'windows', record=record(), profile=profile(),
        instruction='already dispatched', cwd=tmp_path, runner=runner,
        observe_log=log, popen=lambda *a, **k: Process())
    launch = json.loads(Path(result['launch_file']).read_text(encoding='utf-8'))
    assert launch['observe_log'] == str(log)
    assert 'OBSERVE_EXISTING_ONLY' in launch['prompt']
    assert '--conversation' not in launch['argv']
    assert launch['new_conversation'] is True


def test_runner_executes_literal_argv_and_reports_failure_without_receipt(tmp_path):
    # Hidden test child only; the production launcher creates the visible console.
    status = tmp_path/'status.json'
    launch = tmp_path/'launch.json'
    task = tmp_path/'task.txt'; task.write_text('测试任务', encoding='utf-8')
    marker = tmp_path/'marker.txt'
    launch.write_text(json.dumps({'agent': 'fixture', 'title': 'test', 'instruction': 'test',
        'handoff': record(), 'prompt_file': str(task), 'status_file': str(status), 'cwd': str(tmp_path),
        'argv': [sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); sys.exit(3)', str(marker), 'literal $(not a command)'],
        'observe_log': None, 'new_conversation': True}), encoding='utf-8')
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'scripts/agent_window.py'),
                             str(launch), '--no-pause'], input='\n', text=True, encoding='utf-8', capture_output=True)
    assert result.returncode == 3, result.stderr
    assert marker.read_text() == 'literal $(not a command)'
    final = json.loads(status.read_text())
    assert final['state'] == 'AGENT_FAILED' and final['exit_code'] == 3
    assert final['received'] is False


def test_tampered_handoff_does_not_open_any_window(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    runner = tmp_path/'runner.py'; runner.write_text('')
    tampered = record(); tampered['payload']['goal'] = 'tampered'
    with pytest.raises(ValueError, match='hash mismatch'):
        desktop.launch_window(directory=tmp_path/'windows', record=tampered, profile=profile(),
                              instruction='task', cwd=tmp_path, runner=runner,
                              popen=lambda *a, **k: pytest.fail('must not spawn'))


def test_agent_failure_is_preserved_on_duplicate_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    monkeypatch.setattr(desktop, 'process_token', lambda pid: 'fixture-time')
    class Process:
        pid = 12345
    runner = tmp_path/'runner.py'; runner.write_text('')
    args = dict(directory=tmp_path/'windows', record=record(), profile=profile(),
                instruction='task', cwd=tmp_path, runner=runner, popen=lambda *a, **k: Process())
    first = desktop.launch_window(**args)
    status = Path(first['launch_file']).parent/'status.json'
    desktop.write_json(status, {'state': 'AGENT_FAILED', 'exit_code': 9, 'received': False})
    result = desktop.launch_window(**args)
    assert result['state'] == 'AGENT_FAILED' and result['exit_code'] == 9


def test_unstarted_runner_is_uncertain_even_if_launcher_returned_success(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    monkeypatch.setattr(desktop, 'process_token', lambda pid: 'fixture-time')
    class Process:
        pid = 12345
    runner = tmp_path/'runner.py'; runner.write_text('')
    args = dict(directory=tmp_path/'windows', record=record(), profile=profile(),
                instruction='task', cwd=tmp_path, runner=runner, popen=lambda *a, **k: Process())
    first = desktop.launch_window(**args)
    assert first['state'] == 'WINDOW_LAUNCHED'
    desktop.write_json(Path(first['launch_file']).parent/'status.json', {'state': 'WINDOW_STARTING'})
    assert desktop.launch_window(**args)['state'] == 'DISPATCH_UNCERTAIN'


def test_builtin_hosts_reject_unrecognized_startup_shape(tmp_path):
    with pytest.raises(ValueError, match='validated startup'):
        desktop.agent_argv({**profile(), 'agent': 'codex',
                           'new_args': ['--config', 'new_policy=true', '{prompt}']}, 'task', tmp_path/'task.txt')


def test_post_spawn_persistence_failure_does_not_spawn_again(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'is_windows', lambda: True)
    monkeypatch.setattr(desktop, 'process_token', lambda pid: 'fixture-time')
    real_write, writes, spawned = desktop.write_json, [], []
    def fragile_write(path, value):
        if Path(path).name == 'dispatch.json':
            writes.append(value['state'])
            if len(writes) == 2:
                raise OSError('fixture post-spawn disk failure')
        return real_write(path, value)
    class Process:
        pid = 12345
    def spawn(*a, **k):
        spawned.append(True)
        return Process()
    monkeypatch.setattr(desktop, 'write_json', fragile_write)
    runner = tmp_path/'runner.py'; runner.write_text('')
    args = dict(directory=tmp_path/'windows', record=record(), profile=profile(),
                instruction='task', cwd=tmp_path, runner=runner, popen=spawn)
    with pytest.raises(OSError, match='disk failure'):
        desktop.launch_window(**args)
    result = desktop.launch_window(**args)
    assert result['state'] == 'ALREADY_DISPATCHED'
    assert len(spawned) == 1


@pytest.mark.parametrize('exit_code,expected', [(9, 'WINDOW_FAILED'), (0, 'DISPATCH_UNCERTAIN')])
def test_runner_handshake_does_not_call_stub_exit_a_visible_window(tmp_path, simulated_window_handshake, exit_code, expected):
    status = tmp_path/'status.json'
    desktop.write_json(status, {'state': 'WINDOW_STARTING'})
    class Stub:
        def poll(self):
            return exit_code
    actual = simulated_window_handshake(status, Stub(), timeout=0)
    assert actual['state'] == expected and actual['received'] is False


def test_scoped_cli_creates_handoff_then_requests_visible_new_chat(tmp_path, monkeypatch, capsys):
    from scripts import dispatch_work_handoff as cli
    task = tmp_path/'task.txt'; task.write_text('直接任务授权', encoding='utf-8')
    payload = tmp_path/'payload.json'; payload.write_text(json.dumps(record()['payload']), encoding='utf-8')
    config = tmp_path/'profile.json'; config.write_text(json.dumps(profile()), encoding='utf-8')
    received = []
    def fake_window(**args):
        received.append(args)
        return {'state': 'WINDOW_LAUNCHED', 'received': False, 'new_conversation': True,
                'handoff_id': args['record']['id']}
    monkeypatch.setattr(cli, 'launch_window', fake_window)
    monkeypatch.setattr(sys, 'argv', ['dispatch', '--data-dir', str(tmp_path/'data'), '--principal', 'sender',
        '--scope', 'project-a', '--agent', 'fixture', '--project-dir', str(tmp_path),
        '--instruction-file', str(task), '--profile-file', str(config), '--input', str(payload),
        '--request-key', 'first'])
    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output['new_conversation'] is True
    assert len(received) == 1 and received[0]['cwd'] == tmp_path
    from backend.adapters.mcp.stdio_server import build_service
    server = build_service(tmp_path/'data', 'project-a', True, 'sender')
    actual = json.loads(server.execute_tool('get_work_handoff', {'handoff_id': output['handoff_id'],
                        'include_receipts': True})['content'][0]['text'])
    assert actual['payload']['goal'] == record()['payload']['goal']
    assert actual['receipts'] == []


def test_scoped_cli_refuses_other_scope_before_any_window(tmp_path, monkeypatch, capsys):
    from scripts import dispatch_work_handoff as cli
    from backend.adapters.mcp.stdio_server import build_service
    server = build_service(tmp_path/'data', 'private', False, 'sender')
    created = json.loads(server.execute_tool('create_work_handoff', {
        'payload': record()['payload'], 'request_key': 'private'})['content'][0]['text'])
    task = tmp_path/'task.txt'; task.write_text('task', encoding='utf-8')
    config = tmp_path/'profile.json'; config.write_text(json.dumps(profile()), encoding='utf-8')
    monkeypatch.setattr(cli, 'launch_window', lambda **k: pytest.fail('must not open window'))
    monkeypatch.setattr(sys, 'argv', ['dispatch', '--data-dir', str(tmp_path/'data'), '--principal', 'sender',
        '--scope', 'other', '--agent', 'fixture', '--instruction-file', str(task), '--profile-file', str(config),
        '--handoff-id', created['id']])
    assert cli.main() == 1
    error = json.loads(capsys.readouterr().err)
    assert error['received'] is False and error['state'] == 'DISPATCH_FAILED'


def test_cli_returns_nonzero_for_previous_launch_failure(tmp_path, monkeypatch, capsys):
    from scripts import dispatch_work_handoff as cli
    task = tmp_path/'task.txt'; task.write_text('task', encoding='utf-8')
    payload = tmp_path/'payload.json'; payload.write_text(json.dumps(record()['payload']), encoding='utf-8')
    config = tmp_path/'profile.json'; config.write_text(json.dumps(profile()), encoding='utf-8')
    monkeypatch.setattr(cli, 'launch_window', lambda **k: {'state': 'AGENT_FAILED', 'exit_code': 3, 'received': False})
    monkeypatch.setattr(sys, 'argv', ['dispatch', '--data-dir', str(tmp_path/'data'), '--principal', 'sender',
        '--agent', 'fixture', '--instruction-file', str(task), '--profile-file', str(config),
        '--input', str(payload), '--request-key', 'failure'])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().err)['state'] == 'AGENT_FAILED'


@pytest.mark.parametrize('client', ['codex', 'gemini-cli'])
def test_other_builtin_profiles_start_fresh_conversations(client):
    try:
        p = desktop.builtin_profile(client)
    except ValueError:
        pytest.skip('Client not installed')
    argv = desktop.agent_argv(p, 'test task', Path.cwd()/'task.txt')
    assert not any(arg in ('resume', '--resume', '--continue', '--conversation') for arg in argv)


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing behavior')
def test_status_replace_survives_short_windows_reader_lock(tmp_path):
    status = tmp_path/'status.json'
    desktop.write_json(status, {'state': 'before'})
    errors = []
    def update():
        try:
            desktop.write_json(status, {'state': 'after'})
        except Exception as exc:
            errors.append(exc)
    with status.open('rb') as reader:
        worker = threading.Thread(target=update)
        worker.start()
        time.sleep(0.1)
        reader.read()
    worker.join(timeout=3)
    assert not worker.is_alive() and errors == []
    assert json.loads(status.read_text())['state'] == 'after'


def test_status_reader_allows_atomic_replace_and_reads_consistent_snapshot(tmp_path, monkeypatch):
    status = tmp_path/'status.json'
    desktop.write_json(status, {'state': 'before'})
    load_json = json.loads
    def replace_before_parse(content):
        desktop.write_json(status, {'state': 'after'})
        return load_json(content)
    monkeypatch.setattr(desktop.json, 'loads', replace_before_parse)
    assert desktop.read_state(status) == {'state': 'before'}
    assert load_json(status.read_text()) == {'state': 'after'}
