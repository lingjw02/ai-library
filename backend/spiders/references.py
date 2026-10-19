"""Current scoped references; historical content never becomes a current location."""
from backend.core.retrieval.keyword_index import currently_valid
from backend.schema.models import GraphNode


CURRENT = """WITH current_objects AS (
 SELECT id, 'note' AS kind, title, content, revision, scope FROM notes
 WHERE is_current=1 AND scope=:scope
 UNION ALL
 SELECT id, 'memory' AS kind, title, content, version AS revision, scope FROM nodes
 WHERE is_active=1 AND scope=:scope AND spider_memory_valid(data_json)=1
) """


def prepare(conn):
    def valid(data):
        try:
            return int(currently_valid(GraphNode.model_validate_json(data)))
        except (ValueError, TypeError, OverflowError):
            return 0
    conn.create_function('spider_memory_valid', 1, valid)


def current_ref(conn, scope, kind, object_id):
    prepare(conn)
    row = conn.execute(CURRENT + 'SELECT id, kind, title, revision FROM current_objects WHERE id=:id AND kind=:kind',
                       {'scope':scope,'id':object_id,'kind':kind}).fetchone()
    return dict(row) if row else None


def current_topic(conn, scope, topic_id):
    row = conn.execute('SELECT id,title,parent_id,revision FROM collection_topics WHERE scope=? AND id=?',
                       (scope,topic_id)).fetchone()
    return dict(row) if row else None


def location_for(conn, scope, kind, object_id):
    ref = current_ref(conn,scope,kind,object_id)
    if not ref:
        return None
    topic = conn.execute('''SELECT t.id FROM collection_members m JOIN collection_topics t ON t.id=m.topic_id
      WHERE t.scope=? AND m.kind=? AND m.object_id=? ORDER BY t.id LIMIT 1''',(scope,kind,object_id)).fetchone()
    return {'topic_id':topic['id'] if topic else None,'kind':kind,'object_id':object_id,'revision':ref['revision']}
