"""Inert, reviewable local stdio plans. This module never reads or writes files.

The caller supplies the original bytes, reviews the diff, and separately authorizes
any installation. Unknown client formats, name collisions and credential handling
are rejected; existing settings are not treated as permission to execute a server.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from hashlib import sha256
import json
import re
import tomllib


def _hash(value: bytes | None) -> str:
    return sha256(b'absent' if value is None else b'present\0' + value).hexdigest()


@dataclass(frozen=True)
class ClientConfigPlan:
    client: str
    before: bytes | None
    after: bytes
    diff: str

    @property
    def before_hash(self) -> str:
        return _hash(self.before)

    @property
    def after_hash(self) -> str:
        return _hash(self.after)

    def apply_to(self, current: bytes | None) -> bytes:
        if current not in (self.before, self.after):
            raise ValueError('Configuration changed after planning; review a fresh plan')
        return self.after

    def rollback(self, current: bytes | None) -> bytes | None:
        if current not in (self.before, self.after):
            raise ValueError('Configuration changed after installation; automatic rollback refused')
        return self.before


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON keys require manual resolution')
        result[key] = value
    return result


def plan_local_mcp_config(client: str, before: bytes | None, servers: dict) -> ClientConfigPlan:
    """Add new local bindings, or no-op identical ones; never replace a collision.

    Codex keeps the exact original text and appends new tables. Gemini keeps all
    parsed settings, but reformats strict JSON. JSONC/JSON5 are deliberately not
    rewritten. Rollback restores exact bytes, including original file absence.
    Plans may contain the caller's private settings: keep them local, never log
    or publish them. These are single-writer plans, not filesystem locks.
    """
    if client not in {'codex', 'gemini-cli'}:
        raise ValueError('Unsupported client')
    text = (before or b'').decode('utf-8-sig')
    original = (tomllib.loads(text) if client == 'codex' else
                json.loads(text or '{}', object_pairs_hook=_unique_object))
    if not isinstance(original, dict):
        raise ValueError('Configuration must be an object')
    key = 'mcp_servers' if client == 'codex' else 'mcpServers'
    existing = original.get(key, {})
    if not isinstance(existing, dict):
        raise ValueError('MCP server definitions must be an object')
    if not isinstance(servers, dict) or not servers:
        raise ValueError('At least one server is required')
    additions = {}
    for name, server in sorted(servers.items()):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name):
            raise ValueError('Invalid server name')
        if not isinstance(server, dict) or set(server) - {'command', 'args', 'cwd', 'env'}:
            raise ValueError('Only local stdio command/args/cwd are supported')
        if server.get('env'):
            raise ValueError('Environment and credential bindings require separate client-specific review')
        command, args = server.get('command'), server.get('args', [])
        if not isinstance(command, str) or not command.strip() or '\0' in command:
            raise ValueError('A local command is required')
        if not isinstance(args, list) or not all(isinstance(x, str) and '\0' not in x for x in args):
            raise ValueError('Arguments must be strings')
        normalized = {'command': command, 'args': args}
        if 'cwd' in server:
            if not isinstance(server['cwd'], str) or not server['cwd'] or '\0' in server['cwd']:
                raise ValueError('cwd must be a nonempty path')
            normalized['cwd'] = server['cwd']
        if name in existing:
            if existing[name] != normalized:
                raise ValueError(f'Server {name} already exists with different settings; explicit rebinding required')
        else:
            additions[name] = normalized
    if not additions:
        after = before if before is not None else b''
    elif client == 'codex':
        suffix = '\n' if text.endswith('\n') else '\n\n' if text else ''
        for name, server in additions.items():
            suffix += f'[mcp_servers.{name}]\n'
            for field, value in server.items():
                suffix += f'{field} = {json.dumps(value, ensure_ascii=False)}\n'
            suffix += '\n'
        after = (before or b'') + suffix.encode('utf-8')
        tomllib.loads(after.decode('utf-8-sig'))
    else:
        original[key] = {**existing, **additions}
        after = (json.dumps(original, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    diff = ''.join(unified_diff(text.splitlines(keepends=True), after.decode('utf-8-sig').splitlines(keepends=True),
                                fromfile='before', tofile='proposed'))
    return ClientConfigPlan(client, before, after, diff)
