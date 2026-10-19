"""Saved queries contain declarative data only; all SQL identifiers are fixed here."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from backend.maintenance.backlog import ApprovalStatus, BacklogPriority, BacklogStatus, BacklogType
from backend.maintenance.logsheet import LogsheetEventType
from backend.schema.models import MemoryAuthority, MemoryDepth, MemoryMaturity, NodeType


Workspace = Literal['notes', 'memory', 'backlog', 'logsheet']


def values(enum):
    return [item.value for item in enum]


FILTERS = {
    'notes': {'query': None, 'author': None, 'topic_id': None},
    'memory': {'query': None, 'topic_id': None, 'node_type': values(NodeType),
               'authority': values(MemoryAuthority), 'maturity': values(MemoryMaturity),
               'depth': values(MemoryDepth), 'verification_status': ['UNVERIFIED', 'VERIFIED', 'REJECTED', 'PENDING']},
    'backlog': {'query': None, 'status': values(BacklogStatus), 'type': values(BacklogType),
                'priority': values(BacklogPriority), 'approval_status': values(ApprovalStatus)},
    'logsheet': {'query': None, 'event_type': values(LogsheetEventType), 'backlog_id': None},
}
SORT_FIELDS = {
    'notes': ['updated_at', 'title', 'id'],
    'memory': ['updated_at', 'title', 'id', 'maturity', 'depth'],
    'backlog': ['updated_at', 'created_at', 'id', 'priority'],
    'logsheet': ['updated_at', 'occurred_at', 'id'],
}


def aware_time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError('timezone is required')
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ValueError('Time must be an ISO timestamp with an explicit timezone') from exc


def timestamp_microseconds(value):
    """Exact canonical instant; legacy naive timestamps follow existing UTC read policy."""
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None  # An invalid canonical time cannot qualify through a time filter.


class SortField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: StrictStr
    direction: Literal['ASC', 'DESC'] = 'ASC'


class SavedQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['v1'] = 'v1'
    workspace: Workspace
    filters: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=10)
    sort: list[SortField] = Field(default_factory=lambda: [
        SortField(field='updated_at', direction='DESC'), SortField(field='id')], min_length=1, max_length=4)
    limit: StrictInt = Field(default=50, ge=1, le=100)
    updated_within_days: StrictInt | None = Field(default=None, ge=1, le=3650)
    updated_after: StrictStr | None = Field(default=None, max_length=50)
    updated_before: StrictStr | None = Field(default=None, max_length=50)

    @model_validator(mode='after')
    def validate_contract(self):
        for key, value in self.filters.items():
            if key not in FILTERS[self.workspace]:
                raise ValueError(f'Filter {key} is unavailable in {self.workspace}')
            allowed = FILTERS[self.workspace][key]
            if allowed is not None and value not in allowed:
                raise ValueError(f'Invalid value for {key}')
            if len(value) > (2000 if key == 'query' else 200) or (key != 'query' and not value.strip()):
                raise ValueError(f'Invalid length for {key}')
        fields = [item.field for item in self.sort]
        if len(set(fields)) != len(fields) or any(f not in SORT_FIELDS[self.workspace] for f in fields):
            raise ValueError('Unsupported or duplicate sort field')
        after = aware_time(self.updated_after) if self.updated_after is not None else None
        before = aware_time(self.updated_before) if self.updated_before is not None else None
        if after and before and after >= before:
            raise ValueError('updated_after must precede updated_before')
        return self


def query_contract():
    return {'query_schema_version': 'v1', 'workspaces': list(FILTERS),
            'filters': FILTERS, 'sort_fields': SORT_FIELDS, 'max_page_size': 100,
            'max_offset': 1_000_000, 'execution_timeout_seconds': 2,
            'time_basis': {'notes': 'current revision created_at', 'memory': 'updated_at',
                           'backlog': 'updated_at', 'logsheet': 'recorded_at'},
            'query_schema': SavedQuery.model_json_schema(),
            'result_kind': 'current_dynamic_query', 'historical_as_of': False}
