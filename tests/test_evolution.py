import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.policy import AccessPolicy
from backend.notes.notes_manager import NotesManager
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


@pytest.fixture
def env(tmp_path):
    from backend.evolution import EvolutionManager, create_evolution_router
    storage = CanonicalStorage(str(tmp_path / 'evolution.db'))
    policy = AccessPolicy(storage)
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw')))
    manager = EvolutionManager(storage)
    app = FastAPI()
    app.include_router(create_evolution_router(storage, manager=manager))
    return manager, notes, policy, TestClient(app)


def anchor(note):
    return {'source_id': note.id, 'source_revision': note.revision, 'content_hash': note.content_hash}


def report(notes, scope='a', author='tester', correctness=True):
    return notes.create_note('test report', json.dumps({'correctness': correctness, 'policy_compliance': True, 'latency_ms': 12, 'confidence': 0.2}), scope, author)


def execution(note, event='result', event_type='tool_result', revision=1):
    return dict(run_id='run', source_agent_profile='agent', source_event_id=event, source_revision=revision,
                scope='a', event_type=event_type, session_ref='session', turn_ref='turn', call_id='call' if event in ('result','call') else 'call-'+event,
                attempt_id='attempt-1', evidence_refs=[anchor(note)], result='success', coverage='partial')


def dataset(notes, refs, correctness=True):
    return notes.create_note('held out dataset', json.dumps({'environment': {'model': 'fixed', 'budget_tokens': 1000, 'runtime': 'simulated'},
        'cases': [{'record_id': record['id'], 'record_revision': record['revision'], 'evidence': record['evidence_refs'][0],
                   'criteria': [{'field': 'correctness', 'operator': 'eq', 'expected': correctness},
                                {'field': 'policy_compliance', 'operator': 'eq', 'expected': True}]} for record in refs]}), 'a', 'independent-tester')


def test_execution_idempotence_pairing_history_and_scope(env):
    mgr, notes, _, _ = env
    source = report(notes)
    result = mgr.import_record(execution(source))
    assert result['pairing'] == 'unpaired'
    assert result['verification'] == 'UNVERIFIED_OBSERVATION'
    assert mgr.import_record(execution(source))['id'] == result['id']
    call = mgr.import_record(execution(source, 'call', 'tool_call'))
    assert mgr.get_record(result['id'], scope='a')['pairing'] == 'paired'
    changed = execution(source)
    changed['result'] = 'failed'
    with pytest.raises(ValueError):
        mgr.import_record(changed)
    updated = execution(source, revision=2)
    updated['result'] = 'unknown'
    v2 = mgr.import_record(updated, expected_revision=1)
    assert v2['revision'] == 2
    assert mgr.get_record(result['id'], scope='a', revision=1)['result'] == 'success'
    assert mgr.get_record(result['id'], scope='b') is None


def test_missing_cross_scope_and_forged_evidence_rejected(env):
    mgr, notes, _, _ = env
    data = execution(report(notes, scope='b'))
    with pytest.raises(ValueError):
        mgr.import_record(data)
    data = execution(report(notes))
    data['evidence_refs'][0]['content_hash'] = '0' * 64
    with pytest.raises(ValueError):
        mgr.import_record(data)
    data = execution(report(notes))
    data['payload'] = {'api_key': 'secret'}
    with pytest.raises(ValueError):
        mgr.import_record(data)


def evaluated_strategy(mgr, notes, key='context_config'):
    record = mgr.import_record(execution(report(notes)))
    data = dataset(notes, [record])
    strategy = mgr.propose_strategy(scope='a', strategy_key=key, author='agent', configuration={'max_nodes': 3}, baseline_id=None,
                                    evidence_refs=[anchor(data)], rationale='bounded context')
    evaluation = mgr.evaluate(scope='a', evaluator='independent-tester', evaluator_version='deterministic-v1',
                              dataset_ref=anchor(data), record_refs=[{'id': record['id'], 'revision': 1}], strategy_id=strategy['id'], expected_revision=1)
    assert evaluation['outcome'] == 'PASS'
    return mgr.get_strategy(strategy['id'], scope='a'), evaluation


def test_independent_evaluation_reproducible_and_no_self_evaluation(env):
    mgr, notes, _, _ = env
    strategy, evaluation = evaluated_strategy(mgr, notes)
    assert strategy['status'] == 'EVALUATED'
    assert evaluation['correctness_gate'] is True
    with pytest.raises(PermissionError):
        mgr.evaluate(scope='a', evaluator='agent', evaluator_version='deterministic-v1', dataset_ref=evaluation['dataset_ref'],
                     record_refs=evaluation['record_refs'])
    again = mgr.evaluate(scope='a', evaluator='independent-tester', evaluator_version='deterministic-v1',
                         dataset_ref=evaluation['dataset_ref'], record_refs=evaluation['record_refs'])
    assert again['results'] == evaluation['results']
    assert again['reproducibility_hash'] == evaluation['reproducibility_hash']


def test_stale_evidence_blocks_evaluation_and_promotion(env):
    mgr, notes, _, _ = env
    strategy, evaluation = evaluated_strategy(mgr, notes)
    ref = evaluation['dataset_ref']
    notes.revise_note(ref['source_id'], 'changed dataset', '{}', expected_revision=1)
    ev = mgr.evaluate(scope='a', evaluator='independent-tester', evaluator_version='deterministic-v1',
                      dataset_ref=ref, record_refs=evaluation['record_refs'])
    assert ev['outcome'] == 'INCONCLUSIVE'
    with pytest.raises(ValueError):
        mgr.request_approval(strategy['id'], scope='a', expected_revision=strategy['revision'])


def test_atomic_owner_activation_single_use_approval_and_rollback(env):
    mgr, notes, policy, _ = env
    strategy, _ = evaluated_strategy(mgr, notes)
    pending = mgr.request_approval(strategy['id'], scope='a', expected_revision=strategy['revision'])
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a', write=True):
        with pytest.raises(PermissionError):
            mgr.approve(strategy['id'], scope='a', expected_revision=pending['revision'], reason='self approved')
    approval = mgr.approve(strategy['id'], scope='a', expected_revision=pending['revision'], reason='owner reviewed report')
    active = mgr.activate(strategy['id'], scope='a', expected_revision=pending['revision'], approval_id=approval['id'])
    assert active['status'] == 'ACTIVE'
    assert mgr.active_configuration('a', 'context_config') == {'max_nodes': 3}
    with pytest.raises(ValueError):
        mgr.activate(strategy['id'], scope='a', expected_revision=pending['revision'], approval_id=approval['id'])
    restored = mgr.rollback(strategy['id'], scope='a', expected_revision=active['revision'], reason='regression')
    assert restored['status'] == 'ROLLED_BACK'
    assert mgr.active_configuration('a', 'context_config') is None


def test_protected_mutations_never_become_strategy(env):
    mgr, notes, _, _ = env
    for key in ('authentication', 'storage_engine', 'credential_policy', 'schema_registry'):
        with pytest.raises(ValueError):
            mgr.propose_strategy(scope='a', strategy_key=key, author='agent', configuration={}, evidence_refs=[anchor(report(notes))], rationale='change')


def test_agent_identity_and_cross_scope_enforced(env):
    mgr, notes, policy, _ = env
    source = report(notes)
    policy.ensure_launch_grant('other', 'a')
    with policy.activate('other', 'a', write=True):
        with pytest.raises(PermissionError):
            mgr.import_record(execution(source))
        with pytest.raises(PermissionError):
            mgr.list_records('b')


def test_evidence_backed_distillation_never_promotes_maturity(env):
    mgr, notes, _, _ = env
    _, ev = evaluated_strategy(mgr, notes)
    experience = mgr.distill(scope='a', evaluation_id=ev['id'], heuristic='Inspect sources before changing context.',
                            decision='Bound context size', factors=['Measured correctness retained'], author='agent')
    assert experience['maturity'] == 'L2'
    assert experience['authority'] == 'INFERRED'
    assert experience['evaluation_id'] == ev['id']
    with pytest.raises(ValueError):
        mgr.distill(scope='a', evaluation_id='missing', heuristic='guess', decision='guess', factors=[], author='agent')


def test_scheduler_debounce_budget_and_no_timer_work(env):
    mgr, notes, _, _ = env
    record = mgr.import_record(execution(report(notes)))
    first = mgr.schedule(scope='a', trigger='user_correction', target_key='context', record_refs=[{'id': record['id'], 'revision': 1}],
                         estimated_tokens=100, token_budget=50, health={'confidence': 0.2})
    assert first['status'] == 'DEFERRED'
    second = mgr.schedule(scope='a', trigger='user_correction', target_key='context', record_refs=[{'id': record['id'], 'revision': 1}],
                          estimated_tokens=100, token_budget=200, health={'confidence': 0.2})
    assert second['id'] == first['id']
    assert second['status'] == 'QUEUED'
    with pytest.raises(ValueError):
        mgr.schedule(scope='a', trigger='timer', target_key='context', record_refs=[], estimated_tokens=100, token_budget=200)
    claimed = mgr.claim_job(second['id'], scope='a', expected_revision=second['revision'])
    assert claimed['status'] == 'RUNNING'
    done = mgr.finish_job(second['id'], scope='a', expected_revision=claimed['revision'], actual_tokens=80, outcome='FAILED', reason='missing output')
    assert done['status'] == 'DEAD_LETTER'
    assert mgr.job_budget('a')['actual_tokens'] == 80


def test_api_validation_scope_history_and_owner_boundary(env):
    mgr, notes, policy, client = env
    data = execution(report(notes))
    response = client.post('/api/evolution/records', json=data)
    assert response.status_code == 201
    id = response.json()['id']
    assert client.get(f'/api/evolution/records/{id}?scope=a&revision=1').json()['result'] == 'success'
    assert client.get(f'/api/evolution/records/{id}?scope=b').status_code == 404
    data['source_revision'] = 0
    assert client.post('/api/evolution/records', json=data).status_code == 422
    assert client.post('/api/evolution/records', json={**execution(report(notes)), 'chain_of_thought': 'private'}).status_code == 422


def test_historical_import_replay_is_idempotent(env):
    mgr, notes, _, _ = env
    data = execution(report(notes))
    first = mgr.import_record(data)
    second = mgr.import_record({**data, 'source_revision': 2, 'result': 'unknown'}, expected_revision=1)
    replay = mgr.import_record(data)
    assert replay['revision'] == 1
    assert mgr.get_record(first['id'], scope='a')['revision'] == second['revision']


def test_scheduler_durable_spend_blocks_further_reservations(env):
    mgr, notes, _, _ = env
    record = mgr.import_record(execution(report(notes)))
    data = dict(scope='a', trigger='user_correction', target_key='one', record_refs=[{'id': record['id'], 'revision': 1}], estimated_tokens=80, token_budget=100)
    job = mgr.schedule(**data)
    job = mgr.claim_job(job['id'], scope='a', expected_revision=job['revision'])
    mgr.finish_job(job['id'], scope='a', expected_revision=job['revision'], actual_tokens=80, outcome='FAILED')
    restarted = type(mgr)(mgr.storage)
    next_job = restarted.schedule(**{**data, 'target_key': 'two'})
    assert next_job['status'] == 'DEFERRED'
    assert restarted.job_budget('a')['actual_tokens'] == 80


def test_failed_hard_gate_and_missing_measurement_cannot_pass(env):
    mgr, notes, _, _ = env
    record = mgr.import_record(execution(report(notes, correctness=False)))
    data = dataset(notes, [record])
    ev = mgr.evaluate(scope='a', evaluator='independent-tester', evaluator_version='deterministic-v1', dataset_ref=anchor(data), record_refs=[{'id': record['id'], 'revision':1}])
    assert ev['outcome'] == 'FAIL'
    incomplete = notes.create_note('incomplete report', json.dumps({'policy_compliance': True}), 'a', 'tester')
    record2 = mgr.import_record(execution(incomplete, event='incomplete'))
    data2 = dataset(notes, [record2])
    ev2 = mgr.evaluate(scope='a', evaluator='independent-tester', evaluator_version='deterministic-v1', dataset_ref=anchor(data2), record_refs=[{'id': record2['id'], 'revision':1}])
    assert ev2['outcome'] == 'INCONCLUSIVE'
    assert ev2['correctness_gate'] is False


def test_ambiguous_tool_pairing_and_unbounded_payload_rejected(env):
    mgr, notes, _, _ = env
    data = execution(report(notes))
    del data['attempt_id']
    with pytest.raises(ValueError):
        mgr.import_record(data)
    data = execution(report(notes))
    data['payload'] = {'body': 'x' * 65537}
    with pytest.raises(ValueError):
        mgr.import_record(data)


def test_api_owner_promotion_lifecycle_and_stale_approval(env):
    mgr, notes, policy, client = env
    strategy, _ = evaluated_strategy(mgr, notes)
    id = strategy['id']
    pending = client.post(f'/api/evolution/strategies/{id}/request-approval', json={'scope':'a', 'expected_revision':strategy['revision']}).json()
    approval = client.post(f'/api/evolution/strategies/{id}/approve', json={'scope':'a','expected_revision':pending['revision'],'reason':'verified supplied report'}).json()
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a', write=True):
        denied = client.post(f'/api/evolution/strategies/{id}/activate', json={'scope':'a','expected_revision':pending['revision'],'approval_id':approval['id']})
        assert denied.status_code == 403
    active = client.post(f'/api/evolution/strategies/{id}/activate', json={'scope':'a','expected_revision':pending['revision'],'approval_id':approval['id']})
    assert active.status_code == 200
    assert client.get('/api/evolution/active/context_config?scope=a').json()['configuration']['max_nodes'] == 3
    assert client.post(f'/api/evolution/strategies/{id}/activate', json={'scope':'a','expected_revision':pending['revision'],'approval_id':approval['id']}).status_code == 409
    assert client.post(f'/api/evolution/strategies/{id}/rollback', json={'scope':'a','expected_revision':active.json()['revision'],'reason':'regression'}).status_code == 200


def benchmark_candidate(mgr, notes, latency, baseline_id=None, event='benchmark', environment_model='fixed', include_benchmark=True):
    source = notes.create_note('measured report', json.dumps({'correctness':True,'policy_compliance':True,'latency_ms':latency}), 'a', 'independent-tester')
    record = mgr.import_record(execution(source,event=event))
    criteria=[
            {'field':'correctness','operator':'eq','expected':True},
            {'field':'policy_compliance','operator':'eq','expected':True},
            {'field':'latency_ms','operator':'le','expected':100}]
    benchmark=notes.get_note('benchmark-heldout-v1')
    if benchmark is None:
        benchmark=notes.create_note('fixed held-out tasks',json.dumps({'environment':{'model':'fixed','budget_tokens':1000,'runtime':'simulated'},
            'cases':[{'case_id':'task-a','criteria':criteria}]}),'a','independent-tester',note_id='benchmark-heldout-v1')
    spec = {'benchmark_ref':anchor(benchmark),'environment':{'model':environment_model,'budget_tokens':1000,'runtime':'simulated'},'cases':[
        {'case_id':'task-a','record_id':record['id'],'record_revision':1,'evidence':anchor(source),'criteria':criteria}]}
    if not include_benchmark:
        spec.pop('benchmark_ref')
    data = notes.create_note('benchmark-v1',json.dumps(spec),'a','independent-tester')
    proposed = mgr.propose_strategy(scope='a',strategy_key='context_config',author='agent',configuration={'max_nodes':4 if baseline_id else 5},
                                    baseline_id=baseline_id,evidence_refs=[anchor(data)],rationale='measured bounded context')
    ev = mgr.evaluate(scope='a',evaluator='independent-tester',evaluator_version='deterministic-v1',dataset_ref=anchor(data),
                      record_refs=[{'id':record['id'],'revision':1}],strategy_id=proposed['id'],expected_revision=1)
    return mgr.get_strategy(proposed['id'],scope='a'),ev


def activate_strategy(mgr,strategy):
    pending=mgr.request_approval(strategy['id'],scope='a',expected_revision=strategy['revision'])
    approval=mgr.approve(strategy['id'],scope='a',expected_revision=pending['revision'],reason='owner benchmark review')
    return mgr.activate(strategy['id'],scope='a',expected_revision=pending['revision'],approval_id=approval['id'])


def test_pinned_benchmark_improvement_and_atomic_prior_version_rollback(env):
    mgr,notes,_,_=env
    baseline,ev1=benchmark_candidate(mgr,notes,12)
    baseline=activate_strategy(mgr,baseline)
    candidate,ev2=benchmark_candidate(mgr,notes,8,baseline['id'],'faster')
    assert ev2['benchmark']['outcome']=='IMPROVED'
    assert ev2['benchmark']['deltas'][0]['delta']==-4
    active=activate_strategy(mgr,candidate)
    assert mgr.get_strategy(baseline['id'],scope='a')['status']=='SUPERSEDED'
    rolled=mgr.rollback(active['id'],scope='a',expected_revision=active['revision'],reason='field regression')
    assert rolled['rollback_target']==baseline['id']
    assert mgr.active_configuration('a','context_config')=={'max_nodes':5}


def test_unchanged_or_incomparable_benchmark_cannot_claim_improvement(env):
    mgr,notes,_,_=env
    baseline,_=benchmark_candidate(mgr,notes,12)
    baseline=activate_strategy(mgr,baseline)
    candidate,ev=benchmark_candidate(mgr,notes,12,baseline['id'],'unchanged')
    assert ev['outcome']=='INCONCLUSIVE'
    assert candidate['status']=='INCONCLUSIVE'
    candidate2,ev2=benchmark_candidate(mgr,notes,8,baseline['id'],'different-model','changed')
    assert ev2['outcome']=='INCONCLUSIVE'
    assert ev2['benchmark']['outcome']=='INCOMPARABLE'


def test_scheduler_rejects_unmeasured_health_and_stale_execution(env):
    mgr,notes,_,_=env
    source=report(notes)
    record=mgr.import_record(execution(source))
    args=dict(scope='a',trigger='user_correction',target_key='context',record_refs=[{'id':record['id'],'revision':1}],estimated_tokens=10,token_budget=100)
    with pytest.raises(ValueError):
        mgr.schedule(**args,health={'confidence':0.1})
    mgr.import_record(execution(source,revision=2),expected_revision=1)
    with pytest.raises(ValueError):
        mgr.schedule(**args)


def test_active_strategy_automatically_stops_using_stale_sources(env):
    mgr,notes,_,_=env
    strategy,ev=evaluated_strategy(mgr,notes)
    activate_strategy(mgr,strategy)
    assert mgr.active_configuration('a','context_config')=={'max_nodes':3}
    source=ev['evidence_refs'][0]
    notes.revise_note(source['source_id'],'updated measurement',json.dumps({'correctness':False}),expected_revision=source['source_revision'])
    assert mgr.active_configuration('a','context_config') is None


def test_duplicate_stable_tool_call_identity_rejected(env):
    mgr,notes,_,_=env
    source=report(notes)
    mgr.import_record({**execution(source,event='call-one',event_type='tool_call'),'call_id':'same-call'})
    with pytest.raises(ValueError):
        mgr.import_record({**execution(source,event='call-two',event_type='tool_call'),'call_id':'same-call'})


def test_improvement_requires_shared_exact_heldout_task_source(env):
    mgr,notes,_,_=env
    baseline,_=benchmark_candidate(mgr,notes,12)
    baseline=activate_strategy(mgr,baseline)
    _,ev=benchmark_candidate(mgr,notes,8,baseline['id'],'unpinned',include_benchmark=False)
    assert ev['outcome']=='INCONCLUSIVE'
    assert ev['benchmark']['outcome']=='INCOMPARABLE'


def test_plugin_health_and_mcp_routing_strategy_proposal(env):
    mgr, notes, _, _ = env
    source = report(notes)
    proposal = mgr.propose_strategy(
        scope='a',
        strategy_key='plugin_health',
        author='system-health',
        configuration={'disabled_plugins': ['dsh-our-free-model'], 'fallback_port': 3090},
        evidence_refs=[anchor(source)],
        rationale='OpenCode free models are returning 403 INVALID_CREDENTIAL, disable plugin and route to default'
    )
    assert proposal['status'] == 'PROPOSED'
    assert proposal['configuration']['fallback_port'] == 3090

    mcp_proposal = mgr.propose_strategy(
        scope='a',
        strategy_key='mcp_routing',
        author='system-health',
        configuration={'disabled_servers': ['brevo']},
        evidence_refs=[anchor(source)],
        rationale='Disable unused cloud MCP server for local library scope'
    )
    assert mcp_proposal['status'] == 'PROPOSED'
    assert mcp_proposal['configuration']['disabled_servers'] == ['brevo']

