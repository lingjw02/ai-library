"""Scoped keyword retrieval, bounded graph expansion and evidence compilation.

Current-time canonical reads, with optional externally supplied query vectors.
Context is regenerated to avoid stale time windows.
"""
import json
import sqlite3
import time
from backend.core.retrieval import consistency
from backend.cache.cache_manager import CacheManager
from backend.context.context_compiler import ContextCompiler
from backend.core.evidence.evidence_packet import EvidencePacketBuilder
from backend.core.retrieval.keyword_index import KeywordIndex, currently_valid
from backend.core.retrieval.vector_index import VectorIndex
from backend.core.retrieval.rrf import ReciprocalRankFusion
from backend.graph.traversal.bounded_traversal import BoundedGraphTraversal
from backend.routing.router import SourceRouter, QueryRouter, SearchTargetPlane, QueryIntent
from backend.schema.models import GraphNode, NodeType, MemoryAuthority, MemoryMaturity, MemoryDepth, utc_now


class RetrievalCoordinator:
    def __init__(self, storage, notes_mgr, cache_mgr=None, strategy_manager=None):
        self.storage = storage
        self.notes_mgr = notes_mgr
        self.cache_mgr = cache_mgr or CacheManager()
        self.traversal = BoundedGraphTraversal(storage)
        self.keyword_index = KeywordIndex(storage)
        self.vector_index = VectorIndex(storage)
        self.rrf = ReciprocalRankFusion()
        self.strategy_manager = strategy_manager

    def _configuration(self, scope):
        if self.strategy_manager is None:
            return {}
        return {key: self.strategy_manager.active_configuration(scope, key) or {}
                for key in ('retrieval_ranking', 'context_config')}

    def _read_policy(self, read_mode, minimum_revision, as_of, recorded_as_of, deadline_ms):
        if read_mode not in ('best_available', 'at_least_revision', 'historical_as_of'):
            raise ValueError('Unsupported read_mode')
        if minimum_revision is not None and (type(minimum_revision) is not int or minimum_revision < 0):
            raise ValueError('minimum_revision must be a nonnegative commit revision')
        if read_mode == 'at_least_revision' and minimum_revision is None:
            raise ValueError('at_least_revision requires minimum_revision')
        if read_mode == 'historical_as_of' and as_of is None:
            raise ValueError('historical_as_of requires as_of')
        if read_mode != 'historical_as_of' and (as_of is not None or recorded_as_of is not None):
            raise ValueError('as_of/recorded_as_of require historical_as_of')
        if not 1 <= deadline_ms <= 10000:
            raise ValueError('deadline_ms must be 1–10000')
        return {'read_mode':read_mode, 'minimum_revision':minimum_revision,
                'as_of':consistency.instant(as_of), 'recorded_as_of':consistency.instant(recorded_as_of)}

    def search(self, query, scope='default', plane='BOTH', limit=20, node_type=None, embedding_query=None, *,
               allowed_objects=None, read_mode='best_available', minimum_revision=None, as_of=None,
               recorded_as_of=None, authorization_fingerprint=None, policy_revision='1', deadline_ms=500):
        if plane not in ('MEMORY', 'NOTES', 'BOTH') or not query.strip() or len(query) > 2000 or not 1 <= limit <= 100:
            raise ValueError('Invalid query, plane or limit')
        policy = self._read_policy(read_mode, minimum_revision, as_of, recorded_as_of, deadline_ms)
        start = time.monotonic()
        configuration = self._configuration(scope)
        material = dict(query=query.strip(), scope=scope, plane=plane, limit=limit, node_type=node_type,
                        embedding_query=embedding_query, allowed_objects=sorted(allowed_objects) if allowed_objects is not None else None,
                        authorization=authorization_fingerprint or f'scope:{scope}', policy_revision=policy_revision,
                        policy=policy, deadline_ms=deadline_ms, router_version='2', ranking_version='rrf-1', configuration=configuration)
        key = self.cache_mgr.key(material)
        epoch = self.cache_mgr.generation(scope)
        snap = consistency.snapshot(self.storage, scope)
        cached = self.cache_mgr.get_retrieval(key, manifest=snap)
        result = cached
        if result is None:
            if read_mode == 'historical_as_of':
                if embedding_query is not None:
                    raise ValueError('Historical vector retrieval is unavailable; use retained canonical keyword retrieval')
                result = consistency.historical_search(self.storage, query, scope, plane, limit, node_type,
                                                      allowed_objects, as_of, recorded_as_of)
            else:
                result = self._search_current(query, scope, plane, limit, node_type, embedding_query,
                                              allowed_objects=allowed_objects, configuration=configuration)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            valid = [item for item in result['items'] if consistency.eligible(conn, item, scope,
                     historical=read_mode == 'historical_as_of') and
                     (allowed_objects is None or (item['plane'], item['id']) in allowed_objects)]
        if len(valid) != len(result['items']):
            result['canonical_changed'] = True
            result['items'] = valid
            result['total'] = len(valid)
        final_snap = consistency.snapshot(self.storage, scope)
        changed = final_snap != snap
        minimum_satisfied = minimum_revision is None or final_snap['canonical_revision'] >= minimum_revision
        result['consistency'] = {'mode':read_mode, 'minimum_revision':minimum_revision,
            'minimum_satisfied':minimum_satisfied, 'canonical_revision':final_snap['canonical_revision'],
            'historical_snapshot':read_mode == 'historical_as_of',
            'fallback':'retained_canonical' if read_mode == 'historical_as_of' else ('canonical' if result['mode'] == 'canonical_fallback' else None),
            'degraded':bool(result.get('canonical_changed') or changed or not minimum_satisfied or result.get('warnings')),
            'history_coverage':result.get('history_coverage'), 'recorded_as_of':result.get('recorded_as_of'),
            'index_revisions':final_snap['indexes'], 'wait_ms':0, 'snapshot_changed':changed}
        result['dependency_manifest'] = final_snap
        result['elapsed_ms'] = round((time.monotonic() - start) * 1000, 3)
        if changed:
            result['items'], result['total'], result['has_more'] = [], 0, True
            result['warnings'] = [*result.get('warnings', []), 'concurrent_write_retry_required']
        if cached is None and not changed:
            self.cache_mgr.put_retrieval(key, result, scope, manifest=snap, expected_generation=epoch)
        return result

    def _search_current(self, query, scope='default', plane='BOTH', limit=20, node_type=None, embedding_query=None, *, allowed_objects=None, configuration=None):
        result = self.keyword_index.search(query, scope, plane, 100 if embedding_query else limit, node_type,
                                           allowed_objects=allowed_objects)
        if embedding_query is None:
            return result
        if not isinstance(embedding_query, dict) or set(embedding_query) != {'space', 'vector'}:
            raise ValueError('embedding_query must contain space and vector')
        try:
            vectors = self.vector_index.search(
                embedding_query['vector'],
                space=embedding_query['space'],
                scope=scope,
                plane=plane,
                limit=100,
                allowed_objects=allowed_objects,
            )
        except sqlite3.DatabaseError as exc:
            result['vector'] = {'availability': 'unavailable', 'partial': True, 'reason': type(exc).__name__}
            result['items'] = result['items'][:limit]
            result['has_more'] = result['total'] > limit
            return result
        key = lambda item: item['plane'] + ':' + item['id']
        vector_items = [item for item in vectors['items'] if not node_type or item.get('node_type') == node_type]
        pool = {key(item): item for item in [*result['items'], *vector_items]}
        rrf_k = (configuration or {}).get('retrieval_ranking', {}).get('rrf_k', 60)
        fused = ReciprocalRankFusion(k=rrf_k).fuse([[key(x) for x in result['items']], [key(x) for x in vector_items]], top_k=len(pool))
        ordered = [pool[identity] for identity, _ in fused]
        # Exact canonical IDs retain the entity fast path ahead of fusion.
        ordered.sort(key=lambda item: item['id'] != query.strip())
        valid, changed = [], False
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            for item in ordered:
                current = self.vector_index._canonical(conn, item['plane'], item['id'], scope, utc_now())
                if not current or current['revision'] != item.get('revision', item.get('version')):
                    changed = True
                    continue
                valid.append(current)
        return {**result, 'items': valid[:limit], 'mode': 'hybrid_rrf',
                'total': len(valid), 'total_is_candidate_count': True,
                'has_more': result['has_more'] or bool(vectors['has_more']) or len(valid) > limit,
                'canonical_changed': changed,
                'vector': {k: v for k, v in vectors.items() if k != 'items'}}

    def retrieve(self, query, scope='default', user_specified_plane=None, token_budget=2048, embedding_query=None, *,
                 allowed_objects=None, lean=False, cache_prefix_layout=True, read_mode='best_available',
                 minimum_revision=None, as_of=None, recorded_as_of=None, authorization_fingerprint=None,
                 policy_revision='1', deadline_ms=500, max_depth=None):
        policy = self._read_policy(read_mode, minimum_revision, as_of, recorded_as_of, deadline_ms)
        if max_depth is not None and (type(max_depth) is not int or not 0 <= max_depth <= 3):
            raise ValueError('max_depth must be 0–3')
        if token_budget < 1:
            raise ValueError('token_budget must be positive')
        config = self._configuration(scope)
        token_budget = min(token_budget, config.get('context_config', {}).get('max_tokens', token_budget))
        key = self.cache_mgr.key(dict(query=query.strip(), scope=scope, plane=user_specified_plane, token_budget=token_budget,
            embedding=embedding_query, authorization=authorization_fingerprint or f'scope:{scope}', policy_revision=policy_revision,
            policy=policy, deadline_ms=deadline_ms, max_depth=max_depth, compiler_version='2', serializer_version='2',
            stable_revision=ContextCompiler.DEFAULT_STABLE_PREFIX, configuration=config, lean=lean, cache_prefix_layout=cache_prefix_layout,
            allowed_objects=sorted(allowed_objects) if allowed_objects is not None else None))
        started = time.monotonic()
        options = dict(allowed_objects=allowed_objects, read_mode=read_mode, minimum_revision=minimum_revision,
                       as_of=as_of, recorded_as_of=recorded_as_of, authorization_fingerprint=authorization_fingerprint,
                       policy_revision=policy_revision, deadline_ms=deadline_ms)
        for attempt in range(2):
            snap = consistency.snapshot(self.storage, scope)
            epoch = self.cache_mgr.generation(scope)
            cached = self.cache_mgr.get_context_artifact(key, manifest=snap)
            if cached is not None and consistency.snapshot(self.storage, scope) == snap:
                cached.retrieval['cache'] = {'layer':'context', 'hit':True}
                return cached
            artifact = self._retrieve_once(query, scope, user_specified_plane, token_budget, embedding_query,
                                           max_depth=max_depth, configuration=config, lean=lean,
                                           cache_prefix_layout=cache_prefix_layout, **options)
            final_snap = consistency.snapshot(self.storage, scope)
            if snap == final_snap:
                artifact.retrieval['dependency_manifest'] = final_snap
                artifact.retrieval['cache'] = {'layer':'context', 'hit':False}
                artifact.retrieval['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
                self.cache_mgr.put_context_artifact(key, artifact, scope, manifest=snap, expected_generation=epoch)
                return artifact
            self.cache_mgr.invalidate_scope(scope)
            if (time.monotonic() - started) * 1000 >= deadline_ms:
                break
        empty = ContextCompiler.compile(EvidencePacketBuilder.build(query, [], scope=scope), token_budget=token_budget, lean=lean)
        empty.retrieval = {'scope':scope, 'plane':user_specified_plane or 'BOTH', 'evidence_refs':[],
            'coverage':'partial', 'stop_reason':'budget_exhausted', 'warnings':['concurrent_write_retry_required'],
            'consistency':{'mode':read_mode, 'minimum_satisfied':False, 'snapshot_changed':True}}
        return empty

    def _retrieve_once(self, query, scope='default', user_specified_plane=None, token_budget=2048, embedding_query=None, *,
                       allowed_objects=None, max_depth=None, configuration=None, lean=False, cache_prefix_layout=True, **read_options):
        source_route = SourceRouter.route(query, caller_scope=scope, user_specified_plane=user_specified_plane)
        query_route = QueryRouter.route(query)
        plane = source_route.target_planes[0].value
        result = self.search(query, scope, plane, min(query_route.top_k * 2, 100), embedding_query=embedding_query,
                             allowed_objects=allowed_objects, **read_options)
        matched = []
        memory = []
        for item in result['items']:
            if item['plane'] == 'MEMORY':
                memory.append(GraphNode.model_validate(item))
            else:
                # Agent-authored notes are preserved source text, not user instruction.
                authority = MemoryAuthority.INFERRED if item['author'].startswith('mcp-') else MemoryAuthority.EXPLICIT
                matched.append(GraphNode(
                    id=item['id'], node_type=NodeType.SOURCE, scope=item['scope'],
                    title=f"[Note] {item['title']}", content=item['content'],
                    maturity=MemoryMaturity.L0_RAW, depth=MemoryDepth.D4_EVIDENCE_RAW,
                    authority=authority,
                    source_props={'source_id': item['id'], 'source_revision': item['revision'], 'content_hash': item['content_hash']},
                ))
        note_objects = {id(node):node for node in matched}
        graph_stop = None
        if allowed_objects is None and query_route.use_graph_expansion and memory:
            hist_as_of = read_options.get('as_of') if result['historical_snapshot'] else None
            hist_rec = read_options.get('recorded_as_of') if result['historical_snapshot'] else None
            expansion = self.traversal.traverse(
                [n.id for n in memory[:3]],
                max_depth=query_route.max_depth if max_depth is None else max_depth,
                max_nodes=15,
                scope=scope,
                deadline_ms=read_options['deadline_ms'],
                token_budget=token_budget,
                as_of=hist_as_of,
                recorded_as_of=hist_rec,
            )
            unique = {n.id: n for n in memory}
            valid_instant = consistency.instant(hist_as_of) if result['historical_snapshot'] else None
            unique.update({n.id: n for n in expansion.nodes if currently_valid(n, valid_instant)})
            memory = list(unique.values())
            graph_stop = expansion.stop_reason
        elif allowed_objects is not None and query_route.use_graph_expansion and memory:
            graph_stop = 'project_filter_no_graph_expansion'
        depth_trace = []
        if query_route.intent == QueryIntent.OVERVIEW_TOPIC:
            coarse = [node for node in memory if node.depth.value in ('D0', 'D1') and node.provenance]
            memory = coarse + [node for node in memory if node not in coarse]
            depth_trace.append({'stage':'coarse', 'depths':['D0', 'D1'], 'matched':len(coarse),
                                'fallback':'missing_or_unverified_summary' if not coarse else 'evidence_validation'})
        depth_trace.append({'stage':'fine', 'depths':sorted({node.depth.value for node in memory}), 'matched':len(memory)})
        top_k = min(query_route.top_k, (configuration or {}).get('context_config', {}).get('max_nodes', query_route.top_k))
        matched = memory[:top_k] + matched[:5]
        conflicts = []
        conflict_lookup_partial = False
        if result['historical_snapshot']:
            historical_conflicts = consistency.historical_search(self.storage, 'Conflict', scope, 'MEMORY', 100,
                'Conflict', allowed_objects, read_options['as_of'], read_options['recorded_as_of'])
            conflicts = [GraphNode.model_validate(item) for item in historical_conflicts['items']]
            conflict_lookup_partial = historical_conflicts['has_more']
        elif plane != 'NOTES' and matched:
            # Match participants before limiting: unrelated reports must not hide
            # warnings for claims actually included in this context.
            with self.storage.session() as conn:
                rows = conn.execute("""SELECT nodes.data_json FROM nodes
                    WHERE node_type = 'Conflict' AND scope = ? AND is_active = 1
                    AND COALESCE(json_extract(data_json, '$.core_props.status'), 'OPEN')
                        NOT IN ('RESOLVED', 'SUPERSEDED', 'IGNORED')
                    AND EXISTS (SELECT 1 FROM json_each(nodes.data_json, '$.core_props.conflicting_nodes') p
                        JOIN json_each(?) targets ON targets.value = p.value)
                    ORDER BY id LIMIT 101""", (scope, json.dumps([n.id for n in matched]))).fetchall()
            conflict_lookup_partial = len(rows) > 100
            conflicts = [GraphNode.model_validate_json(row['data_json']) for row in rows[:100]]
            conflicts = [n for n in conflicts if currently_valid(n)]
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            matched = [node for node in matched if consistency.eligible(conn,
                ({'id':node.id, 'plane':'NOTES', 'revision':node.source_props['source_revision']}
                 if id(node) in note_objects else {**node.model_dump(mode='json'), 'plane':'MEMORY'}),
                scope, historical=result['historical_snapshot'])]
            conflicts = [node for node in conflicts if consistency.eligible(conn,
                {**node.model_dump(mode='json'), 'plane':'MEMORY'}, scope, historical=result['historical_snapshot'])]
        packet = EvidencePacketBuilder.build(query=query, nodes=matched, conflicts=conflicts, scope=scope)
        artifact = ContextCompiler.compile(packet, token_budget=token_budget, lean=lean, cache_prefix_layout=cache_prefix_layout)
        item_nodes = {id(item):node for item,node in zip(packet.items,matched)}
        evidence_refs = []
        for item in artifact._included_evidence_items:
            node = item_nodes[id(item)]
            is_note = id(node) in note_objects
            evidence_refs.append({'id':node.id,'kind':'note' if is_note else 'memory',
                'revision':node.source_props.get('source_revision',1) if is_note else node.version})
        artifact.retrieval = {
            'evidence_refs':evidence_refs,
            'mode': result['mode'], 'index': result['index'], 'scope': scope,
            'plane': plane, 'as_of': result['as_of'], 'historical_snapshot': result['historical_snapshot'],
            'vector_search': result['mode'] == 'hybrid_rrf', 'vector': result.get('vector'), 'graph_stop_reason': graph_stop,
            'conflict_lookup_partial': conflict_lookup_partial,
            'coverage': 'partial' if conflict_lookup_partial or result['has_more'] or artifact.is_truncated or result.get('canonical_changed') or (result.get('vector') or {}).get('partial') or result['consistency']['degraded'] or graph_stop in ('budget_exhausted', 'historical_graph_expansion_unavailable', 'project_filter_no_graph_expansion') else ('matches_found' if matched else 'insufficient_evidence'),
            'historical_query_unsupported': query_route.intent == QueryIntent.HISTORICAL and not result['historical_snapshot'],
            'consistency':result['consistency'], 'warnings':result.get('warnings', []),
            'depth_trace':depth_trace, 'stop_reason':graph_stop or ('budget_exhausted' if artifact.is_truncated else 'no_new_evidence'),
            'graph': expansion.to_dict() if 'expansion' in locals() else None,
        }
        return artifact
