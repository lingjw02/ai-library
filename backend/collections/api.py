"""Local-owner HTTP boundary for scoped collection browsing and explicit edits."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.collections.manager import CollectionManager, TopicConflict, TopicNotFound
from backend.schema.models import utc_now


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)


class CreateTopicRequest(ScopeRequest):
    title: str = Field(min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, min_length=1, max_length=200)


class RenameTopicRequest(ScopeRequest):
    title: str = Field(min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)


class MembershipRequest(ScopeRequest):
    kind: Literal['note', 'memory', 'capability']
    object_id: str = Field(min_length=1, max_length=500)
    expected_revision: StrictInt = Field(ge=1)


class ClusterTopicRequest(ScopeRequest):
    title: str = Field(min_length=1, max_length=200)
    item_ids: list[str] = Field(min_length=1, max_length=500)


def create_collection_router(storage):
    manager = CollectionManager(storage)
    router = APIRouter()

    def call(operation):
        try:
            return operation()
        except TopicNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except TopicConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.get('/api/topics/{topic_id}/capabilities')
    def capability_members(topic_id: str, scope: str = 'default',
                           offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        return call(lambda: manager.capability_members(topic_id, scope=scope, offset=offset, limit=limit))

    @router.get('/api/universe')
    def universe(scope: str = Query(default='default', min_length=1, max_length=200),
                 topic_id: str | None = Query(default=None, max_length=200),
                 query: str = Query(default='', max_length=2000), kind: Literal['all', 'note', 'memory'] = 'all',
                 agent_id: str | None = Query(default=None, max_length=100),
                 offset: int = Query(default=0, ge=0), limit: int = Query(default=60, ge=1, le=500)):
        return call(lambda: manager.universe(scope, topic_id, query, kind, agent_id, offset, limit))

    @router.get('/api/universe/fingerprint')
    def universe_fingerprint(scope: str = Query(default='default', min_length=1, max_length=200)):
        """星图内容指纹：内容变更（含外部进程/其他会话写入）时该值会变化，
        前端据此自动刷新视图，无需手动 refresh。"""
        return {'fingerprint': manager.content_fingerprint(scope), 'checked_at': utc_now().isoformat()}

    @router.get('/api/universe/agents')
    def agents(scope: str = Query(default='default', min_length=1, max_length=200)):
        return call(lambda: manager.list_agents(scope))

    @router.get('/api/universe/tractor-lines')
    def tractor_lines(scope: str = Query(default='default', min_length=1, max_length=200),
                      topic_id: str | None = Query(default=None, max_length=200),
                      limit: int = Query(default=200, ge=1, le=500)):
        return call(lambda: manager.get_tractor_lines(scope, topic_id, limit))

    @router.get('/api/topics')
    def topics(scope: str = Query(default='default', min_length=1, max_length=200),
               offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)):
        return call(lambda: manager.list_topics(scope, offset, limit))

    @router.get('/api/topics/{topic_id}/cluster-suggestions')
    def cluster_suggestions(topic_id: str,
                            scope: str = Query(default='default', min_length=1, max_length=200),
                            threshold: int = Query(default=5, ge=2, le=50)):
        return call(lambda: manager.get_cluster_suggestions(topic_id, scope, threshold))

    @router.post('/api/topics/{topic_id}/cluster')
    def cluster_apply(topic_id: str, req: ClusterTopicRequest):
        return call(lambda: manager.apply_clustering(topic_id, req.title, req.item_ids, req.scope))

    @router.post('/api/topics', status_code=201)
    def create(req: CreateTopicRequest):
        return call(lambda: manager.create(**req.model_dump()))

    @router.post('/api/topics/import-markers')
    def import_markers(req: ScopeRequest):
        return call(lambda: manager.import_markers(req.scope))

    @router.patch('/api/topics/{topic_id}')
    def rename(topic_id: str, req: RenameTopicRequest):
        return call(lambda: manager.rename(topic_id, **req.model_dump()))

    @router.post('/api/topics/{topic_id}/members')
    def add(topic_id: str, req: MembershipRequest):
        return call(lambda: manager.membership(topic_id, **req.model_dump()))

    @router.delete('/api/topics/{topic_id}/members')
    def remove(topic_id: str, req: MembershipRequest):
        return call(lambda: manager.membership(topic_id, **req.model_dump(), remove=True))

    @router.post('/api/topics/{topic_id}/split')
    def split(topic_id: str, req: ScopeRequest):
        return call(lambda: manager.split_topic(topic_id, req.scope))

    return router
