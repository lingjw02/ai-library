"""Organization audit/outbox is separate from System Memory Logsheet."""


def initialize_audit(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS collection_capability_members (
            topic_id TEXT NOT NULL REFERENCES collection_topics(id),
            capability_id TEXT NOT NULL REFERENCES capabilities(id), added_at TEXT NOT NULL,
            PRIMARY KEY(topic_id,capability_id));
        CREATE TABLE IF NOT EXISTS collection_organization_audit (
            event_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, scope TEXT NOT NULL,
            operation TEXT NOT NULL, details_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
    ''')
    for table, operations in {'collection_topics': ('INSERT', 'UPDATE', 'DELETE'),
                              'collection_members': ('INSERT', 'DELETE'),
                              'collection_capability_members': ('INSERT', 'DELETE')}.items():
        for operation in operations:
            row = 'OLD' if operation == 'DELETE' else 'NEW'
            if table == 'collection_topics':
                topic, scope = f'{row}.id', f'{row}.scope'
                detail = f"json_object('scope',{scope},'revision',{row}.revision,'title',{row}.title)"
            else:
                topic = f'{row}.topic_id'
                scope = f'(SELECT scope FROM collection_topics WHERE id={topic})'
                if table == 'collection_members':
                    detail = f"json_object('scope',{scope},'kind',{row}.kind,'object_id',{row}.object_id)"
                else:
                    detail = f"json_object('scope',{scope},'kind','capability','object_id',{row}.capability_id)"
            label = table.upper() + '_' + operation
            conn.executescript(f'''
                CREATE TRIGGER IF NOT EXISTS audit_{table}_{operation.lower()} AFTER {operation} ON {table}
                BEGIN
                  INSERT INTO collection_organization_audit VALUES(
                    lower(hex(randomblob(16))),{topic},{scope},'{label}',{detail},strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                  INSERT INTO transactional_outbox(id,aggregate_type,aggregate_id,event_type,payload_json,status,created_at)
                    VALUES('out-'||lower(hex(randomblob(16))),'COLLECTION',{topic},'{label}',{detail},'PENDING',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                END;
            ''')
