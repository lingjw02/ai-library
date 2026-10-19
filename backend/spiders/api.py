"""Local-owner observer. No commands or task dispatch are exposed here."""
from fastapi import APIRouter, Query
from backend.spiders.activity import SpiderActivity


def create_spider_router(storage):
    activity = SpiderActivity(storage)
    router = APIRouter()

    @router.get('/api/spider-mecha')
    def observe(scope: str = Query(default='default',min_length=1,max_length=200)):
        return activity.snapshot(scope)

    return router
