"""Opt-in, isolated checks against installed clients; never installs clients.

No prompt turn is sent. Personal client config and credentials are not copied.
Evidence and fixture data stay in a newly created .agent-tmp/c2-clients-* folder.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.capabilities.connections.client_config import plan_local_mcp_config

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--codex', default=shutil.which('codex'), help='Installed Codex executable')
parser.add_argument('--gemini-bundle', type=Path, required=True, help='Installed Gemini bundle/gemini.js')
parser.add_argument('--gemini-core', type=Path, help='Optional explicit installed core chunk containing McpClient')
parser.add_argument('--embedding', action='store_true', help='Also exercise the separately deployed local embedding MCP')
parser.add_argument('--handoff', action='store_true', help='Also exchange versioned work between actual Codex and installed Gemini MCP clients')
args = parser.parse_args()
if not args.codex or not args.gemini_bundle.is_file() or not shutil.which('node'):
    parser.error('Existing Codex, Node and Gemini CLI are required; nothing is installed automatically')
gemini_script = args.gemini_bundle.resolve()
gemini_version = json.loads((gemini_script.parent.parent/'package.json').read_text(encoding='utf-8'))['version']
core = args.gemini_core
if core is None:
    for name in re.findall(r'"(\./chunk-[^"/]+\.js)"', gemini_script.read_text(encoding='utf-8')):
        candidate = gemini_script.parent/name
        if 'var McpClient = class' in candidate.read_text(encoding='utf-8'):
            core = candidate
            break
if core is None or not core.is_file():
    parser.error('Installed Gemini core layout is unsupported; inspect it before using --gemini-core')
(ROOT/'.agent-tmp').mkdir(exist_ok=True)
run = Path(tempfile.mkdtemp(prefix='c2-clients-', dir=ROOT/'.agent-tmp'))
print('EVIDENCE', run, flush=True)
workspace = run/'workspace'
workspace.mkdir()
allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
           'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'PROGRAMFILES', 'PROGRAMFILES(X86)'}
env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
env['PYTHONUTF8'] = '1'
server = {'command': str(ROOT/'.venv/Scripts/python.exe'), 'args': [str(ROOT/'scripts/run_mcp.py'),
    '--data-dir', str(run/'data'), '--scope', 'c2-fixture', '--principal', 'c2-codex']}
servers = {'ai-memory-library': server}
if args.embedding:
    embedding_python = ROOT/'.agent-tmp/embedding-runtime/Scripts/python.exe'
    if not embedding_python.is_file():
        parser.error('Deploy the separate embedding environment first')
    servers['ai-library-embedding'] = {'command': str(embedding_python), 'args': [str(ROOT/'scripts/run_embedding_mcp.py')]}
codex_home = run/'codex-home'
codex_home.mkdir()
codex_base = b'check_for_update_on_startup = false\n[analytics]\nenabled = false\n'
cp = plan_local_mcp_config('codex', codex_base, servers)
(codex_home/'config.toml').write_bytes(cp.after)
env['CODEX_HOME'] = str(codex_home)
codex = args.codex
codex_version = subprocess.run([codex, '--version'], capture_output=True, text=True, check=True).stdout.strip()
log = (run/'codex-stderr.log').open('w', encoding='utf-8')
p = subprocess.Popen([codex, 'app-server', '--stdio'], cwd=workspace, env=env,
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                     text=True, encoding='utf-8')
q = queue.Queue()
def reader():
    for line in p.stdout:
        q.put(json.loads(line))
threading.Thread(target=reader, daemon=True).start()
counter = 0
def rpc(method, params):
    global counter
    counter += 1
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': counter, 'method': method, 'params': params})+'\n')
    p.stdin.flush()
    while True:
        message = q.get(timeout=180 if args.embedding else 45)
        if message.get('id') == counter:
            if 'error' in message:
                raise RuntimeError(message['error'])
            return message.get('result')
try:
    init = rpc('initialize', {'clientInfo': {'name': 'ai_library_c2_probe', 'version': '1.0.0'},
                              'capabilities': {'experimentalApi': True}})
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'initialized'})+'\n')
    p.stdin.flush()
    status = rpc('mcpServerStatus/list', {'serverName': 'ai-memory-library'})
    (run/'codex-status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    memory_status = next(s for s in status['data'] if s['name'] == 'ai-memory-library')
    assert memory_status['tools']
    print('CODEX_DISCOVERY', len(memory_status['tools']), flush=True)
    started = rpc('thread/start', {'ephemeral': True, 'cwd': str(workspace),
        'model': 'offline-fixture', 'modelProvider': 'local-fixture',
        'config': {'model_providers.local-fixture': {'name': 'No model requests',
            'base_url': 'http://127.0.0.1:1', 'wire_api': 'responses', 'requires_openai_auth': False}}})
    thread_id = started['thread']['id']
    called = rpc('mcpServer/tool/call', {'threadId': thread_id, 'server': 'ai-memory-library',
        'tool': 'create_user_note', 'arguments': {'title': 'C2 actual Codex client', 'content': 'isolated C2 reuse evidence'}})
    (run/'codex-tool-call.json').write_text(json.dumps(called, indent=2), encoding='utf-8')
    assert not called.get('isError')
    print('CODEX_NOTE_WRITE_PASS', flush=True)
    def tool(server_name, name, arguments):
        result = rpc('mcpServer/tool/call', {'threadId': thread_id, 'server': server_name, 'tool': name, 'arguments': arguments})
        assert not result.get('isError'), result
        return json.loads(result['content'][0]['text'])
    if args.handoff:
        handoff = tool('ai-memory-library', 'create_work_handoff', {'request_key': 'actual-codex-handoff-v1',
            'payload': {'schema_version': '1.0', 'title': 'C2 actual client work exchange',
                'goal': 'Validate Codex to Gemini handoff without a model prompt',
                'approved_scope': 'Isolated client protocol fixture only', 'target_system': 'gemini-cli',
                'next_steps': ['Read this handoff, record receipt and append the verified protocol result']}})
        (run/'codex-handoff.json').write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding='utf-8')
        print('CODEX_WORK_HANDOFF_WRITE_PASS', flush=True)
    if args.embedding:
        text = '知识库备份流程：创建一致性快照，核对原文文件哈希，再恢复到全新的目录。不要覆盖现有数据。'
        source = tool('ai-memory-library', 'create_user_note', {'title': 'C2 semantic source', 'content': text})
        encoded = tool('ai-library-embedding', 'embed_texts', {'texts': [text], 'role': 'document'})
        vector = encoded['items'][0]
        assert vector['content_hash'] == source['content_hash']
        tool('ai-memory-library', 'submit_embedding', {'space': encoded['space'], 'plane': 'NOTES', 'object_id': source['id'],
            'revision': source['revision'], 'content_hash': vector['content_hash'], 'vector': vector['vector']})
        query = '电脑坏了以后怎样把资料救回来？'
        encoded_query = tool('ai-library-embedding', 'embed_texts', {'texts': [query], 'role': 'query'})
        found = tool('ai-memory-library', 'search_knowledge_context', {'query': query, 'plane': 'NOTES',
            'embedding_query': {'space': encoded_query['space'], 'vector': encoded_query['items'][0]['vector']}})
        assert found['retrieval']['vector_search'] and text in found['combined_prompt']
        (run/'codex-embedding.json').write_text(json.dumps({'source_id': source['id'], 'space': encoded['space'],
            'vector_dimension': len(vector['vector']), 'hybrid_retrieval': True}, indent=2), encoding='utf-8')
        print('CODEX_LOCAL_EMBEDDING_AND_RETRIEVAL_PASS', flush=True)
finally:
    p.stdin.close()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.terminate()
        p.wait(timeout=10)
    log.close()

gemini_home = run/'gemini-home'
(gemini_home/'.gemini').mkdir(parents=True)
server['args'][-1] = 'c2-gemini'
gp = plan_local_mcp_config('gemini-cli', b'{"advanced":{"autoConfigureMemory":false},"telemetry":{"enabled":false}}', servers)
(gemini_home/'.gemini/settings.json').write_bytes(gp.after)
(gemini_home/'.gemini/trustedFolders.json').write_text(json.dumps({str(workspace): 'TRUST_FOLDER'}), encoding='utf-8')
env.pop('CODEX_HOME')
env.update(GEMINI_CLI_HOME=str(gemini_home), GEMINI_CLI_NO_RELAUNCH='1',
           GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(run/'no-system.json'),
           GEMINI_CLI_SYSTEM_DEFAULTS_PATH=str(run/'no-defaults.json'))
result = subprocess.run([shutil.which('node'), str(gemini_script), 'mcp', 'list'], env=env,
                         cwd=workspace, text=True, encoding='utf-8', capture_output=True, timeout=45)
(run/'gemini-list.txt').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
print('GEMINI_STATUS', result.returncode, result.stdout[:2000], result.stderr[:1000], flush=True)
assert result.returncode == 0 and ' - Connected' in result.stderr + result.stdout
runtime = subprocess.run([shutil.which('node'), str(ROOT/'scripts/gemini_mcp_probe.mjs'), str(core),
    str(gemini_home/'.gemini/settings.json'), str(workspace), str(run/'gemini-runtime.json'), gemini_version,
    *(['--handoff'] if args.handoff else [])],
    env=env, cwd=workspace, text=True, encoding='utf-8', capture_output=True, timeout=180 if args.embedding else 45)
(run/'gemini-runtime.txt').write_text(runtime.stdout+'\n'+runtime.stderr, encoding='utf-8')
print('GEMINI_RUNTIME', runtime.returncode, runtime.stdout[-1500:], runtime.stderr[-1500:], flush=True)
assert runtime.returncode == 0
# Apply exact rollback in isolated files and ask the actual clients to reread.
(gemini_home/'.gemini/settings.json').write_bytes(gp.rollback((gemini_home/'.gemini/settings.json').read_bytes()))
rolled = subprocess.run([shutil.which('node'), str(gemini_script), 'mcp', 'list'], env=env,
    cwd=workspace, text=True, encoding='utf-8', capture_output=True, timeout=45)
assert rolled.returncode == 0 and 'No MCP servers configured' in rolled.stdout + rolled.stderr
(codex_home/'config.toml').write_bytes(cp.rollback((codex_home/'config.toml').read_bytes()))
env.pop('GEMINI_CLI_HOME')
env['CODEX_HOME'] = str(codex_home)
rolled_codex = subprocess.run([codex, 'mcp', 'list', '--json'], env=env, cwd=workspace,
    text=True, encoding='utf-8', capture_output=True, timeout=45)
assert rolled_codex.returncode == 0 and json.loads(rolled_codex.stdout) == []
(run/'rollback.json').write_text(json.dumps({'codex': True, 'gemini-cli': True}), encoding='utf-8')
(run/'manifest.json').write_text(json.dumps({'codex_version': codex_version, 'gemini_version': gemini_version,
    'gemini_core_sha256': hashlib.sha256(core.read_bytes()).hexdigest(),
    'tool_count': len(memory_status['tools']), 'scope': 'c2-fixture', 'real_embedding_workflow': args.embedding,
    'real_client_handoff': args.handoff,
    'configuration_rollback': True, 'llm_prompt_turns': 0, 'personal_configuration_modified': False,
    'gemini_tool_validation': 'installed McpClient module with fixture workspace context; not interactive prompt QA',
    'server_source_hashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT/'backend').rglob('*.py'))}}, indent=2), encoding='utf-8')
print('BOTH_CONFIG_ROLLBACK_PASS', flush=True)
print('EVIDENCE', run, flush=True)
