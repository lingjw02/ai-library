"""Local-owner maintenance/configuration and read-only backend discovery."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from backend.lineage.manager import guard
from backend.projections.manager import GraphProjection, ViewStateStore, ViewState
from backend.maintenance.health import KnowledgeHealth
from backend.telemetry.budgets import BudgetLedger
from backend.schema.registry import global_schema_registry
from backend.schema.migrations import SchemaMigrator


class Scoped(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)


class SaveLayout(Scoped):
    owner_id: str = Field(default='local-owner', min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=0)
    state: ViewState


class Policy(Scoped):
    daily_quota: StrictInt = Field(ge=0, le=1_000_000_000)
    expected_revision: StrictInt = Field(ge=0)


class Reserve(Scoped):
    request_key: str = Field(min_length=1, max_length=200)
    tokens: StrictInt = Field(ge=0, le=1_000_000_000)
    category: Literal['read', 'write', 'distill', 'overhead'] = 'read'
    ttl_seconds: StrictInt = Field(default=300, ge=1, le=3600)


class Settle(Scoped):
    tokens: StrictInt = Field(ge=0, le=1_000_000_000)
    counting_mode: Literal['estimated', 'reported']
    cached_tokens: StrictInt | None = Field(default=None, ge=0, le=1_000_000_000)


class QueueReview(Scoped):
    report_hash: str = Field(pattern='^[0-9a-f]{64}$')
    selected_ids: list[str] = Field(min_length=1, max_length=100)
    limit: StrictInt = Field(default=1000, ge=1, le=5000)


class Migration(Scoped):
    expected_revision: StrictInt = Field(ge=1)
    fields: dict
    preview_hash: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')


def create_backend_operations_router(storage):
    graph, layout = GraphProjection(storage), ViewStateStore(storage)
    health, ledger, migrator = KnowledgeHealth(storage), BudgetLedger(storage), SchemaMigrator(storage)
    router = APIRouter(tags=['backend contracts'])

    def call(action):
        try:
            return action()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            code = 409 if any(term in str(exc).lower() for term in ('conflict', 'revision')) else 422
            if 'quota insufficient' in str(exc):
                code = 429
            raise HTTPException(code, str(exc)) from exc

    @router.get('/api/schema/contracts')
    def schemas():
        return global_schema_registry.contracts()

    @router.post('/api/schema/nodes/{node_id}/preview')
    def migration_preview(node_id: str, req: Migration):
        return call(lambda: migrator.preview(node_id, scope=req.scope, expected_revision=req.expected_revision, fields=req.fields))

    @router.post('/api/schema/nodes/{node_id}/migrate')
    def migrate(node_id: str, req: Migration):
        if not req.preview_hash:
            raise HTTPException(422, 'Preview hash required')
        return call(lambda: migrator.commit(node_id, **req.model_dump()))

    @router.get('/api/projections/knowledge/status')
    def projection_status():
        return graph.status()

    @router.post('/api/projections/knowledge/rebuild')
    def rebuild():
        return call(graph.rebuild)

    @router.post('/api/projections/knowledge/advance')
    def advance(limit: int = Query(default=200, ge=1, le=1000)):
        return call(lambda: graph.pump(limit))

    @router.get('/api/projections/knowledge')
    def projection(scope: str = 'default', node_budget: int = Query(default=100, ge=1, le=500),
                   edge_budget: int = Query(default=200, ge=0, le=1000), cursor: str | None = Query(default=None, max_length=4000), neighborhood: str | None = None):
        return call(lambda: graph.query(scope=scope, node_budget=node_budget, edge_budget=edge_budget, cursor=cursor, neighborhood=neighborhood))

    @router.get('/api/view-state')
    def get_layout(workspace_id: str, owner_id: str = 'local-owner', scope: str = 'default'):
        return call(lambda: layout.get(owner_id, workspace_id, scope))

    @router.put('/api/view-state')
    def save_layout(req: SaveLayout):
        value = req.model_dump()
        return call(lambda: layout.save(**value))

    @router.post('/api/view-state/reset')
    def reset_layout(req: SaveLayout):
        return call(lambda: layout.reset(req.owner_id, req.workspace_id, req.scope, expected_revision=req.expected_revision))

    @router.get('/api/maintenance/health')
    def health_report(scope: str = 'default', limit: int = Query(default=1000, ge=1, le=5000)):
        return call(lambda: health.report(scope=scope, limit=limit))

    @router.post('/api/maintenance/health/review')
    def queue_review(req: QueueReview):
        return call(lambda: health.queue_review(**req.model_dump()))

    @router.get('/api/maintenance/idle-plan')
    def idle_plan(scope: str = 'default', max_jobs: int = Query(default=10, ge=1, le=100), token_budget: int = Query(default=5000, ge=0, le=100_000)):
        return call(lambda: health.idle_plan(scope=scope, max_jobs=max_jobs, token_budget=token_budget))

    @router.get('/api/token-budget')
    def budget(scope: str = 'default', day: str | None = None):
        return call(lambda: ledger.status(scope=scope, day=day))

    @router.put('/api/token-budget/policy')
    def budget_policy(req: Policy):
        return call(lambda: ledger.configure(**req.model_dump()))

    @router.post('/api/token-budget/reservations', status_code=201)
    def reserve(req: Reserve):
        return call(lambda: ledger.reserve(**req.model_dump()))

    @router.post('/api/token-budget/reservations/{reservation_id}/settle')
    def settle(reservation_id: str, req: Settle):
        return call(lambda: ledger.settle(reservation_id, **req.model_dump()))

    @router.post('/api/token-budget/reservations/{reservation_id}/cancel')
    def cancel(reservation_id: str, req: Scoped):
        return call(lambda: ledger.cancel(reservation_id, scope=req.scope))

    return router
