"""Evidence-driven improvement. Never executes tools or mutates runtime/skills.

All state is canonical SQLite with immutable revision snapshots. Evaluation is
an exact, reproducible comparison of supplied report observations and a pinned
held-out dataset, not proof of facts outside those sources.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime

from backend.auth.policy import current_access
from backend.schema.models import utc_now
from backend.evolution.models import ExecutionRecord, StrategyRequest, EvaluationRequest, DistillRequest, ScheduleRequest


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class EvolutionConflict(ValueError):
    pass


class EvolutionManager:
    MAX_BYTES = 65536

    def __init__(self, storage):
        self.storage = storage
        if current_access.get() is not None:
            raise PermissionError('Evolution schema initialization requires local owner startup')
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS evolution_revisions (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, scope TEXT NOT NULL,
                    kind TEXT NOT NULL, payload_json TEXT NOT NULL, is_current INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY(id, revision));
                CREATE UNIQUE INDEX IF NOT EXISTS evolution_current ON evolution_revisions(id) WHERE is_current=1;
                CREATE INDEX IF NOT EXISTS evolution_scope_kind ON evolution_revisions(scope,kind,is_current);
                CREATE TABLE IF NOT EXISTS evolution_active (
                    scope TEXT NOT NULL, strategy_key TEXT NOT NULL, strategy_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, PRIMARY KEY(scope,strategy_key));
                CREATE TABLE IF NOT EXISTS evolution_approvals (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, strategy_id TEXT NOT NULL,
                    strategy_revision INTEGER NOT NULL, configuration_hash TEXT NOT NULL,
                    reason TEXT NOT NULL, actor TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    consumed_at TEXT);
            ''')

    def _access(self, scope, identity=None, owner=False):
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 200:
            raise ValueError('A bounded explicit scope is required')
        context = current_access.get()
        if context is not None:
            if owner:
                raise PermissionError('Local owner approval is required')
            if context.scope != scope:
                raise PermissionError('Evolution scope exceeds connection authorization')
            if identity is not None and context.principal != identity:
                raise PermissionError('Actor identity differs from authorized connection')

    def _bounded(self, value):
        sensitive = {'chain_of_thought', 'private_reasoning', 'api_key', 'access_token', 'password', 'credential', 'authorization', 'secret'}
        def inspect(item, depth=0):
            if depth > 12:
                raise ValueError('Record nesting exceeds allowed depth')
            if isinstance(item, dict):
                for key, child in item.items():
                    if key.lower().replace('-', '_') in sensitive:
                        raise ValueError('Credentials or model-private reasoning are forbidden')
                    inspect(child, depth+1)
            elif isinstance(item, list):
                for child in item:
                    inspect(child, depth+1)
            elif isinstance(item, str) and re.search(r'(?i)\bbearer\s+[A-Za-z0-9._-]{12,}|\bsk-[A-Za-z0-9_-]{16,}', item):
                raise ValueError('Redact credential-like values before import')
        inspect(value)
        text = encoded(value)
        if len(text.encode()) > self.MAX_BYTES:
            raise ValueError('Structured record exceeds 64 KiB; store a source reference')

    def _report(self,content):
        if len(content.encode())>self.MAX_BYTES:
            raise ValueError('Evaluation report exceeds 64 KiB')
        try:
            item=json.loads(content)
        except (ValueError,RecursionError) as exc:
            raise ValueError('Evaluation reports must contain bounded JSON objects') from exc
        self._bounded(item)
        if not isinstance(item,dict):
            raise ValueError('Evaluation reports must contain JSON objects')
        return item

    def _load(self, conn, id, scope, kind=None, revision=None):
        where = 'id=? AND scope=? AND ' + ('is_current=1' if revision is None else 'revision=?')
        args = [id, scope] + ([] if revision is None else [revision])
        if kind:
            where += ' AND kind=?'
            args.append(kind)
        row = conn.execute('SELECT payload_json FROM evolution_revisions WHERE ' + where, args).fetchone()
        return json.loads(row['payload_json']) if row else None

    def _save(self, conn, kind, value, expected_revision=None):
        self._bounded(value)
        existing = self._load(conn, value['id'], value['scope'])
        if existing:
            if expected_revision != existing['revision']:
                raise EvolutionConflict('Revision changed; reload before editing')
            revision = existing['revision']+1
            conn.execute('UPDATE evolution_revisions SET is_current=0 WHERE id=? AND is_current=1', (value['id'],))
        else:
            if expected_revision is not None:
                raise EvolutionConflict('Record does not exist at expected revision')
            revision = 1
        value = {**value, 'revision': revision, 'recorded_at': utc_now().isoformat()}
        conn.execute('INSERT INTO evolution_revisions VALUES (?,?,?,?,?,1,?)',
                     (value['id'], revision, value['scope'], kind, encoded(value), value['recorded_at']))
        self.storage._insert_outbox_event(conn, 'EVOLUTION', value['id'], kind.upper()+'_CHANGED',
                                         {'scope': value['scope'], 'revision': revision, 'kind': kind})
        return value

    def _source(self, conn, ref, scope, current=False):
        if not isinstance(ref, dict) or set(ref) != {'source_id','source_revision','content_hash'}:
            raise ValueError('An exact source revision and hash are required')
        row = conn.execute('SELECT * FROM notes WHERE id=? AND revision=? AND scope=?',
                           (ref['source_id'], ref['source_revision'], scope)).fetchone()
        if not row or row['content_hash'] != ref['content_hash'] or hashlib.sha256(row['content'].encode()).hexdigest() != ref['content_hash']:
            raise ValueError('Evidence source is unavailable, unauthorized or has a different hash')
        if current and not row['is_current']:
            raise ValueError('Evidence source is stale; reevaluate the current revision')
        return dict(row)

    def _pairing(self, conn, record):
        if record['event_type'] not in ('tool_call','tool_result'):
            return 'unavailable'
        opposite = 'tool_call' if record['event_type']=='tool_result' else 'tool_result'
        keys = ('source_agent_profile','run_id','session_ref','turn_ref','call_id','attempt_id')
        for row in conn.execute("SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind='record' AND is_current=1", (record['scope'],)):
            candidate = json.loads(row['payload_json'])
            if candidate['event_type'] == opposite and all(record[k]==candidate[k] for k in keys):
                return 'paired'
        return 'unpaired'

    def import_record(self, data, expected_revision=None):
        data = ExecutionRecord.model_validate(data).model_dump(mode='json')
        self._access(data['scope'], data['source_agent_profile'])
        self._bounded(data)
        if data['occurred_at']:
            try:
                time = datetime.fromisoformat(data['occurred_at'])
                if time.tzinfo is None:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('Source time must include timezone or remain null')
        id = 'execution-'+digest([data[k] for k in ('scope','source_agent_profile','run_id','source_event_id')])[:32]
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for ref in data['evidence_refs']:
                self._source(conn, ref, data['scope'])
            old = self._load(conn, id, data['scope'], 'record')
            replay = next((json.loads(row['payload_json']) for row in conn.execute('SELECT payload_json FROM evolution_revisions WHERE id=? AND scope=? AND kind=?', (id,data['scope'],'record'))
                           if json.loads(row['payload_json'])['source_revision']==data['source_revision']), None)
            if replay:
                if replay['import_hash'] != digest(data):
                    raise EvolutionConflict('The same source event revision has conflicting content')
                replay['pairing'] = self._pairing(conn, replay)
                conn.execute('COMMIT')
                return replay
            if old and data['source_revision'] <= old['source_revision']:
                raise EvolutionConflict('Source revisions must increase monotonically')
            stable=('source_agent_profile','run_id','session_ref','turn_ref','call_id','attempt_id','event_type')
            if old and any(old[k]!=data[k] for k in stable):
                raise EvolutionConflict('Stable event type and correlation identifiers cannot change across source revisions')
            if data['event_type'] in ('tool_call','tool_result'):
                for row in conn.execute("SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind='record' AND is_current=1",(data['scope'],)):
                    other=json.loads(row['payload_json'])
                    if other['id']!=id and all(other[k]==data[k] for k in stable):
                        raise ValueError('A stable tool event identity already belongs to another source event')
            record = {**data,'id':id, 'import_hash':digest(data), 'verification':'UNVERIFIED_OBSERVATION', 'pairing': self._pairing(conn, data)}
            record = self._save(conn, 'record', record, expected_revision)
            conn.execute('COMMIT')
        return record

    def get_record(self, id, *, scope, revision=None):
        self._access(scope)
        with self.storage.session() as conn:
            item = self._load(conn,id,scope,'record',revision)
            if item:
                item['pairing'] = self._pairing(conn,item)
            return item

    def list_items(self, scope, kind, limit=100, offset=0):
        self._access(scope)
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError('Invalid bounded pagination')
        with self.storage.session() as conn:
            rows = conn.execute('SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind=? AND is_current=1 ORDER BY recorded_at DESC,id LIMIT ? OFFSET ?',
                                (scope,kind,limit,offset))
            return [json.loads(row['payload_json']) for row in rows]

    def list_records(self, scope, limit=100, offset=0):
        return self.list_items(scope,'record',limit,offset)

    def get_strategy(self, id, *, scope, revision=None):
        self._access(scope)
        with self.storage.session() as conn:
            return self._load(conn,id,scope,'strategy',revision)

    def _configuration(self, key, configuration):
        options = {
            'retrieval_ranking': {'rrf_k':(int,1,1000)},
            'context_config': {'max_nodes':(int,1,200),'max_tokens':(int,100,100000)},
            'skill_selection': {'preferred_capability_ids':(list,None,None)},
            'workflow_recommendation': {'recommendation':(str,None,None)},
            'plugin_health': {'disabled_plugins':(list,None,None),'fallback_port':(int,1024,65535)},
            'mcp_routing': {'disabled_servers':(list,None,None)},
        }
        if key not in options or not configuration or set(configuration)-set(options[key]):
            raise ValueError('Strategy configuration exceeds approved safe configuration options')
        for name,value in configuration.items():
            expected, minimum, maximum = options[key][name]
            if isinstance(value,bool) or not isinstance(value,expected):
                raise ValueError('Strategy option has invalid type')
            if minimum is not None and (not math.isfinite(value) or not minimum <= value <= maximum):
                raise ValueError('Strategy option is out of bounds')
            if isinstance(value,list) and (len(value)>50 or any(not isinstance(x,str) or not 1<=len(x)<=200 for x in value)):
                raise ValueError('List elements are invalid')
            if isinstance(value,str) and not 1<=len(value)<=4000:
                raise ValueError('Workflow recommendation is out of bounds')

    def propose_strategy(self, **data):
        data = StrategyRequest.model_validate(data).model_dump(mode='json')
        self._access(data['scope'],data['author'])
        self._configuration(data['strategy_key'],data['configuration'])
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for ref in data['evidence_refs']:
                self._source(conn,ref,data['scope'],current=True)
            active = conn.execute('SELECT strategy_id FROM evolution_active WHERE scope=? AND strategy_key=?',(data['scope'],data['strategy_key'])).fetchone()
            actual_baseline = active['strategy_id'] if active else None
            if data['baseline_id'] != actual_baseline:
                raise EvolutionConflict('Baseline must be the current active strategy')
            baseline = self._load(conn,actual_baseline,data['scope'],'strategy') if active else None
            item = self._save(conn,'strategy',{**data,'id':'strategy-'+uuid.uuid4().hex,'status':'PROPOSED',
                'configuration_hash':digest(data['configuration']), 'baseline_revision': baseline['revision'] if baseline else None,
                'evaluation_id':None,'approval_basis':None})
            conn.execute('COMMIT')
            return item

    def _evaluation_inputs(self, conn, data):
        reports = []
        generators = set()
        for ref in data['record_refs']:
            record = self._load(conn,ref['id'],data['scope'],'record',ref['revision'])
            if not record:
                raise ValueError('Execution revision is unavailable')
            latest = self._load(conn,ref['id'],data['scope'],'record')
            if latest['revision'] != record['revision']:
                raise ValueError('Execution evidence is stale')
            generators.add(record['source_agent_profile'])
            for evidence in record['evidence_refs']:
                source = self._source(conn,evidence,data['scope'],current=True)
                reports.append(evidence)
        dataset_source = self._source(conn,data['dataset_ref'],data['scope'],current=True)
        if dataset_source['author'] in generators:
            raise PermissionError('Dataset must be independent of the producing agent')
        if data['evaluator'] in generators:
            raise PermissionError('Producing agents cannot evaluate their own executions')
        return self._report(dataset_source['content']), reports

    def evaluate(self, **data):
        data = EvaluationRequest.model_validate(data).model_dump(mode='json')
        self._access(data['scope'],data['evaluator'])
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            strategy = None
            if data['strategy_id']:
                strategy = self._load(conn,data['strategy_id'],data['scope'],'strategy')
                if not strategy or strategy['revision'] != data['expected_revision'] or strategy['status'] != 'PROPOSED':
                    raise EvolutionConflict('Evaluation requires the exact proposed strategy revision')
                if strategy['author'] == data['evaluator']:
                    raise PermissionError('Strategy authors cannot evaluate their own strategy')
            results, refs, uncertainty = [], [], []
            environment, benchmark_signature = None, None
            benchmark={'outcome':'NOT_ESTABLISHED_INITIAL_BASELINE','deltas':[]}
            outcome, correctness, compliance = 'INCONCLUSIVE', False, False
            try:
                dataset, refs = self._evaluation_inputs(conn,data)
                environment = dataset.get('environment')
                if not isinstance(environment,dict) or not all(k in environment for k in ('model','budget_tokens','runtime')):
                    raise ValueError('Dataset must pin model, budget and runtime environment')
                if environment['runtime'] not in ('simulated','isolated'):
                    raise ValueError('Evaluation only accepts isolated or simulated reports')
                if not isinstance(environment['model'],str) or not environment['model'].strip() or type(environment['budget_tokens']) is not int or environment['budget_tokens']<=0:
                    raise ValueError('Model and budget pins must be explicit and valid')
                cases = dataset.get('cases')
                if not isinstance(cases,list) or not 1<=len(cases)<=100:
                    raise ValueError('Dataset must provide bounded held-out cases')
                supplied = {(r['id'],r['revision']) for r in data['record_refs']}
                case_refs = {(r.get('record_id'),r.get('record_revision')) for r in cases}
                if case_refs != supplied or len(cases) != len(supplied):
                    raise ValueError('Held-out cases must cover every supplied execution exactly once')
                benchmark_ref = dataset.get('benchmark_ref')
                if strategy and strategy['baseline_id']:
                    if not isinstance(benchmark_ref,dict):
                        benchmark={'outcome':'INCOMPARABLE','deltas':[]}
                        raise ValueError('Comparative evaluation requires a shared exact held-out benchmark source')
                    self._source(conn,benchmark_ref,data['scope'],current=True)
                benchmark_signature=digest({'benchmark_ref':benchmark_ref,'environment':environment,
                    'criteria':[case.get('criteria') for case in cases]})
                correctness_cases, compliance_cases = [], []
                for case in cases:
                    evidence = case.get('evidence')
                    if evidence not in refs:
                        raise ValueError('Case evidence is not linked to an execution')
                    record = self._load(conn,case['record_id'],data['scope'],'record',case['record_revision'])
                    if evidence not in record['evidence_refs']:
                        raise ValueError('Case evidence belongs to another execution')
                    source = self._source(conn,evidence,data['scope'],current=True)
                    if source['author'] == record['source_agent_profile']:
                        raise PermissionError('Self-reported output cannot independently prove correctness')
                    observation = self._report(source['content'])
                    criteria = case.get('criteria')
                    if not isinstance(criteria,list) or not 1<=len(criteria)<=30:
                        raise ValueError('Each case requires explicit bounded criteria')
                    case_correct, case_compliant = [], []
                    for criterion in criteria:
                        field = criterion.get('field')
                        op = criterion.get('operator')
                        expected = criterion.get('expected')
                        if field not in ('correctness','policy_compliance','latency_ms','context_tokens','ai_tokens','retries','human_corrections','success_rate','failure_rate','retrieval_coverage') or op not in ('eq','le','ge'):
                            raise ValueError('Criterion is unsupported')
                        actual = observation.get(field)
                        if actual is None:
                            raise ValueError('Required measurement is unavailable')
                        if op=='eq':
                            passed = type(actual)==type(expected) and actual==expected
                        else:
                            if type(actual) not in (int,float) or type(expected) not in (int,float) or not math.isfinite(actual) or not math.isfinite(expected):
                                raise ValueError('Ordered criteria require finite numerical measurements')
                            passed = actual<=expected if op=='le' else actual>=expected
                        if field in ('correctness','policy_compliance'):
                            if op!='eq' or expected is not True:
                                raise ValueError('Correctness and policy gates must require true')
                            (case_correct if field=='correctness' else case_compliant).append(passed and actual is True)
                        results.append({'record_id':case['record_id'],'field':field,'operator':op,'expected':expected,'actual':actual,'passed':passed,'evidence':evidence})
                    correctness_cases.append(bool(case_correct) and all(case_correct))
                    compliance_cases.append(bool(case_compliant) and all(case_compliant))
                correctness = all(correctness_cases)
                compliance = all(compliance_cases)
                if not correctness or not compliance:
                    outcome = 'FAIL' if any(not r['passed'] for r in results) else 'INCONCLUSIVE'
                    if outcome=='INCONCLUSIVE':
                        uncertainty.append('Every case requires correctness and policy compliance hard gates')
                else:
                    outcome = 'PASS' if all(r['passed'] for r in results) else 'FAIL'
                if strategy and strategy['baseline_id'] and outcome=='PASS':
                    baseline=self._load(conn,strategy['baseline_id'],data['scope'],'strategy')
                    previous=self._fresh_evaluation(conn,baseline) if baseline else None
                    if not previous or previous.get('benchmark_signature')!=benchmark_signature:
                        benchmark={'outcome':'INCOMPARABLE','deltas':[]}
                        outcome='INCONCLUSIVE'
                        uncertainty.append('Baseline and candidate require identical model, data criteria, budget and environment pins')
                    else:
                        lower={'latency_ms','context_tokens','ai_tokens','retries','human_corrections','failure_rate'}
                        higher={'success_rate','retrieval_coverage'}
                        deltas=[]
                        for before,after in zip(previous['results'],results):
                            if after['field'] in lower|higher:
                                if type(before['actual']) not in (int,float) or type(after['actual']) not in (int,float):
                                    raise ValueError('Comparative benchmark requires finite numeric measurements')
                                delta=after['actual']-before['actual']
                                deltas.append({'field':after['field'],'baseline':before['actual'],'candidate':after['actual'],
                                               'delta':delta,'improved':delta<0 if after['field'] in lower else delta>0,
                                               'regressed':delta>0 if after['field'] in lower else delta<0})
                        if any(x['regressed'] for x in deltas):
                            benchmark={'outcome':'REGRESSED','deltas':deltas}
                            outcome='FAIL'
                        elif any(x['improved'] for x in deltas):
                            benchmark={'outcome':'IMPROVED','deltas':deltas}
                        else:
                            benchmark={'outcome':'NO_MEASURED_IMPROVEMENT','deltas':deltas}
                            outcome='INCONCLUSIVE'
                            uncertainty.append('Passing criteria alone do not prove improvement over the baseline')
            except (ValueError, TypeError, KeyError) as exc:
                uncertainty.append(str(exc))
            # Permission violations deliberately propagate rather than being recorded as inconclusive.
            item = {**data,'id':'evaluation-'+uuid.uuid4().hex,'outcome':outcome,'results':results,
                'correctness_gate':correctness,'policy_gate':compliance,'evidence_refs':refs,'uncertainty':uncertainty,
                'environment':environment,'benchmark_signature':benchmark_signature,'benchmark':benchmark,
                'verification_boundary':'SUPPLIED_REPORT_COMPARISON',
                'reproducibility_hash':digest({'dataset_ref':data['dataset_ref'],'record_refs':data['record_refs'],'results':results,'evaluator_version':data['evaluator_version']})}
            if strategy:
                item['strategy_configuration_hash']=strategy['configuration_hash']
            item = self._save(conn,'evaluation',item)
            if strategy:
                strategy.update(status={'PASS':'EVALUATED','FAIL':'REJECTED','INCONCLUSIVE':'INCONCLUSIVE'}[outcome],evaluation_id=item['id'])
                self._save(conn,'strategy',strategy,data['expected_revision'])
            conn.execute('COMMIT')
            return item

    def _fresh_evaluation(self, conn, strategy):
        evaluation = self._load(conn,strategy['evaluation_id'],strategy['scope'],'evaluation') if strategy['evaluation_id'] else None
        if not evaluation or evaluation['outcome']!='PASS' or evaluation.get('strategy_configuration_hash')!=strategy['configuration_hash']:
            raise ValueError('A passing independent evaluation for this configuration is required')
        self._evaluation_inputs(conn,evaluation)
        for ref in strategy['evidence_refs']:
            self._source(conn,ref,strategy['scope'],current=True)
        return evaluation

    def _strategy_transition(self, conn, id, scope, expected_revision, status):
        value = self._load(conn,id,scope,'strategy')
        if not value or value['revision']!=expected_revision or value['status'] not in status:
            raise EvolutionConflict('Strategy state or revision changed')
        return value

    def request_approval(self,id,*,scope,expected_revision):
        self._access(scope)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value=self._strategy_transition(conn,id,scope,expected_revision,{'EVALUATED'})
            self._fresh_evaluation(conn,value)
            value['status']='APPROVAL_PENDING'
            value=self._save(conn,'strategy',value,expected_revision)
            conn.execute('COMMIT')
            return value

    def approve(self,id,*,scope,expected_revision,reason):
        self._access(scope,owner=True)
        if not isinstance(reason,str) or not reason.strip() or len(reason)>4000:
            raise ValueError('An owner approval reason is required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value=self._strategy_transition(conn,id,scope,expected_revision,{'APPROVAL_PENDING'})
            self._fresh_evaluation(conn,value)
            approval={'id':'approval-'+uuid.uuid4().hex,'scope':scope,'strategy_id':id,'strategy_revision':expected_revision,
                      'configuration_hash':value['configuration_hash'],'reason':reason,'actor':'local-owner','recorded_at':utc_now().isoformat(),'consumed_at':None}
            conn.execute('INSERT INTO evolution_approvals VALUES (?,?,?,?,?,?,?,?,NULL)',tuple(approval[k] for k in ('id','scope','strategy_id','strategy_revision','configuration_hash','reason','actor','recorded_at')))
            conn.execute('COMMIT')
            return approval

    def activate(self,id,*,scope,expected_revision,approval_id):
        self._access(scope,owner=True)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value=self._strategy_transition(conn,id,scope,expected_revision,{'APPROVAL_PENDING'})
            self._fresh_evaluation(conn,value)
            approval=conn.execute('SELECT * FROM evolution_approvals WHERE id=? AND scope=?',(approval_id,scope)).fetchone()
            if not approval or approval['consumed_at'] or approval['strategy_id']!=id or approval['strategy_revision']!=expected_revision or approval['configuration_hash']!=value['configuration_hash']:
                raise ValueError('An unused local-owner approval for this exact revision is required')
            active=conn.execute('SELECT * FROM evolution_active WHERE scope=? AND strategy_key=?',(scope,value['strategy_key'])).fetchone()
            baseline=active['strategy_id'] if active else None
            if baseline!=value['baseline_id']:
                raise EvolutionConflict('Active baseline changed since evaluation')
            if active:
                old=self._load(conn,baseline,scope,'strategy')
                if old['revision']!=value['baseline_revision']:
                    raise EvolutionConflict('Baseline revision changed since proposal')
                old['status']='SUPERSEDED'
                self._save(conn,'strategy',old,old['revision'])
            value.update(status='ACTIVE',approval_basis=approval_id,active_generation=(active['generation']+1 if active else 1))
            value=self._save(conn,'strategy',value,expected_revision)
            conn.execute('INSERT INTO evolution_active VALUES (?,?,?,?) ON CONFLICT(scope,strategy_key) DO UPDATE SET strategy_id=excluded.strategy_id,generation=excluded.generation',
                         (scope,value['strategy_key'],id,value['active_generation']))
            conn.execute('UPDATE evolution_approvals SET consumed_at=? WHERE id=?',(utc_now().isoformat(),approval_id))
            conn.execute('COMMIT')
            return value

    def rollback(self,id,*,scope,expected_revision,reason):
        self._access(scope,owner=True)
        if not isinstance(reason,str) or not reason.strip() or len(reason)>4000:
            raise ValueError('Rollback requires a recorded reason')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value=self._strategy_transition(conn,id,scope,expected_revision,{'ACTIVE'})
            active=conn.execute('SELECT * FROM evolution_active WHERE scope=? AND strategy_key=?',(scope,value['strategy_key'])).fetchone()
            if not active or active['strategy_id']!=id:
                raise EvolutionConflict('Only the current active strategy can be rolled back')
            baseline = self._load(conn,value['baseline_id'],scope,'strategy') if value['baseline_id'] else None
            if baseline:
                self._fresh_evaluation(conn,baseline)
                baseline.update(status='ACTIVE',active_generation=active['generation']+1)
                self._save(conn,'strategy',baseline,baseline['revision'])
                conn.execute('UPDATE evolution_active SET strategy_id=?,generation=generation+1 WHERE scope=? AND strategy_key=?',(baseline['id'],scope,value['strategy_key']))
            else:
                conn.execute('DELETE FROM evolution_active WHERE scope=? AND strategy_key=?',(scope,value['strategy_key']))
            value.update(status='ROLLED_BACK',rollback_reason=reason,rollback_target=value['baseline_id'])
            value=self._save(conn,'strategy',value,expected_revision)
            conn.execute('COMMIT')
            return value

    def active_configuration(self,scope,strategy_key):
        self._access(scope)
        with self.storage.session() as conn:
            row=conn.execute('SELECT strategy_id FROM evolution_active WHERE scope=? AND strategy_key=?',(scope,strategy_key)).fetchone()
            if not row:
                return None
            value=self._load(conn,row['strategy_id'],scope,'strategy')
            if not value or value['status']!='ACTIVE':
                return None
            try:
                self._fresh_evaluation(conn,value)
                self._configuration(strategy_key,value['configuration'])
            except (ValueError,PermissionError):
                return None
            return value['configuration']

    def distill(self,**data):
        data=DistillRequest.model_validate(data).model_dump(mode='json')
        self._access(data['scope'],data['author'])
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            ev=self._load(conn,data['evaluation_id'],data['scope'],'evaluation')
            if not ev or ev['outcome']=='INCONCLUSIVE':
                raise ValueError('Distillation requires usable independent evaluation evidence')
            self._evaluation_inputs(conn,ev)
            identity=digest(data)
            old=self._load(conn,'experience-'+identity[:32],data['scope'],'experience')
            if old:
                conn.execute('COMMIT')
                return old
            value=self._save(conn,'experience',{**data,'id':'experience-'+identity[:32],'authority':'INFERRED','maturity':'L2',
                'evaluation_outcome':ev['outcome'],'evidence_refs':ev['evidence_refs'],'dataset_ref':ev['dataset_ref'],
                'verification':'EVIDENCE_BACKED_CANDIDATE','raw_trace_in_context':False})
            conn.execute('COMMIT')
            return value

    def schedule(self,**data):
        data=ScheduleRequest.model_validate(data).model_dump(mode='json')
        self._access(data['scope'])
        for field,value in data['health'].items():
            if field not in ('confidence','conflict','freshness','retrieval_quality') or not math.isfinite(value) or not 0<=value<=1:
                raise ValueError('Health signals must be bounded observed values')
        priority=sum(1-v if k in ('confidence','freshness','retrieval_quality') else v for k,v in data['health'].items())
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            observations=[]
            for ref in data['record_refs']:
                record=self._load(conn,ref['id'],data['scope'],'record',ref['revision'])
                latest=self._load(conn,ref['id'],data['scope'],'record')
                if not record or latest['revision']!=ref['revision']:
                    raise ValueError('Trigger requires scoped existing execution evidence')
                for evidence in record['evidence_refs']:
                    source=self._source(conn,evidence,data['scope'],current=True)
                    try:
                        observation=self._report(source['content'])
                        if isinstance(observation,dict):
                            observations.append(observation)
                    except ValueError:
                        pass
            for field,value in data['health'].items():
                if not any(type(item.get(field)) in (int,float) and item[field]==value for item in observations):
                    raise ValueError('Priority health signals require matching scoped source measurements')
            candidates=conn.execute("SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind='job' AND is_current=1",(data['scope'],))
            existing=None
            refs=set((r['id'],r['revision']) for r in data['record_refs'])
            for row in candidates:
                job=json.loads(row['payload_json'])
                if job['target_key']==data['target_key'] and job['status'] in ('DEFERRED','QUEUED'):
                    existing=job
                    refs.update((r['id'],r['revision']) for r in job['record_refs'])
                    break
            if len(refs)>100:
                raise ValueError('Debounced evidence batch exceeds 100 records')
            reserved=sum(json.loads(row['payload_json'])['estimated_tokens'] for row in conn.execute("SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind='job' AND is_current=1",(data['scope'],))
                         if json.loads(row['payload_json'])['status'] in ('QUEUED','RUNNING') and (not existing or json.loads(row['payload_json'])['id']!=existing['id']))
            charges={}
            for row in conn.execute("SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind='job'",(data['scope'],)):
                historical=json.loads(row['payload_json'])
                if historical['status'] in ('SUCCEEDED','DEAD_LETTER','DEFERRED') and historical.get('attempts',0)>0:
                    charges[(historical['id'],historical['attempts'])]=historical['actual_tokens']
            spent=sum(charges.values())
            data['estimated_tokens']=max(data['estimated_tokens'],existing['estimated_tokens'] if existing else 0)
            data['record_refs']=[{'id':id,'revision':rev} for id,rev in sorted(refs)]
            value=self._save(conn,'job',{**data,'id':existing['id'] if existing else 'job-'+uuid.uuid4().hex,
                'status':'QUEUED' if spent+reserved+data['estimated_tokens']<=data['token_budget'] else 'DEFERRED',
                'priority':max(priority,existing['priority'] if existing else 0),'actual_tokens':0,
                'coverage':'SOURCE_EVENTS_ONLY','attempts':existing['attempts'] if existing else 0},existing['revision'] if existing else None)
            conn.execute('COMMIT')
            return value

    def claim_job(self,id,*,scope,expected_revision):
        self._access(scope)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value=self._load(conn,id,scope,'job')
            if not value or value['status']!='QUEUED' or value['revision']!=expected_revision:
                raise EvolutionConflict('Job is not available at expected revision')
            value.update(status='RUNNING',attempts=value['attempts']+1)
            value=self._save(conn,'job',value,expected_revision)
            conn.execute('COMMIT')
            return value

    def finish_job(self,id,*,scope,expected_revision,actual_tokens,outcome,reason='',evaluation_id=None):
        self._access(scope)
        if type(actual_tokens) is not int or not 0<=actual_tokens<=1000000 or outcome not in ('SUCCEEDED','FAILED','INCONCLUSIVE'):
            raise ValueError('A bounded measured token count and explicit outcome are required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value=self._load(conn,id,scope,'job')
            if not value or value['status']!='RUNNING' or value['revision']!=expected_revision:
                raise EvolutionConflict('Only a running exact job revision can be completed')
            if outcome=='SUCCEEDED':
                ev=self._load(conn,evaluation_id,scope,'evaluation') if evaluation_id else None
                if not ev or ev['outcome']!='PASS':
                    raise ValueError('Success requires a passing independent evaluation')
                self._evaluation_inputs(conn,ev)
                if sorted(ev['record_refs'],key=encoded)!=sorted(value['record_refs'],key=encoded):
                    raise ValueError('Evaluation must cover the exact debounced job evidence')
            value.update(status={'SUCCEEDED':'SUCCEEDED','FAILED':'DEAD_LETTER','INCONCLUSIVE':'DEFERRED'}[outcome],
                         actual_tokens=actual_tokens,reason=reason,evaluation_id=evaluation_id,
                         budget_exceeded=actual_tokens>value['estimated_tokens'])
            value=self._save(conn,'job',value,expected_revision)
            conn.execute('COMMIT')
            return value

    def job_budget(self,scope):
        self._access(scope)
        jobs=self.list_items(scope,'job',200)
        # Charge completed attempts from history, not only current snapshots.
        with self.storage.session() as conn:
            rows=conn.execute("SELECT payload_json FROM evolution_revisions WHERE scope=? AND kind='job'",(scope,))
            charges={}
            for row in rows:
                job=json.loads(row['payload_json'])
                if job['status'] in ('SUCCEEDED','DEAD_LETTER','DEFERRED') and job.get('attempts',0)>0:
                    charges[(job['id'],job['attempts'])]=job['actual_tokens']
        return {'scope':scope,'actual_tokens':sum(charges.values()),
                'reserved_tokens':sum(j['estimated_tokens'] for j in jobs if j['status'] in ('QUEUED','RUNNING')),
                'accounting':'OBSERVED_JOB_ATTEMPTS','cost_currency':None,'cost_amount':None}
