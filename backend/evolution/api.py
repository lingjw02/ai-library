"""REST service boundary. Call once at owner startup, then include returned router."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from backend.evolution.manager import EvolutionManager, EvolutionConflict
from backend.evolution.models import ExecutionRecord, StrategyRequest, EvaluationRequest, DistillRequest, ScheduleRequest, TransitionRequest, FinishJobRequest


def create_evolution_router(storage, *, manager=None):
    manager=manager or EvolutionManager(storage)
    router=APIRouter(prefix='/api/evolution',tags=['evolution'])

    def execute(fn,*args,**kwargs):
        try:
            return fn(*args,**kwargs)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from exc
        except EvolutionConflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(422,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @router.post('/records',status_code=201)
    def import_record(data:ExecutionRecord,expected_revision:int|None=Query(default=None,ge=1)):
        return execute(manager.import_record,data.model_dump(mode='json'),expected_revision)

    @router.get('/records')
    def records(scope:str=Query(min_length=1,max_length=200),limit:int=Query(default=100,ge=1,le=200),offset:int=Query(default=0,ge=0)):
        return execute(manager.list_records,scope,limit,offset)

    @router.get('/records/{id}')
    def record(id:str,scope:str=Query(min_length=1,max_length=200),revision:int|None=Query(default=None,ge=1)):
        item=execute(manager.get_record,id,scope=scope,revision=revision)
        if item is None:
            raise HTTPException(404,'Execution revision not found in scope')
        return item

    @router.post('/evaluations',status_code=201)
    def evaluate(data:EvaluationRequest):
        return execute(manager.evaluate,**data.model_dump(mode='json'))

    @router.post('/strategies',status_code=201)
    def strategy(data:StrategyRequest):
        return execute(manager.propose_strategy,**data.model_dump(mode='json'))

    @router.get('/strategies/{id}')
    def get_strategy(id:str,scope:str=Query(min_length=1,max_length=200),revision:int|None=Query(default=None,ge=1)):
        item=execute(manager.get_strategy,id,scope=scope,revision=revision)
        if item is None:
            raise HTTPException(404,'Strategy revision not found in scope')
        return item

    @router.post('/strategies/{id}/request-approval')
    def request_approval(id:str,data:TransitionRequest):
        return execute(manager.request_approval,id,scope=data.scope,expected_revision=data.expected_revision)

    @router.post('/strategies/{id}/approve',status_code=201)
    def approve(id:str,data:TransitionRequest):
        return execute(manager.approve,id,scope=data.scope,expected_revision=data.expected_revision,reason=data.reason)

    @router.post('/strategies/{id}/activate')
    def activate(id:str,data:TransitionRequest):
        return execute(manager.activate,id,scope=data.scope,expected_revision=data.expected_revision,approval_id=data.approval_id)

    @router.post('/strategies/{id}/rollback')
    def rollback(id:str,data:TransitionRequest):
        return execute(manager.rollback,id,scope=data.scope,expected_revision=data.expected_revision,reason=data.reason)

    @router.get('/active/{strategy_key}')
    def active(strategy_key:str,scope:str=Query(min_length=1,max_length=200)):
        return {'scope':scope,'strategy_key':strategy_key,'configuration':execute(manager.active_configuration,scope,strategy_key)}

    @router.post('/experiences',status_code=201)
    def distill(data:DistillRequest):
        return execute(manager.distill,**data.model_dump(mode='json'))

    @router.post('/jobs',status_code=201)
    def schedule(data:ScheduleRequest):
        return execute(manager.schedule,**data.model_dump(mode='json'))

    @router.post('/jobs/{id}/claim')
    def claim(id:str,data:TransitionRequest):
        return execute(manager.claim_job,id,scope=data.scope,expected_revision=data.expected_revision)

    @router.post('/jobs/{id}/finish')
    def finish(id:str,data:FinishJobRequest):
        fields=data.model_dump(exclude={'approval_id'})
        return execute(manager.finish_job,id,**fields)

    @router.get('/budget')
    def budget(scope:str=Query(min_length=1,max_length=200)):
        return execute(manager.job_budget,scope)

    @router.get('/items/{kind}')
    def list_items(kind:str,scope:str=Query(min_length=1,max_length=200),limit:int=Query(default=100,ge=1,le=200),offset:int=Query(default=0,ge=0)):
        if kind not in ('evaluation','experience','strategy','job'):
            raise HTTPException(422,'Unsupported evolution item kind')
        return execute(manager.list_items,scope,kind,limit,offset)

    return router
