"""Local-owner write approvals bound to one exact selected item.

Consumption and the protected write MUST share the caller's transaction; call
complete before commit. Idempotent replay returns the recorded result and must
not repeat the write. These tickets never grant connection permissions.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.auth.policy import current_access, normalized_expiry
from backend.schema.models import utc_now


def hash_payload(payload):
    text=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(text.encode())>65536:
        raise ValueError('Approval payload exceeds 64 KiB')
    return hashlib.sha256(text.encode()).hexdigest()


class TicketBinding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    principal:str=Field(min_length=1,max_length=200)
    scope:str=Field(min_length=1,max_length=200)
    action:str=Field(min_length=1,max_length=200)
    target_id:str=Field(min_length=1,max_length=200)
    target_revision:StrictInt=Field(ge=1)
    payload_hash:str=Field(pattern='^[0-9a-f]{64}$')


class IssueTicket(TicketBinding):
    reason:str=Field(min_length=1,max_length=4000)
    expires_at:str


class IssueBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    items:list[TicketBinding]=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=4000)
    expires_at:str


class RevokeTicket(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=1,max_length=4000)


class ApprovalTickets:
    BINDING=('principal','scope','action','target_id','target_revision','payload_hash')

    def __init__(self,storage,*,initialize=True):
        self.storage=storage
        if not initialize:
            return
        self._owner()
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS write_approval_tickets (
                    id TEXT PRIMARY KEY, principal TEXT NOT NULL, scope TEXT NOT NULL,
                    action TEXT NOT NULL, target_id TEXT NOT NULL, target_revision INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                    issued_by TEXT NOT NULL, expires_at TEXT NOT NULL, issued_at TEXT NOT NULL,
                    operation_id TEXT, consumed_at TEXT, result_json TEXT);
                CREATE INDEX IF NOT EXISTS write_approvals_pending ON write_approval_tickets(scope,principal,status);
                CREATE TABLE IF NOT EXISTS write_approval_audit (
                    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, event TEXT NOT NULL,
                    actor TEXT NOT NULL, details_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
            ''')

    def _owner(self):
        if current_access.get() is not None:
            raise PermissionError('Only the local owner can issue or revoke write approval tickets')

    def _audit(self,conn,id,event,details):
        context=current_access.get()
        conn.execute('INSERT INTO write_approval_audit VALUES (?,?,?,?,?,?)',
                     (uuid.uuid4().hex,id,event,context.principal if context else 'local-owner',json.dumps(details,sort_keys=True),utc_now().isoformat()))

    def _expiry(self,value):
        value=normalized_expiry(value)
        if value is None:
            raise ValueError('Approval tickets require an explicit expiration')
        from datetime import datetime
        expires=datetime.fromisoformat(value)
        if not utc_now()<expires<=utc_now()+timedelta(days=7):
            raise ValueError('Approval expiration must be in the next 7 days')
        return value

    def _issue(self,conn,binding,reason,expires_at):
        value={**binding,'id':'write-approval-'+uuid.uuid4().hex,'status':'PENDING','reason':reason,
               'issued_by':'local-owner','expires_at':expires_at,'issued_at':utc_now().isoformat(),
               'operation_id':None,'consumed_at':None,'result':None}
        conn.execute('INSERT INTO write_approval_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)',
                     tuple(value[k] for k in ('id',*self.BINDING,'status','reason','issued_by','expires_at','issued_at')))
        self._audit(conn,value['id'],'ISSUED',{'binding':binding,'reason':reason,'expires_at':expires_at})
        self.storage._insert_outbox_event(conn,'WRITE_APPROVAL',value['id'],'WRITE_APPROVAL_ISSUED',
                                         {'scope':binding['scope'],'principal':binding['principal'],'action':binding['action'],'target_id':binding['target_id']})
        return value

    def issue(self,*,principal,scope,action,target_id,target_revision,payload_hash,reason,expires_at):
        self._owner()
        value=IssueTicket.model_validate(locals_to_binding(principal,scope,action,target_id,target_revision,payload_hash,reason,expires_at)).model_dump()
        expiry=self._expiry(value.pop('expires_at'))
        reason=value.pop('reason')
        if not reason.strip():
            raise ValueError('Owner approval requires a concrete reason')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            item=self._issue(conn,value,reason,expiry)
            conn.execute('COMMIT')
            return item

    def approve_batch(self,*,items,reason,expires_at):
        self._owner()
        value=IssueBatch.model_validate({'items':items,'reason':reason,'expires_at':expires_at}).model_dump()
        expiry=self._expiry(expires_at)
        fingerprints=[hash_payload(item) for item in value['items']]
        if len(set(fingerprints))!=len(fingerprints) or not reason.strip():
            raise ValueError('Select distinct checklist items and provide an approval reason')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            result=[self._issue(conn,item,reason,expiry) for item in value['items']]
            conn.execute('COMMIT')
            return result

    def _readable(self,scope,principal=None):
        context=current_access.get()
        if context is not None and (context.scope!=scope or (principal is not None and principal!=context.principal)):
            raise PermissionError('Approval ticket scope or principal exceeds connection authorization')

    def _row(self,row):
        if not row:
            return None
        value=dict(row)
        value['result']=json.loads(value.pop('result_json')) if value['result_json'] is not None else None
        return value

    def get(self,id):
        with self.storage.session() as conn:
            value=self._row(conn.execute('SELECT * FROM write_approval_tickets WHERE id=?',(id,)).fetchone())
            if value:
                self._readable(value['scope'],value['principal'])
            return value

    def list_pending(self,*,scope,principal=None,limit=100,offset=0):
        self._readable(scope,principal)
        context=current_access.get()
        if context is not None:
            principal=context.principal
        if not 1<=limit<=200 or offset<0:
            raise ValueError('Invalid bounded pagination')
        query='SELECT * FROM write_approval_tickets WHERE scope=? AND status=?'
        args=[scope,'PENDING']
        if principal:
            query+=' AND principal=?'
            args.append(principal)
        query+=' ORDER BY issued_at DESC,id LIMIT ? OFFSET ?'
        with self.storage.session() as conn:
            return [self._row(row) for row in conn.execute(query,(*args,limit,offset))]

    def consume(self,conn,ticket_id,*,principal,scope,action,target_id,target_revision,payload_hash,operation_id):
        binding=TicketBinding.model_validate({k:v for k,v in locals().copy().items() if k in self.BINDING}).model_dump()
        self._readable(scope,principal)
        if not conn.in_transaction:
            raise ValueError('Approval consumption must share an explicit protected-write transaction')
        if not isinstance(operation_id,str) or not 1<=len(operation_id)<=200:
            raise ValueError('A stable bounded operation ID is required')
        value=self._row(conn.execute('SELECT * FROM write_approval_tickets WHERE id=?',(ticket_id,)).fetchone())
        if not value:
            raise PermissionError('A local-owner approval ticket is required')
        if value['principal'] != principal or value['scope'] != scope:
            raise PermissionError('Approval belongs to another principal or scope')
        if any(value[k]!=binding[k] for k in self.BINDING):
            raise ValueError('Approval does not match principal, scope, action, item revision or payload')
        if value['status']=='CONSUMED' and value['operation_id']==operation_id and value['result'] is not None:
            return {**value,'replayed':True}
        if value['status']!='PENDING':
            raise PermissionError('Approval was already consumed, revoked or incompletely finalized')
        from datetime import datetime
        if datetime.fromisoformat(value['expires_at'])<=utc_now():
            raise PermissionError('Approval ticket expired')
        consumed_at=utc_now().isoformat()
        changed=conn.execute("UPDATE write_approval_tickets SET status='CONSUMED',operation_id=?,consumed_at=? WHERE id=? AND status='PENDING'",
                             (operation_id,consumed_at,ticket_id))
        if changed.rowcount!=1:
            raise ValueError('Approval was concurrently consumed')
        self._audit(conn,ticket_id,'CONSUMED',{'operation_id':operation_id,'binding':binding})
        return {**value,'status':'CONSUMED','operation_id':operation_id,'consumed_at':consumed_at,'replayed':False}

    def complete(self,conn,ticket_id,*,operation_id,result):
        if not conn.in_transaction:
            raise ValueError('Approval completion must share the protected-write transaction')
        if result is None:
            raise ValueError('A concrete operation result is required for idempotent replay')
        hash_payload(result)
        row=conn.execute('SELECT * FROM write_approval_tickets WHERE id=?',(ticket_id,)).fetchone()
        if not row or row['status']!='CONSUMED' or row['operation_id']!=operation_id or row['result_json'] is not None:
            raise ValueError('Approval is not awaiting this operation result')
        self._readable(row['scope'],row['principal'])
        conn.execute('UPDATE write_approval_tickets SET result_json=? WHERE id=?',(json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False),ticket_id))
        self._audit(conn,ticket_id,'COMPLETED',{'operation_id':operation_id,'result_hash':hash_payload(result)})
        self.storage._insert_outbox_event(conn,'WRITE_APPROVAL',ticket_id,'WRITE_APPROVAL_CONSUMED',
                                         {'scope':row['scope'],'action':row['action'],'target_id':row['target_id'],'operation_id':operation_id})

    def revoke(self,id,*,reason):
        self._owner()
        if not isinstance(reason,str) or not reason.strip() or len(reason)>4000:
            raise ValueError('Revocation reason is required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed=conn.execute("UPDATE write_approval_tickets SET status='REVOKED' WHERE id=? AND status='PENDING'",(id,))
            if changed.rowcount!=1:
                raise ValueError('Only a pending approval can be revoked')
            self._audit(conn,id,'REVOKED',{'reason':reason})
            conn.execute('COMMIT')
        return self.get(id)


def locals_to_binding(principal,scope,action,target_id,target_revision,payload_hash,reason,expires_at):
    return dict(principal=principal,scope=scope,action=action,target_id=target_id,target_revision=target_revision,
                payload_hash=payload_hash,reason=reason,expires_at=expires_at)


def create_approval_router(storage,*,manager=None):
    manager=manager or ApprovalTickets(storage)
    router=APIRouter(prefix='/api/approvals',tags=['write approvals'])

    def call(fn,*args,**kwargs):
        try:
            return fn(*args,**kwargs)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @router.post('',status_code=201)
    def issue(data:IssueTicket):
        return call(manager.issue,**data.model_dump())

    @router.post('/batch',status_code=201)
    def batch(data:IssueBatch):
        return call(manager.approve_batch,**data.model_dump())

    @router.get('')
    def pending(scope:str=Query(min_length=1,max_length=200),principal:str|None=Query(default=None,min_length=1,max_length=200),limit:int=Query(default=100,ge=1,le=200),offset:int=Query(default=0,ge=0)):
        return call(manager.list_pending,scope=scope,principal=principal,limit=limit,offset=offset)

    @router.get('/{id}')
    def get(id:str):
        item=call(manager.get,id)
        if item is None:
            raise HTTPException(404,'Approval ticket not found')
        return item

    @router.post('/{id}/revoke')
    def revoke(id:str,data:RevokeTicket):
        return call(manager.revoke,id,reason=data.reason)

    return router
