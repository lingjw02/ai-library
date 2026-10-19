import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';

const root = resolve(import.meta.dirname, '../../..');
const settings = (dataDir) => ({
  pythonExecutable: resolve(root, '.venv/Scripts/python.exe'),
  bridgeScript: resolve(root, 'scripts/run_harness_bridge.py'),
  dataDir, scope: 'plugin-test', principal: 'deepseek-harness',
  maxContextChars: 6000, maxItems: 3, excerptChars: 240,
  timeoutMs: 30000, maxReplyBytes: 262144,
});
const user = (text) => ({ id: 'user-1', role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text }] });
const projectServices = () => ({ commands: { register() { return () => {}; } }, sessionProjections: {
  register() { return () => {}; }, stateOf() { return { project: null, pending: [] }; },
} });

test('installed bundle manifest can be resolved through the package export', () => {
  assert.equal(createRequire(import.meta.url).resolve('@ling/ai-library-harness/package.json'), resolve(root, 'plugins/deepseek-harness-ai-library/package.json'));
});

test('real bridge supports concurrent requests, bounded snapshots and awaited teardown', async () => {
  const { BridgeClient } = await import('../bridge-client.js');
  const data = await mkdtemp(resolve(tmpdir(), 'ai-library-bridge-test-'));
  const client = new BridgeClient(settings(data));
  try {
    const ping = await client.request({ operation: 'ping' });
    assert.equal(ping.scope, 'plugin-test');
    assert.equal(ping.read_only, true);
    const abort = new AbortController();
    const cancelled = client.request({ operation: 'prepare', query: 'cancelled', session_key: 'cancelled-chat' }, abort.signal);
    const rejection = assert.rejects(cancelled, /cancelled by test/);
    abort.abort(new Error('cancelled by test'));
    await rejection;
    assert.equal((await client.request({ operation: 'ping' })).read_only, true);
    const packets = await Promise.all(['chat-a', 'chat-b'].map(session_key =>
      client.request({ operation: 'prepare', query: 'no matching evidence', session_key })));
    for (const p of packets) {
      assert.equal(p.evidence_count, 0);
      assert.ok(p.context_text.length <= 6000);
    }
    await assert.rejects(client.request({ operation: 'create_user_note' }));
  } finally {
    await client.close();
    assert.equal(client.closed, true);
    await rm(data, { recursive: true, force: true });
  }
});

test('native hook preserves next decisions, ignores tool continuations and injects only one sourced snapshot', async () => {
  const { apply } = await import('../index.js');
  const data = await mkdtemp(resolve(tmpdir(), 'ai-library-plugin-test-'));
  const callbacks = new Map(); const disposers = [];
  const ctx = {
    ...projectServices(),
    on(name, callback) { callbacks.set(name, callback); return () => callbacks.delete(name); },
    effect(factory) { const dispose = factory(); if (dispose) disposers.push(dispose); return dispose; },
  };
  try {
    await apply(ctx, settings(data));
    const hook = callbacks.get('agent/pre-step');
    assert.equal(typeof hook, 'function');
    const payload = { agent: { session: { id: 'session-a' } }, signal: new AbortController().signal, turn: 1, step: 1 };
    const reject = { kind: 'reject' };
    assert.equal(await hook(payload, async () => reject), reject);
    const empty = { kind: 'enter', messages: [], startsRequestSeries: true };
    assert.equal(await hook({ ...payload, step: 2 }, async () => empty), empty);
    const entered = { kind: 'enter', messages: [user('AI library')], startsRequestSeries: true };
    const result = await hook(payload, async () => entered);
    assert.equal(result.startsRequestSeries, true);
    assert.equal(result.messages.length, 2);
    assert.equal(result.messages[0], entered.messages[0]);
    const snapshot = result.messages[1];
    assert.equal(snapshot.role, 'user');
    assert.equal(snapshot.source.kind, 'ai-library');
    assert.equal(snapshot.source.form, 'snapshot');
    assert.ok(Object.isFrozen(snapshot));
    assert.match(snapshot.content[0].text, /untrusted/i);
    const abort = new AbortController(); abort.abort();
    assert.equal(await hook({ ...payload, signal: abort.signal }, async () => entered), entered);
  } finally {
    for (const dispose of disposers.reverse()) await dispose();
    await rm(data, { recursive: true, force: true });
  }
});

test('library command supports latest_import, match_topic, and create_topic', async () => {
  const { apply } = await import('../index.js');
  const data = await mkdtemp(resolve(tmpdir(), 'ai-library-cmd-test-'));
  let libraryCommand;
  const disposers = [];
  const ctx = {
    ...projectServices(),
    commands: {
      register(spec) {
        if (spec.name === 'library') libraryCommand = spec.handler;
        return () => {};
      },
    },
    on() { return () => {}; },
    effect(factory) { const dispose = factory(); if (dispose) disposers.push(dispose); return dispose; },
  };
  try {
    await apply(ctx, settings(data));
    assert.equal(typeof libraryCommand, 'function');
    const mockAgent = {
      session: {
        id: 'test-session-cmd',
        deriveMessages: () => [{ role: 'user', content: [{ type: 'text', text: '讨论 deepseek harness 架构和自动化插件' }] }],
      },
    };
    // 1. latest_import in empty test data
    const res1 = await libraryCommand({ agent: mockAgent, rawInput: JSON.stringify({ operation: 'latest_import' }), signal: new AbortController().signal });
    assert.equal(res1.kind, 'success');
    const data1 = JSON.parse(res1.text);
    assert.equal(data1.operation, 'latest_import');

    // 2. match_topic before creating topic: should return matched=false and suggest new topic
    const res2 = await libraryCommand({ agent: mockAgent, rawInput: JSON.stringify({ operation: 'match_topic', text: '' }), signal: new AbortController().signal });
    assert.equal(res2.kind, 'success');
    const data2 = JSON.parse(res2.text);
    assert.equal(data2.operation, 'match_topic');
    assert.equal(data2.matched, false);
    assert.ok(data2.suggested_title);

    // 3. create_topic to establish 'deepseek harness'
    const res3 = await libraryCommand({ agent: mockAgent, rawInput: JSON.stringify({ operation: 'create_topic', title: 'deepseek harness' }), signal: new AbortController().signal });
    assert.equal(res3.kind, 'success');
    const data3 = JSON.parse(res3.text);
    assert.equal(data3.operation, 'create_topic');
    assert.ok(data3.topic);
    assert.equal(data3.topic.title, 'deepseek harness');

    // 4. match_topic after creating topic: should now match 'deepseek harness'
    const res4 = await libraryCommand({ agent: mockAgent, rawInput: JSON.stringify({ operation: 'match_topic', text: '' }), signal: new AbortController().signal });
    assert.equal(res4.kind, 'success');
    const data4 = JSON.parse(res4.text);
    assert.equal(data4.operation, 'match_topic');
    assert.equal(data4.matched, true);
    assert.equal(data4.topic_title, 'deepseek harness');
  } finally {
    for (const dispose of disposers.reverse()) await dispose();
    await rm(data, { recursive: true, force: true });
  }
});

test('invalid configuration fails at activation instead of silently disabling retrieval', async () => {
  const { apply, Config } = await import('../index.js');
  await assert.rejects(apply({}, { ...settings(tmpdir()), maxItems: 0 }), /maxItems/);
  assert.equal(Config['~standard'].version, 1);
  assert.equal(Config['~standard'].validate(settings(tmpdir())).value.scope, 'plugin-test');
  assert.match(Config['~standard'].validate({ ...settings(tmpdir()), maxItems: 0 }).issues[0].message, /maxItems/);
});

test('plugin still closes its process if an agent cleanup reports an error', async () => {
  const { apply } = await import('../index.js');
  const data = await mkdtemp(resolve(tmpdir(), 'ai-library-cleanup-test-'));
  let hook, cleanup;
  const cleanups = [];
  const ctx = { ...projectServices(), on(_name, callback) { hook = callback; }, effect(factory) {
    const dispose = factory(); cleanups.push(dispose); cleanup = async () => {
      const outcomes = await Promise.allSettled(cleanups.toReversed().map(fn => fn()));
      const failures = outcomes.filter(result => result.status === 'rejected').map(result => result.reason);
      if (failures.length) throw new AggregateError(failures, 'AI Library cleanup failed');
    }; return dispose;
  } };
  await apply(ctx, settings(data));
  const agent = { session: { id: 'cleanup-failure' }, ctx: {
    effect(factory) { const end = factory(); return async () => { await end(); throw new Error('agent cleanup test failure'); }; },
  } };
  try {
    await hook({ agent, signal: new AbortController().signal }, async () => ({ kind: 'enter', messages: [user('library')] }));
    await assert.rejects(cleanup(), error => error instanceof AggregateError && /cleanup failed/.test(error.message));
  } finally {
    await rm(data, { recursive: true, force: true });
  }
});
