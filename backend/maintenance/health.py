"""Measured knowledge debt and bounded maintenance plans; no model or deletion loop."""
from datetime import datetime, timedelta
import hashlib
import json
from backend.auth.policy import current_access
from backend.lineage.manager import guard, stable
from backend.schema.models import GraphNode, NodeType, utc_now
from backend.maintenance.backlog import MemoryBacklog, BacklogType, BacklogPriority


class KnowledgeHealth:
    def __init__(self, storage):
        self.storage = storage

    def report(self, *, scope='default', limit=1000):
        guard(scope)
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError('Health scan budget must be 1..5000')
        metrics = {'node_count': 0, 'unverified_count': 0, 'expired_count': 0, 'missing_source_count': 0,
            'possible_duplicate_count': 0, 'open_conflict_count': 0, 'accuracy': None, 'retrieval_quality': None}
        issues, seen, revisions, now = [], {}, [], utc_now()
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            guard(scope, conn)
            rows = conn.execute('SELECT data_json FROM nodes WHERE scope=? AND is_active=1 ORDER BY id LIMIT ?', (scope, limit + 1)).fetchall()
            for row in rows[:limit]:
                node = GraphNode.model_validate_json(row['data_json'])
                metrics['node_count'] += 1
                revisions.append((node.id, node.version))
                reasons, actions = [], []
                if node.node_type != NodeType.SOURCE and node.verification_props.get('status') not in ('VERIFIED', 'USER_VERIFIED'):
                    metrics['unverified_count'] += 1
                    reasons.append('unverified')
                    actions.append(BacklogType.VERIFY_CLAIM.value)
                expired = node.temporal_props.valid_to is not None and node.temporal_props.valid_to <= now
                if expired:
                    metrics['expired_count'] += 1
                    reasons.append('expired_validity')
                    actions.insert(0, BacklogType.REFRESH_STALE.value)
                if node.provenance:
                    ref = node.provenance
                    note = conn.execute('SELECT scope,content_hash FROM notes WHERE id=? AND revision=?', (ref.source_id, ref.source_revision)).fetchone()
                    if not note or note['scope'] != scope or note['content_hash'] != ref.content_hash:
                        metrics['missing_source_count'] += 1
                        reasons.append('missing_exact_source')
                if node.node_type == NodeType.CONFLICT and node.core_props.get('status', 'OPEN') not in ('RESOLVED', 'SUPERSEDED', 'IGNORED'):
                    metrics['open_conflict_count'] += 1
                    reasons.append('open_conflict')
                    actions.insert(0, BacklogType.RESOLVE_CONFLICT.value)
                signature = hashlib.sha256(stable([node.node_type.value, node.content]).encode('utf-8')).hexdigest()
                if signature in seen:
                    metrics['possible_duplicate_count'] += 1
                    reasons.append('same_type_and_exact_content; independent_provenance_not_merged')
                    actions.append(BacklogType.RESOLVE_ENTITY.value if node.node_type == NodeType.ENTITY else BacklogType.CONSOLIDATE_MEMORY.value)
                else:
                    seen[signature] = node.id
                if reasons:
                    issues.append({'id': node.id, 'version': node.version, 'reasons': reasons, 'suggested_action': actions[0] if actions else BacklogType.VERIFY_CLAIM.value})
            guard(scope, conn)
            conn.execute('COMMIT')
        count = metrics['node_count']
        metrics['evidence_coverage'] = None if not count else (count - metrics['missing_source_count']) / count
        # Evidence coverage counts nodes with a valid exact binding, not merely
        # absence of a broken binding; missing provenance contributes no coverage.
        with self.storage.session() as conn:
            bound = 0
            for nid, _ in revisions:
                row = conn.execute('SELECT data_json FROM nodes WHERE id=? AND scope=?', (nid, scope)).fetchone()
                node = GraphNode.model_validate_json(row['data_json']) if row else None
                if node and node.provenance:
                    ref = node.provenance
                    if conn.execute('SELECT 1 FROM notes WHERE id=? AND revision=? AND scope=? AND content_hash=?', (ref.source_id, ref.source_revision, scope, ref.content_hash)).fetchone():
                        bound += 1
            metrics['evidence_coverage'] = bound / count if count else None
        report = {'scope': scope, 'schema_version': 'health-v1', 'limit': limit, 'metrics': metrics, 'issues': issues,
            'dependencies': revisions, 'coverage': 'partial_scope' if len(rows) > limit else 'complete_scope',
            'uncertainties': ['accuracy_requires_independent_ground_truth', 'retrieval_quality_requires_labeled_queries',
                'exact_text_duplicates_are_review_leads', 'freshness_uses_declared_validity_not_truth']}
        report['report_hash'] = hashlib.sha256(stable(report).encode('utf-8')).hexdigest()
        return report

    def queue_review(self, *, scope, report_hash, selected_ids, limit=1000):
        guard(scope, write=True)
        if current_access.get() is not None:
            raise PermissionError('Review selection must be confirmed by local owner')
        if not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 100 or len(set(selected_ids)) != len(selected_ids):
            raise ValueError('Select 1..100 distinct issue IDs')
        report = self.report(scope=scope, limit=limit)
        if report['report_hash'] != report_hash:
            raise ValueError('Health report revision conflict; preview the current report')
        issues = {issue['id']: issue for issue in report['issues']}
        if any(nid not in issues for nid in selected_ids):
            raise ValueError('Selected item is outside this report')
        backlog, items = MemoryBacklog(self.storage), []
        # Each item is an independent atomic maintenance capture, not a batch
        # canonical update. Replays are deduplicated by the existing Backlog.
        for nid in selected_ids:
            issue = issues[nid]
            current = self.storage.get_node(nid)
            if not current or current.scope != scope or current.version != issue['version']:
                raise ValueError('Health issue revision conflict before capture')
            item = backlog.capture_item(BacklogType(issue['suggested_action']), scope,
                targets=[{'id': nid, 'revision': issue['version']}], reason='Health review: ' + '; '.join(issue['reasons']),
                created_by='local-owner', priority=BacklogPriority.NORMAL, priority_reason='Measured knowledge debt')
            items.append({'id': item.id, 'target_id': nid, 'status': item.status.value})
        return {'items': items, 'canonical_changes': 0}

    def idle_plan(self, *, scope='default', max_jobs=10, token_budget=5000):
        guard(scope)
        if type(max_jobs) is not int or not 1 <= max_jobs <= 100 or type(token_budget) is not int or not 0 <= token_budget <= 100_000:
            raise ValueError('Invalid idle execution budget')
        with self.storage.session() as conn:
            guard(scope, conn)
            rows = conn.execute("SELECT * FROM system_memory_backlog WHERE scope=? AND status IN ('OPEN','READY') AND approval_status IN ('NOT_REQUIRED','APPROVED') AND blocker IS NULL ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,created_at,id LIMIT ?", (scope, max_jobs)).fetchall()
            jobs, remaining = [], token_budget
            for row in rows:
                dependencies = json.loads(row['dependencies_json'])
                if any(not (item := conn.execute('SELECT status,scope FROM system_memory_backlog WHERE id=?', (ref,)).fetchone()) or item['scope'] != scope or item['status'] != 'RESOLVED' for ref in dependencies):
                    continue
                estimate = 500
                if remaining < estimate:
                    break
                jobs.append({'backlog_id': row['id'], 'revision': row['revision'], 'type': row['type'],
                    'targets': json.loads(row['targets_json']), 'reserved_estimate': estimate, 'counting_mode': 'estimated',
                    'requires': 'exact evidence + explicit change approval + completion verification'})
                remaining -= estimate
            return {'jobs': jobs, 'remaining_estimate': remaining, 'execution': 'external_agent_required',
                'automatic_canonical_updates': False, 'stop_reason': 'budget' if remaining < 500 else 'eligible_queue_exhausted'}
