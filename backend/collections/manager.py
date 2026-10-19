"""Scope-local collection references. Content and canonical history are read-only here."""
import re
import uuid

from backend.schema.models import utc_now
from backend.lineage.manager import guard
from backend.collections.audit import initialize_audit


class TopicNotFound(ValueError):
    pass


class TopicConflict(ValueError):
    pass


CURRENT = """WITH current_objects AS (
    SELECT id, 'note' AS kind, title, content, revision, scope, NULL AS node_type,
        created_at AS updated_at, author FROM notes WHERE is_current = 1 AND scope = :scope
    UNION ALL
    SELECT id, 'memory' AS kind, title, content, version AS revision, scope, node_type,
        updated_at, json_extract(data_json, '$.provenance.author_or_actor') AS author
    FROM nodes WHERE is_active = 1 AND scope = :scope
) """

TOPIC_SELECT = """SELECT t.*,
    (SELECT COUNT(*) FROM collection_members m JOIN current_objects o
      ON o.id = m.object_id AND o.kind = m.kind WHERE m.topic_id = t.id) AS count,
    (SELECT COUNT(*) FROM collection_topics c WHERE c.parent_id = t.id AND c.scope = t.scope) AS child_count,
    (SELECT COUNT(*) FROM collection_capability_members cm JOIN capabilities cap ON cap.id=cm.capability_id
      WHERE cm.topic_id=t.id AND json_extract(cap.status_json,'$.status')='REGISTERED') AS capability_count
    FROM collection_topics t """

# universe() 一次请求要跑 7 条独立查询，每条都会重新物化 CURRENT 里的 CTE
# （notes UNION ALL nodes，约 2000 行）。改为在事务开始时物化一次到临时表，
# 后续查询直接读临时表。同时临时表不带 content 字段：正文合计约 4.8 MB，
# 只有 items 列表需要前 240 字，按需回表取比每次全量物化便宜得多。
_CUR_TABLE_DDL = """CREATE TEMP TABLE _cur_objects AS
    SELECT id, 'note' AS kind, title, content, revision, scope, NULL AS node_type,
        created_at AS updated_at, author FROM notes WHERE is_current = 1 AND scope = :scope
    UNION ALL
    SELECT id, 'memory' AS kind, title, content, version AS revision, scope, node_type,
        updated_at, json_extract(data_json, '$.provenance.author_or_actor') AS author
    FROM nodes WHERE is_active = 1 AND scope = :scope"""

_TOPIC_SELECT_T = TOPIC_SELECT.replace('current_objects', '_cur_objects')


class CollectionManager:
    def __init__(self, storage):
        self.storage = storage
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS collection_topics (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, title TEXT NOT NULL,
                    parent_id TEXT REFERENCES collection_topics(id),
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    CHECK (parent_id IS NULL OR parent_id != id));
                CREATE INDEX IF NOT EXISTS collection_topic_scope ON collection_topics(scope, parent_id);
                CREATE TABLE IF NOT EXISTS collection_members (
                    topic_id TEXT NOT NULL REFERENCES collection_topics(id),
                    kind TEXT NOT NULL CHECK (kind IN ('note', 'memory')),
                    object_id TEXT NOT NULL, added_at TEXT NOT NULL,
                    PRIMARY KEY(topic_id, kind, object_id));
                CREATE INDEX IF NOT EXISTS collection_member_object ON collection_members(kind, object_id);
                CREATE TABLE IF NOT EXISTS collection_imports (
                    scope TEXT NOT NULL, migration TEXT NOT NULL, imported_at TEXT NOT NULL,
                    PRIMARY KEY(scope, migration));
                CREATE TABLE IF NOT EXISTS collection_marker_evidence (
                    scope TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL,
                    object_revision INTEGER NOT NULL, original_title TEXT NOT NULL,
                    marker TEXT NOT NULL, topic_id TEXT NOT NULL REFERENCES collection_topics(id),
                    imported_at TEXT NOT NULL,
                    PRIMARY KEY(scope, kind, object_id, marker));
            ''')
            initialize_audit(conn)

    @staticmethod
    def _text(value, name, maximum=200):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f'{name} must be nonempty and at most {maximum} characters')
        return value.strip()

    def _get(self, conn, topic_id, scope):
        guard(scope, conn)
        row = conn.execute(CURRENT + TOPIC_SELECT + 'WHERE t.id = :id AND t.scope = :scope',
                           {'id': topic_id, 'scope': scope}).fetchone()
        if row is None:
            raise TopicNotFound('Topic not found in this scope')
        return dict(row)

    @staticmethod
    def _cas(topic, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('A positive expected_revision is required')
        if topic['revision'] != expected_revision:
            raise TopicConflict('Topic changed; refresh before editing. Your change was not saved.')

    def _create(self, conn, title, scope, parent_id):
        guard(scope, conn, write=True)
        if parent_id:
            self._get(conn, parent_id, scope)
        topic_id = 'topic-' + uuid.uuid4().hex
        now = utc_now().isoformat()
        conn.execute('INSERT INTO collection_topics VALUES (?, ?, ?, ?, 1, ?, ?)',
                     (topic_id, scope, title, parent_id, now, now))
        return self._get(conn, topic_id, scope)

    def create(self, title, scope='default', parent_id=None):
        title, scope = self._text(title, 'Title'), self._text(scope, 'Scope')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            result = self._create(conn, title, scope, parent_id)
            conn.execute('COMMIT')
            return result

    def rename(self, topic_id, title, scope, expected_revision):
        title, scope = self._text(title, 'Title'), self._text(scope, 'Scope')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            topic = self._get(conn, topic_id, scope)
            self._cas(topic, expected_revision)
            if title != topic['title']:
                conn.execute('UPDATE collection_topics SET title = ?, revision = revision + 1, updated_at = ? WHERE id = ?',
                             (title, utc_now().isoformat(), topic_id))
            result = self._get(conn, topic_id, scope)
            conn.execute('COMMIT')
            return result

    def membership(self, topic_id, scope, kind, object_id, expected_revision, remove=False):
        scope = self._text(scope, 'Scope')
        if kind == 'capability':
            return self._capability_membership(topic_id, scope, object_id, expected_revision, remove)
        if kind not in ('note', 'memory'):
            raise ValueError('Only note, memory or capability references are allowed')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            topic = self._get(conn, topic_id, scope)
            self._cas(topic, expected_revision)
            params = {'scope': scope, 'id': object_id, 'kind': kind}
            if not conn.execute(CURRENT + 'SELECT 1 FROM current_objects WHERE id = :id AND kind = :kind', params).fetchone():
                raise TopicNotFound('Current object not found in this scope')
            if remove:
                changed = conn.execute('DELETE FROM collection_members WHERE topic_id = ? AND kind = ? AND object_id = ?',
                                       (topic_id, kind, object_id)).rowcount
            else:
                changed = conn.execute('INSERT OR IGNORE INTO collection_members VALUES (?, ?, ?, ?)',
                                       (topic_id, kind, object_id, utc_now().isoformat())).rowcount
            if changed:
                conn.execute('UPDATE collection_topics SET revision = revision + 1, updated_at = ? WHERE id = ?',
                             (utc_now().isoformat(), topic_id))
            result = self._get(conn, topic_id, scope)
            conn.execute('COMMIT')
            return result

    def split_topic(self, topic_id, scope='default'):
        """拆分散落：把子主题的全部成员移到父主题（当前行星）内，
        空容器删除；如有孙主题则上移为父主题的直接子主题，保留结构不误删。"""
        scope = self._text(scope, 'Scope')
        topic_id = self._text(topic_id, 'Topic ID')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            topic = self._get(conn, topic_id, scope)
            parent_id = topic['parent_id']
            if not parent_id:
                raise ValueError('根主题不能拆分，请进入其所在的行星（父主题）后对该子主题操作')
            self._get(conn, parent_id, scope)
            now = utc_now().isoformat()
            # 1. 孙主题上移：成为父主题的直接子主题（保留树结构）
            conn.execute('UPDATE collection_topics SET parent_id = ?, revision = revision + 1, updated_at = ? WHERE parent_id = ? AND scope = ?',
                         (parent_id, now, topic_id, scope))
            # 2. 成员移入父主题（散落在当前行星内）；先移除父主题已有的同对象，避免唯一冲突
            conn.execute("""
            DELETE FROM collection_members
            WHERE topic_id = :parent AND EXISTS (
              SELECT 1 FROM collection_members c2
              WHERE c2.topic_id = :child AND c2.kind = collection_members.kind AND c2.object_id = collection_members.object_id
            )
            """, {'parent': parent_id, 'child': topic_id})
            moved = conn.execute('UPDATE collection_members SET topic_id = ?, added_at = ? WHERE topic_id = ?',
                                 (parent_id, now, topic_id)).rowcount
            # 3. 删除空容器（原子主题）
            conn.execute('DELETE FROM collection_topics WHERE id = ? AND scope = ?', (topic_id, scope))
            # 4. 父主题版本递增
            conn.execute('UPDATE collection_topics SET revision = revision + 1, updated_at = ? WHERE id = ?',
                         (now, parent_id))
            result = self._get(conn, parent_id, scope)
            conn.execute('COMMIT')
            return {'split_title': topic['title'], 'moved': moved, 'parent': result}

    @staticmethod
    def _page(offset, limit):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 500:
            raise ValueError('Invalid pagination')

    def list_topics(self, scope='default', offset=0, limit=200):
        scope = self._text(scope, 'Scope')
        guard(scope)
        self._page(offset, limit)
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            total = conn.execute('SELECT COUNT(*) FROM collection_topics WHERE scope = ?', (scope,)).fetchone()[0]
            rows = conn.execute(CURRENT + TOPIC_SELECT +
                'WHERE t.scope = :scope ORDER BY t.title COLLATE NOCASE, t.id LIMIT :limit OFFSET :offset',
                {'scope': scope, 'limit': limit, 'offset': offset}).fetchall()
            return {'topics': [dict(r) for r in rows], 'total': total, 'offset': offset, 'limit': limit,
                    'partial': offset > 0 or offset + len(rows) < total}

    @staticmethod
    def _agent_condition(agent_id: str, col: str = 'o.author') -> str:
        if agent_id == 'user':
            # Personal space = only user's own genuine notes/memories,
            # never AI-generated session extracts or deepseek/harness notes.
            return f"({col} IN ('user', 'operator') AND (o.title NOT LIKE '【%】%' AND lower(ifnull({col}, '')) NOT LIKE '%harness%' AND lower(ifnull({col}, '')) NOT LIKE '%deepseek%'))"
        elif agent_id == 'deepseek':
            return f"(instr(lower({col}), 'deepseek') > 0 OR instr(lower({col}), 'harness') > 0)"
        elif agent_id in ('antigravity', 'codex', 'qwen', 'doubao', 'pangu'):
            return f"instr(lower({col}), '{agent_id}') > 0"
        elif agent_id == 'external':
            return f"({col} IS NOT NULL AND instr(lower({col}), 'antigravity') = 0 AND instr(lower({col}), 'codex') = 0 AND instr(lower({col}), 'deepseek') = 0 AND instr(lower({col}), 'harness') = 0 AND instr(lower({col}), 'qwen') = 0 AND instr(lower({col}), 'doubao') = 0 AND instr(lower({col}), 'pangu') = 0 AND ({col} NOT IN ('user', 'operator') OR o.title LIKE '【%】%'))"
        return "1"

    def universe(self, scope='default', topic_id=None, query='', kind='all', agent_id=None, offset=0, limit=60):
        scope = self._text(scope, 'Scope')
        self._page(offset, limit)
        if kind not in ('all', 'note', 'memory'):
            raise ValueError('Unknown kind')
        query = query.strip()
        if len(query) > 2000:
            raise ValueError('Query too long')
        with self.storage.session() as conn:
            conn.execute('BEGIN')  # Counts, page, and memberships share a read snapshot.
            params = {'scope': scope, 'query': query, 'kind': kind, 'topic_id': topic_id}
            # 物化一次，供本次请求的 7 条查询复用（临时表按连接隔离，不影响并发请求）
            conn.execute('DROP TABLE IF EXISTS temp._cur_objects')
            conn.execute(_CUR_TABLE_DDL, {'scope': scope})
            current_topic = None
            breadcrumbs = []
            agent_name = None
            if agent_id:
                agent_names = {
                    'user': '我的内容',
                    'antigravity': 'Antigravity 导入',
                    'codex': 'Codex 导入',
                    'deepseek': 'DeepSeek 导入',
                    'qwen': 'Qwen 导入',
                    'doubao': '豆包导入',
                    'pangu': '盘古导入',
                    'external': '其他导入'
                }
                agent_name = agent_names.get(agent_id, f'{agent_id} Agent')
                breadcrumbs.append({'id': f'__agent_{agent_id}', 'title': agent_name, 'agent_id': agent_id})

            if topic_id and topic_id not in ('all', 'unclassified'):
                current_topic = self._get(conn, topic_id, scope)
                ancestor, visited = current_topic, set()
                topic_crumbs = []
                while ancestor:
                    if ancestor['id'] in visited:
                        raise ValueError('Invalid cyclic topic hierarchy')
                    visited.add(ancestor['id'])
                    topic_crumbs.insert(0, {'id': ancestor['id'], 'title': ancestor['title']})
                    ancestor = self._get(conn, ancestor['parent_id'], scope) if ancestor['parent_id'] else None
                breadcrumbs.extend(topic_crumbs)

            topic_where = 't.scope = :scope'
            item_where = "(:kind = 'all' OR o.kind = :kind)"
            if agent_id:
                agent_cond = self._agent_condition(agent_id, 'o.author')
                agent_cond_o2 = self._agent_condition(agent_id, 'o2.author')
                item_where += f" AND {agent_cond}"

            if query:
                # Literal substring search: % and _ do not turn into wildcards.
                topic_where += ' AND instr(lower(t.title), lower(:query)) > 0'
                item_where += ' AND (instr(lower(o.title), lower(:query)) > 0 OR instr(lower(o.content), lower(:query)) > 0)'
            elif topic_id == 'all':
                topic_where += ' AND 0'
            elif topic_id == 'unclassified':
                topic_where += ' AND 0'
                item_where += ''' AND NOT EXISTS (SELECT 1 FROM collection_members m JOIN collection_topics t
                    ON t.id = m.topic_id WHERE m.object_id = o.id AND m.kind = o.kind AND t.scope = :scope)'''
            elif current_topic:
                topic_where += ' AND t.parent_id = :topic_id'
                item_where += ' AND EXISTS (SELECT 1 FROM collection_members m WHERE m.topic_id = :topic_id AND m.object_id = o.id AND m.kind = o.kind)'
            else:
                topic_where += ' AND t.parent_id IS NULL'
                if agent_id:
                    # In agent partition overview, filter topics to those contributed by this agent
                    topic_where += f" AND EXISTS (SELECT 1 FROM collection_members m JOIN _cur_objects o2 ON o2.id = m.object_id AND o2.kind = m.kind WHERE m.topic_id = t.id AND {agent_cond_o2})"
                    if kind != 'all' or agent_id == 'user':
                        # Content partitions (e.g. Personal space / 我的空间) show their items
                        # directly, not grouped into topics — the list itself is the partition.
                        topic_where += ' AND 0'
                    else:
                        item_where += ' AND 0'
                else:
                    item_where += ' AND 0'

            topic_total = conn.execute('SELECT COUNT(*) FROM collection_topics t WHERE ' + topic_where, params).fetchone()[0]
            item_total = conn.execute('SELECT COUNT(*) FROM _cur_objects o WHERE ' + item_where, params).fetchone()[0]
            params.update({'offset': offset, 'limit': limit})
            topics = [dict(r) for r in conn.execute(_TOPIC_SELECT_T + 'WHERE ' + topic_where +
                ' ORDER BY t.title COLLATE NOCASE, t.id LIMIT :limit OFFSET :offset', params)]
            params.update({'offset': max(0, offset - topic_total), 'limit': limit - len(topics)})
            items = [dict(r) for r in conn.execute('''SELECT o.id, o.kind, o.title, o.revision, o.scope,
                o.node_type, o.updated_at, o.author,
                substr(o.content, 1, 240) AS excerpt FROM _cur_objects o WHERE ''' + item_where +
                ' ORDER BY o.title COLLATE NOCASE, o.kind, o.id LIMIT :limit OFFSET :offset', params)]
            # 批量取这批 item 的主题归属。原来是逐条 item 各查一次（一页最多 500 次
            # 单行查询），是 universe() 在主题视图下 200ms 起步的主因。
            if items:
                ids = list({it['id'] for it in items})
                marks = ','.join('?' for _ in ids)
                grouped = {}
                for r in conn.execute('''SELECT m.kind AS m_kind, m.object_id AS m_object_id,
                        t.id, t.title, t.parent_id
                    FROM collection_topics t JOIN collection_members m ON m.topic_id = t.id
                    WHERE t.scope = ? AND m.object_id IN (''' + marks + ''')
                    ORDER BY t.title, t.id''', [scope, *ids]):
                    grouped.setdefault((r['m_kind'], r['m_object_id']), []).append(
                        {'id': r['id'], 'title': r['title'], 'parent_id': r['parent_id']})
                for item in items:
                    item['topics'] = grouped.get((item['kind'], item['id']), [])
                    item['topic_ids'] = [t['id'] for t in item['topics']]
            else:
                for item in items:
                    item['topics'] = []
                    item['topic_ids'] = []
            counts = {'notes': 0, 'memories': 0}
            for row in conn.execute('SELECT kind, COUNT(*) AS n FROM _cur_objects GROUP BY kind', params):
                counts['notes' if row['kind'] == 'note' else 'memories'] = row['n']
            counts['topics'] = conn.execute('SELECT COUNT(*) FROM collection_topics WHERE scope = ?', (scope,)).fetchone()[0]
            counts['unclassified'] = conn.execute('''SELECT COUNT(*) FROM _cur_objects o WHERE NOT EXISTS
                (SELECT 1 FROM collection_members m JOIN collection_topics t ON t.id = m.topic_id
                 WHERE m.kind = o.kind AND m.object_id = o.id AND t.scope = :scope)''', params).fetchone()[0]
            total = topic_total + item_total
            return {'topics': topics, 'items': items, 'total': total, 'offset': offset, 'limit': limit,
                    'partial': offset > 0 or offset + len(topics) + len(items) < total,
                    'topic': current_topic, 'breadcrumbs': breadcrumbs, 'counts': counts,
                    'topic_total': topic_total, 'item_total': item_total,
                    'agent_id': agent_id, 'agent_name': agent_name}

    def content_fingerprint(self, scope='default'):
        """星图内容指纹：(计数, 最新更新时刻) 组合，覆盖当前笔记/记忆、主题与归属关系。

        任何内容新增、修改或删除都会改变指纹；后台关键词索引写入（keyword_* 表）
        不在指纹范围内，不会造成假阳性。前端以该指纹为信号自动刷新视图，
        用户无需手动 refresh 即可看到最新内容。
        """
        scope = self._text(scope, 'Scope')
        with self.storage.session() as conn:
            conn.execute('BEGIN')  # 与 universe() 一致：共享一个读快照
            objects = conn.execute(CURRENT + '''SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS ts
                FROM current_objects o''', {'scope': scope}).fetchone()
            topics = conn.execute('''SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS ts
                FROM collection_topics WHERE scope = ?''', (scope,)).fetchone()
            members = conn.execute('''SELECT COUNT(*) AS n, COALESCE(MAX(added_at), '') AS ts
                FROM collection_members''').fetchone()
        return f"{objects['n']}:{objects['ts']}|{topics['n']}:{topics['ts']}|{members['n']}:{members['ts']}"

    def import_markers(self, scope='default'):
        scope = self._text(scope, 'Scope')
        migration = 'explicit-title-markers-v1'
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM collection_imports WHERE scope = ? AND migration = ?', (scope, migration)).fetchone():
                return {'imported': False, 'already_imported': True, 'topics_created': 0, 'members_added': 0}
            now = utc_now().isoformat()
            created = added = 0
            # Only explicit title markers are evidence. No body inference or content mutation.
            rows = conn.execute(CURRENT + 'SELECT id, kind, title, revision FROM current_objects', {'scope': scope}).fetchall()
            for row in rows:
                for marker in dict.fromkeys(m.strip() for m in re.findall(r'【([^【】]{1,200})】', row['title']) if m.strip()):
                    found = conn.execute('SELECT id FROM collection_topics WHERE scope = ? AND title = ? AND parent_id IS NULL ORDER BY created_at, id LIMIT 1', (scope, marker)).fetchone()
                    if found:
                        topic_id = found['id']
                    else:
                        topic_id = self._create(conn, marker, scope, None)['id']
                        created += 1
                    changed = conn.execute('INSERT OR IGNORE INTO collection_members VALUES (?, ?, ?, ?)',
                                           (topic_id, row['kind'], row['id'], now)).rowcount
                    added += changed
                    if changed:
                        conn.execute('UPDATE collection_topics SET revision = revision + 1, updated_at = ? WHERE id = ?', (now, topic_id))
                    conn.execute('INSERT INTO collection_marker_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                                 (scope, row['kind'], row['id'], row['revision'], row['title'], marker, topic_id, now))
            conn.execute('INSERT INTO collection_imports VALUES (?, ?, ?)', (scope, migration, now))
            conn.execute('COMMIT')
            return {'imported': True, 'already_imported': False, 'topics_created': created, 'members_added': added}

    @staticmethod
    def _normalize_agent(author: str | None, title: str = '') -> tuple[str, str]:
        if not author:
            return 'external', '其他导入'
        lower = author.lower()
        if 'deepseek' in lower or 'harness' in lower:
            return 'deepseek', 'DeepSeek Harness 导入'
        if 'antigravity' in lower:
            return 'antigravity', 'Antigravity 导入'
        if 'codex' in lower:
            return 'codex', 'Codex 导入'
        if 'qwen' in lower:
            return 'qwen', 'Qwen 导入'
        if 'doubao' in lower:
            return 'doubao', '豆包导入'
        if 'pangu' in lower:
            return 'pangu', '盘古导入'
        if author in ('user', 'operator'):
            if title and title.startswith('【'):
                return 'external', '其他导入'
            return 'user', '我的内容'
        return 'external', '其他导入'

    def list_agents(self, scope='default'):
        # Source partitions = 我的内容 + 各导入来源。
        # 与前端 normalizeAgent / 概念定稿《我的空间》保持一致：
        # Antigravity / Codex / 豆包 / 盘古 / 其他导入（external 收纳 profile、trajectory 等批量导入）。
        AGENT_PARTITIONS = {'user', 'antigravity', 'codex', 'pangu', 'deepseek', 'qwen', 'doubao', 'external'}
        scope = self._text(scope, 'Scope')
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            query = """
            SELECT o.id, o.kind, o.author, o.title, m.topic_id, t.title AS topic_title
            FROM current_objects o
            LEFT JOIN collection_members m ON m.object_id = o.id AND m.kind = o.kind
            LEFT JOIN collection_topics t ON t.id = m.topic_id AND t.scope = o.scope
            WHERE o.scope = :scope
            """
            rows = conn.execute(CURRENT + query, {'scope': scope}).fetchall()
            agents_map = {}
            for row in rows:
                agent_id, display_name = self._normalize_agent(row['author'], row['title'] or '')
                if agent_id not in agents_map:
                    agents_map[agent_id] = {
                        'id': agent_id,
                        'name': display_name,
                        'notes_count': 0,
                        'memories_count': 0,
                        'total_items': 0,
                        'topic_ids': set(),
                        'topics': []
                    }
                entry = agents_map[agent_id]
                entry['total_items'] += 1
                if row['kind'] == 'note':
                    entry['notes_count'] += 1
                else:
                    entry['memories_count'] += 1
                if row['topic_id']:
                    entry['topic_ids'].add(row['topic_id'])

            result = []
            for agent_id, data in agents_map.items():
                if agent_id not in AGENT_PARTITIONS:
                    continue
                data['topic_count'] = len(data['topic_ids'])
                data['topic_ids'] = sorted(data.pop('topic_ids'))
                result.append(data)
            result.sort(key=lambda a: (0 if a['id'] == 'user' else 1, -a['total_items']))
            return {'agents': result, 'scope': scope, 'total': len(result)}

    def get_tractor_lines(self, scope='default', topic_id=None, limit=200):
        scope = self._text(scope, 'Scope')
        limit = max(1, min(limit, 500))
        lines = []
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            topic_rows = conn.execute("""
            SELECT id, title, parent_id FROM collection_topics WHERE scope = ?
            """, (scope,)).fetchall()
            topic_by_title_lower = {r['title'].lower().strip(): dict(r) for r in topic_rows}

            # 1. Topic Hierarchy (Parent-Child Inheritance)
            hier_rows = conn.execute("""
            SELECT c.id AS child_id, c.title AS child_title, p.id AS parent_id, p.title AS parent_title
            FROM collection_topics c
            JOIN collection_topics p ON p.id = c.parent_id
            WHERE c.scope = ?
            ORDER BY c.title
            """, (scope,)).fetchall()
            for hr in hier_rows:
                if topic_id and topic_id not in (hr['parent_id'], hr['child_id']):
                    continue
                lines.append({
                    'id': f"hier_{hr['parent_id']}_{hr['child_id']}",
                    'from_id': hr['parent_id'],
                    'to_id': hr['child_id'],
                    'from_topic_id': hr['parent_id'],
                    'to_topic_id': hr['child_id'],
                    'from_title': hr['parent_title'],
                    'to_title': hr['child_title'],
                    'type': 'INHERITANCE',
                    'relation': 'PARENT_CHILD',
                    'confidence': 1.0,
                    'cross_topic': True
                })

            # 2. Skill Space Station Tooling Lines (CITATION / USES_TOOL)
            skill_topic = None
            for r in topic_rows:
                if re.match(r'^skills?(?:\s*[/|｜]\s*.*)?$', r['title'].strip(), re.IGNORECASE) or r['id'] == '__local_skills':
                    skill_topic = r
                    break

            if skill_topic:
                skill_id = skill_topic['id']
                dev_patterns = [
                    '02-development-tools', 'ai library', 'ai-workdesk-local',
                    'general / antigravity', 'live2d-maker', 'social-app',
                    'ui-builder', 'map-maker', 'desktop-pet',
                    'autocad 2d to 3d - codex', 'antigravity harness'
                ]
                for r in topic_rows:
                    if r['id'] == skill_id:
                        continue
                    if topic_id and topic_id not in (skill_id, r['id']):
                        continue
                    t_title = r['title'].lower().strip()
                    if any(p in t_title for p in dev_patterns) or re.search(r'maker|builder|develop|engine|tools', t_title):
                        domain_id = 'ui' if re.search(r'ui|design|css|component', t_title) \
                            else 'vfx' if re.search(r'live2d|vfx|3d|cad|autocad|animat|motion|map', t_title) \
                            else 'agents' if re.search(r'agent|pet|workdesk|social|antigravity', t_title) \
                            else 'data' if re.search(r'data|library|sql|search|knowledge', t_title) \
                            else 'qa' if re.search(r'test|qa|harness|diagnos|debug', t_title) \
                            else 'writing' if re.search(r'writing|prompt|novel|talk', t_title) \
                            else 'backend'
                        lines.append({
                            'id': f"tooling_{skill_id}_{r['id']}",
                            'from_id': skill_id,
                            'to_id': r['id'],
                            'from_topic_id': skill_id,
                            'to_topic_id': r['id'],
                            'from_title': skill_topic['title'],
                            'to_title': r['title'],
                            'domain_id': domain_id,
                            'type': 'CITATION',
                            'relation': 'USES_TOOL',
                            'confidence': 1.0,
                            'cross_topic': True
                        })

            # 3. Domain Affinities / Associations (SAME_TOPIC / RELATES_TO)
            affinity_pairs = [
                ('game-design', '盘古AI叙事引擎与游戏系统'),
                ('novel-writing', '盘古AI叙事引擎与游戏系统'),
                ('desktop-pet', 'pet'),
                ('talkmaker', 'social-app'),
                ('wallpaper / codex wallpaper', 'anime 3d model'),
                ('project-assignment', '02-development-tools'),
                ('study uses', 'general / antigravity')
            ]
            for p1, p2 in affinity_pairs:
                t1 = topic_by_title_lower.get(p1.lower().strip())
                t2 = topic_by_title_lower.get(p2.lower().strip())
                if t1 and t2 and t1['id'] != t2['id']:
                    if topic_id and topic_id not in (t1['id'], t2['id']):
                        continue
                    lines.append({
                        'id': f"affinity_{t1['id']}_{t2['id']}",
                        'from_id': t1['id'],
                        'to_id': t2['id'],
                        'from_topic_id': t1['id'],
                        'to_topic_id': t2['id'],
                        'from_title': t1['title'],
                        'to_title': t2['title'],
                        'type': 'SAME_TOPIC',
                        'relation': 'RELATES_TO',
                        'confidence': 0.88,
                        'cross_topic': True
                    })

            # 4. Cross-Topic Edges / Conflicts from edges table
            # topic_id == 'all' (我的空间全量视图): return object-level edges unfiltered
            edge_params = [scope]
            edge_filter = "e.scope = ?"
            if topic_id and topic_id != 'all':
                edge_filter += " AND (m1.topic_id = ? OR m2.topic_id = ?)"
                edge_params.extend([topic_id, topic_id])

            edge_limit = max(10, limit - len(lines))
            edge_params.append(edge_limit)

            edge_rows = conn.execute(f"""
            SELECT e.id, e.source_id, e.target_id, e.relation, e.confidence,
                   m1.topic_id AS from_topic_id, m2.topic_id AS to_topic_id,
                   COALESCE(n1.title, r1.title, e.source_id) AS from_title,
                   COALESCE(n2.title, r2.title, e.target_id) AS to_title
            FROM edges e
            LEFT JOIN nodes n1 ON n1.id = e.source_id AND n1.scope = e.scope
            LEFT JOIN notes r1 ON r1.id = e.source_id AND r1.scope = e.scope
            LEFT JOIN nodes n2 ON n2.id = e.target_id AND n2.scope = e.scope
            LEFT JOIN notes r2 ON r2.id = e.target_id AND r2.scope = e.scope
            LEFT JOIN collection_members m1 ON m1.object_id = e.source_id
            LEFT JOIN collection_members m2 ON m2.object_id = e.target_id
            WHERE {edge_filter}
            LIMIT ?
            """, tuple(edge_params)).fetchall()

            for r in edge_rows:
                rel = r['relation']
                line_type = 'CITATION' if rel == 'DERIVED_FROM' else 'CONFLICT' if rel == 'CONTRADICTS' else 'INHERITANCE' if rel == 'APPLIES_TO' else 'SAME_TOPIC'
                is_cross = bool(r['from_topic_id'] and r['to_topic_id'] and r['from_topic_id'] != r['to_topic_id'])
                lines.append({
                    'id': r['id'],
                    'from_id': r['from_topic_id'] if (is_cross and not topic_id) else r['source_id'],
                    'to_id': r['to_topic_id'] if (is_cross and not topic_id) else r['target_id'],
                    'from_topic_id': r['from_topic_id'],
                    'to_topic_id': r['to_topic_id'],
                    'from_title': r['from_title'],
                    'to_title': r['to_title'],
                    'type': line_type,
                    'relation': rel,
                    'confidence': r['confidence'],
                    'cross_topic': is_cross
                })

            # 5. Shared memberships (intra-topic clustering) — skipped for the
            #    all-notes view (would explode into tens of thousands of lines)
            if len(lines) < limit and topic_id and topic_id != 'all':
                remaining = limit - len(lines)
                shared = conn.execute("""
                SELECT m1.topic_id, m1.object_id AS id1, m2.object_id AS id2,
                       COALESCE(n1.title, r1.title, m1.object_id) AS title1,
                       COALESCE(n2.title, r2.title, m2.object_id) AS title2
                FROM collection_members m1
                JOIN collection_members m2 ON m1.topic_id = m2.topic_id AND m1.object_id < m2.object_id
                JOIN collection_topics t ON t.id = m1.topic_id
                LEFT JOIN nodes n1 ON n1.id = m1.object_id AND n1.scope = t.scope
                LEFT JOIN notes r1 ON r1.id = m1.object_id AND r1.scope = t.scope
                LEFT JOIN nodes n2 ON n2.id = m2.object_id AND n2.scope = t.scope
                LEFT JOIN notes r2 ON r2.id = m2.object_id AND r2.scope = t.scope
                WHERE t.scope = ? AND m1.topic_id = ?
                LIMIT ?
                """, (scope, topic_id, remaining)).fetchall()
                for r in shared:
                    lines.append({
                        'id': f"shared_{r['topic_id']}_{r['id1']}_{r['id2']}",
                        'from_id': r['id1'],
                        'to_id': r['id2'],
                        'from_topic_id': r['topic_id'],
                        'to_topic_id': r['topic_id'],
                        'from_title': r['title1'],
                        'to_title': r['title2'],
                        'type': 'SAME_TOPIC',
                        'relation': 'SAME_TOPIC',
                        'topic_id': r['topic_id'],
                        'confidence': 1.0,
                        'cross_topic': False
                    })
        return {'lines': lines, 'total': len(lines), 'scope': scope}

    def get_cluster_suggestions(self, topic_id, scope='default', threshold=5):
        scope = self._text(scope, 'Scope')
        topic_id = self._text(topic_id, 'Topic ID')
        threshold = max(2, min(threshold, 50))
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            topic = self._get(conn, topic_id, scope)
            rows = conn.execute(CURRENT + """
            SELECT o.id, o.kind, o.title
            FROM current_objects o
            JOIN collection_members m ON m.object_id = o.id AND m.kind = o.kind
            WHERE m.topic_id = :topic_id AND o.scope = :scope
            ORDER BY o.title
            """, {'topic_id': topic_id, 'scope': scope}).fetchall()

            groups = {}
            for r in rows:
                title = r['title']
                marker_match = re.search(r'【([^【】]{2,100})】', title)
                if marker_match:
                    key = marker_match.group(1).strip()
                else:
                    sep_match = re.search(r'^([^｜|/—–-]{2,40})[｜|/—–-]', title)
                    if sep_match:
                        key = sep_match.group(1).strip()
                    else:
                        continue
                if key not in groups:
                    groups[key] = []
                groups[key].append(r['id'])

            suggestions = []
            for key, ids in groups.items():
                if len(ids) > threshold:
                    suggestions.append({
                        'title': key,
                        'count': len(ids),
                        'item_ids': ids,
                        'classification': 'title_pattern_lead_only',
                        'requires_semantic_review': True
                    })
            suggestions.sort(key=lambda s: -s['count'])
            return {'topic_id': topic_id, 'topic_title': topic['title'], 'suggestions': suggestions, 'total': len(suggestions)}

    def apply_clustering(self, topic_id, title, item_ids, scope='default'):
        scope = self._text(scope, 'Scope')
        topic_id = self._text(topic_id, 'Topic ID')
        title = self._text(title, 'Title')
        if not item_ids:
            raise ValueError('item_ids must not be empty')
        now = utc_now().isoformat()
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            parent = self._get(conn, topic_id, scope)
            if len(item_ids) > 500 or len(set(item_ids)) != len(item_ids):
                raise ValueError('Select at most 500 distinct current members')
            for obj_id in item_ids:
                present = conn.execute(CURRENT + '''SELECT 1 FROM current_objects o JOIN collection_members m
                    ON m.object_id=o.id AND m.kind=o.kind WHERE m.topic_id=:topic AND o.id=:id''',
                    {'scope': scope, 'topic': topic_id, 'id': obj_id}).fetchone()
                if not present:
                    raise ValueError('Clustering accepts only current members of the selected topic')
            existing = conn.execute("""
            SELECT id, revision FROM collection_topics
            WHERE parent_id = ? AND title = ? AND scope = ?
            """, (topic_id, title, scope)).fetchone()
            if existing:
                child_id = existing['id']
            else:
                child = self._create(conn, title, scope, parent_id=topic_id)
                child_id = child['id']

            moved = 0
            for obj_id in item_ids:
                row = conn.execute("SELECT kind FROM collection_members WHERE topic_id = ? AND object_id = ?", (topic_id, obj_id)).fetchone()
                obj_kind = row['kind'] if row else ('note' if str(obj_id).startswith('note-') else 'memory')
                ins = conn.execute("INSERT OR IGNORE INTO collection_members VALUES (?, ?, ?, ?)",
                                   (child_id, obj_kind, obj_id, now)).rowcount
                rem = conn.execute("DELETE FROM collection_members WHERE topic_id = ? AND object_id = ? AND kind = ?",
                                   (topic_id, obj_id, obj_kind)).rowcount
                if ins or rem:
                    moved += 1

            if moved:
                conn.execute("UPDATE collection_topics SET revision = revision + 1, updated_at = ? WHERE id IN (?, ?)",
                             (now, topic_id, child_id))
            conn.execute('COMMIT')
            return {'parent_id': topic_id, 'child_id': child_id, 'child_title': title, 'moved_count': moved}

    def _capability_membership(self, topic_id, scope, capability_id, expected_revision, remove):
        guard(scope, write=True)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            topic = self._get(conn, topic_id, scope)
            self._cas(topic, expected_revision)
            if not conn.execute("SELECT 1 FROM capabilities WHERE id=? AND json_extract(status_json,'$.status')='REGISTERED'", (capability_id,)).fetchone():
                raise TopicNotFound('Registered capability unavailable')
            if remove:
                changed = conn.execute('DELETE FROM collection_capability_members WHERE topic_id=? AND capability_id=?', (topic_id, capability_id)).rowcount
            else:
                changed = conn.execute('INSERT OR IGNORE INTO collection_capability_members VALUES(?,?,?)', (topic_id, capability_id, utc_now().isoformat())).rowcount
            if changed:
                conn.execute('UPDATE collection_topics SET revision=revision+1,updated_at=? WHERE id=?', (utc_now().isoformat(), topic_id))
            result = self._get(conn, topic_id, scope)
            conn.execute('COMMIT')
            return result

    def capability_members(self, topic_id, *, scope='default', offset=0, limit=50):
        self._page(offset, limit)
        with self.storage.session() as conn:
            self._get(conn, topic_id, scope)
            rows = conn.execute("SELECT c.id,c.kind,c.name,c.description,c.version,c.content_hash FROM capabilities c JOIN collection_capability_members m ON m.capability_id=c.id WHERE m.topic_id=? AND json_extract(c.status_json,'$.status')='REGISTERED' ORDER BY c.id LIMIT ? OFFSET ?", (topic_id, limit + 1, offset)).fetchall()
            return {'items': [dict(row) | {'installed': False, 'membership': 'reference_only'} for row in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit else None}
