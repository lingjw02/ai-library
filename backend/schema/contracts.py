"""Published per-type v2 requirements. v1 remains a legacy compatibility contract."""
from copy import deepcopy
from jsonschema import Draft202012Validator


TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 8000}
ID = {'type': 'string', 'minLength': 1, 'maxLength': 500}
STRINGS = {'type': 'array', 'items': ID, 'maxItems': 100}
REF = {'type': 'object', 'required': ['source_id', 'source_revision', 'content_hash'], 'properties': {
    'source_id': ID, 'source_revision': {'type': 'integer', 'minimum': 1},
    'content_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}


def namespace(name, required, fields):
    return {name: {'type': 'object', 'required': required, 'properties': fields}}


TYPE_FIELDS = {
    'Source': namespace('source_props', ['source_kind', 'source_id'], {'source_kind': {'enum': ['note', 'web_capture', 'file_import', 'execution', 'conversation']}, 'source_id': ID}),
    'Document': namespace('source_props', ['document_format'], {'document_format': TEXT}),
    'Chunk': namespace('source_props', ['parent_source_id', 'chunk_index'], {'parent_source_id': ID, 'chunk_index': {'type': 'integer', 'minimum': 0}}),
    'Entity': namespace('core_props', ['entity_type'], {'entity_type': TEXT, 'aliases': STRINGS}),
    'Fact': namespace('verification_props', ['status'], {'status': {'enum': ['UNVERIFIED', 'PENDING', 'VERIFIED', 'REJECTED', 'USER_VERIFIED']}}),
    'Concept': namespace('core_props', ['domain'], {'domain': TEXT}),
    'Event': namespace('core_props', ['event_type'], {'event_type': TEXT}),
    'Task': namespace('core_props', ['status', 'objective'], {'status': {'enum': ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED', 'BLOCKED']}, 'objective': TEXT}),
    'Experience': namespace('memory_props', ['outcome', 'run_ref'], {'outcome': {'enum': ['unknown', 'success', 'failure', 'partial']}, 'run_ref': ID}),
    'Decision': namespace('memory_props', ['rationale', 'decision_status'], {'rationale': TEXT, 'decision_status': {'enum': ['PROPOSED', 'APPROVED', 'SUPERSEDED', 'REVOKED']}}),
    'Reasoning': namespace('memory_props', ['premise_refs'], {'premise_refs': dict(STRINGS, minItems=1)}),
    'Evidence': namespace('source_props', ['source_id', 'source_revision', 'content_hash'], REF['properties']),
    'Hypothesis': namespace('verification_props', ['status', 'test_plan'], {'status': {'enum': ['UNTESTED', 'PENDING', 'SUPPORTED', 'REFUTED', 'INCONCLUSIVE']}, 'test_plan': TEXT}),
    'Heuristic': namespace('memory_props', ['applicability', 'limitations'], {'applicability': TEXT, 'limitations': TEXT}),
    'Strategy': namespace('memory_props', ['strategy_key', 'strategy_revision', 'status'], {'strategy_key': ID, 'strategy_revision': {'type': 'integer', 'minimum': 1}, 'status': {'enum': ['PROPOSED', 'EVALUATED', 'APPROVAL_PENDING', 'ACTIVE', 'SUPERSEDED', 'ROLLED_BACK', 'REJECTED', 'INCONCLUSIVE']}}),
    'RecoveryStrategy': namespace('memory_props', ['failure_class', 'recovery_steps'], {'failure_class': TEXT, 'recovery_steps': dict(STRINGS, minItems=1)}),
    'Conflict': namespace('core_props', ['conflict_type', 'conflicting_nodes', 'status'], {'conflict_type': {'enum': ['PROPERTY_CONFLICT', 'SEMANTIC_CONFLICT', 'TEMPORAL_CONFLICT']}, 'conflicting_nodes': dict(STRINGS, minItems=2), 'status': {'enum': ['OPEN', 'UNRESOLVED', 'UNDER_REVIEW', 'RESOLVED', 'SUPERSEDED', 'IGNORED']}}),
    'Session': namespace('source_props', ['source_system', 'native_session_id'], {'source_system': ID, 'native_session_id': ID}),
    'UserContext': namespace('core_props', ['subject_id'], {'subject_id': ID}),
    'ProjectContext': namespace('core_props', ['project_id'], {'project_id': ID}),
}


def contracts():
    return {'legacy_version': 1, 'current_version': 2, 'migration': 'explicit_preview_and_commit',
        'types': {kind: {'version': 2, 'schema': {'type': 'object', 'required': list(fields), 'properties': deepcopy(fields)}} for kind, fields in TYPE_FIELDS.items()}}


def validate_type(node):
    if node.schema_version == 1:
        return
    data = node.model_dump(mode='json')
    schema = contracts()['types'][node.node_type.value]['schema']
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise ValueError(f'{node.node_type.value} v2: {errors[0].message}')
    if not node.title.strip() or not node.content.strip():
        raise ValueError('v2 nodes require nonblank title and semantic content')
    if node.node_type.value in {'Document', 'Chunk', 'Fact', 'Evidence', 'Experience', 'Decision', 'Reasoning'} and node.provenance is None:
        raise ValueError(f'{node.node_type.value} v2 requires exact provenance')
    if node.maturity.value in {'L3', 'L4', 'L5'}:
        refs = node.verification_props.get('evidence_refs')
        if node.verification_props.get('status') not in {'VERIFIED', 'USER_VERIFIED'} or not refs:
            raise ValueError('v2 verified maturity requires verification status and evidence_refs')
        for ref in refs:
            Draft202012Validator(REF).validate(ref)
