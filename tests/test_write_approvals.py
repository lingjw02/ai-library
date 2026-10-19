from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.policy import AccessPolicy
from backend.schema.models import utc_now
from backend.storage.canonical.sqlite_storage import CanonicalStorage


@pytest.fixture
def env(tmp_path):
    from backend.auth.approvals import ApprovalTickets, create_approval_router, hash_payload
    storage=CanonicalStorage(str(tmp_path/'approvals.db'))
    policy=AccessPolicy(storage)
    tickets=ApprovalTickets(storage)
    app=FastAPI()
    app.include_router(create_approval_router(storage, manager=tickets))
    return storage,policy,tickets,TestClient(app),hash_payload


def binding(hash_payload):
    return dict(principal='agent',scope='a',action='candidate.accept',target_id='candidate',target_revision=2,
                payload_hash=hash_payload({'claim':'exact requested write'}))


def issue(tickets,kwargs):
    return tickets.issue(**kwargs,reason='owner inspected this specific item',expires_at=(utc_now()+timedelta(minutes=10)).isoformat())


def test_owner_ticket_consumption_and_exact_retry(env):
    storage,policy,tickets,_,hash_payload=env
    item=issue(tickets,binding(hash_payload))
    policy.ensure_launch_grant('agent','a')
    with policy.activate('agent','a',write=True):
        with storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            consumed=tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write-1')
            assert consumed['replayed'] is False
            tickets.complete(conn,item['id'],operation_id='write-1',result={'node_id':'accepted'})
            conn.execute('COMMIT')
        with storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            replay=tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write-1')
            assert replay['replayed'] is True
            assert replay['result']=={'node_id':'accepted'}
            with pytest.raises(PermissionError):
                tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write-2')
            conn.execute('ROLLBACK')


def test_agents_cannot_issue_and_every_binding_field_is_checked(env):
    storage,policy,tickets,_,hash_payload=env
    item=issue(tickets,binding(hash_payload))
    policy.ensure_launch_grant('agent','a')
    with policy.activate('agent','a',write=True):
        with pytest.raises(PermissionError):
            issue(tickets,binding(hash_payload))
        for key,value in [('principal','other'),('scope','b'),('action','candidate.reject'),('target_id','other'),('target_revision',3),('payload_hash','0'*64)]:
            with storage.session() as conn:
                conn.execute('BEGIN IMMEDIATE')
                with pytest.raises((ValueError,PermissionError)):
                    tickets.consume(conn,item['id'],**{**binding(hash_payload),key:value},operation_id='write')
                conn.execute('ROLLBACK')


def test_rolled_back_write_does_not_burn_ticket(env):
    storage,_,tickets,_,hash_payload=env
    item=issue(tickets,binding(hash_payload))
    with pytest.raises(RuntimeError):
        with storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write')
            raise RuntimeError('write failed')
    assert tickets.get(item['id'])['status']=='PENDING'
    with storage.session() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write')['replayed'] is False
        tickets.complete(conn,item['id'],operation_id='write',result={'ok':True})
        conn.execute('COMMIT')


def test_expired_revoked_and_autocommit_consumption_rejected(env):
    storage,_,tickets,_,hash_payload=env
    item=issue(tickets,binding(hash_payload))
    with storage.session() as conn:
        with pytest.raises(ValueError):
            tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write')
    tickets.revoke(item['id'],reason='owner changed decision')
    with storage.session() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(PermissionError):
            tickets.consume(conn,item['id'],**binding(hash_payload),operation_id='write')
        conn.execute('ROLLBACK')
    with pytest.raises(ValueError):
        tickets.issue(**binding(hash_payload),reason='expired',expires_at=(utc_now()-timedelta(seconds=1)).isoformat())


def test_batch_issues_individual_tickets_and_api_blocks_self_approval(env):
    _,policy,tickets,client,hash_payload=env
    items=[binding(hash_payload),{**binding(hash_payload),'target_id':'candidate-2'}]
    response=client.post('/api/approvals/batch',json={'items':items,'reason':'reviewed selected checklist items','expires_at':(utc_now()+timedelta(minutes=10)).isoformat()})
    assert response.status_code==201
    assert len(response.json())==2
    assert response.json()[0]['id']!=response.json()[1]['id']
    assert len(tickets.list_pending(scope='a',principal='agent'))==2
    policy.ensure_launch_grant('agent','a')
    with policy.activate('agent','a',write=True):
        assert client.post('/api/approvals',json={**binding(hash_payload),'reason':'self','expires_at':(utc_now()+timedelta(minutes=10)).isoformat()}).status_code==403
        assert client.get('/api/approvals?scope=b').status_code==403


def test_concurrent_single_use_consumption_has_one_winner(env):
    from concurrent.futures import ThreadPoolExecutor
    storage,_,tickets,_,hash_payload=env
    item=issue(tickets,binding(hash_payload))
    def consume(operation):
        try:
            with storage.session() as conn:
                conn.execute('BEGIN IMMEDIATE')
                tickets.consume(conn,item['id'],**binding(hash_payload),operation_id=operation)
                tickets.complete(conn,item['id'],operation_id=operation,result={'winner':operation})
                conn.execute('COMMIT')
                return True
        except PermissionError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume,['one','two']))==[False,True]


def test_noninitializing_manager_under_agent_and_revoked_connection(env):
    from backend.auth.approvals import ApprovalTickets
    storage,policy,tickets,_,hash_payload=env
    item=issue(tickets,binding(hash_payload))
    policy.ensure_launch_grant('agent','a')
    with policy.activate('agent','a',write=True):
        manager=ApprovalTickets(storage,initialize=False)
        with pytest.raises(PermissionError):
            ApprovalTickets(storage)
    grant=policy.get_grant('agent','a')
    policy.update_grant('agent','a',read=False,write=False,expected_revision=grant['revision'],actor='owner')
    with pytest.raises(PermissionError):
        with policy.activate('agent','a',write=True):
            manager.get(item['id'])
