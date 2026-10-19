"""Read-only topic navigation with a fixed one-hop association boundary."""
from backend.spiders.references import CURRENT, prepare, current_topic, location_for


class SpiderExplorer:
    def __init__(self, service):
        self.service = service
        self.storage = service.storage

    def neighbors(self, conn, scope, origin):
        # Only real hierarchy, shared current sources and canonical edges.
        # Existing heuristic title/skill affinity lines are intentionally excluded.
        prepare(conn)
        rows = conn.execute(CURRENT + '''SELECT DISTINCT t.id,t.title FROM collection_topics t WHERE t.scope=:scope AND t.id!=:origin AND (
          t.parent_id=:origin OR t.id=(SELECT parent_id FROM collection_topics WHERE id=:origin AND scope=:scope)
          OR EXISTS(SELECT 1 FROM collection_members a JOIN collection_members b
            ON a.kind=b.kind AND a.object_id=b.object_id JOIN current_objects o ON o.kind=a.kind AND o.id=a.object_id
            WHERE a.topic_id=:origin AND b.topic_id=t.id)
          OR EXISTS(SELECT 1 FROM edges e JOIN collection_members a ON a.kind='memory' AND a.object_id=e.source_id
            JOIN collection_members b ON b.kind='memory' AND b.object_id=e.target_id
            JOIN current_objects x ON x.kind='memory' AND x.id=e.source_id
            JOIN current_objects y ON y.kind='memory' AND y.id=e.target_id
            WHERE e.scope=:scope AND ((a.topic_id=:origin AND b.topic_id=t.id) OR (b.topic_id=:origin AND a.topic_id=t.id)))
        ) ORDER BY t.id LIMIT 101''',{'scope':scope,'origin':origin}).fetchall()
        return [dict(r) for r in rows[:100]], len(rows)>100

    def topic_page(self, conn, scope, topic_id, query, limit, offset):
        topic = current_topic(conn,scope,topic_id)
        if not topic:
            raise LookupError('Topic is unavailable in this scope')
        prepare(conn)
        params = {'scope':scope,'topic':topic_id,'query':query,'limit':limit,'offset':offset}
        condition = '''FROM current_objects o JOIN collection_members m ON m.kind=o.kind AND m.object_id=o.id
          WHERE m.topic_id=:topic AND (:query='' OR instr(lower(o.title),lower(:query))>0 OR instr(lower(o.content),lower(:query))>0)'''
        total = conn.execute(CURRENT + 'SELECT COUNT(*) ' + condition,params).fetchone()[0]
        rows = conn.execute(CURRENT + '''SELECT o.id,o.kind,o.title,o.revision,
          substr(o.content,CASE WHEN :query='' OR instr(lower(o.content),lower(:query))=0 THEN 1
            ELSE max(1,instr(lower(o.content),lower(:query))-100) END,420) AS excerpt ''' + condition +
          ' ORDER BY CASE WHEN :query!=\'\' AND instr(lower(o.title),lower(:query))>0 THEN 0 ELSE 1 END,o.id LIMIT :limit OFFSET :offset',params).fetchall()
        neighbors, partial = self.neighbors(conn,scope,topic_id)
        return {'topic':topic,'items':[dict(r) for r in rows],'total':total,'has_more':offset+len(rows)<total,
                'offset':offset,'neighbors':neighbors,'neighbors_partial':partial,
                'retrieval_mode':'literal_relevance' if query else 'catalogue','historical_snapshot':False}

    def drive(self, args):
        service = self.service
        session = service.spider_activity.get(service.spider_connection,args['session_id'])
        position = session['position']
        action = args['action']
        topic_id = args.get('topic_id') or position.get('topic_id')
        origin = session['origin']
        with self.storage.session() as conn:
            conn.execute('BEGIN')
            if action in ('navigate','enter','follow','catalogue'):
                if not topic_id:
                    raise ValueError('Choose a topic before entering or reading its catalogue')
                if action == 'follow':
                    if not origin or not position.get('topic_id'):
                        raise ValueError('Navigate to an origin before following a link')
                    neighbors,_ = self.neighbors(conn,service.scope,origin)
                    current_neighbors,_ = self.neighbors(conn,service.scope,position['topic_id'])
                    allowed = {n['id'] for n in neighbors} | {origin}
                    if topic_id not in allowed or topic_id not in {n['id'] for n in current_neighbors}:
                        raise ValueError('Target is not a direct authorized association within this exploration origin')
                elif action != 'navigate' and position.get('topic_id') != topic_id:
                    raise ValueError('Navigate or follow before entering a different topic')
                page = self.topic_page(conn,service.scope,topic_id,args.get('query',''),args.get('limit',20),args.get('offset',0))
                service.access_policy.check(service.principal,service.scope)
                # All source claims and the final location belong to the same
                # returned page. Roll them back together if observation fails.
                with service.spider_activity.db() as activity_conn:
                    for item in page['items']:
                        service.spider_activity.event(args['session_id'],'drive_spider','read','completed',
                            position={'topic_id':topic_id,'kind':item['kind'],'object_id':item['id'],'revision':item['revision']},
                            update_position=False,_conn=activity_conn)
                    service.spider_activity.event(args['session_id'],'drive_spider',action,'completed',
                        position={'topic_id':topic_id},from_topic_id=position.get('topic_id'),
                        mode='crawl' if action=='follow' else ('fly' if action=='navigate' else None),
                        origin=topic_id if action=='navigate' and origin is None else None,_conn=activity_conn)
                page['exploration_origin'] = origin or topic_id
                page['association_depth_limit'] = 1
                return page
            if action == 'read':
                kind, object_id = args.get('kind'),args.get('object_id')
                if kind not in ('note','memory') or not object_id or not topic_id or topic_id != position.get('topic_id'):
                    raise ValueError('Choose a current topic and a note or memory member before reading')
                if not conn.execute('SELECT 1 FROM collection_members WHERE topic_id=? AND kind=? AND object_id=?',
                                    (topic_id,kind,object_id)).fetchone():
                    raise LookupError('Current member is unavailable in this topic')
                loc = location_for(conn,service.scope,kind,object_id)
                if not loc:
                    raise LookupError('Current member is unavailable in this scope')
                if kind == 'note':
                    result = service._scoped(service.notes_mgr.get_note(object_id)).to_dict()
                else:
                    result = service._node(object_id).model_dump(mode='json')
                service.access_policy.check(service.principal,service.scope)
                loc['topic_id'] = topic_id
                service.spider_activity.event(args['session_id'],'drive_spider',action,'completed',
                    position=loc,from_topic_id=position.get('topic_id'))
                return result
        raise ValueError('Unknown driving action')
