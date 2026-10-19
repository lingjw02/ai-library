"""
Schema definitions, Node types, Relationships, Maturity levels, and Attribute Namespaces.
Ref: AI Knowledge OS Backend Specification §§4, 6, 7, 10, 12, 13, 14, 55.
"""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NodeType(str, Enum):
    """The 20 strictly typed Graph Node types from §6."""
    SOURCE = "Source"
    DOCUMENT = "Document"
    CHUNK = "Chunk"
    ENTITY = "Entity"
    FACT = "Fact"
    CONCEPT = "Concept"
    EVENT = "Event"
    TASK = "Task"
    EXPERIENCE = "Experience"
    DECISION = "Decision"
    REASONING = "Reasoning"
    EVIDENCE = "Evidence"
    HYPOTHESIS = "Hypothesis"
    HEURISTIC = "Heuristic"
    STRATEGY = "Strategy"
    RECOVERY_STRATEGY = "RecoveryStrategy"
    CONFLICT = "Conflict"
    SESSION = "Session"
    USER_CONTEXT = "UserContext"
    PROJECT_CONTEXT = "ProjectContext"


class RelationshipType(str, Enum):
    """The strictly typed relationship types from §7."""
    DERIVED_FROM = "DERIVED_FROM"
    EVIDENCED_BY = "EVIDENCED_BY"
    ABOUT = "ABOUT"
    RELATED_TO = "RELATED_TO"
    INSTANCE_OF = "INSTANCE_OF"
    PART_OF = "PART_OF"
    CAUSED = "CAUSED"
    RESULTED_IN = "RESULTED_IN"
    FAILED_WITH = "FAILED_WITH"
    SUCCEEDED_WITH = "SUCCEEDED_WITH"
    DECIDED_BY = "DECIDED_BY"
    LEARNED_FROM = "LEARNED_FROM"
    DISTILLED_FROM = "DISTILLED_FROM"
    APPLIES_TO = "APPLIES_TO"
    RECOVERS_FROM = "RECOVERS_FROM"
    CONTRADICTS = "CONTRADICTS"
    SUPERSEDES = "SUPERSEDES"
    VALID_DURING = "VALID_DURING"
    OBSERVED_AT = "OBSERVED_AT"
    UPDATED_AT = "UPDATED_AT"
    SAME_AS = "SAME_AS"
    POSSIBLY_SAME_AS = "POSSIBLY_SAME_AS"
    SUPPORTED_BY = "SUPPORTED_BY"
    REJECTED_BY = "REJECTED_BY"


class MemoryMaturity(str, Enum):
    """Maturity levels L0–L5 from §10.1."""
    L0_RAW = "L0"                    # Raw input preserved; not yet structured
    L1_INDEXED = "L1"                # Has title, source, timestamp, tags, indexed
    L2_STRUCTURED = "L2"            # Extracted typed entity/fact/hypothesis
    L3_VERIFIED = "L3"              # Passed evidence, temporal & conflict verification
    L4_CONSOLIDATED = "L4"          # Traceably consolidated & distilled
    L5_CANONICAL = "L5"             # Accepted stable state within scope


class MemoryDepth(str, Enum):
    """Granularity depths D0–D4 from §10.2."""
    D0_DOMAIN = "D0"                # Domain level (highest overview)
    D1_TOPIC = "D1"                 # Topic level
    D2_ENTITY_CONCEPT = "D2"        # Entity or concept level
    D3_FACT_RELATION = "D3"         # Fact or relation level
    D4_EVIDENCE_RAW = "D4"          # Evidence or raw detail level


class MemoryAuthority(str, Enum):
    """Authority classifications from §55."""
    EXPLICIT = "EXPLICIT"            # Explicitly taught or configured by user
    OBSERVED = "OBSERVED"            # Directly observed facts/events
    INFERRED = "INFERRED"            # Inferred by LLM/reasoning (cannot pose as fact)


class ConflictType(str, Enum):
    """The 4 conflict classifications from §4."""
    SCHEMA_CONFLICT = "SCHEMA_CONFLICT"        # Invalid types/schema -> Quarantine
    PROPERTY_CONFLICT = "PROPERTY_CONFLICT"    # Same entity, mutually exclusive property
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"    # Contradicting claims -> Conflict Node
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"    # Time validity or precedence conflict


class CandidateLifecycleStatus(str, Enum):
    """Candidate Bridge workflow statuses from §5."""
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


class TemporalValidity(BaseModel):
    """Temporal validity boundary model (§28)."""
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    observed_at: datetime = Field(default_factory=utc_now)
    superseded_by: Optional[str] = None
    as_of: Optional[datetime] = None

    model_config = ConfigDict(extra="ignore")


class Provenance(BaseModel):
    """Provenance tracing model (§5, §11)."""
    source_id: str
    source_revision: int = 1
    content_hash: str
    extractor_version: str = "v1.0"
    created_at: datetime = Field(default_factory=utc_now)
    author_or_actor: str = "system"

    model_config = ConfigDict(extra="ignore")


class GraphNode(BaseModel):
    """Canonical Graph Node representation adhering to §6, §10, §13."""
    id: str
    node_type: NodeType
    schema_version: int = Field(default=1, ge=1, le=2, strict=True)
    scope: str = "default"
    title: str
    content: str
    maturity: MemoryMaturity = MemoryMaturity.L0_RAW
    depth: MemoryDepth = MemoryDepth.D2_ENTITY_CONCEPT
    authority: MemoryAuthority = MemoryAuthority.INFERRED
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    version: int = 1
    is_active: bool = True
    
    # Attribute Namespaces (§13)
    core_props: Dict[str, Any] = Field(default_factory=dict)
    source_props: Dict[str, Any] = Field(default_factory=dict)
    graph_props: Dict[str, Any] = Field(default_factory=dict)
    memory_props: Dict[str, Any] = Field(default_factory=dict)
    verification_props: Dict[str, Any] = Field(default_factory=dict)
    temporal_props: TemporalValidity = Field(default_factory=TemporalValidity)
    custom_props: Dict[str, Any] = Field(default_factory=dict)

    provenance: Optional[Provenance] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="ignore")


class GraphEdge(BaseModel):
    """Canonical Graph Edge representation adhering to §7, §11."""
    id: str
    source_id: str
    target_id: str
    version: int = Field(default=1, ge=1)
    relation: RelationshipType
    scope: str = "default"
    weight: float = Field(default=1.0, ge=0.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    authority: MemoryAuthority = MemoryAuthority.INFERRED
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="ignore")


class QuarantineRecord(BaseModel):
    """Quarantine record for bad data, schema errors and malformed inputs (§16)."""
    id: str
    payload: Dict[str, Any]
    reason: str
    conflict_type: ConflictType = ConflictType.SCHEMA_CONFLICT
    source_id: Optional[str] = None
    quarantined_at: datetime = Field(default_factory=utc_now)
    resolved: bool = False
    resolution_notes: Optional[str] = None
    scope: Optional[str] = None
    source_revision: Optional[int] = None
    revision: int = Field(default=1, ge=1)
    submission: Optional[Dict[str, Any]] = None
    result_candidate_id: Optional[str] = None

    model_config = ConfigDict(extra="ignore")
