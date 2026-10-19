"""Canonical guards and retained history. Layout state is excluded from caches.

Scoped canonical digests conservatively detect writes without outbox events.
This is a correctness fallback, not a constant-time partition revision index.
"""
from datetime import datetime, timezone
import json
import hashlib
from backend.schema.models import GraphNode, utc_now
from backend.core.retrieval.keyword_index import currently_valid
from backend.core.retrieval.bm25 import BM25Scorer


def instant(value):
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(result, datetime):
        raise ValueError('as_of must be an ISO datetime')
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def revoked(conn, scope, source_id):
    if not source_id:
        return False
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lineage_sessions'").fetchone():
        return False
    return bool(conn.execute("""SELECT 1 FROM lineage_sessions WHERE scope=? AND revoked=1
        AND json_extract(data_json, '$.source_ref.source_id')=? LIMIT 1""", (scope, source_id)).fetchone())


def eligible(conn, item, scope, *, historical=False, now=None):
    plane, identity = item['plane'], item['id']
    table, predicate = ('nodes', 'is_active=1') if plane == 'MEMORY' else ('notes', 'is_current=1')
    row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND scope=? AND {predicate}', (identity, scope)).fetchone()
    if not row:
        return False
    if plane == 'NOTES':
        return row['revision'] == item['revision'] and not revoked(conn, scope, identity)
    node = GraphNode.model_validate_json(row['data_json'])
    source = node.provenance.source_id if node.provenance else node.source_props.get('source_id')
    old = GraphNode.model_validate(item)
    old_source = old.provenance.source_id if old.provenance else old.source_props.get('source_id')
    if revoked(conn, scope, source) or revoked(conn, scope, old_source):
        return False
    if historical:
        return True
    return node.version == item['version'] and currently_valid(node, now or utc_now())


def snapshot(storage, scope):
    with storage.session() as conn:
        conn.execute('BEGIN')
        now, material = utc_now(), {}
        for table in ('nodes', 'edges', 'notes', 'lineage_sessions', 'evolution_revisions', 'evolution_active'):
            cols = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
            if 'scope' not in cols:
                continue
            rows = [dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE scope=? ORDER BY rowid', (scope,))]
            if table == 'nodes':
                for row in rows:
                    row['currently_valid'] = currently_valid(GraphNode.model_validate_json(row['data_json']), now)
            material[table] = rows
        indexes = {}
        for table in ('keyword_index_state', 'vector_index_state', 'graph_index_state'):
            if conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                indexes[table] = [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
        revision = conn.execute('SELECT COALESCE(MAX(seq),0) FROM outbox_sequence').fetchone()[0]
        material['indexes'] = indexes
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
        return {'partition_hash': digest, 'canonical_revision': revision, 'indexes': indexes}


def historical_search(storage, query, scope, plane, limit, node_type, allowed_objects, as_of, recorded_as_of):
    valid_at, recorded_at = instant(as_of), instant(recorded_as_of or as_of)
    if valid_at is None:
        raise ValueError('historical_as_of requires as_of')
    candidates = {}
    with storage.session() as conn:
        conn.execute('BEGIN')
        for row in conn.execute('SELECT * FROM node_revisions WHERE scope=? ORDER BY id, version', (scope,)):
            if instant(row['recorded_at']) > recorded_at:
                continue
            node = GraphNode.model_validate_json(row['data_json'])
            candidates[node.id] = node
        items = {}
        for node in candidates.values():
            item = {**node.model_dump(mode='json'), 'plane': 'MEMORY'}
            if plane == 'NOTES' or (allowed_objects is not None and ('MEMORY', node.id) not in allowed_objects):
                continue
            if (node_type and node.node_type.value != node_type) or not currently_valid(node, valid_at):
                continue
            if eligible(conn, item, scope, historical=True):
                items[node.id] = item
        scorer = BM25Scorer()
        scorer.fit({identity: f"{item['id']} {item['title']} {item['content']}" for identity, item in items.items()})
        ordered = [items[identity] for identity, _ in scorer.score(query, top_k=len(items))]
        if query.strip() in items:
            ordered = [items[query.strip()], *[item for item in ordered if item['id'] != query.strip()]]
    return {'items': ordered[:limit], 'total':len(ordered), 'has_more':len(ordered)>limit,
            'mode':'retained_history', 'index':{}, 'scope':scope, 'plane':plane,
            'as_of':valid_at.isoformat(), 'recorded_as_of':recorded_at.isoformat(),
            'historical_snapshot':True, 'history_coverage':'retained_revisions_only',
            'warnings':['history_before_retention_not_reconstructed'] + (['notes_history_unavailable'] if plane != 'MEMORY' else [])}
