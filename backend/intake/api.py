"""Local-owner REST contract for explicit prepare/preview/confirm source intake."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool

from backend.intake.manager import IntakeManager, IntakeConflict


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)


class PrepareRequest(ScopeRequest):
    source_type: Literal['manual_note', 'file_import', 'web_capture']
    title: str = Field(default='', max_length=500)
    content: str | None = Field(default=None, max_length=IntakeManager.MAX_BYTES)
    filename: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=4096)
    source_key: str | None = Field(default=None, min_length=1, max_length=500)


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)


class CollectionReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)


class ConfirmRequest(ScopeRequest):
    expected_revision: StrictInt = Field(ge=1)
    expected_hash: str = Field(min_length=64, max_length=64, pattern='^[a-f0-9]{64}$')
    idempotency_key: str = Field(min_length=1, max_length=200)
    selection: Selection
    collections: list[CollectionReference] = Field(default_factory=list, max_length=100)
    user_comment: str = Field(default='', max_length=65536)


class CommentRequest(ScopeRequest):
    content: str = Field(min_length=1, max_length=65536)
    expected_revision: StrictInt = Field(ge=1)


def create_intake_router(storage, notes_mgr, raw_store=None):
    manager = IntakeManager(storage, notes_mgr, raw_store)
    router = APIRouter()
    # Exposed for owner startup cleanup and service-level integration, not a route.
    router.intake_manager = manager

    def call(operation):
        try:
            return operation()
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except IntakeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/api/intake/support')
    def support():
        return manager.supported()

    @router.post('/api/intake/prepare', status_code=201)
    def prepare(request: PrepareRequest):
        return call(lambda: manager.prepare(**request.model_dump()))

    @router.post('/api/intake/files', status_code=201)
    async def upload(request: Request, filename: str = Query(min_length=1, max_length=255),
                     scope: str = Query(default='default', min_length=1, max_length=200),
                     title: str = Query(default='', max_length=500),
                     source_key: str | None = Query(default=None, min_length=1, max_length=500)):
        """Bounded raw-body upload. Filename is metadata, never a server file path."""
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > manager.MAX_BYTES:
                raise HTTPException(413, 'Uploaded file exceeds the source byte limit')
        return await run_in_threadpool(call, lambda: manager.prepare('file_import', content=bytes(data),
                                      filename=filename, scope=scope, title=title, source_key=source_key))

    @router.get('/api/intake/items/{note_id}')
    def item(note_id: str, scope: str = Query(default='default', min_length=1, max_length=200),
             revision: int | None = Query(default=None, ge=1)):
        return call(lambda: manager.get_item(note_id, scope=scope, revision=revision))

    @router.post('/api/intake/items/{note_id}/comments', status_code=201)
    def comment(note_id: str, request: CommentRequest):
        return call(lambda: manager.add_comment(note_id, **request.model_dump()))

    @router.get('/api/intake/{preview_id}')
    def preview(preview_id: str, scope: str = Query(default='default', min_length=1, max_length=200)):
        return call(lambda: manager.get(preview_id, scope=scope))

    @router.post('/api/intake/{preview_id}/confirm')
    def confirm(preview_id: str, request: ConfirmRequest):
        return call(lambda: manager.confirm(preview_id, **request.model_dump()))

    @router.post('/api/intake/{preview_id}/cancel')
    def cancel(preview_id: str, request: ScopeRequest):
        return call(lambda: manager.cancel(preview_id, **request.model_dump()))

    @router.post('/api/intake/{preview_id}/retry')
    def retry(preview_id: str, request: ScopeRequest):
        return call(lambda: manager.retry(preview_id, **request.model_dump()))

    return router
