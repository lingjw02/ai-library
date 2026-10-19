"""
Schema Registry and Validation Engine.
Ref: AI Knowledge OS Backend Specification §§3, 4, 12, 13, 14, 16.
Enforces that knowledge conflicts or validation failures become DATA STATES,
never unhandled system crashes.
"""

from __future__ import annotations
import uuid
import math
from typing import Any, Dict, Optional, Tuple, Callable
from pydantic import ValidationError
from backend.schema.models import (
    GraphNode,
    GraphEdge,
    NodeType,
    RelationshipType,
    QuarantineRecord,
    ConflictType,
    utc_now,
)


class SchemaValidationResult:
    """Encapsulates the result of schema validation."""
    def __init__(
        self,
        is_valid: bool,
        node: Optional[GraphNode] = None,
        quarantine_record: Optional[QuarantineRecord] = None,
        error_message: Optional[str] = None,
    ):
        self.is_valid = is_valid
        self.node = node
        self.quarantine_record = quarantine_record
        self.error_message = error_message


class SchemaValidationError(ValueError):
    """A rejected write; adapters may persist its original input to quarantine."""
    def __init__(self, result):
        super().__init__(result.error_message or 'Schema validation failed')
        self.quarantine_record = result.quarantine_record


def _aware(value, name):
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f'{name} must include a timezone')


def _interval(start, end):
    _aware(start, 'valid_from')
    _aware(end, 'valid_to')
    if start is not None and end is not None and start >= end:
        raise ValueError('valid_to must be later than valid_from')


def _common(value):
    for name in ('id', 'scope'):
        if not getattr(value, name).strip():
            raise ValueError(f'{name} must not be blank')
    if value.version < 1:
        raise ValueError('version must be positive')
    _aware(value.created_at, 'created_at')
    for name in ('confidence', 'importance', 'weight'):
        number = getattr(value, name, None)
        if number is not None and not math.isfinite(number):
            raise ValueError(f'{name} must be finite')


class SchemaRegistry:
    """
    Central registry for node schemas and constraints.
    Common write invariants and registered domain validators. The descriptive
    type contracts below are not yet a complete set of per-type schemas.
    """

    def __init__(self):
        self._type_schemas: Dict[NodeType, Dict[str, Any]] = {}
        self._custom_validators: Dict[NodeType, list[Callable[[GraphNode], Tuple[bool, Optional[str]]]]] = {}
        self._schema_version: int = 1
        self._register_default_schemas()

    def _register_default_schemas(self) -> None:
        """Register default schemas and constraints for all 20 node types."""
        # Core property requirements for specific types
        self._type_schemas[NodeType.DECISION] = {
            "required_fields": ["title", "content"],
            "custom_rules": "Must record authority and rationale",
        }
        self._type_schemas[NodeType.EVIDENCE] = {
            "required_fields": ["source_props"],
            "custom_rules": "Must bind to concrete source reference",
        }
        self._type_schemas[NodeType.EXPERIENCE] = {
            "required_fields": ["title", "content"],
            "custom_rules": "Outcome should be evaluated",
        }
        self._type_schemas[NodeType.CONFLICT] = {
            "required_fields": ["title", "content"],
            "custom_rules": "Must state conflicting parties or claims",
        }

    def register_validator(
        self,
        node_type: NodeType,
        validator_fn: Callable[[GraphNode], Tuple[bool, Optional[str]]],
    ) -> None:
        """Register a domain-specific custom validator for a node type."""
        if node_type not in self._custom_validators:
            self._custom_validators[node_type] = []
        self._custom_validators[node_type].append(validator_fn)

    def contracts(self):
        from backend.schema.contracts import contracts
        return contracts()

    def validate_node_data(
        self,
        raw_data: Dict[str, Any],
        source_id: Optional[str] = None,
    ) -> SchemaValidationResult:
        """
        Validate raw node dictionary. If invalid, generates a QuarantineRecord
        instead of crashing the caller.
        """
        # Check basic ID and type
        try:
            node = GraphNode.model_validate(raw_data)
            from backend.schema.contracts import validate_type
            validate_type(node)
            _common(node)
            _aware(node.updated_at, 'updated_at')
            _interval(node.temporal_props.valid_from, node.temporal_props.valid_to)
            _aware(node.temporal_props.observed_at, 'observed_at')
            _aware(node.temporal_props.as_of, 'as_of')
            if node.provenance is not None:
                if node.provenance.source_revision < 1 or not node.provenance.source_id.strip():
                    raise ValueError('Provenance requires a source and positive revision')
                _aware(node.provenance.created_at, 'provenance.created_at')
        except ValidationError as ve:
            quarantine = QuarantineRecord(
                id=f"quar-{uuid.uuid4().hex[:12]}",
                payload=raw_data,
                reason=f"Pydantic Validation Error: {str(ve)}",
                conflict_type=ConflictType.SCHEMA_CONFLICT,
                source_id=source_id,
                quarantined_at=utc_now(),
            )
            return SchemaValidationResult(
                is_valid=False,
                quarantine_record=quarantine,
                error_message=str(ve),
            )
        except Exception as ex:
            quarantine = QuarantineRecord(
                id=f"quar-{uuid.uuid4().hex[:12]}",
                payload=raw_data,
                reason=f"Schema validation failed: {str(ex)}",
                conflict_type=ConflictType.SCHEMA_CONFLICT,
                source_id=source_id,
                quarantined_at=utc_now(),
            )
            return SchemaValidationResult(
                is_valid=False,
                quarantine_record=quarantine,
                error_message=str(ex),
            )

        # Run registered custom validators
        if node.node_type in self._custom_validators:
            for validator in self._custom_validators[node.node_type]:
                try:
                    passed, err = validator(node)
                except Exception as exc:
                    passed, err = False, f'Validator failed: {exc}'
                if not passed:
                    quarantine = QuarantineRecord(
                        id=f"quar-{uuid.uuid4().hex[:12]}",
                        payload=raw_data,
                        reason=f"Custom validation failed for {node.node_type}: {err}",
                        conflict_type=ConflictType.SCHEMA_CONFLICT,
                        source_id=source_id,
                        quarantined_at=utc_now(),
                    )
                    return SchemaValidationResult(
                        is_valid=False,
                        quarantine_record=quarantine,
                        error_message=err,
                    )

        return SchemaValidationResult(is_valid=True, node=node)

    def require_node(self, raw_data: Dict[str, Any]) -> GraphNode:
        result = self.validate_node_data(raw_data)
        if not result.is_valid:
            raise SchemaValidationError(result)
        return result.node

    def validate_edge(self, edge_data: Dict[str, Any]) -> Tuple[bool, Optional[GraphEdge], Optional[str]]:
        """Validate edge data against GraphEdge schema."""
        try:
            edge = GraphEdge.model_validate(edge_data)
            _common(edge)
            _interval(edge.valid_from, edge.valid_to)
            if not edge.source_id.strip() or not edge.target_id.strip():
                raise ValueError('Edge endpoints must not be blank')
            return True, edge, None
        except (ValueError, TypeError) as ve:
            return False, None, str(ve)


# Global default registry instance
global_schema_registry = SchemaRegistry()
