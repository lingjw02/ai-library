"""Bounded public contracts for imported evidence; no model-private reasoning."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class BoundedModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvidenceRef(BoundedModel):
    source_id: str = Field(min_length=1, max_length=200)
    source_revision: StrictInt = Field(ge=1)
    content_hash: str = Field(pattern='^[0-9a-f]{64}$')


class RecordRef(BoundedModel):
    id: str = Field(min_length=1, max_length=200)
    revision: StrictInt = Field(ge=1)


class ExecutionRecord(BoundedModel):
    run_id: str = Field(min_length=1, max_length=200)
    source_agent_profile: str = Field(min_length=1, max_length=200)
    source_event_id: str = Field(min_length=1, max_length=200)
    source_revision: StrictInt = Field(ge=1)
    scope: str = Field(min_length=1, max_length=200)
    event_type: Literal['session', 'turn', 'step', 'tool_call', 'tool_result', 'outcome', 'report', 'coverage_gap']
    schema_version: Literal['v1'] = 'v1'
    occurred_at: str | None = Field(default=None, max_length=100)
    session_ref: str | None = Field(default=None, max_length=200)
    turn_ref: str | None = Field(default=None, max_length=200)
    call_id: str | None = Field(default=None, max_length=200)
    attempt_id: str | None = Field(default=None, max_length=200)
    parent_event_ref: str | None = Field(default=None, max_length=200)
    capability_version_refs: list[str] = Field(default_factory=list, max_length=50)
    context_artifact_ref: str | None = Field(default=None, max_length=200)
    policy_version_ref: str | None = Field(default=None, max_length=200)
    result: Literal['success', 'failed', 'unknown'] = 'unknown'
    coverage: Literal['complete', 'partial', 'unavailable'] = 'partial'
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=50)

    @model_validator(mode='after')
    def paired_fields(self):
        if self.event_type in ('tool_call', 'tool_result') and not all((self.session_ref, self.turn_ref, self.call_id, self.attempt_id)):
            raise ValueError('Tool events require explicit session, turn, call and attempt IDs')
        if self.event_type == 'report' and not self.evidence_refs:
            raise ValueError('Imported reports require verifiable source evidence')
        return self


class StrategyRequest(BoundedModel):
    scope: str = Field(min_length=1, max_length=200)
    strategy_key: Literal['retrieval_ranking', 'context_config', 'skill_selection', 'workflow_recommendation', 'plugin_health', 'mcp_routing']
    author: str = Field(min_length=1, max_length=200)
    configuration: dict[str, Any] = Field(max_length=30)
    baseline_id: str | None = Field(default=None, max_length=200)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=4000)


class EvaluationRequest(BoundedModel):
    scope: str = Field(min_length=1, max_length=200)
    evaluator: str = Field(min_length=1, max_length=200)
    evaluator_version: Literal['deterministic-v1']
    dataset_ref: EvidenceRef
    record_refs: list[RecordRef] = Field(min_length=1, max_length=100)
    strategy_id: str | None = Field(default=None, max_length=200)
    expected_revision: StrictInt | None = Field(default=None, ge=1)


class TransitionRequest(BoundedModel):
    scope: str = Field(min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)
    reason: str = Field(default='', max_length=4000)
    approval_id: str | None = Field(default=None, max_length=200)


class DistillRequest(BoundedModel):
    scope: str = Field(min_length=1, max_length=200)
    evaluation_id: str = Field(min_length=1, max_length=200)
    heuristic: str = Field(min_length=1, max_length=4000)
    decision: str = Field(min_length=1, max_length=4000)
    factors: list[str] = Field(min_length=1, max_length=30)
    author: str = Field(min_length=1, max_length=200)


class ScheduleRequest(BoundedModel):
    scope: str = Field(min_length=1, max_length=200)
    trigger: Literal['new_important_knowledge', 'user_correction', 'repeated_failure', 'repeated_success', 'new_discovery', 'conflict', 'stale_information', 'low_confidence_high_usage', 'knowledge_debt']
    target_key: str = Field(min_length=1, max_length=200)
    record_refs: list[RecordRef] = Field(min_length=1, max_length=100)
    estimated_tokens: StrictInt = Field(ge=1, le=1000000)
    token_budget: StrictInt = Field(ge=0, le=10000000)
    health: dict[str, float] = Field(default_factory=dict, max_length=4)


class FinishJobRequest(TransitionRequest):
    actual_tokens: StrictInt = Field(ge=0, le=1000000)
    outcome: Literal['SUCCEEDED', 'FAILED', 'INCONCLUSIVE']
    evaluation_id: str | None = Field(default=None, max_length=200)
