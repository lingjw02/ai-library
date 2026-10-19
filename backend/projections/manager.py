"""Continuous graph checkpoints with final canonical checks (§95)."""
import base64
import json
import uuid
from pydantic import BaseModel, ConfigDict, Field
from backend.auth.policy import current_access
from backend.core.retrieval.keyword_index import currently_valid
from backend.lineage.manager import guard, stable
from backend.schema.models import GraphNode, utc_now


def owner_only():
    if current_access.get() is not None:
        raise PermissionError('Local owner operation only')


class GraphProjection:
    def __init__(self, storage):
        self.storage = storage
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS graph_projection_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT NOT NULL,
                    checkpoint INTEGER NOT NULL, revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO graph_projection_meta VALUES(1, 'unbuilt', 0, 0);
                CREATE TABLE IF NOT EXISTS graph_projection_nodes (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, version INTEGER NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS projection_scope ON graph_projection_nodes(scope, id);
                CREATE TABLE IF NOT EXISTS graph_projection_edges (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, version INTEGER NOT NULL,
                    source_id TEXT NOT NULL, target_id TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS projection_edges_scope ON graph_projection_edges(scope, id);
            ''')

    @staticmethod
    def _head(conn):
        return conn.execute('SELECT COALESCE(MAX(seq),0) FROM outbox_sequence').fetchone()[0]

    def status(self):
        with self.storage.session() as conn:
            meta = dict(conn.execute('SELECT * FROM graph_projection_meta').fetchone())
            meta.pop('singleton')
            meta['canonical_checkpoint'] = self._head(conn)
            meta['lag'] = meta['canonical_checkpoint'] - meta['checkpoint']
            return meta

    def rebuild(self):
        owner_only()
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM graph_projection_nodes')
            conn.execute('DELETE FROM graph_projection_edges')
            conn.execute('INSERT INTO graph_projection_nodes SELECT id,scope,version,data_json FROM nodes WHERE is_active=1')
            conn.execute("INSERT INTO graph_projection_edges SELECT id,scope,COALESCE(json_extract(data_json,'$.version'),1),source_id,target_id,data_json FROM edges")
            conn.execute('UPDATE graph_projection_meta SET generation=?,checkpoint=?,revision=revision+1', (uuid.uuid4().hex, self._head(conn)))
            conn.execute('COMMIT')
        return self.status()

    def pump(self, limit=200):
        owner_only()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Invalid worker event budget')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            checkpoint = conn.execute('SELECT checkpoint FROM graph_projection_meta').fetchone()[0]
            events = conn.execute('SELECT s.seq,o.aggregate_id,o.aggregate_type FROM outbox_sequence s JOIN transactional_outbox o ON o.id=s.event_id WHERE s.seq>? ORDER BY s.seq LIMIT ?', (checkpoint, limit)).fetchall()
            for event in events:
                ref = event['aggregate_id']
                node = conn.execute('SELECT id,scope,version,data_json FROM nodes WHERE id=? AND is_active=1', (ref,)).fetchone()
                edge = conn.execute("SELECT id,scope,COALESCE(json_extract(data_json,'$.version'),1) AS version,source_id,target_id,data_json FROM edges WHERE id=?", (ref,)).fetchone()
                if node:
                    conn.execute('INSERT INTO graph_projection_nodes VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET scope=excluded.scope,version=excluded.version,data_json=excluded.data_json', tuple(node))
                else:
                    conn.execute('DELETE FROM graph_projection_nodes WHERE id=?', (ref,))
                if edge:
                    conn.execute('INSERT INTO graph_projection_edges VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET scope=excluded.scope,version=excluded.version,source_id=excluded.source_id,target_id=excluded.target_id,data_json=excluded.data_json', tuple(edge))
                else:
                    conn.execute('DELETE FROM graph_projection_edges WHERE id=?', (ref,))
                # Projection mutation and continuous prefix advancement commit together.
                conn.execute('UPDATE graph_projection_meta SET checkpoint=?,revision=revision+1', (event['seq'],))
            conn.execute('COMMIT')
        return self.status()

    @staticmethod
    def _source_visible(conn, node):
        # SQL manifests remain authoritative. A retained lineage source may also
        # carry an explicit owner revocation, which beats an older graph projection.
        if not node.provenance:
            return True
        source = node.provenance
        note = conn.execute('SELECT scope,content_hash FROM notes WHERE id=? AND revision=?',
            (source.source_id, source.source_revision)).fetchone()
        if not note or note['scope'] != node.scope or note['content_hash'] != source.content_hash:
            return False
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lineage_sessions'").fetchone():
            if conn.execute("SELECT 1 FROM lineage_sessions WHERE scope=? AND revoked=1 AND json_extract(data_json,'$.source_ref.source_id')=?", (node.scope, source.source_id)).fetchone():
                return False
        return True

    def query(self, *, scope='default', node_budget=100, edge_budget=200, cursor=None, neighborhood=None):
        guard(scope)
        if type(node_budget) is not int or not 1 <= node_budget <= 500 or type(edge_budget) is not int or not 0 <= edge_budget <= 1000:
            raise ValueError('Invalid graph budgets')
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            guard(scope, conn)
            meta = dict(conn.execute('SELECT * FROM graph_projection_meta').fetchone())
            after = ''
            if cursor:
                try:
                    decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                    if decoded['scope'] != scope or decoded['generation'] != meta['generation'] or decoded['revision'] != meta['revision'] or decoded.get('neighborhood') != neighborhood:
                        raise ValueError('Graph cursor conflict; resynchronize')
                    after = decoded['after']
                    if not isinstance(after, str) or len(after) > 500:
                        raise ValueError('Invalid graph cursor')
                except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError('Invalid graph cursor') from exc
            params = [scope, after]
            clause = ''
            if neighborhood:
                center = conn.execute('SELECT data_json FROM nodes WHERE id=? AND scope=? AND is_active=1', (neighborhood, scope)).fetchone()
                if not center or not currently_valid(GraphNode.model_validate_json(center['data_json']), utc_now()):
                    raise LookupError('Authorized graph center unavailable')
                clause = ' AND (p.id=? OR p.id IN (SELECT target_id FROM edges WHERE source_id=? AND scope=?) OR p.id IN (SELECT source_id FROM edges WHERE target_id=? AND scope=?))'
                params += [neighborhood, neighborhood, scope, neighborhood, scope]
            rows = conn.execute('SELECT p.* FROM graph_projection_nodes p WHERE p.scope=? AND p.id>?' + clause + ' ORDER BY p.id LIMIT ?', params + [node_budget + 1]).fetchall()
            nodes, now = [], utc_now()
            for row in rows[:node_budget]:
                current = conn.execute('SELECT data_json,version FROM nodes WHERE id=? AND scope=? AND is_active=1', (row['id'], scope)).fetchone()
                if not current or current['version'] != row['version']:
                    continue
                node = GraphNode.model_validate_json(current['data_json'])
                if currently_valid(node, now) and self._source_visible(conn, node):
                    nodes.append({'id': node.id, 'version': node.version, 'title': node.title,
                        'content': node.content[:1000], 'truncated': len(node.content) > 1000, 'node_type': node.node_type.value})
            ids = {node['id'] for node in nodes}
            edges = []
            # Both endpoints must occur in this authorized page; counts never reveal
            # hidden nodes. Cross-page edges are deliberately partial and declared.
            if ids and edge_budget:
                placeholders = ','.join('?' for _ in ids)
                edge_rows = conn.execute(f'SELECT p.* FROM graph_projection_edges p JOIN edges e ON e.id=p.id WHERE p.scope=? AND e.scope=? AND p.source_id IN ({placeholders}) AND p.target_id IN ({placeholders}) ORDER BY p.id LIMIT ?', [scope, scope, *ids, *ids, edge_budget]).fetchall()
                for row in edge_rows:
                    current = conn.execute('SELECT data_json FROM edges WHERE id=?', (row['id'],)).fetchone()
                    data = json.loads(current['data_json'])
                    if data.get('version', 1) != row['version']:
                        continue
                    from datetime import datetime
                    start, end = data.get('valid_from'), data.get('valid_to')
                    if (start and datetime.fromisoformat(start) > now) or (end and datetime.fromisoformat(end) <= now):
                        continue
                    edges.append({'id': row['id'], 'version': row['version'], 'source_id': row['source_id'], 'target_id': row['target_id'], 'relation': data['relation']})
            head = self._head(conn)
            continuation = None
            if len(rows) > node_budget:
                continuation = base64.urlsafe_b64encode(stable({'scope': scope, 'generation': meta['generation'],
                    'revision': meta['revision'], 'after': rows[node_budget - 1]['id'], 'neighborhood': neighborhood}).encode()).decode()
            guard(scope, conn)
            conn.execute('COMMIT')
            return {'nodes': nodes, 'edges': edges, 'projection_revision': meta['revision'], 'generation': meta['generation'],
                'source_checkpoint': meta['checkpoint'], 'canonical_checkpoint': head, 'coverage': 'degraded' if head > meta['checkpoint'] else 'current_page',
                'edge_coverage': 'page_endpoints_only', 'continuation': continuation}


class Point(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float
    y: float


class Viewport(Point):
    zoom: float = Field(default=1, ge=0.01, le=100)


class ViewState(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: str = 'viewstate-v1'
    projection_generation: str | None = Field(default=None, max_length=200)
    node_positions: dict[str, Point] = Field(default_factory=dict, max_length=2000)
    pinned_nodes: list[str] = Field(default_factory=list, max_length=2000)
    collapsed_groups: list[str] = Field(default_factory=list, max_length=2000)
    selected_node_id: str | None = Field(default=None, max_length=500)
    viewport: Viewport = Field(default_factory=lambda: Viewport(x=0, y=0))


class ViewStateStore:
    def __init__(self, storage):
        self.storage = storage
        with storage.session() as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS graph_view_states (
                owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, scope TEXT NOT NULL,
                revision INTEGER NOT NULL, state_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_id,workspace_id,scope));
                CREATE TABLE IF NOT EXISTS graph_view_state_history (
                owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, scope TEXT NOT NULL,
                revision INTEGER NOT NULL, state_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_id,workspace_id,scope,revision));''')

    @staticmethod
    def _guard(owner, workspace, scope, conn=None, write=False):
        guard(scope, conn, write=write)
        for value in (owner, workspace):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError('Bounded owner/workspace identity required')
        context = current_access.get()
        if context and context.principal != owner:
            raise PermissionError('Owner layout access denied')

    @staticmethod
    def _filtered(conn, state, scope):
        def visible(ref):
            if not isinstance(ref, str) or len(ref) > 500:
                return False
            row = conn.execute('SELECT data_json FROM nodes WHERE id=? AND scope=? AND is_active=1', (ref, scope)).fetchone()
            return bool(row and currently_valid(GraphNode.model_validate_json(row['data_json']), utc_now()) and GraphProjection._source_visible(conn, GraphNode.model_validate_json(row['data_json'])))
        state['node_positions'] = {ref: position for ref, position in state['node_positions'].items() if visible(ref)}
        state['pinned_nodes'] = list(dict.fromkeys(ref for ref in state['pinned_nodes'] if visible(ref)))
        state['collapsed_groups'] = list(dict.fromkeys(ref for ref in state['collapsed_groups'] if visible(ref)))
        if state['selected_node_id'] and not visible(state['selected_node_id']):
            state['selected_node_id'] = None
        return state

    def get(self, owner_id, workspace_id, scope='default'):
        self._guard(owner_id, workspace_id, scope)
        with self.storage.session() as conn:
            self._guard(owner_id, workspace_id, scope, conn)
            row = conn.execute('SELECT * FROM graph_view_states WHERE owner_id=? AND workspace_id=? AND scope=?', (owner_id, workspace_id, scope)).fetchone()
            return {'revision': row['revision'] if row else 0, 'state': self._filtered(conn, json.loads(row['state_json']) if row else ViewState().model_dump(), scope)}

    def save(self, owner_id, workspace_id, scope, state, *, expected_revision):
        self._guard(owner_id, workspace_id, scope, write=True)
        parsed = ViewState.model_validate(state)
        if parsed.schema_version != 'viewstate-v1':
            raise ValueError('Unsupported ViewState schema version')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Nonnegative expected revision required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._guard(owner_id, workspace_id, scope, conn, write=True)
            row = conn.execute('SELECT revision FROM graph_view_states WHERE owner_id=? AND workspace_id=? AND scope=?', (owner_id, workspace_id, scope)).fetchone()
            if (row['revision'] if row else 0) != expected_revision:
                raise ValueError('ViewState revision conflict')
            value = self._filtered(conn, parsed.model_dump(), scope)
            revision, now = expected_revision + 1, utc_now().isoformat()
            conn.execute('INSERT INTO graph_view_states VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id,workspace_id,scope) DO UPDATE SET revision=excluded.revision,state_json=excluded.state_json,updated_at=excluded.updated_at', (owner_id, workspace_id, scope, revision, stable(value), now))
            conn.execute('INSERT INTO graph_view_state_history VALUES(?,?,?,?,?,?)', (owner_id, workspace_id, scope, revision, stable(value), now))
            # Layout history is separate from knowledge outbox: moving a node cannot
            # invalidate the context/retrieval dependency vector.
            conn.execute('COMMIT')
            return {'revision': revision, 'state': value}

    def reset(self, owner_id, workspace_id, scope, *, expected_revision):
        return self.save(owner_id, workspace_id, scope, {}, expected_revision=expected_revision)
