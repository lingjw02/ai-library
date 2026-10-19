"""
Bounded Graph Traversal Engine.
Ref: AI Knowledge OS Backend Specification §§21, 83.5.
Guarantees hard protection against combinatorial graph explosion:
- Strict max depth (<= 3)
- Strict max nodes (<= 20)
- Branching factor cap
- Loop avoidance via visited set
- Explicit stop reasons: 'sufficient', 'no_new_evidence', 'budget_exhausted'
"""

from __future__ import annotations
from typing import List, Set, Dict, Optional, Any
import time
import math
from datetime import timezone
from backend.schema.models import GraphNode, GraphEdge, RelationshipType
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.core.retrieval.keyword_index import currently_valid


class TraversalResult:
    def __init__(
        self,
        nodes: List[GraphNode],
        edges: List[GraphEdge],
        stop_reason: str,
        depth_reached: int,
        visited_count: int,
        edges_examined: int = 0,
        elapsed_ms: float = 0,
        estimated_tokens: int = 0,
    ):
        self.nodes = nodes
        self.edges = edges
        self.stop_reason = stop_reason
        self.depth_reached = depth_reached
        self.visited_count = visited_count
        self.edges_examined = edges_examined
        self.elapsed_ms = elapsed_ms
        self.estimated_tokens = estimated_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_ids": [n.id for n in self.nodes],
            "edge_ids": [e.id for e in self.edges],
            "stop_reason": self.stop_reason,
            "depth_reached": self.depth_reached,
            "visited_count": self.visited_count,
            "edges_examined": self.edges_examined,
            "elapsed_ms": self.elapsed_ms,
            "estimated_tokens": self.estimated_tokens,
        }


class BoundedGraphTraversal:
    """
    Safely expands knowledge graph frontiers from seed nodes under hard limits.
    """

    def __init__(
        self,
        storage: CanonicalStorage,
        hard_max_depth: int = 3,
        hard_max_nodes: int = 20,
        max_branching: int = 4,
    ):
        self.storage = storage
        self.hard_max_depth = hard_max_depth
        self.hard_max_nodes = hard_max_nodes
        self.max_branching = max_branching

    def traverse(
        self,
        seed_node_ids: List[str],
        allowed_relations: Optional[List[RelationshipType]] = None,
        max_depth: Optional[int] = None,
        max_nodes: Optional[int] = None,
        scope: Optional[str] = None,
        max_edges: int = 40,
        deadline_ms: int = 500,
        token_budget: int = 2048,
        as_of: Optional[Any] = None,
        recorded_as_of: Optional[Any] = None,
    ) -> TraversalResult:
        """
        Execute bounded breadth-first graph traversal from seed nodes.
        Supports both current canonical state and historical as_of snapshots via retained revisions.
        """
        from backend.core.retrieval.consistency import eligible, instant, utc_now
        depth_limit = min(self.hard_max_depth if max_depth is None else max_depth, self.hard_max_depth)
        node_limit = min(self.hard_max_nodes if max_nodes is None else max_nodes, self.hard_max_nodes)
        if depth_limit < 0 or node_limit < 1 or max_edges < 1 or deadline_ms < 1 or token_budget < 1:
            raise ValueError('Invalid graph budgets')
        edge_limit = min(max_edges, 100)
        historical = as_of is not None
        valid_at = instant(as_of) if historical else utc_now()
        recorded_at = instant(recorded_as_of or as_of) if historical else valid_at
        started, now = time.monotonic(), valid_at
        def overdue():
            return (time.monotonic() - started) * 1000 >= deadline_ms
        def edge_valid(edge):
            def aware(value):
                return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
            start, end = aware(edge.valid_from), aware(edge.valid_to)
            return (start is None or start <= now) and (end is None or now < end)

        visited_ids: Set[str] = set()
        collected_nodes: Dict[str, GraphNode] = {}
        collected_edges: List[GraphEdge] = []

        current_frontier, current_depth = [], 0
        edges_examined, estimated_tokens = 0, 0
        limited = False
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            def fetch(identity):
                if historical:
                    rows = conn.execute(
                        'SELECT data_json, recorded_at FROM node_revisions WHERE id=? ORDER BY version ASC',
                        (identity,),
                    ).fetchall()
                    chosen = None
                    for r in rows:
                        if instant(r['recorded_at']) <= recorded_at:
                            chosen = r['data_json']
                    if not chosen:
                        return None
                    node = GraphNode.model_validate_json(chosen)
                    effective_scope = scope or node.scope
                    if node.scope != effective_scope or not currently_valid(node, now):
                        return None
                    item = {**node.model_dump(mode='json'), 'plane': 'MEMORY'}
                    return node if eligible(conn, item, effective_scope, historical=True, now=now) else None
                row = conn.execute('SELECT data_json FROM nodes WHERE id=?', (identity,)).fetchone()
                if not row:
                    return None
                node = GraphNode.model_validate_json(row['data_json'])
                effective_scope = scope or node.scope
                item = {**node.model_dump(mode='json'), 'plane':'MEMORY'}
                return node if eligible(conn, item, effective_scope, now=now) else None

            def fetch_incident_edges(node_id, effective_scope, remaining_budget):
                if not historical:
                    sql = 'SELECT data_json FROM edges WHERE (source_id=? OR target_id=?) AND scope=?'
                    args = [node_id, node_id, effective_scope]
                    if allowed_relations is not None:
                        if not allowed_relations:
                            return []
                        sql += ' AND relation IN (' + ','.join('?' for _ in allowed_relations) + ')'
                        args.extend(relation.value if hasattr(relation, 'value') else relation for relation in allowed_relations)
                    sql += ' ORDER BY id LIMIT ?'
                    args.append(remaining_budget)
                    return [r['data_json'] for r in conn.execute(sql, args).fetchall()]
                if allowed_relations is not None and not allowed_relations:
                    return []
                allowed_rel_set = (
                    {relation.value if hasattr(relation, 'value') else relation for relation in allowed_relations}
                    if allowed_relations is not None else None
                )
                rev_rows = conn.execute(
                    'SELECT id, version, data_json, recorded_at FROM edge_revisions WHERE scope=? ORDER BY id ASC, version ASC',
                    (effective_scope,),
                ).fetchall()
                latest_by_edge: Dict[str, str] = {}
                for r in rev_rows:
                    if instant(r['recorded_at']) <= recorded_at:
                        latest_by_edge[r['id']] = r['data_json']
                matched_json: List[str] = []
                for edge_id in sorted(latest_by_edge.keys()):
                    raw = latest_by_edge[edge_id]
                    edge_obj = GraphEdge.model_validate_json(raw)
                    if edge_obj.source_id != node_id and edge_obj.target_id != node_id:
                        continue
                    rel_val = edge_obj.relation.value if hasattr(edge_obj.relation, 'value') else str(edge_obj.relation)
                    if allowed_rel_set is not None and rel_val not in allowed_rel_set:
                        continue
                    matched_json.append(raw)
                    if len(matched_json) >= remaining_budget:
                        break
                return matched_json

            for sid in seed_node_ids:
                if overdue() or len(collected_nodes) >= node_limit:
                    limited = True
                    break
                node = fetch(sid)
                if node and sid not in visited_ids:
                    cost = math.ceil(len(node.title + node.content) / 4)
                    if estimated_tokens + cost > token_budget:
                        limited = True
                        continue
                    visited_ids.add(sid)
                    collected_nodes[sid] = node
                    current_frontier.append(sid)
                    estimated_tokens += cost
            while current_depth < depth_limit and current_frontier:
                if overdue() or len(collected_nodes) >= node_limit or edges_examined >= edge_limit:
                    limited = True
                    break
                next_frontier = []
                current_depth += 1
                for node_id in current_frontier:
                    if overdue() or len(collected_nodes) >= node_limit or edges_examined >= edge_limit:
                        limited = True
                        break
                    effective_scope = scope or collected_nodes[node_id].scope
                    edge_jsons = fetch_incident_edges(node_id, effective_scope, edge_limit - edges_examined + 1)
                    branch_count = 0
                    for raw_json in edge_jsons:
                        if overdue() or edges_examined >= edge_limit or len(collected_nodes) >= node_limit:
                            limited = True
                            break
                        edges_examined += 1
                        edge = GraphEdge.model_validate_json(raw_json)
                        if not edge_valid(edge):
                            continue
                        neighbor_id = edge.target_id if edge.source_id == node_id else edge.source_id
                        if neighbor_id in visited_ids:
                            continue
                        if branch_count >= self.max_branching:
                            limited = True
                            break
                        neighbor = fetch(neighbor_id)
                        if neighbor is None or neighbor.scope != effective_scope:
                            continue
                        cost = math.ceil(len(neighbor.title + neighbor.content) / 4)
                        if estimated_tokens + cost > token_budget:
                            limited = True
                            continue
                        visited_ids.add(neighbor_id)
                        collected_nodes[neighbor_id] = neighbor
                        collected_edges.append(edge)
                        next_frontier.append(neighbor_id)
                        estimated_tokens += cost
                        branch_count += 1
                current_frontier = next_frontier
            if current_frontier and current_depth >= depth_limit:
                limited = True
        # Similarity/seed discovery does not establish query field completeness.
        stop_reason = 'budget_exhausted' if limited else 'no_new_evidence'

        return TraversalResult(
            nodes=list(collected_nodes.values()),
            edges=collected_edges,
            stop_reason=stop_reason,
            depth_reached=current_depth,
            visited_count=len(visited_ids),
            edges_examined=edges_examined,
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            estimated_tokens=estimated_tokens,
        )
