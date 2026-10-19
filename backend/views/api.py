"""Trusted local-owner REST surface. This is not remote user authentication."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.views.manager import SavedViewManager, ViewConflict, ViewNotFound
from backend.views.models import SavedQuery, Workspace, query_contract


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: StrictStr = Field(default='default', min_length=1, max_length=200)


class CreateView(ScopeRequest):
    name: StrictStr = Field(min_length=1, max_length=200)
    query: SavedQuery


class UpdateView(ScopeRequest):
    expected_revision: StrictInt = Field(ge=1)
    name: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    query: SavedQuery | None = None
    archived: StrictBool | None = None

    @model_validator(mode='after')
    def changes(self):
        if self.name is None and self.query is None and self.archived is None:
            raise ValueError('Provide at least one definition change')
        return self


class ExecuteView(ScopeRequest):
    expected_revision: StrictInt = Field(ge=1)
    offset: StrictInt = Field(default=0, ge=0, le=1_000_000)
    limit: StrictInt | None = Field(default=None, ge=1, le=100)


def create_saved_view_router(storage):
    manager, router = SavedViewManager(storage), APIRouter()

    def call(action):
        try:
            return action()
        except ViewNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ViewConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, str(exc)) from exc

    @router.get('/api/saved-views/schema')
    def schema():
        return query_contract()

    @router.get('/api/saved-views')
    def list_views(scope: str = Query(default='default', min_length=1, max_length=200),
                   workspace: Workspace | None = None, include_archived: bool = False,
                   offset: int = Query(default=0, ge=0, le=1_000_000), limit: int = Query(default=50, ge=1, le=100)):
        return call(lambda: manager.list(scope=scope, workspace=workspace, include_archived=include_archived, offset=offset, limit=limit))

    @router.post('/api/saved-views', status_code=201)
    def create(req: CreateView):
        return call(lambda: manager.create(**req.model_dump(mode='json')))

    @router.get('/api/saved-views/{view_id}')
    def get(view_id: str, scope: str = Query(default='default', min_length=1, max_length=200)):
        return call(lambda: manager.get(view_id, scope=scope))

    @router.patch('/api/saved-views/{view_id}')
    def update(view_id: str, req: UpdateView):
        return call(lambda: manager.update(view_id, **req.model_dump(mode='json')))

    @router.get('/api/saved-views/{view_id}/history')
    def history(view_id: str, scope: str = Query(default='default', min_length=1, max_length=200),
                offset: int = Query(default=0, ge=0, le=1_000_000), limit: int = Query(default=50, ge=1, le=100)):
        return call(lambda: manager.history(view_id, scope=scope, offset=offset, limit=limit))

    @router.post('/api/saved-views/{view_id}/execute')
    def execute(view_id: str, req: ExecuteView):
        return call(lambda: manager.execute(view_id, **req.model_dump(mode='json')))

    return router
