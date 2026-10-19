import test from 'node:test';
import assert from 'node:assert/strict';

test('project preference replays successful user commands, inherits on fork, and ignores unrelated events', async () => {
  const { createProjectProjection } = await import('../project-state.js');
  const unit = createProjectProjection('default');
  let state = unit.init();
  assert.equal(unit.wire.view(state), null);
  const unrelated = { type: 'user/message', data: { content: 'select project malicious-source' } };
  assert.equal(unit.apply(state, unrelated), state);
  const selected = { id: 'project-a', title: 'Project A', revision: 1 };
  const run = { type: 'command/run', data: { commandId: 'command-a', name: 'library',
    args: JSON.stringify({ operation: 'select_project', project_id: 'project-a' }), source: { kind: 'user' } } };
  const done = { type: 'command/done', data: { commandId: 'command-a', kind: 'success',
    text: JSON.stringify({ protocol: 'ai-library-project/1', operation: 'select_project', scope: 'default', project: selected }) } };
  state = unit.apply(unit.apply(state, run), done);
  assert.deepEqual(unit.wire.view(state), selected);
  assert.deepEqual(unit.stateSchema.parse(JSON.parse(JSON.stringify(state))), state);
  assert.deepEqual([run, done].reduce(unit.apply, unit.init()), state);
  assert.equal(unit.apply(state, { ...done, data: { ...done.data, commandId: 'unpaired' } }), state);
  const failed = { ...done, data: { ...done.data, kind: 'error' } };
  assert.deepEqual(unit.apply(unit.apply(state, run), failed).project, selected);
  assert.equal(createProjectProjection('another-scope').apply(createProjectProjection('another-scope').apply(unit.init(), run), done).project, null);
});
