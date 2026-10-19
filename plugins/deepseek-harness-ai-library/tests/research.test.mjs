import test from 'node:test';
import assert from 'node:assert/strict';
import { ResearchRunner } from '../research-runner.js';

test('ResearchRunner manages lifecycle, states, and dual modes', async () => {
  const runner = new ResearchRunner();
  assert.equal(runner.getStatus().status, 'idle');
  assert.equal(runner.getStatus().iteration, 0);

  // 1. Start library mode
  const started = await runner.start({
    mode: 'library',
    targetFile: 'retrieval_config.json',
    evalScript: 'eval_retrieval.py',
    instructions: 'Optimize RRF k parameter',
  });
  assert.equal(started.status, 'running');
  assert.equal(started.mode, 'library');
  assert.equal(started.targetFile, 'retrieval_config.json');
  assert.equal(started.baselineScore, 0.800);
  assert.equal(started.bestScore, 0.800);

  // Cannot start twice
  await assert.rejects(runner.start(), /already running/);

  // 2. Step with improvement (Ratchet forward)
  await runner.step(async () => ({
    score: 0.835,
    diffSummary: 'Adjusted rrf_k from 60 to 45',
  }));
  let status = runner.getStatus();
  assert.equal(status.iteration, 1);
  assert.equal(status.bestScore, 0.835);
  assert.equal(status.history.length, 1);
  assert.equal(status.history[0].outcome, 'kept');
  assert.equal(status.history[0].delta, 0.035);

  // 3. Step with regression (Ratchet rollback)
  await runner.step(async () => ({
    score: 0.790,
    diffSummary: 'Tried rrf_k=10, degraded accuracy',
  }));
  status = runner.getStatus();
  assert.equal(status.iteration, 2);
  assert.equal(status.bestScore, 0.835); // Unchanged
  assert.equal(status.history.length, 2);
  assert.equal(status.history[1].outcome, 'reverted');

  // 4. Step with error
  await runner.step(async () => ({
    error: 'Syntax error in retrieval_config.json',
  }));
  status = runner.getStatus();
  assert.equal(status.iteration, 3);
  assert.equal(status.history[2].outcome, 'error');

  // 5. Propose strategy
  const proposal = runner.proposeStrategy();
  assert.equal(proposal.strategy_key, 'retrieval_ranking');
  assert.equal(proposal.configuration.best_score, 0.835);
  assert.match(proposal.rationale, /Autonomous research/);

  // 6. Stop and reset
  runner.stop();
  assert.equal(runner.getStatus().status, 'paused');
  runner.reset();
  assert.equal(runner.getStatus().status, 'idle');
  assert.equal(runner.getStatus().iteration, 0);
  assert.equal(runner.getStatus().bestScore, null);

  // Cannot propose when reset
  assert.throws(() => runner.proposeStrategy(), /No strictly improved strategy/);
});

test('ResearchRunner supports local code lab mode', async () => {
  const runner = new ResearchRunner();
  await runner.start({
    mode: 'local',
    targetFile: 'train.py',
    evalScript: 'prepare.py',
    instructions: 'Test learning rate schedule',
  });
  const status = runner.getStatus();
  assert.equal(status.mode, 'local');
  assert.equal(status.targetFile, 'train.py');
  assert.equal(status.evalScript, 'prepare.py');

  await runner.step(async () => ({ score: 0.910, diffSummary: 'Cosine warmup applied' }));
  const proposal = runner.proposeStrategy();
  assert.equal(proposal.strategy_key, 'plugin_health');
  assert.equal(proposal.configuration.mode, 'local');
  assert.equal(proposal.configuration.best_score, 0.910);
});

test('ResearchRunner supports darwin 2.0 skill evolution mode and 3-part archive payload', async () => {
  const runner = new ResearchRunner();
  await runner.start({
    mode: 'darwin',
    modelEngine: 'deepseek',
    targetIterations: 5,
    targetFile: '.agents/skills/brainstorming/SKILL.md',
    evalScript: 'test-prompts.json',
    instructions: 'Evolve skill instructions using SkillLens 9-dim rubric',
  });
  const status = runner.getStatus();
  assert.equal(status.mode, 'darwin');
  assert.equal(status.modelEngine, 'deepseek');
  assert.equal(status.targetIterations, 5);
  assert.equal(status.targetFile, '.agents/skills/brainstorming/SKILL.md');

  // Step 1: Improved
  await runner.step(async () => ({ score: 0.885, diffSummary: 'Added strict edge-case handling rules' }));
  // Step 2: Regressed
  await runner.step(async () => ({ score: 0.850, diffSummary: 'Verbose instructions dropped quality' }));

  const current = runner.getStatus();
  assert.equal(current.bestScore, 0.885);
  assert.equal(current.iteration, 2);

  // Archive payload check
  const archive = runner.buildArchivePayload();
  assert.match(archive.title, /自主研究成果 · Darwin 2.0 技能演化/);
  assert.match(archive.conclusion, /在【Darwin 2.0 技能演化】模式下完成 2 轮迭代/);
  assert.match(archive.changes, /【改动靶点】：\.agents\/skills\/brainstorming\/SKILL\.md/);
  assert.match(archive.metrics, /初始基线 0.8 | 最佳得分 0.885/);
  assert.equal(archive.metadata.model_engine, 'deepseek');
});

test('index.js /library command dispatches research operations', async () => {
  const { apply } = await import('../index.js');
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { resolve } = await import('node:path');

  const root = resolve(import.meta.dirname, '../../..');
  const settings = (dataDir) => ({
    pythonExecutable: resolve(root, '.venv/Scripts/python.exe'),
    bridgeScript: resolve(root, 'scripts/run_harness_bridge.py'),
    dataDir, scope: 'plugin-test', principal: 'deepseek-harness',
    maxContextChars: 6000, maxItems: 3, excerptChars: 240,
    timeoutMs: 15000, maxReplyBytes: 262144,
  });

  const data = await mkdtemp(resolve(tmpdir(), 'ai-library-cmd-test-'));
  let commandHandler = null;
  const disposers = [];
  const ctx = {
    commands: {
      register(declaration) {
        if (declaration?.name === 'library') commandHandler = declaration.handler;
        return () => { commandHandler = null; };
      },
    },
    sessionProjections: {
      register() { return () => {}; },
      stateOf() { return { project: null, pending: [] }; },
    },
    on() { return () => {}; },
    effect(factory) { const dispose = factory(); if (dispose) disposers.push(dispose); return dispose; },
  };

  try {
    await apply(ctx, settings(data));
    assert.equal(typeof commandHandler, 'function');

    const agent = { session: { id: 'test-session' } };

    // 1. research_status
    let res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_status' }) });
    assert.equal(res.kind, 'success');
    let parsed = JSON.parse(res.text);
    assert.equal(parsed.status, 'idle');

    // 2. research_start
    res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_start', mode: 'library' }) });
    assert.equal(res.kind, 'success');
    parsed = JSON.parse(res.text);
    assert.equal(parsed.status, 'running');
    assert.equal(parsed.mode, 'library');

    // 3. research_step (with improvement)
    res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_step', score: 0.88, diffSummary: 'Tuned RRF' }) });
    assert.equal(res.kind, 'success');
    parsed = JSON.parse(res.text);
    assert.equal(parsed.iteration, 1);
    assert.equal(parsed.bestScore, 0.88);

    // 4. research_propose
    res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_propose' }) });
    assert.equal(res.kind, 'success');
    parsed = JSON.parse(res.text);
    assert.equal(parsed.strategy_key, 'retrieval_ranking');

    // 5. research_archive
    res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_archive' }) });
    assert.equal(res.kind, 'success');
    parsed = JSON.parse(res.text);
    assert.equal(parsed.operation, 'research_archive');
    assert.equal(parsed.archived, true);
    assert.equal(parsed.topic_title, '自主研究成果');

    // 6. research_stop
    res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_stop' }) });
    assert.equal(res.kind, 'success');
    parsed = JSON.parse(res.text);
    assert.equal(parsed.status, 'paused');

    // 7. research_reset
    res = await commandHandler({ agent, rawInput: JSON.stringify({ operation: 'research_reset' }) });
    assert.equal(res.kind, 'success');
    parsed = JSON.parse(res.text);
    assert.equal(parsed.status, 'idle');
  } finally {
    for (const dispose of disposers.reverse()) await dispose();
    await rm(data, { recursive: true, force: true });
  }
});
