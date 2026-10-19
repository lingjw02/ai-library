"""Explicit owner migration; immutable old snapshots remain available."""
import hashlib
from backend.auth.policy import current_access
from backend.lineage.manager import guard, stable
from backend.schema.registry import global_schema_registry


class SchemaMigrator:
    ALLOWED = {'core_props', 'source_props', 'graph_props', 'memory_props', 'verification_props', 'custom_props'}

    def __init__(self, storage):
        self.storage = storage

    def preview(self, node_id, *, scope, expected_revision, fields):
        guard(scope)
        if not isinstance(fields, dict) or not set(fields) <= self.ALLOWED:
            raise ValueError('Migration may only supply typed semantic fields')
        node = self.storage.get_node(node_id)
        if not node or node.scope != scope or not node.is_active:
            raise LookupError('Authorized node unavailable')
        if type(expected_revision) is not int or node.version != expected_revision:
            raise ValueError('Migration revision conflict')
        if node.schema_version != 1:
            raise ValueError('Only explicit v1 to v2 migration is supported')
        value = node.model_dump(mode='json')
        value.update(fields)
        value['schema_version'] = 2
        candidate = global_schema_registry.require_node(value)
        if (candidate.authority, candidate.maturity, candidate.content, candidate.provenance) != (node.authority, node.maturity, node.content, node.provenance):
            raise ValueError('Schema migration cannot alter knowledge authority or content')
        context = current_access.get()
        digest = hashlib.sha256(stable({'scope': scope, 'actor': context.principal if context else 'local-owner', 'base_revision': expected_revision, 'candidate': candidate.model_dump(mode='json')}).encode('utf-8')).hexdigest()
        return {'preview_hash': digest, 'from_schema': 1, 'to_schema': 2, 'expected_revision': expected_revision,
            'candidate': candidate.model_dump(mode='json'), 'status': 'PREVIEW_ONLY'}

    def commit(self, node_id, *, scope, expected_revision, fields, preview_hash):
        guard(scope, write=True)
        if current_access.get() is not None:
            raise PermissionError('Schema migration requires explicit local-owner commit')
        preview = self.preview(node_id, scope=scope, expected_revision=expected_revision, fields=fields)
        if preview['preview_hash'] != preview_hash:
            raise ValueError('Migration preview hash conflict')
        node = global_schema_registry.require_node(preview['candidate'])
        self.storage.upsert_node(node, expected_revision=expected_revision)
        return self.storage.get_node(node_id).model_dump(mode='json')
