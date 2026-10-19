from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from backend.lineage.manager import LineageManager, Snapshot


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)
    snapshot: Snapshot
    expected_revision: StrictInt | None = Field(default=None, ge=0)


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=8000)


def create_lineage_router(storage):
    manager, router = LineageManager(storage), APIRouter()

    def call(action):
        try:
            return action()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409 if 'conflict' in str(exc).lower() else 422, str(exc)) from exc

    @router.post('/api/lineage/import', status_code=201)
    def import_snapshot(req: ImportRequest):
        return call(lambda: manager.import_snapshot(req.snapshot.model_dump(mode='json'), scope=req.scope, expected_revision=req.expected_revision))

    @router.get('/api/lineage/sessions')
    def sessions(scope: str = 'default', offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        return call(lambda: manager.list_sessions(scope=scope, offset=offset, limit=limit))

    @router.get('/api/lineage/sessions/{session_ref}')
    def graph(session_ref: str, scope: str = 'default'):
        return call(lambda: manager.get_graph(session_ref, scope=scope))

    @router.get('/api/lineage/sessions/{session_ref}/events')
    def events(session_ref: str, scope: str = 'default', offset: int = Query(default=0, ge=0),
               limit: int = Query(default=50, ge=1, le=100), source_revision: int | None = Query(default=None, ge=1)):
        return call(lambda: manager.list_events(session_ref, scope=scope, offset=offset, limit=limit, source_revision=source_revision))

    @router.get('/api/lineage/sources/{source_id}')
    def trace(source_id: str, scope: str = 'default'):
        return call(lambda: manager.trace_source(source_id, scope=scope))

    @router.post('/api/lineage/sessions/{session_ref}/revoke')
    def revoke(session_ref: str, req: RevokeRequest):
        return call(lambda: manager.revoke(session_ref, **req.model_dump()))

    return router
